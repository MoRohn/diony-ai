"""Local Blueprint-22-style adapter for generated soundtrack cues.

Actions: `generate-cue` (text -> audio: score/texture) and `generate-foley`
(shot video + text -> audio). The heavy model call is delegated to a
per-generator on-host command template (the site pack pins the real CLI, the
way `/opt/wan22` and the CosyVoice runtime are provisioned):

    DIONY_AUDIOGEN_CLI_THINKSOUND        e.g. ".../python /opt/thinksound/infer.py --video {video} --prompt {prompt} --seconds {seconds} --seed {seed} --output {output}"
    DIONY_AUDIOGEN_CLI_ACE_STEP          e.g. ".../python /opt/ace-step/infer.py --prompt {prompt} --seconds {seconds} --seed {seed} --output {output}"
    DIONY_AUDIOGEN_CLI_STABLE_AUDIO_OPEN same placeholder contract

The adapter owns the honest part regardless of generator: it verifies the
output decodes, matches the requested duration, carries real energy, contains
NO speech (CosyVoice remains the only speech instrument), respects the true
peak ceiling — and writes a receipt with the generator id, prompt, seed, and
output sha256. A cue that fails any gate is deleted and the action fails.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shlex
import subprocess
import sys
from pathlib import Path
from typing import Any

from diony_producer.speech_audio import (
    SpeechAudioError as BackendError,
    ffmpeg as _ffmpeg,
    media_duration_seconds as _duration_seconds,
    measure_loudness,
    speech_activity,
)


def _loudness(path: Path, target: float, peak: float) -> tuple[float, float]:
    return measure_loudness(path, target_lufs=target, true_peak_dbtp=peak)


def speech_presence_score(samples: Any, *, sample_rate: int) -> float:
    return speech_activity(list(samples), sample_rate=sample_rate)[0]

GENERATOR_ENV = {
    "thinksound": "DIONY_AUDIOGEN_CLI_THINKSOUND",
    "ace-step": "DIONY_AUDIOGEN_CLI_ACE_STEP",
    "stable-audio-open": "DIONY_AUDIOGEN_CLI_STABLE_AUDIO_OPEN",
}

# Generated cues may never read as dialogue. This is the fdba451 speech
# detector run in reverse: voiced-speech structure above this fraction fails.
SPEECH_PRESENCE_CEILING = 0.20
DURATION_TOLERANCE = 0.10
TRUE_PEAK_CEILING_DBTP = -1.0


def _generator_command(generator: str, substitutions: dict[str, str]) -> list[str]:
    env_name = GENERATOR_ENV.get(generator)
    if env_name is None:
        raise BackendError(f"unknown audio generator: {generator}")
    template = os.environ.get(env_name, "").strip()
    if not template:
        raise BackendError(f"{env_name} is not configured on this site")
    command: list[str] = []
    for token in shlex.split(template):
        try:
            command.append(token.format(**substitutions))
        except (KeyError, IndexError) as exc:
            raise BackendError(f"{env_name} has an unknown placeholder: {token}") from exc
    return command


def _decode_measurements(path: Path) -> dict[str, float]:
    import numpy as np

    result = subprocess.run(
        [_ffmpeg(), "-v", "error", "-i", str(path), "-ac", "1", "-ar", "16000", "-f", "f32le", "-"],
        capture_output=True,
        timeout=120,
        check=False,
    )
    if result.returncode:
        raise BackendError("generated cue does not decode")
    samples = np.frombuffer(result.stdout, dtype="<f4").astype(np.float64)
    if samples.size < 1600:
        raise BackendError("generated cue is too short to measure")
    rms = float(np.sqrt(np.mean(samples * samples)))
    return {
        "rms": round(rms, 6),
        "speech_presence_score": speech_presence_score(samples, sample_rate=16_000),
    }


def _generate(request: dict[str, Any], *, action: str) -> dict[str, Any]:
    generator = str(request.get("generator") or "")
    prompt = " ".join(str(request.get("prompt") or "").split())
    seconds = float(request.get("seconds") or 0)
    seed = int(request.get("seed") or 0)
    output = Path(str(request.get("output") or ""))
    video = str(request.get("video") or "")
    if not prompt or seconds <= 0 or not str(output):
        raise BackendError("audio generation requires prompt, seconds, and output")
    if action == "generate-foley" and not Path(video).is_file():
        raise BackendError("generate-foley requires the shot's accepted video")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.unlink(missing_ok=True)
    command = _generator_command(
        generator,
        {
            "prompt": prompt,
            "seconds": f"{seconds:g}",
            "seed": str(seed),
            "output": str(output),
            "video": video,
        },
    )
    try:
        result = subprocess.run(command, capture_output=True, timeout=900, check=False)
    except (OSError, subprocess.SubprocessError) as exc:
        raise BackendError(f"audio generator failed: {exc}") from exc
    if result.returncode:
        detail = (result.stderr or result.stdout or b"").decode("utf-8", errors="replace")
        lines = [line.strip() for line in detail.splitlines() if line.strip()]
        raise BackendError(lines[-1] if lines else f"audio generator exited {result.returncode}")
    if not output.is_file() or output.stat().st_size <= 44:
        raise BackendError("audio generator returned no audio bytes")
    try:
        measured_seconds = _duration_seconds(output)
        measurements = _decode_measurements(output)
        if abs(measured_seconds - seconds) > max(0.25, seconds * DURATION_TOLERANCE):
            raise BackendError(
                f"generated cue is {measured_seconds:.2f}s but {seconds:g}s was requested"
            )
        if measurements["rms"] < 1e-4:
            raise BackendError("generated cue carries no measurable energy")
        if measurements["speech_presence_score"] >= SPEECH_PRESENCE_CEILING:
            raise BackendError(
                "generated cue contains voiced speech "
                f"({measurements['speech_presence_score']:.2f}); "
                "CosyVoice is the only speech instrument"
            )
        _, true_peak = _loudness(output, -24.0, TRUE_PEAK_CEILING_DBTP)
        if true_peak > TRUE_PEAK_CEILING_DBTP + 0.1:
            raise BackendError(
                f"generated cue true peak {true_peak:g} dBTP exceeds the "
                f"{TRUE_PEAK_CEILING_DBTP:g} dBTP ceiling"
            )
    except BackendError:
        output.unlink(missing_ok=True)
        raise
    payload = {
        "backend": f"diony-audiogen-{generator}-v1",
        "generator": generator,
        "prompt": prompt,
        "seed": seed,
        "seconds": round(measured_seconds, 3),
        "output": str(output),
        "sha256": hashlib.sha256(output.read_bytes()).hexdigest(),
        "measurements": {**measurements, "true_peak_dbtp": true_peak},
    }
    receipt = output.with_suffix(output.suffix + ".receipt.json")
    receipt.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return payload


def dispatch(action: str, request: dict[str, Any], response: Path) -> dict[str, Any]:
    del response
    if action in {"generate-cue", "generate-foley"}:
        return _generate(request, action=action)
    raise BackendError(f"unsupported audiogen action: {action}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Diony local generated-soundtrack adapter")
    parser.add_argument("action", choices=("generate-cue", "generate-foley"))
    parser.add_argument("--request", required=True)
    parser.add_argument("--response", required=True)
    args = parser.parse_args(argv)
    response = Path(args.response)
    try:
        request = json.loads(Path(args.request).read_text(encoding="utf-8"))
        payload = dispatch(args.action, request, response)
        if response.suffix.lower() == ".json":
            response.parent.mkdir(parents=True, exist_ok=True)
            response.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    except (BackendError, OSError, ValueError, KeyError, json.JSONDecodeError) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
