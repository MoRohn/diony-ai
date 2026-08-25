"""Site calibration turns measured temporal QC on with measured, bounded gates."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from diony_producer import calibration as calibration_module
from diony_producer.calibration import (
    CalibrationError,
    ClipMeasurement,
    calibrated_thresholds,
    derive_thresholds,
    load_calibration,
    measured_qc_required_default,
    write_calibration,
)
from diony_producer.quality import MEASUREMENT_VERSION, TemporalQualityReceipt


def _measurement(index: int) -> ClipMeasurement:
    return ClipMeasurement(
        path=f"clip-{index}.mp4",
        sha12="0" * 12,
        delta_p95=0.008 + index * 0.0002,
        residual_p95=0.05 + index * 0.001,
        flicker_p95=0.01 + index * 0.0005,
        frame_count=40,
    )


def test_derived_thresholds_carry_margin_and_respect_guard_rails() -> None:
    measurements = [_measurement(index) for index in range(12)]
    thresholds = derive_thresholds(measurements)
    # Floor sits under the calmest good clip; ceilings over the busiest.
    assert thresholds["delta_p95_min"] == pytest.approx(0.5 * 0.008, abs=1e-6)
    assert thresholds["residual_p95_max"] == pytest.approx(1.5 * (0.05 + 11 * 0.001), abs=1e-6)
    assert thresholds["flicker_p95_max"] == pytest.approx(1.5 * (0.01 + 11 * 0.0005), abs=1e-6)

    wild = [
        ClipMeasurement(
            path="wild.mp4",
            sha12="f" * 12,
            delta_p95=0.2,
            residual_p95=0.9,
            flicker_p95=0.8,
            frame_count=40,
        )
    ] * 12
    clamped = derive_thresholds(wild)
    assert clamped["delta_p95_min"] == 0.01
    assert clamped["residual_p95_max"] == 0.50
    assert clamped["flicker_p95_max"] == 0.30

    with pytest.raises(CalibrationError, match="at least 12"):
        derive_thresholds(measurements[:5])


def _fake_measure(tmp_path, monkeypatch, count: int = 12) -> list[Path]:
    clips = []
    for index in range(count):
        clip = tmp_path / f"good-{index}.mp4"
        clip.write_bytes(b"\x00\x00\x00\x18ftypmp42" + bytes([index]) * 64)
        clips.append(clip)
    monkeypatch.setattr(
        calibration_module,
        "measure_negative_quality",
        lambda path: TemporalQualityReceipt(
            measured=True,
            frame_count=40,
            temporal_delta_p95=0.009,
            temporal_residual_p95=0.06,
            flicker_luma_p95=0.012,
            morph_ok=True,
            flicker_ok=True,
            motion_ok=True,
        ),
    )
    return clips


def test_receipt_roundtrip_digest_and_policy(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("DIONY_VAULT", str(tmp_path / "vault"))
    monkeypatch.setenv("DIONY_PRODUCER_VAULT", str(tmp_path / "vault"))
    monkeypatch.delenv("DIONY_MEASURED_QC_REQUIRED", raising=False)

    # No receipt, no explicit choice: measured QC stays off.
    assert measured_qc_required_default() is False
    assert calibrated_thresholds() is None

    clips = _fake_measure(tmp_path, monkeypatch)
    payload = write_calibration(clips, calibrated_by="Rohn Springfield")
    assert payload["measurement_version"] == MEASUREMENT_VERSION
    assert payload["clip_count"] == 12

    loaded = load_calibration()
    assert loaded is not None
    assert loaded["digest"] == payload["digest"]

    # A valid receipt turns measured QC on by default, with calibrated gates.
    assert measured_qc_required_default() is True
    thresholds = calibrated_thresholds()
    assert thresholds is not None
    assert thresholds.delta_p95_min == pytest.approx(0.0045)
    assert thresholds.source.startswith("calibration:")

    # An explicit operator choice always wins over the receipt.
    monkeypatch.setenv("DIONY_MEASURED_QC_REQUIRED", "0")
    assert measured_qc_required_default() is False
    monkeypatch.setenv("DIONY_MEASURED_QC_REQUIRED", "1")
    assert measured_qc_required_default() is True
    monkeypatch.delenv("DIONY_MEASURED_QC_REQUIRED", raising=False)

    # Tampering with the receipt invalidates it and drops QC back to off.
    receipt_path = calibration_module.calibration_path()
    tampered = json.loads(receipt_path.read_text())
    tampered["thresholds"]["residual_p95_max"] = 0.49
    receipt_path.write_text(json.dumps(tampered))
    assert load_calibration() is None
    assert measured_qc_required_default() is False

    # A receipt from a different measurement version never applies.
    stale = dict(payload) | {"measurement_version": "diony-temporal-v1"}
    stale["digest"] = calibration_module._payload_digest(stale)
    receipt_path.write_text(json.dumps(stale))
    assert load_calibration() is None


def test_calibrated_site_fails_a_frozen_negative_by_default(tmp_path, monkeypatch) -> None:
    """With a valid receipt and no env choice, a frozen plate is a hard camera FAIL."""
    from diony_contracts.enums import QCVerdict
    from diony_contracts.models import Brief, Take
    from diony_contracts.studio import expand_idea
    from diony_producer import farm as farm_module

    monkeypatch.setenv("DIONY_VAULT", str(tmp_path / "vault"))
    monkeypatch.setenv("DIONY_PRODUCER_VAULT", str(tmp_path / "vault"))
    monkeypatch.delenv("DIONY_MEASURED_QC_REQUIRED", raising=False)
    clips = _fake_measure(tmp_path, monkeypatch)
    write_calibration(clips, calibrated_by="test")

    project = expand_idea(
        Brief(
            idea="A courier delivers a letter that rewrites the city map.",
            customer_kind="feature",
            target_minutes=0.5,
        )
    )
    shot = project.shots[0]
    take = Take(id="sh-010-t1", shot_id=shot.id, attempt=1, engine=shot.engine, seed=shot.seed)
    project.takes = [take]
    relative = f"projects/{project.id}/takes/{take.id}/negative_720.mp4"
    negative = tmp_path / "vault" / relative
    negative.parent.mkdir(parents=True)
    negative.write_bytes(b"\x00\x00\x00\x18ftypmp42" + b"frozen" * 32)
    monkeypatch.setattr(
        farm_module,
        "probe_media",
        lambda *args, **kwargs: {"width": 1280, "height": 720, "fps": 23.976, "frames": 81},
    )
    monkeypatch.setattr(
        farm_module,
        "measure_negative_quality",
        lambda path, thresholds=None: TemporalQualityReceipt(
            measured=True,
            frame_count=40,
            temporal_delta_p95=0.0001,
            temporal_residual_p95=0.02,
            flicker_luma_p95=0.005,
            morph_ok=True,
            flicker_ok=True,
            motion_ok=False,
        ),
    )

    farm_module.attach_negative(project, take, relative, shot.duration_frames, live=True)

    assert take.qc is not None
    assert take.qc.verdict == QCVerdict.FAIL
    assert take.qc.notes.startswith("Measured motion failed")


def test_v2_analysis_proxy_measures_a_real_clip(tmp_path) -> None:
    from diony_producer.quality import measure_negative_quality

    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        pytest.skip("FFmpeg tools are required")
    clip = tmp_path / "moving.mp4"
    subprocess.run(
        [
            ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
            "-f", "lavfi", "-i", "testsrc2=s=320x180:r=24:d=2",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", str(clip),
        ],
        check=True,
    )
    receipt = measure_negative_quality(clip)
    assert receipt.measured is True
    # 2 s at the 12 fps analysis rate.
    assert 20 <= receipt.frame_count <= 28
    assert receipt.motion_ok is True
    assert receipt.morph_ok is True


def _vault_with_verified_negatives(tmp_path, monkeypatch, count: int) -> None:
    monkeypatch.setenv("DIONY_VAULT", str(tmp_path / "vault"))
    monkeypatch.setenv("DIONY_PRODUCER_VAULT", str(tmp_path / "vault"))
    project_dir = tmp_path / "vault" / "projects" / "studio-auto"
    project_dir.mkdir(parents=True)
    takes = []
    for index in range(count):
        rel = f"projects/studio-auto/takes/sh-{index:03d}-t1/negative_720.mp4"
        negative = tmp_path / "vault" / rel
        negative.parent.mkdir(parents=True)
        negative.write_bytes(b"\x00\x00\x00\x18ftypmp42" + bytes([index]) * 64)
        takes.append(
            {
                "qc": {"verdict": "pass"},
                "artifacts": [{"kind": "negative_720", "path": rel}],
            }
        )
    (project_dir / "project.json").write_text(json.dumps({"takes": takes}))


def test_auto_calibration_arms_after_enough_verified_clips(tmp_path, monkeypatch) -> None:
    from diony_producer.calibration import maybe_auto_calibrate

    monkeypatch.delenv("DIONY_MEASURED_QC_REQUIRED", raising=False)
    _vault_with_verified_negatives(tmp_path, monkeypatch, 12)
    monkeypatch.setattr(
        calibration_module,
        "measure_negative_quality",
        lambda path: TemporalQualityReceipt(
            measured=True,
            frame_count=40,
            temporal_delta_p95=0.009,
            temporal_residual_p95=0.06,
            flicker_luma_p95=0.012,
            morph_ok=True,
            flicker_ok=True,
            motion_ok=True,
        ),
    )
    activity = []
    monkeypatch.setattr(
        calibration_module, "record_activity", lambda src, text, **kw: activity.append(text)
    )

    payload = maybe_auto_calibrate()

    assert payload is not None
    assert payload["calibrated_by"] == "conductor-auto"
    assert measured_qc_required_default() is True
    assert any("Measured picture QC armed" in text for text in activity)

    # An existing receipt is never overwritten.
    assert maybe_auto_calibrate() is None


def test_auto_calibration_defers_below_the_floor_and_honors_operator_choice(
    tmp_path, monkeypatch
) -> None:
    from diony_producer.calibration import maybe_auto_calibrate

    monkeypatch.delenv("DIONY_MEASURED_QC_REQUIRED", raising=False)
    _vault_with_verified_negatives(tmp_path, monkeypatch, 5)
    assert maybe_auto_calibrate() is None  # not enough verified clips yet

    # An explicit operator choice — even "off" — always wins over auto-arming.
    monkeypatch.setenv("DIONY_MEASURED_QC_REQUIRED", "0")
    _vault_with_verified_negatives(tmp_path / "second", monkeypatch, 12)
    assert maybe_auto_calibrate() is None
