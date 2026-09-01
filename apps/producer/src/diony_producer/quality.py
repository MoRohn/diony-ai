"""Bounded, measured temporal evidence for Wan camera negatives."""

from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Sequence


# v2: 320x180 @ 12 fps analysis proxy (v1 was 160x90 @ 8 fps). Stats scale with
# the proxy, so calibration receipts and stored QC evidence bind to this version.
MEASUREMENT_VERSION = "diony-temporal-v2"
ANALYSIS_WIDTH = 320
ANALYSIS_HEIGHT = 180
ANALYSIS_FPS = 12

# Shipped fallback gates, used only when neither an operator env override nor a
# site calibration receipt provides a measured value.
DEFAULT_RESIDUAL_P95_MAX = 0.20
DEFAULT_FLICKER_P95_MAX = 0.12
DEFAULT_DELTA_P95_MIN = 0.0015


@dataclass(frozen=True)
class TemporalThresholds:
    """Resolved gate values, with where each came from for the receipt trail."""

    residual_p95_max: float = DEFAULT_RESIDUAL_P95_MAX
    flicker_p95_max: float = DEFAULT_FLICKER_P95_MAX
    delta_p95_min: float = DEFAULT_DELTA_P95_MIN
    source: str = "default"


@dataclass(frozen=True)
class TemporalQualityReceipt:
    measured: bool
    frame_count: int = 0
    temporal_delta_p95: float = 0.0
    temporal_residual_p95: float = 0.0
    flicker_luma_p95: float = 0.0
    morph_ok: bool = False
    flicker_ok: bool = False
    motion_ok: bool = False
    error: str = ""


def _env_threshold(name: str) -> float | None:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return None
    try:
        return float(raw)
    except ValueError:
        return None


def resolve_thresholds(calibrated: TemporalThresholds | None = None) -> TemporalThresholds:
    """Explicit operator env overrides win, then site calibration, then defaults."""
    base = calibrated or TemporalThresholds()
    overrides = {
        "residual_p95_max": _env_threshold("DIONY_TEMPORAL_RESIDUAL_P95_MAX"),
        "flicker_p95_max": _env_threshold("DIONY_FLICKER_LUMA_P95_MAX"),
        "delta_p95_min": _env_threshold("DIONY_TEMPORAL_DELTA_P95_MIN"),
    }
    if all(value is None for value in overrides.values()):
        return base
    return TemporalThresholds(
        residual_p95_max=(
            overrides["residual_p95_max"]
            if overrides["residual_p95_max"] is not None
            else base.residual_p95_max
        ),
        flicker_p95_max=(
            overrides["flicker_p95_max"]
            if overrides["flicker_p95_max"] is not None
            else base.flicker_p95_max
        ),
        delta_p95_min=(
            overrides["delta_p95_min"]
            if overrides["delta_p95_min"] is not None
            else base.delta_p95_min
        ),
        source=f"{base.source}+env",
    )


def measure_temporal_frames(
    frames: Iterable[object],
    *,
    thresholds: TemporalThresholds | None = None,
) -> TemporalQualityReceipt:
    """Measure motion residual and global luminance changes on RGB frames.

    A global brightness pulse is flicker. Spatially incoherent frame-to-frame
    change is a conservative morph/discontinuity signal. Camera motion remains
    below the residual threshold because most pixels keep local structure.
    """
    import numpy as np

    gates = resolve_thresholds(thresholds)
    lumas = []
    for frame in frames:
        array = np.asarray(frame)
        if array.ndim != 3 or array.shape[2] < 3:
            return TemporalQualityReceipt(False, error="QC frame is not RGB")
        rgb = array[:, :, :3].astype(np.float32) / 255.0
        lumas.append(0.2126 * rgb[:, :, 0] + 0.7152 * rgb[:, :, 1] + 0.0722 * rgb[:, :, 2])
    if len(lumas) < 3:
        return TemporalQualityReceipt(False, frame_count=len(lumas), error="fewer than three QC frames")
    deltas: list[float] = []
    residuals: list[float] = []
    flicker: list[float] = []
    for previous, current in zip(lumas[:-1], lumas[1:], strict=True):
        difference = current - previous
        global_shift = float(np.mean(difference))
        deltas.append(float(np.mean(np.abs(difference))))
        # RMS makes spatially incoherent deformation visible without treating a
        # coherent exposure shift as morphology.
        residuals.append(float(np.sqrt(np.mean(np.square(difference - global_shift)))))
        flicker.append(abs(global_shift))
    delta_p95 = float(np.percentile(deltas, 95))
    residual_p95 = float(np.percentile(residuals, 95))
    flicker_p95 = float(np.percentile(flicker, 95))
    return TemporalQualityReceipt(
        measured=True,
        frame_count=len(lumas),
        temporal_delta_p95=round(delta_p95, 6),
        temporal_residual_p95=round(residual_p95, 6),
        flicker_luma_p95=round(flicker_p95, 6),
        morph_ok=residual_p95 <= gates.residual_p95_max,
        flicker_ok=flicker_p95 <= gates.flicker_p95_max,
        motion_ok=delta_p95 >= gates.delta_p95_min,
    )


