"""Bounded, measured temporal evidence for Wan camera negatives."""

from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


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

    ffmpeg = os.environ.get("DIONY_FFMPEG", "").strip() or shutil.which("ffmpeg")
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
