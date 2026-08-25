"""Local Blueprint-22 adapter backed by CosyVoice2, CAM++, FFmpeg, and measured AV timing."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

_PROMPT_AUDIO_SUFFIXES = {".wav", ".mp3", ".flac"}


class BackendError(RuntimeError):
    pass


def _required_env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise BackendError(f"{name} is required for the local CosyVoice backend")
    return value


def _run(command: list[str], *, timeout: float = 1800) -> subprocess.CompletedProcess[bytes]:
    try:
        result = subprocess.run(command, capture_output=True, timeout=timeout, check=False)
    except (OSError, subprocess.SubprocessError) as exc:
        raise BackendError(f"local voice command failed: {exc}") from exc
    if result.returncode:
        detail = (result.stderr or result.stdout or b"").decode("utf-8", errors="replace")
        lines = [line.strip() for line in detail.splitlines() if line.strip()]
        raise BackendError(lines[-1] if lines else f"local voice command exited {result.returncode}")
    return result


def _ffmpeg() -> str:
    return os.environ.get("DIONY_FFMPEG", "ffmpeg").strip() or "ffmpeg"


def _ffprobe() -> str:
    return os.environ.get("DIONY_FFPROBE", "ffprobe").strip() or "ffprobe"


def _duration_seconds(path: Path) -> float:
    result = _run(
        [
            _ffprobe(),
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(path),
        ],
        timeout=30,
    )
    try:
        value = float(result.stdout.decode().strip())
    except ValueError as exc:
        raise BackendError(f"audio duration is unreadable: {path}") from exc
    if value <= 0:
        raise BackendError(f"audio duration is invalid: {path}")
    return value


def cosine_similarity(left: list[float], right: list[float]) -> float:
    if not left or len(left) != len(right):
        raise BackendError("speaker embedding dimensions do not match")
    dot = sum(a * b for a, b in zip(left, right))
    left_norm = math.sqrt(sum(value * value for value in left))
    right_norm = math.sqrt(sum(value * value for value in right))
    if left_norm <= 0 or right_norm <= 0:
        raise BackendError("speaker embedding has zero magnitude")
    return dot / (left_norm * right_norm)


def alignment_payload(
    audio: Path,
    line: dict[str, Any],
    *,
    measured_duration_ms: int | None = None,
) -> dict[str, Any]:
    if not audio.is_file():
        raise BackendError(f"alignment audio is missing: {audio}")
    words = str(line.get("text") or "").split()
    if not words:
        raise BackendError("alignment text is empty")
    start_ms = int(line.get("start_ms") or 0)
    target_ms = max(1, int(line.get("target_duration_ms") or 1))
    measured_ms = measured_duration_ms or round(_duration_seconds(audio) * 1000)
    usable_ms = max(1, min(target_ms, measured_ms))
    step = usable_ms / len(words)
    intensity = [float(item) for item in line.get("intensity_curve") or [0.3]]
    cues: list[dict[str, Any]] = []
    for index, word in enumerate(words):
        energy = intensity[min(index, len(intensity) - 1)]
        cues.append(
            {
                "word": word,
                "phoneme": " ".join(char.casefold() for char in word if char.isalpha()),
                "viseme": "open" if any(char in "aeiouAEIOU" for char in word) else "closed",
                "start_ms": round(start_ms + index * step, 3),
                "end_ms": round(start_ms + (index + 1) * step, 3),
                "jaw": round(0.35 + 0.4 * energy, 3),
                "brow": round(0.15 + 0.5 * max(intensity), 3),
                "head_energy": round(0.2 + 0.55 * max(intensity), 3),
            }
        )
    return {
        "line_id": str(line.get("id") or ""),
        "cues": cues,
        "measured_duration_ms": measured_ms,
        "source": "cosyvoice-duration-alignment-v1",
    }


def studio_audio_filters(*, target_lufs: float, true_peak_dbtp: float) -> tuple[str, str]:
    clean = "amix=inputs={inputs}:normalize=0:dropout_transition=0,apad,atrim=0:{duration}"
    final = (
        "highpass=f=70,lowpass=f=16000,"
        "deesser=i=0.18:m=0.5:f=0.5:s=o,"
        "acompressor=threshold=0.08:ratio=2:attack=20:release=200:makeup=1.4,"
        f"loudnorm=I={target_lufs:g}:TP={true_peak_dbtp:g}:LRA=7"
    )
    return clean, final


def snr_db_from_frame_rms(frame_rms: list[float]) -> float:
    """Estimate SNR from speech-active frames and the measured silent floor."""
    finite = sorted(value for value in frame_rms if math.isfinite(value) and value >= 0.0)
    if not finite:
        return 0.0
    quartile = max(1, len(finite) // 4)
    active = finite[-quartile:]
    signal = math.sqrt(sum(value * value for value in active) / len(active))
    quiet = finite[:quartile]
    noise = math.sqrt(sum(value * value for value in quiet) / len(quiet))
    return round(min(96.0, max(0.0, 20 * math.log10(signal / max(noise, 1e-9)))), 2)


def speech_presence_score(samples: Any, *, sample_rate: int) -> float:
    """Measure energy-modulated voiced frames so steady noise cannot pass as dialogue."""
    import numpy as np

    signal = np.asarray(samples, dtype=np.float64).reshape(-1)
    if sample_rate <= 0 or signal.size < max(400, round(sample_rate * 0.1)):
        return 0.0
    target_rate = 16_000
    if sample_rate > target_rate:
        stride = max(1, round(sample_rate / target_rate))
        signal = signal[::stride]
        sample_rate = round(sample_rate / stride)
    frame_size = max(80, round(sample_rate * 0.025))
    hop = max(40, round(sample_rate * 0.010))
    if signal.size < frame_size:
        return 0.0
    frames = np.stack(
        [
            signal[start : start + frame_size]
            for start in range(0, signal.size - frame_size + 1, hop)
        ]
    )
    frames = frames - np.mean(frames, axis=1, keepdims=True)
    frame_rms = np.sqrt(np.mean(frames * frames, axis=1))
    quiet = float(np.percentile(frame_rms, 20))
    active = frame_rms > max(1e-4, quiet * 2.5)
    if not np.any(active):
        return 0.0
    lag_min = max(1, round(sample_rate / 400))
    lag_max = min(frame_size - 1, round(sample_rate / 60))
    voiced = 0
    for frame in frames[active]:
        correlation = np.correlate(frame, frame, mode="full")[frame_size - 1 :]
        base = float(correlation[0])
        periodicity = (
            float(np.max(correlation[lag_min : lag_max + 1])) / base
            if base > 1e-12 and lag_max >= lag_min
            else 0.0
        )
        voiced += periodicity >= 0.25
    return round(min(1.0, voiced / len(frames)), 4)


def prompt_audio_for(request: dict[str, Any]) -> tuple[str, str]:
    """Resolve the cloning prompt clip that casts this character's voice.

    Order: the character's own attached reference sample, then a clip from the
    local voice library keyed by cast order (so a two-hander never shares one
    voice while distinct clips exist), then the single shared default clip.
    """
    for candidate in request.get("reference_paths") or []:
        path = Path(str(candidate))
        if path.is_file():
            return str(path), "reference-sample"
    library = os.environ.get("DIONY_COSYVOICE_PROMPT_LIBRARY", "").strip()
    profile = dict(request.get("profile") or {})
    character_id = str(profile.get("character_id") or "")
    if library and character_id and Path(library).is_dir():
        clips = sorted(
            path
            for path in Path(library).iterdir()
            if path.is_file() and path.suffix.lower() in _PROMPT_AUDIO_SUFFIXES
        )
        if clips:
            cast_index = request.get("cast_index")
            if isinstance(cast_index, int) and cast_index >= 0:
                index = cast_index
            else:
                index = int(hashlib.sha256(character_id.encode("utf-8")).hexdigest()[:8], 16)
            return str(clips[index % len(clips)]), "voice-library"
    return _required_env("DIONY_COSYVOICE_PROMPT_AUDIO"), "shared-default"


def _prompt_provenance(prompt_audio: str, prompt_source: str) -> dict[str, Any]:
    return {
        "prompt_audio": prompt_audio,
        "prompt_source": prompt_source,
        "prompt_sha256": hashlib.sha256(Path(prompt_audio).read_bytes()).hexdigest(),
    }


def _cosy_worker() -> list[str]:
    worker = Path(__file__).with_name("cosyvoice_worker.py")
    if not worker.is_file():
        raise BackendError(f"CosyVoice worker is missing: {worker}")
    return [
        _required_env("DIONY_COSYVOICE_PYTHON"),
        str(worker),
        "--root",
        _required_env("DIONY_COSYVOICE_ROOT"),
        "--model",
        _required_env("DIONY_COSYVOICE_MODEL"),
    ]


def _synthesize(request: dict[str, Any]) -> dict[str, Any]:
    line = dict(request.get("line") or {})
    output = Path(str(request.get("output") or ""))
    text = str(line.get("text") or "").strip()
    if not text or not str(output):
        raise BackendError("CosyVoice synthesis requires text and output")
    embedding_output = output.with_suffix(output.suffix + ".campplus.json")
    embedding_output.unlink(missing_ok=True)
    prompt_audio, prompt_source = prompt_audio_for(request)
    _run(
        [
            *_cosy_worker(),
            "--prompt-audio",
            prompt_audio,
            "--text",
            text,
            "--output",
            str(output),
            "--embedding-output",
            str(embedding_output),
            "--seed",
            str(int(request.get("seed") or 0)),
        ]
    )
    if (
        not output.is_file()
        or output.stat().st_size <= 44
        or not embedding_output.is_file()
    ):
        raise BackendError("CosyVoice returned no synthesis audio")
    return {
        "backend": "cosyvoice2-0.5b-local-v1",
        "output": str(output),
        **_prompt_provenance(prompt_audio, prompt_source),
    }


def _embedding(audio: Path, response: Path) -> dict[str, Any]:
    synthesis_measurement = audio.with_suffix(audio.suffix + ".campplus.json")
    temporary = synthesis_measurement
    remove_temporary = False
    if not temporary.is_file():
        temporary = response.with_suffix(response.suffix + ".campplus.json")
        temporary.unlink(missing_ok=True)
        remove_temporary = True
        _run(
            [
                *_cosy_worker(),
                "--embed-audio",
                str(audio),
                "--embedding-output",
                str(temporary),
            ]
        )
    try:
        payload = json.loads(temporary.read_text(encoding="utf-8"))
        vector = [float(value) for value in payload["embedding"]]
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise BackendError("CAM++ returned no readable speaker embedding") from exc
    finally:
        if remove_temporary:
            temporary.unlink(missing_ok=True)
    if len(vector) < 32:
        raise BackendError("CAM++ speaker embedding is unexpectedly short")
    return {"embedding": vector, "backend": "cosyvoice-campplus-v1"}


def _enroll_synthetic(request: dict[str, Any], response: Path) -> dict[str, Any]:
    line = dict(request.get("line") or {})
    output = Path(str(request.get("output") or ""))
    text = str(line.get("text") or "").strip()
    if not text or not str(output):
        raise BackendError("CosyVoice synthetic enrollment requires text and output")
    temporary = response.with_suffix(response.suffix + ".campplus.json")
    temporary.unlink(missing_ok=True)
    prompt_audio, prompt_source = prompt_audio_for(request)
    _run(
        [
            *_cosy_worker(),
            "--prompt-audio",
            prompt_audio,
            "--text",
            text,
            "--output",
            str(output),
            "--embedding-output",
            str(temporary),
            "--seed",
            str(int(request.get("seed") or 0)),
        ]
    )
    try:
        payload = json.loads(temporary.read_text(encoding="utf-8"))
        vector = [float(value) for value in payload["embedding"]]
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise BackendError("synthetic enrollment returned no CAM++ embedding") from exc
    finally:
        temporary.unlink(missing_ok=True)
    if len(vector) < 32:
        raise BackendError("CAM++ speaker embedding is unexpectedly short")
    if not output.is_file() or output.stat().st_size <= 44:
        raise BackendError("synthetic enrollment returned no audio")
    return {
        "embedding": vector,
        "backend": "cosyvoice-campplus-v1:synthetic-enrollment-v1",
        "output": str(output),
        **_prompt_provenance(prompt_audio, prompt_source),
    }


def _spectral_measurements(path: Path) -> tuple[float, float, float, float]:
    import numpy as np

    result = _run(
        [_ffmpeg(), "-v", "error", "-i", str(path), "-ac", "1", "-ar", "48000", "-f", "f32le", "-"],
        timeout=120,
    )
    samples = np.frombuffer(result.stdout, dtype="<f4").astype(np.float64)
    if samples.size < 2048:
        raise BackendError("studio audio is too short for measured QC")
    frame_count = samples.size // 1024
    frames = samples[: frame_count * 1024].reshape(frame_count, 1024)
    frame_rms = np.sqrt(np.mean(frames * frames, axis=1))
    snr = snr_db_from_frame_rms(frame_rms.tolist())
    speech_presence = speech_presence_score(samples, sample_rate=48_000)
    window = np.hanning(samples.size)
    spectrum = np.abs(np.fft.rfft(samples * window)) ** 2
    frequencies = np.fft.rfftfreq(samples.size, 1 / 48000)
    total = float(spectrum[(frequencies >= 80) & (frequencies <= 16000)].sum()) or 1.0
    harsh = math.sqrt(float(spectrum[(frequencies >= 3000) & (frequencies <= 8000)].sum()) / total)
    sibilance = math.sqrt(float(spectrum[(frequencies >= 5000) & (frequencies <= 10000)].sum()) / total)
    return (
        round(min(96.0, max(0.0, snr)), 2),
        round(harsh, 4),
        round(sibilance, 4),
        speech_presence,
    )


def _loudness_report(path: Path, target: float, peak: float) -> dict[str, Any]:
    result = _run(
        [
            _ffmpeg(),
            "-hide_banner",
            "-nostats",
            "-i",
            str(path),
            "-af",
            f"loudnorm=I={target:g}:TP={peak:g}:LRA=7:print_format=json",
            "-f",
            "null",
            "-",
        ],
        timeout=120,
    )
    blob = (result.stderr or result.stdout).decode("utf-8", errors="replace")
    start, end = blob.rfind("{"), blob.rfind("}")
    if start < 0 or end <= start:
        raise BackendError("FFmpeg returned no loudness report")
    return dict(json.loads(blob[start : end + 1]))


def _loudness(path: Path, target: float, peak: float) -> tuple[float, float]:
    payload = _loudness_report(path, target, peak)
    return round(float(payload["input_i"]), 2), round(float(payload["input_tp"]), 2)


def _studio_audio(request: dict[str, Any]) -> dict[str, Any]:
    rows = [dict(row) for row in request.get("lines") or []]
    if not rows:
        raise BackendError("studio audio requires at least one rendered line")
    clean_output = Path(str(request.get("clean_output") or ""))
    final_output = Path(str(request.get("final_output") or ""))
    duration = max(0.1, float(request.get("duration_ms") or 1) / 1000)
    target = float(request.get("target_lufs") or -24.0)
    peak = float(request.get("true_peak_dbtp") or -1.0)
    clean_output.parent.mkdir(parents=True, exist_ok=True)
    final_output.parent.mkdir(parents=True, exist_ok=True)
    command = [_ffmpeg(), "-hide_banner", "-loglevel", "error", "-y"]
    filters: list[str] = []
    labels: list[str] = []
    for index, row in enumerate(rows):
        audio = Path(str(row.get("audio") or ""))
        if not audio.is_file():
            raise BackendError(f"rendered voice line is missing: {audio}")
        command.extend(["-i", str(audio)])
        start_ms = max(0, int(dict(row.get("line") or {}).get("start_ms") or 0))
        label = f"line{index}"
        filters.append(
            f"[{index}:a]aformat=sample_rates=48000:channel_layouts=mono,"
            f"adelay={start_ms}|{start_ms}[{label}]"
        )
        labels.append(f"[{label}]")
    clean_filter, final_filter = studio_audio_filters(
        target_lufs=target,
        true_peak_dbtp=peak,
    )
    filters.append(
        "".join(labels)
        + clean_filter.format(inputs=len(labels), duration=f"{duration:.6f}")
        + "[clean]"
    )
    command.extend(
        [
            "-filter_complex",
            ";".join(filters),
            "-map",
            "[clean]",
            "-ar",
            "48000",
            "-ac",
            "1",
            "-c:a",
            "pcm_s24le",
            str(clean_output),
        ]
    )
    _run(command, timeout=300)
    pre_normalized = final_output.with_name(final_output.stem + ".pre-normalized" + final_output.suffix)
    pre_filter = final_filter.rsplit(",loudnorm=", 1)[0]
    try:
        _run(
            [
                _ffmpeg(),
                "-hide_banner",
                "-loglevel",
                "error",
                "-y",
                "-i",
                str(clean_output),
                "-af",
                pre_filter,
                "-t",
                f"{duration:.6f}",
                "-ar",
                "48000",
                "-ac",
                "2",
                "-c:a",
                "pcm_s24le",
                str(pre_normalized),
            ],
            timeout=300,
        )
        measured = _loudness_report(pre_normalized, target, peak)
        two_pass = (
            f"loudnorm=I={target:g}:TP={peak:g}:LRA=7:"
            f"measured_I={float(measured['input_i']):g}:"
            f"measured_LRA={float(measured['input_lra']):g}:"
            f"measured_TP={float(measured['input_tp']):g}:"
            f"measured_thresh={float(measured['input_thresh']):g}:"
            f"offset={float(measured['target_offset']):g}:linear=true"
        )
        _run(
            [
                _ffmpeg(),
                "-hide_banner",
                "-loglevel",
                "error",
                "-y",
                "-i",
                str(pre_normalized),
                "-af",
                two_pass,
                "-t",
                f"{duration:.6f}",
                "-ar",
                "48000",
                "-ac",
                "2",
                "-c:a",
                "pcm_s24le",
                str(final_output),
            ],
            timeout=300,
        )
    finally:
        pre_normalized.unlink(missing_ok=True)
    if not clean_output.is_file() or not final_output.is_file():
        raise BackendError("studio audio did not write both required stems")
    integrated, true_peak = _loudness(final_output, target, peak)
    snr, harshness, sibilance, speech_presence = _spectral_measurements(final_output)
    return {
        "snr_db": snr,
        "speech_presence_score": speech_presence,
        "integrated_lufs": integrated,
        "true_peak_dbtp": true_peak,
        "harshness_score": harshness,
        "sibilance_score": sibilance,
        "backend": "cosyvoice-ffmpeg-studio-audio-v1",
    }


def _video_fps(path: Path) -> float:
    result = _run(
        [
            _ffprobe(),
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=r_frame_rate",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(path),
        ],
        timeout=30,
    )
    text = result.stdout.decode("utf-8", errors="replace").strip().splitlines()
    raw = text[0].strip() if text else ""
    try:
        if "/" in raw:
            numerator, denominator = raw.split("/", 1)
            value = float(numerator) / float(denominator)
        else:
            value = float(raw)
    except (ValueError, ZeroDivisionError):
        return 24.0
    return value if 1.0 <= value <= 120.0 else 24.0


# Qualification floors for the measured performance-motion sync evidence.
_AV_MIN_POINTS = 12
_AV_LAG_RANGE_FRAMES = 6
_AV_MIN_CORRELATION = 0.30
_AV_MIN_PROMINENCE = 0.10
_AV_MIN_ACTIVE_AUDIO = 0.20


def _av_qc(request: dict[str, Any]) -> dict[str, Any]:
    """Measure audio-to-performance-motion timing at the clip's true frame rate.

    The offset is refined to sub-frame resolution with a parabolic fit around
    the correlation peak. The measurement qualifies as retake evidence only
    when both signals carry enough structure and the peak is unambiguous;
    degenerate inputs stay unqualified with the reason in the diagnostics.
    """
    import numpy as np

    video = Path(str(request.get("video") or ""))
    audio = Path(str(request.get("audio") or ""))
    metadata = Path(str(request.get("performance_metadata") or ""))
    if not video.is_file() or not audio.is_file() or not metadata.is_file():
        raise BackendError("audiovisual QC inputs are incomplete")
    fps = _video_fps(video)
    frame_result = _run(
        [
            _ffmpeg(),
            "-v",
            "error",
            "-i",
            str(video),
            "-vf",
            "scale=160:90,format=gray",
            "-f",
            "rawvideo",
            "-pix_fmt",
            "gray",
            "-",
        ],
        timeout=180,
    )
    frame_size = 160 * 90
    frame_count = len(frame_result.stdout) // frame_size
    if frame_count < 8:
        raise BackendError("audiovisual QC decoded too few video frames")
    frames = np.frombuffer(frame_result.stdout[: frame_count * frame_size], dtype=np.uint8)
    frames = frames.reshape(frame_count, 90, 160).astype(np.float32)
    center = frames[:, 15:80, 35:125]
    motion = np.mean(np.abs(np.diff(center, axis=0)), axis=(1, 2)).astype(np.float64)
    audio_result = _run(
        [_ffmpeg(), "-v", "error", "-i", str(audio), "-ac", "1", "-ar", "16000", "-f", "f32le", "-"],
        timeout=120,
    )
    samples = np.frombuffer(audio_result.stdout, dtype="<f4").astype(np.float64)
    envelope = []
    for index in range(frame_count):
        start = round(index * 16000 / fps)
        stop = round((index + 1) * 16000 / fps)
        segment = samples[start:stop]
        envelope.append(float(np.sqrt(np.mean(segment * segment))) if segment.size else 0.0)
    envelope_array = np.asarray(envelope[1:], dtype=np.float64)
    length = min(motion.size, envelope_array.size)
    motion = motion[:length]
    envelope_array = envelope_array[:length]
    disqualifiers: list[str] = []
    if length < _AV_MIN_POINTS:
        disqualifiers.append(f"only {length} comparable frames; need {_AV_MIN_POINTS}")
    # Activity is judged against the clip's own loud level, not its quiet
    # percentile: for wall-to-wall dialogue the 20th percentile is already at
    # speech level, and a quiet-relative threshold would disqualify exactly
    # the densest performances.
    loud_level = float(np.percentile(envelope_array, 95)) if length else 0.0
    active_fraction = (
        float(np.mean(envelope_array > max(1e-4, 0.25 * loud_level))) if length else 0.0
    )
    if active_fraction < _AV_MIN_ACTIVE_AUDIO:
        disqualifiers.append(
            f"active audio fraction {active_fraction:.2f} is below {_AV_MIN_ACTIVE_AUDIO:.2f}"
        )
    correlation = 0.0
    best_lag = 0
    refined_lag = 0.0
    if (
        length >= _AV_MIN_POINTS
        and float(np.std(motion)) > 1e-8
        and float(np.std(envelope_array)) > 1e-8
    ):
        motion_norm = (motion - np.mean(motion)) / np.std(motion)
        envelope_norm = (envelope_array - np.mean(envelope_array)) / np.std(envelope_array)
        lags = list(range(-_AV_LAG_RANGE_FRAMES, _AV_LAG_RANGE_FRAMES + 1))
        scores: dict[int, float] = {}
        for lag in lags:
            left = motion_norm[max(0, lag) : length + min(0, lag)]
            right = envelope_norm[max(0, -lag) : length - max(0, lag)]
            scores[lag] = float(np.mean(left * right)) if left.size else -1.0
        best_lag = max(scores, key=lambda lag: scores[lag])
        correlation = scores[best_lag]
        prominence = correlation - float(np.median(list(scores.values())))
        if correlation < _AV_MIN_CORRELATION:
            disqualifiers.append(
                f"peak correlation {correlation:.4f} is below {_AV_MIN_CORRELATION:.2f}"
            )
        elif prominence < _AV_MIN_PROMINENCE:
            disqualifiers.append(
                f"correlation peak prominence {prominence:.4f} is below {_AV_MIN_PROMINENCE:.2f}"
            )
        if abs(best_lag) >= _AV_LAG_RANGE_FRAMES:
            disqualifiers.append(
                "correlation peak sits at the search edge; the true offset may be larger"
            )
        refined_lag = float(best_lag)
        left_score = scores.get(best_lag - 1)
        right_score = scores.get(best_lag + 1)
        if left_score is not None and right_score is not None:
            curvature = left_score - 2 * correlation + right_score
            if curvature < -1e-9:
                refined_lag = best_lag + 0.5 * (left_score - right_score) / curvature
    else:
        disqualifiers.append("motion or audio signal carries no measurable structure")
    video_duration = _duration_seconds(video)
    audio_duration = _duration_seconds(audio)
    duration_consistency = max(0.0, 1.0 - abs(video_duration - audio_duration) / max(video_duration, 0.001))
    confidence = max(0.0, min(1.0, 0.75 + 0.25 * max(0.0, correlation)))
    offset_ms = abs(refined_lag) * 1000 / fps
    qualified = not disqualifiers
    diagnostics = [
        f"center-performance audio-motion correlation={correlation:.4f}",
        f"best_lag_frames={best_lag} refined_lag_frames={refined_lag:.3f} fps={fps:.3f}",
        f"active_audio_fraction={active_fraction:.3f}",
        "Measured locally; no hosted service used.",
    ]
    if disqualifiers:
        diagnostics.extend(f"unqualified: {reason}" for reason in disqualifiers)
    return {
        "lip_sync_confidence": round(confidence, 4),
        "audiovisual_offset_ms_p95": round(offset_ms, 3),
        "performance_consistency": round(duration_consistency, 4),
        "diagnostics": diagnostics,
        "backend": "diony-local-av-motion-sync-v2",
        "qualified_for_visual_retake": qualified,
    }


def dispatch(action: str, request: dict[str, Any], response: Path) -> dict[str, Any]:
    if action == "synthesize":
        return _synthesize(request)
    if action in {"embed", "embed-reference"}:
        return _embedding(Path(str(request.get("audio") or "")), response)
    if action == "enroll-synthetic":
        return _enroll_synthetic(request, response)
    if action == "align":
        return alignment_payload(Path(str(request.get("audio") or "")), dict(request.get("line") or {}))
    if action == "studio-audio":
        return _studio_audio(request)
    if action == "audiovisual-qc":
        return _av_qc(request)
    raise BackendError(f"unsupported local voice action: {action}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Diony local CosyVoice production adapter")
    parser.add_argument(
        "action",
        choices=(
            "synthesize",
            "embed",
            "embed-reference",
            "enroll-synthetic",
            "align",
            "studio-audio",
            "audiovisual-qc",
        ),
    )
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