def measure_negative_quality(
    path: Path,
    *,
    thresholds: TemporalThresholds | None = None,
) -> TemporalQualityReceipt:
    """Decode a small analysis stream; never retain full-resolution video."""
    import numpy as np

    from diony_producer.gallery_join import resolve_media_tool

    ffmpeg = resolve_media_tool("ffmpeg")
    if not ffmpeg:
        return TemporalQualityReceipt(False, error="FFmpeg is unavailable")
    width, height = ANALYSIS_WIDTH, ANALYSIS_HEIGHT
    try:
        process = subprocess.run(
            [
                ffmpeg,
                "-hide_banner",
                "-loglevel",
                "error",
                "-i",
                str(path),
                "-vf",
                f"scale={width}:{height}:flags=area,fps={ANALYSIS_FPS}",
                "-an",
                "-f",
                "rawvideo",
                "-pix_fmt",
                "rgb24",
                "pipe:1",
            ],
            capture_output=True,
            timeout=60,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return TemporalQualityReceipt(False, error=str(exc)[:300])
    if process.returncode != 0:
        return TemporalQualityReceipt(
            False,
            error=(process.stderr.decode("utf-8", errors="replace")[-300:] or "FFmpeg QC decode failed"),
        )
    frame_bytes = width * height * 3
    frames = [
        np.frombuffer(process.stdout[index : index + frame_bytes], dtype=np.uint8).reshape(
            height, width, 3
        )
        for index in range(0, len(process.stdout) - frame_bytes + 1, frame_bytes)
    ]
    return measure_temporal_frames(frames, thresholds=thresholds)


# --------------------------------------------------------------------------- sampled-frame evidence
#
# Blueprint 24. Five frames (first, quarter, middle, three-quarter, last) on a
# 640x360 luma proxy. Sharpness is the variance of a 3x3 Laplacian; a black
# frame is near-zero mean and spread; exposure and contrast are advisory
# until a site calibrates them. Only a black frame is a hard failure.

FRAME_QUALITY_VERSION = "diony-frame-quality-v1"
FRAME_QUALITY_WIDTH = 640
FRAME_QUALITY_HEIGHT = 360
DEFAULT_SHARPNESS_MIN = 20.0
DEFAULT_BRIGHTNESS_MIN = 35.0
DEFAULT_BRIGHTNESS_MAX = 225.0
DEFAULT_CONTRAST_MIN = 18.0
BLACK_FRAME_MEAN_MAX = 10.0
BLACK_FRAME_STD_MAX = 6.0


@dataclass(frozen=True)
class FrameQualityReceipt:
    measured: bool
    frame_count: int = 0
    sharpness_min: float = 0.0
    brightness_min: float = 0.0
    brightness_max: float = 0.0
    contrast_min: float = 0.0
    black_frames: int = 0
    sharpness_ok: bool = False
    exposure_ok: bool = False
    contrast_ok: bool = False
    error: str = ""
    # Which sampled frames (by decoded frame index) measured black, so a
    # program review can name the print that produced them.
    sample_indices: tuple[int, ...] = ()
    black_indices: tuple[int, ...] = ()

    @property
    def advisory_notes(self) -> list[str]:
        notes: list[str] = []
        if not self.measured:
            return notes
        if not self.sharpness_ok:
            notes.append(f"soft sampled frame (sharpness {self.sharpness_min:.1f})")
        if not self.exposure_ok:
            notes.append(
                f"sampled exposure outside range (luma {self.brightness_min:.0f}–{self.brightness_max:.0f})"
            )
        if not self.contrast_ok:
            notes.append(f"flat sampled contrast (std {self.contrast_min:.1f})")
        return notes


def _frame_threshold(name: str, default: float) -> float:
    value = _env_threshold(name)
    return default if value is None else value


def sample_frame_indices(frame_count: int, samples: int = 5) -> list[int]:
    """Evenly spaced decoded frame indices including the first and last, de-duplicated.

    Five samples on an 81-frame negative are the first, quarter, middle,
    three-quarter, and last frames; a program master asks for more.
    """
    total = max(1, int(frame_count))
    last = total - 1
    count = max(2, int(samples)) if total > 1 else 1
    picks = [round(last * position / (count - 1)) for position in range(count)] if count > 1 else [0]
    return list(dict.fromkeys(max(0, min(last, index)) for index in picks))


# Above this many frames a whole-file `select` decode is a full decode of the
# program; sample by seeking instead (ProRes masters are all-intra, so a seek
# lands on the exact frame).
SEEK_SAMPLING_FRAMES = 600


def decode_sampled_frames(
    path: Path,
    indices: Iterable[int],
    *,
    width: int = FRAME_QUALITY_WIDTH,
    height: int = FRAME_QUALITY_HEIGHT,
    fps: float | None = None,
) -> list[object]:
    """Decode exactly the requested frame indices as RGB arrays.

    One `select` pass for short negatives; with `fps` on a long program, one
    accurate seek per index so an hour-long master costs five decodes, not
    eighty-six thousand.
    """
    import numpy as np

    wanted = sorted(dict.fromkeys(int(index) for index in indices))
    if not wanted:
        return []
    from diony_producer.gallery_join import resolve_media_tool

    ffmpeg = resolve_media_tool("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("FFmpeg is unavailable")
    frame_bytes = width * height * 3
    if fps and fps > 0 and max(wanted) >= SEEK_SAMPLING_FRAMES:
        frames: list[object] = []
        for index in wanted:
            process = subprocess.run(
                [
                    ffmpeg,
                    "-hide_banner",
                    "-loglevel",
                    "error",
                    "-ss",
                    f"{index / fps:.6f}",
                    "-i",
                    str(path),
                    "-frames:v",
                    "1",
                    "-vf",
                    f"scale={width}:{height}:flags=area",
                    "-an",
                    "-f",
                    "rawvideo",
                    "-pix_fmt",
                    "rgb24",
                    "pipe:1",
                ],
                capture_output=True,
                timeout=120,
                check=False,
            )
            if process.returncode != 0 or len(process.stdout) < frame_bytes:
                raise RuntimeError(
                    process.stderr.decode("utf-8", errors="replace")[-300:] or f"frame {index} seek decode failed"
                )
            frames.append(np.frombuffer(process.stdout[:frame_bytes], dtype=np.uint8).reshape(height, width, 3))
        return frames
    select = "+".join(f"eq(n\\,{index})" for index in wanted)
    process = subprocess.run(
        [
            ffmpeg,
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            str(path),
            "-vf",
            f"select='{select}',scale={width}:{height}:flags=area",
            "-fps_mode",
            "passthrough",
            "-an",
            "-f",
            "rawvideo",
            "-pix_fmt",
            "rgb24",
            "pipe:1",
        ],
        capture_output=True,
        timeout=120,
        check=False,
    )
    if process.returncode != 0:
        raise RuntimeError(process.stderr.decode("utf-8", errors="replace")[-300:] or "frame decode failed")
    return [
        np.frombuffer(process.stdout[index : index + frame_bytes], dtype=np.uint8).reshape(height, width, 3)
        for index in range(0, len(process.stdout) - frame_bytes + 1, frame_bytes)
    ]


def measure_frame_quality_frames(
    frames: Iterable[object], *, indices: Sequence[int] | None = None
) -> FrameQualityReceipt:
    import numpy as np

    sharpness: list[float] = []
    brightness: list[float] = []
    contrast: list[float] = []
    black = 0
    black_at: list[int] = []
    for position, frame in enumerate(frames):
        array = np.asarray(frame)
        if array.ndim != 3 or array.shape[2] < 3:
            return FrameQualityReceipt(False, error="QC frame is not RGB")
        rgb = array[:, :, :3].astype(np.float32)
        luma = 0.2126 * rgb[:, :, 0] + 0.7152 * rgb[:, :, 1] + 0.0722 * rgb[:, :, 2]
        mean = float(np.mean(luma))
        std = float(np.std(luma))
        laplacian = (
            -4.0 * luma[1:-1, 1:-1]
            + luma[:-2, 1:-1]
            + luma[2:, 1:-1]
            + luma[1:-1, :-2]
            + luma[1:-1, 2:]
        )
        sharpness.append(float(np.var(laplacian)))
        brightness.append(mean)
        contrast.append(std)
        if mean < BLACK_FRAME_MEAN_MAX and std < BLACK_FRAME_STD_MAX:
            black += 1
            black_at.append(indices[position] if indices is not None and position < len(indices) else position)
    if not sharpness:
        return FrameQualityReceipt(False, error="no QC frames decoded")
    sharpness_min = _frame_threshold("DIONY_SHARPNESS_MIN", DEFAULT_SHARPNESS_MIN)
    brightness_min = _frame_threshold("DIONY_BRIGHTNESS_MIN", DEFAULT_BRIGHTNESS_MIN)
    brightness_max = _frame_threshold("DIONY_BRIGHTNESS_MAX", DEFAULT_BRIGHTNESS_MAX)
    contrast_min = _frame_threshold("DIONY_CONTRAST_MIN", DEFAULT_CONTRAST_MIN)
    lit = [value for value, mean in zip(sharpness, brightness) if mean >= BLACK_FRAME_MEAN_MAX]
    return FrameQualityReceipt(
        measured=True,
        frame_count=len(sharpness),
        sharpness_min=round(min(lit) if lit else min(sharpness), 4),
        brightness_min=round(min(brightness), 3),
        brightness_max=round(max(brightness), 3),
        contrast_min=round(min(contrast), 4),
        black_frames=black,
        sharpness_ok=(min(lit) if lit else 0.0) >= sharpness_min,
        exposure_ok=black == 0 and min(brightness) >= brightness_min and max(brightness) <= brightness_max,
        contrast_ok=black == 0 and min(contrast) >= contrast_min,
        sample_indices=tuple(int(value) for value in indices) if indices is not None else tuple(range(len(sharpness))),
        black_indices=tuple(black_at),
    )


def measure_frame_quality(
    path: Path, *, frame_count: int, fps: float | None = None, samples: int = 5
) -> FrameQualityReceipt:
    """Sampled sharpness/exposure/black-frame evidence on a landed negative or a master."""
    indices = sample_frame_indices(frame_count, samples)
    try:
        frames = decode_sampled_frames(path, indices, fps=fps)
    except (OSError, subprocess.SubprocessError, RuntimeError) as exc:
        return FrameQualityReceipt(False, error=str(exc)[:300])
    if not frames:
        return FrameQualityReceipt(False, error="no QC frames decoded")
    return measure_frame_quality_frames(frames, indices=indices[: len(frames)])


# --------------------------------------------------------------------------- chain colour drift

CHAIN_DRIFT_WARN = 0.06
CHAIN_DRIFT_MATCH = 0.02


def channel_stats(frame: object) -> tuple[list[float], list[float]]:
    """Per-channel mean and standard deviation on a 0–1 scale."""
    import numpy as np

    rgb = np.asarray(frame)[:, :, :3].astype(np.float32) / 255.0
    return (
        [round(float(value), 6) for value in rgb.reshape(-1, 3).mean(axis=0)],
        [round(float(value), 6) for value in rgb.reshape(-1, 3).std(axis=0)],
    )


def frame_channel_stats(path: Path, *, last: bool = False) -> tuple[list[float], list[float]] | None:
    """Channel stats of a media file's first (or last) frame on the analysis proxy."""
    import numpy as np

    from diony_producer.gallery_join import resolve_media_tool

    ffmpeg = resolve_media_tool("ffmpeg")
    if not ffmpeg or not Path(path).is_file():
        return None
    width, height = ANALYSIS_WIDTH, ANALYSIS_HEIGHT
    args = [ffmpeg, "-hide_banner", "-loglevel", "error"]
    if last:
        args += ["-sseof", "-0.1"]
    args += [
        "-i",
        str(path),
        "-vf",
        f"scale={width}:{height}:flags=area",
        "-frames:v",
        "1" if not last else "8",
        "-an",
        "-f",
        "rawvideo",
        "-pix_fmt",
        "rgb24",
        "pipe:1",
    ]
    try:
        process = subprocess.run(args, capture_output=True, timeout=60, check=False)
    except (OSError, subprocess.SubprocessError):
        return None
    frame_bytes = width * height * 3
    if process.returncode != 0 or len(process.stdout) < frame_bytes:
        return None
    usable = len(process.stdout) - len(process.stdout) % frame_bytes
    frame = np.frombuffer(process.stdout[usable - frame_bytes : usable], dtype=np.uint8).reshape(height, width, 3)
    return channel_stats(frame)


def chain_color_drift(
    current: tuple[list[float], list[float]],
    reference: tuple[list[float], list[float]],
) -> float:
    """Mean absolute per-channel mean shift between two frames (0–1)."""
    return round(
        sum(abs(a - b) for a, b in zip(current[0], reference[0])) / 3.0,
        6,
    )
