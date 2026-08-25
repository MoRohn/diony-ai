"""Per-site measured-QC calibration from verified reference clips.

Blueprint 21 keeps measured temporal QC off until thresholds are calibrated.
This module is that calibration: it measures a set of known-good 720p negatives
on this site, derives gate values with margin around what good clips actually
measure, and writes a digest-protected receipt into the vault. A valid receipt
turns measured QC on by default; an explicit DIONY_MEASURED_QC_REQUIRED env
value (on or off) always wins.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from diony_producer.quality import (
    MEASUREMENT_VERSION,
    TemporalThresholds,
    measure_negative_quality,
)
from diony_producer.vault import record_activity, vault_root

CALIBRATION_VERSION = "diony-qc-calibration-v1"
CALIBRATION_REL = ".cluster/measured-qc-calibration.json"
MIN_CALIBRATION_CLIPS = 12

# Margins around what verified-good clips measure: the motion floor sits well
# under the calmest good clip, the morph/flicker ceilings well over the busiest.
MOTION_FLOOR_FRACTION = 0.5
CEILING_MARGIN = 1.5

# Absolute guard rails: calibration adapts the gates to the site's material but
# can never weaken them past these bounds.
MOTION_FLOOR_BOUNDS = (0.0005, 0.01)
RESIDUAL_MAX_BOUNDS = (0.05, 0.50)
FLICKER_MAX_BOUNDS = (0.02, 0.30)


class CalibrationError(RuntimeError):
    pass


@dataclass(frozen=True)
class ClipMeasurement:
    path: str
    sha12: str
    delta_p95: float
    residual_p95: float
    flicker_p95: float
    frame_count: int


def calibration_path() -> Path:
    return vault_root() / CALIBRATION_REL


def _clamp(value: float, bounds: tuple[float, float]) -> float:
    low, high = bounds
    return min(high, max(low, value))


def _payload_digest(payload: dict) -> str:
    scrubbed = {key: value for key, value in payload.items() if key != "digest"}
    return hashlib.sha256(
        json.dumps(scrubbed, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def measure_calibration_clips(paths: list[Path]) -> list[ClipMeasurement]:
    measurements: list[ClipMeasurement] = []
    for path in paths:
        if not path.is_file():
            raise CalibrationError(f"calibration clip is missing: {path}")
        receipt = measure_negative_quality(path)
        if not receipt.measured:
            raise CalibrationError(
                f"calibration clip could not be measured ({receipt.error or 'no frames'}): {path}"
            )
        measurements.append(
            ClipMeasurement(
                path=str(path),
                sha12=hashlib.sha256(path.read_bytes()).hexdigest()[:12],
                delta_p95=receipt.temporal_delta_p95,
                residual_p95=receipt.temporal_residual_p95,
                flicker_p95=receipt.flicker_luma_p95,
                frame_count=receipt.frame_count,
            )
        )
    return measurements


def derive_thresholds(measurements: list[ClipMeasurement]) -> dict:
    if len(measurements) < MIN_CALIBRATION_CLIPS:
        raise CalibrationError(
            f"calibration needs at least {MIN_CALIBRATION_CLIPS} verified clips; "
            f"got {len(measurements)}"
        )
    return {
        "delta_p95_min": round(
            _clamp(
                MOTION_FLOOR_FRACTION * min(item.delta_p95 for item in measurements),
                MOTION_FLOOR_BOUNDS,
            ),
            6,
        ),
        "residual_p95_max": round(
            _clamp(
                CEILING_MARGIN * max(item.residual_p95 for item in measurements),
                RESIDUAL_MAX_BOUNDS,
            ),
            6,
        ),
        "flicker_p95_max": round(
            _clamp(
                CEILING_MARGIN * max(item.flicker_p95 for item in measurements),
                FLICKER_MAX_BOUNDS,
            ),
            6,
        ),
    }


def write_calibration(paths: list[Path], *, calibrated_by: str = "") -> dict:
    measurements = measure_calibration_clips(paths)
    thresholds = derive_thresholds(measurements)
    payload = {
        "version": CALIBRATION_VERSION,
        "measurement_version": MEASUREMENT_VERSION,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "calibrated_by": calibrated_by.strip(),
        "clip_count": len(measurements),
        "clips": [measurement.__dict__ for measurement in measurements],
        "thresholds": thresholds,
    }
    payload["digest"] = _payload_digest(payload)
    destination = calibration_path()
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return payload


def load_calibration() -> dict | None:
    """The receipt, if present, current, complete, and digest-intact."""
    path = calibration_path()
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    if payload.get("version") != CALIBRATION_VERSION:
        return None
    if payload.get("measurement_version") != MEASUREMENT_VERSION:
        return None
    if int(payload.get("clip_count") or 0) < MIN_CALIBRATION_CLIPS:
        return None
    if payload.get("digest") != _payload_digest(payload):
        return None
    thresholds = payload.get("thresholds")
    if not isinstance(thresholds, dict):
        return None
    try:
        floor = float(thresholds["delta_p95_min"])
        residual = float(thresholds["residual_p95_max"])
        flicker = float(thresholds["flicker_p95_max"])
    except (KeyError, TypeError, ValueError):
        return None
    bounds_ok = (
        MOTION_FLOOR_BOUNDS[0] <= floor <= MOTION_FLOOR_BOUNDS[1]
        and RESIDUAL_MAX_BOUNDS[0] <= residual <= RESIDUAL_MAX_BOUNDS[1]
        and FLICKER_MAX_BOUNDS[0] <= flicker <= FLICKER_MAX_BOUNDS[1]
    )
    return payload if bounds_ok else None


def calibrated_thresholds() -> TemporalThresholds | None:
    payload = load_calibration()
    if payload is None:
        return None
    thresholds = payload["thresholds"]
    return TemporalThresholds(
        residual_p95_max=float(thresholds["residual_p95_max"]),
        flicker_p95_max=float(thresholds["flicker_p95_max"]),
        delta_p95_min=float(thresholds["delta_p95_min"]),
        source=f"calibration:{payload['digest'][:12]}",
    )


def measured_qc_required_default() -> bool:
    """Measured QC policy: an explicit env choice wins; else on iff calibrated."""
    raw = os.environ.get("DIONY_MEASURED_QC_REQUIRED", "").strip().lower()
    if raw in {"1", "true", "yes", "on"}:
        return True
    if raw in {"0", "false", "no", "off"}:
        return False
    return load_calibration() is not None


def verified_negative_paths() -> list[Path]:
    """Known-good calibration sources: PASS-verdict takes' 720 negatives."""
    root = vault_root()
    found: list[Path] = []
    for project_file in sorted(root.glob("projects/*/project.json")):
        try:
            project = json.loads(project_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        for take in project.get("takes") or []:
            qc = take.get("qc") or {}
            if qc.get("verdict") != "pass":
                continue
            for artifact in take.get("artifacts") or []:
                if artifact.get("kind") != "negative_720":
                    continue
                path = root / str(artifact.get("path") or "")
                if path.is_file():
                    found.append(path)
    return found


def maybe_auto_calibrate(*, calibrated_by: str = "conductor-auto") -> dict | None:
    """Arm measured picture QC once the vault holds enough verified clips.

    Runs at the end of a successful farm pass. An explicit operator
    DIONY_MEASURED_QC_REQUIRED choice (either way) always wins, an existing
    valid receipt is never overwritten, and any measurement problem simply
    defers to the next run — arming is a visible activity line, never a stall.
    """
    # Only an explicit operator OFF suppresses calibration. With the env forced
    # on, the gate already runs — a calibrated receipt then replaces the
    # shipped defaults with thresholds measured from this site's own material.
    if os.environ.get("DIONY_MEASURED_QC_REQUIRED", "").strip().lower() in {
        "0",
        "false",
        "no",
        "off",
    }:
        return None
    if load_calibration() is not None:
        return None
    paths = [
        path
        for path in verified_negative_paths()
        if path.suffix.lower() in {".mp4", ".mov"} and path.stat().st_size > 32
    ]
    if len(paths) < MIN_CALIBRATION_CLIPS:
        return None
    try:
        payload = write_calibration(paths, calibrated_by=calibrated_by)
    except (CalibrationError, OSError):
        return None
    thresholds = payload["thresholds"]
    record_activity(
        "qc",
        (
            f"Measured picture QC armed: calibrated from {payload['clip_count']} verified "
            f"clips (motion floor {thresholds['delta_p95_min']:g}, residual max "
            f"{thresholds['residual_p95_max']:g}, flicker max {thresholds['flicker_p95_max']:g})."
        ),
    )
    return payload


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Calibrate measured temporal QC thresholds for this site"
    )
    parser.add_argument("clips", nargs="*", help="known-good 720p negatives")
    parser.add_argument(
        "--from-vault",
        action="store_true",
        help="calibrate from PASS-verdict take negatives already in the vault",
    )
    parser.add_argument("--calibrated-by", default="", help="operator name for the receipt")
    args = parser.parse_args(argv)
    paths = [Path(clip) for clip in args.clips]
    if args.from_vault:
        paths.extend(verified_negative_paths())
    try:
        payload = write_calibration(paths, calibrated_by=args.calibrated_by)
    except CalibrationError as exc:
        print(str(exc))
        return 2
    thresholds = payload["thresholds"]
    print(
        f"Calibrated {payload['clip_count']} clips -> "
        f"motion floor {thresholds['delta_p95_min']}, "
        f"residual max {thresholds['residual_p95_max']}, "
        f"flicker max {thresholds['flicker_p95_max']} "
        f"({calibration_path()})"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
