"""Measure and conform the speech Wan2.2 delivers inside a spoken negative.

Everything here is FFmpeg plus a little numpy: no model, no network. The lane
does not synthesize or judge a voice — Wan2.2 S2V's native TTS performs the
line and animates the face — so what remains to measure is whether the
negative actually carries voiced speech, whether the line finished inside the
Shot window, and whether the dialogue stem sits on the Bible loudness target
for the program mix.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import wave
from array import array
from pathlib import Path
from typing import Any

from diony_producer.gallery_join import resolve_media_tool


class SpeechAudioError(RuntimeError):
    """A media tool failed or the audio could not be measured."""


def ffmpeg() -> str:
    tool = resolve_media_tool("ffmpeg") or os.environ.get("DIONY_FFMPEG", "").strip() or shutil.which("ffmpeg")
    if not tool:
        raise SpeechAudioError("FFmpeg is required to measure the dialogue Wan2.2 delivered")
    return str(tool)


def ffprobe() -> str:
    tool = resolve_media_tool("ffprobe") or os.environ.get("DIONY_FFPROBE", "").strip() or shutil.which("ffprobe")
    if not tool:
        raise SpeechAudioError("FFprobe is required to measure the dialogue Wan2.2 delivered")
    return str(tool)


def _run(command: list[str], *, timeout: float = 300) -> subprocess.CompletedProcess[bytes]:
    try:
        result = subprocess.run(command, capture_output=True, check=False, timeout=timeout)
    except (OSError, subprocess.SubprocessError) as exc:
        raise SpeechAudioError(f"{Path(command[0]).name} failed: {exc}") from exc
    if result.returncode != 0:
        detail = result.stderr.decode("utf-8", errors="replace").strip()[-400:]
        raise SpeechAudioError(f"{Path(command[0]).name} exited {result.returncode}: {detail}")
    return result


def media_duration_seconds(path: Path) -> float:
    """Duration of a local clip, measured from the bytes rather than trusted."""
    try:
        with wave.open(str(path), "rb") as source:
            rate = source.getframerate()
            if rate > 0:
                return source.getnframes() / rate
    except (EOFError, OSError, wave.Error):
        pass
    try:
        result = _run(
            [ffprobe(), "-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
            timeout=60,
        )
    except SpeechAudioError:
        return 0.0
    try:
        return float(result.stdout.decode("utf-8", errors="replace").strip())
    except ValueError:
        return 0.0


def audio_streams(path: Path) -> list[dict[str, Any]]:
    """Audio streams in a media file, as ffprobe reports them."""
    result = _run(
        [ffprobe(), "-v", "error", "-select_streams", "a", "-show_entries", "stream=codec_name,sample_rate,channels,duration", "-of", "json", str(path)],
        timeout=60,
    )
    try:
        payload = json.loads(result.stdout.decode("utf-8", errors="replace") or "{}")
    except json.JSONDecodeError as exc:
        raise SpeechAudioError("ffprobe returned an unreadable stream report") from exc
    streams = payload.get("streams") if isinstance(payload, dict) else None
    return [dict(stream) for stream in streams or [] if isinstance(stream, dict)]


def prompt_clip_sample_rate(path: Path) -> int:
    """Sample rate of a prompt clip (Wan2.2 wants > 16 kHz)."""
    for stream in audio_streams(path):
        try:
            return int(stream.get("sample_rate") or 0)
        except (TypeError, ValueError):
            continue
    return 0


def extract_dialogue(video: Path, destination: Path) -> Path:
    """Copy the negative's audio track out as a 48 kHz mono 24-bit WAV, untouched otherwise."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    pending = destination.with_name(f".{destination.stem}.part{destination.suffix}")
    pending.unlink(missing_ok=True)
    _run(
        [
            ffmpeg(), "-nostdin", "-hide_banner", "-loglevel", "error", "-y",
            "-i", str(video), "-vn", "-ac", "1", "-ar", "48000", "-c:a", "pcm_s24le", str(pending),
        ]
    )
    if not pending.is_file() or pending.stat().st_size <= 44:
        pending.unlink(missing_ok=True)
        raise SpeechAudioError("the negative's audio track could not be extracted")
    pending.replace(destination)
    return destination


def decode_pcm(path: Path, *, sample_rate: int = 16_000) -> list[float]:
    """Decode to mono float samples at ``sample_rate`` for the activity measurement."""
    result = _run(
        [ffmpeg(), "-nostdin", "-hide_banner", "-loglevel", "error", "-i", str(path), "-ac", "1", "-ar", str(sample_rate), "-f", "f32le", "-"],
        timeout=120,
    )
    samples = array("f")
    samples.frombytes(result.stdout[: len(result.stdout) - len(result.stdout) % 4])
    return samples.tolist()


def speech_activity(samples: list[float], *, sample_rate: int) -> tuple[float, float | None]:
    """(voiced-speech presence, last speech-active moment in ms).

    Presence is the fraction of analysis frames that are both energetic and
    periodic (pitch between 60 and 400 Hz), so steady noise or a test tone
    cannot pass as dialogue. The last active moment is the end of the last
    energetic frame; a line still active at the end of the window was cut by
    the picture, not finished.
    """
    import numpy as np

    signal = np.asarray(samples, dtype=np.float64).reshape(-1)
    if sample_rate <= 0 or signal.size < max(400, round(sample_rate * 0.1)):
        return 0.0, None
    frame_size = max(80, round(sample_rate * 0.025))
    hop = max(40, round(sample_rate * 0.010))
    if signal.size < frame_size:
        return 0.0, None
    starts = range(0, signal.size - frame_size + 1, hop)
    frames = np.stack([signal[start : start + frame_size] for start in starts])
    frames = frames - np.mean(frames, axis=1, keepdims=True)
    frame_rms = np.sqrt(np.mean(frames * frames, axis=1))
    quiet = float(np.percentile(frame_rms, 20))
    active = frame_rms > max(1e-4, quiet * 2.5)
    if not np.any(active):
        return 0.0, None
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
    presence = round(min(1.0, voiced / len(frames)), 4)
    last_index = int(np.flatnonzero(active)[-1])
    last_active_ms = (last_index * hop + frame_size) / sample_rate * 1000.0
    return presence, round(last_active_ms, 1)


def loudness_report(path: Path, *, target_lufs: float, true_peak_dbtp: float) -> dict[str, Any]:
    result = _run(
        [
            ffmpeg(), "-nostdin", "-hide_banner", "-nostats", "-i", str(path),
            "-af", f"loudnorm=I={target_lufs:g}:TP={true_peak_dbtp:g}:LRA=7:print_format=json",
            "-f", "null", "-",
        ],
        timeout=120,
    )
    blob = (result.stderr or result.stdout).decode("utf-8", errors="replace")
    start, end = blob.rfind("{"), blob.rfind("}")
    if start < 0 or end <= start:
        raise SpeechAudioError("FFmpeg returned no loudness report")
    return dict(json.loads(blob[start : end + 1]))


def measure_loudness(path: Path, *, target_lufs: float, true_peak_dbtp: float) -> tuple[float, float]:
    """(integrated LUFS, true peak dBTP) of a stem."""
    payload = loudness_report(path, target_lufs=target_lufs, true_peak_dbtp=true_peak_dbtp)
    return round(float(payload["input_i"]), 2), round(float(payload["input_tp"]), 2)


def conform_mix_stem(
    dialogue: Path,
    destination: Path,
    *,
    duration_seconds: float,
    target_lufs: float,
    true_peak_dbtp: float,
) -> Path:
    """Write the program-mix stem: the delivered dialogue on the Bible loudness target.

    Deterministic two-pass ``loudnorm`` (linear gain when the measurement
    allows it) after a 70 Hz high-pass, 48 kHz stereo 24-bit, padded or cut to
    the picture. Nothing here re-performs the line; the voice is Wan's.
    """
    destination.parent.mkdir(parents=True, exist_ok=True)
    duration = f"{max(0.1, float(duration_seconds)):.6f}"
    pre = destination.with_name(f".{destination.stem}.pre{destination.suffix}")
    pending = destination.with_name(f".{destination.stem}.part{destination.suffix}")
    pre.unlink(missing_ok=True)
    pending.unlink(missing_ok=True)
    try:
        _run(
            [
                ffmpeg(), "-nostdin", "-hide_banner", "-loglevel", "error", "-y", "-i", str(dialogue),
                "-af", f"highpass=f=70,apad,atrim=0:{duration}",
                "-ar", "48000", "-ac", "2", "-c:a", "pcm_s24le", str(pre),
            ]
        )
        measured = loudness_report(pre, target_lufs=target_lufs, true_peak_dbtp=true_peak_dbtp)
        two_pass = (
            f"loudnorm=I={target_lufs:g}:TP={true_peak_dbtp:g}:LRA=7:"
            f"measured_I={float(measured['input_i']):g}:"
            f"measured_LRA={float(measured['input_lra']):g}:"
            f"measured_TP={float(measured['input_tp']):g}:"
            f"measured_thresh={float(measured['input_thresh']):g}:"
            f"offset={float(measured['target_offset']):g}:linear=true"
        )
        _run(
            [
                ffmpeg(), "-nostdin", "-hide_banner", "-loglevel", "error", "-y", "-i", str(pre),
                "-af", two_pass, "-t", duration,
                "-ar", "48000", "-ac", "2", "-c:a", "pcm_s24le", str(pending),
            ]
        )
    finally:
        pre.unlink(missing_ok=True)
    if not pending.is_file() or pending.stat().st_size <= 44:
        pending.unlink(missing_ok=True)
        raise SpeechAudioError("the dialogue mix stem was not written")
    pending.replace(destination)
    return destination
