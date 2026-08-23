import shutil
import subprocess
from datetime import datetime, timezone

import pytest

from diony_contracts.enums import AssetKind, GateName, MixLockState
from diony_contracts.fixture import demo_project
from diony_producer.advance import reopen_picture_for_retake
from diony_producer.mix import (
    MixError,
    _mux_program,
    bounce_mix,
    mix_evidence_blockers,
    program_master_has_audio,
)


@pytest.mark.skipif(not shutil.which("ffmpeg") or not shutil.which("ffprobe"), reason="FFmpeg is required")
def test_bounce_mix_creates_measured_stem_and_audio_program_master(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("DIONY_VAULT", str(tmp_path))
    monkeypatch.setenv("DIONY_PRODUCER_VAULT", str(tmp_path))
    project = demo_project()
    for shot in project.shots:
        shot.audio_line = None
    project.timeline.preview_path = (
        f"projects/{project.id}/program/{project.timeline.id}/master_1080.mov"
    )
    project.timeline.picture_sha256 = "picture-sha"
    project.timeline.source_artifact_sha256s = [
        f"source-{index}" for index, _ in enumerate(project.shots)
    ]
    project.timeline.gallery_removed_at = datetime.now(timezone.utc)
    monkeypatch.setattr(
        "diony_producer.mix.gallery_program_preview_path",
        lambda *_args: tmp_path / "browser-preview.mp4",
    )
    master = tmp_path / project.timeline.preview_path
    master.parent.mkdir(parents=True)
    subprocess.run(
        [
            shutil.which("ffmpeg"),
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "color=c=black:s=1920x1080:r=24:d=0.5",
            "-an",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            str(master),
        ],
        check=True,
    )

    bounce_mix(project)

    assert project.mix.state == MixLockState.OPEN
    assert len(project.mix.stem_asset_ids) == 1
    stem = project.asset(project.mix.stem_asset_ids[0])
    assert stem.kind == AssetKind.AUDIO
    assert stem.role == "room_tone_stem"
    assert (tmp_path / stem.path).is_file()
    assert abs(project.mix.measured_lufs - (-24.0)) <= 1.0
    assert project.mix.true_peak_dbtp <= project.bible.true_peak_dbtp
    assert project.timeline.gallery_removed_at is None
    assert program_master_has_audio(project) is True
    assert mix_evidence_blockers(project, verify_program=True) == []


def test_required_dialogue_never_falls_back_to_room_tone(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("DIONY_VAULT", str(tmp_path))
    monkeypatch.setenv("DIONY_PRODUCER_VAULT", str(tmp_path))
    monkeypatch.setenv("DIONY_COSYVOICE_ROOT", str(tmp_path / "missing-cosyvoice"))
    monkeypatch.setenv("DIONY_COSYVOICE_MODEL", str(tmp_path / "missing-model"))
    monkeypatch.setenv("DIONY_COSYVOICE_PROMPT_AUDIO", str(tmp_path / "missing-voice.wav"))
    project = demo_project()
    project.timeline.preview_path = (
        f"projects/{project.id}/program/{project.timeline.id}/master_1080.mov"
    )
    master = tmp_path / project.timeline.preview_path
    master.parent.mkdir(parents=True)
    master.write_bytes(b"picture" * 16)
    monkeypatch.setattr("diony_producer.mix._visual_qc_required", lambda: False)
    monkeypatch.setattr("diony_producer.mix._tool", lambda name: name)
    monkeypatch.setattr("diony_producer.mix._duration_seconds", lambda *_args: 20.0)

    with pytest.raises(MixError, match="Required dialogue.*CosyVoice"):
        bounce_mix(project)

    assert project.mix.stem_asset_ids == []


@pytest.mark.skipif(not shutil.which("ffmpeg") or not shutil.which("ffprobe"), reason="FFmpeg is required")
def test_program_mux_never_shortens_the_locked_picture(tmp_path) -> None:
    ffmpeg = shutil.which("ffmpeg") or "ffmpeg"
    ffprobe = shutil.which("ffprobe") or "ffprobe"
    master = tmp_path / "master.mov"
    stem = tmp_path / "stem.wav"
    subprocess.run(
        [
            ffmpeg,
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "testsrc2=s=160x90:r=24000/1001",
            "-frames:v",
            "240",
            "-an",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            str(master),
        ],
        check=True,
    )
    subprocess.run(
        [
            ffmpeg,
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "anullsrc=r=48000:cl=stereo",
            "-t",
            "9.95",
            "-c:a",
            "pcm_s24le",
            str(stem),
        ],
        check=True,
    )

    _mux_program(master, stem, ffmpeg=ffmpeg)

    probe = subprocess.run(
        [
            ffprobe,
            "-v",
            "error",
            "-count_frames",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=nb_read_frames",
            "-of",
            "default=nw=1:nk=1",
            str(master),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    assert int(probe.stdout.strip()) == 240


def test_retake_invalidates_the_prior_mix_receipt() -> None:
    project = demo_project()
    picture = next(gate for gate in project.gates if gate.name == GateName.PICTURE)
    mix_gate = next(gate for gate in project.gates if gate.name == GateName.MIX)
    picture.open = False
    mix_gate.open = False
    project.mix.state = MixLockState.LOCKED
    project.mix.measured_lufs = -24.0
    project.mix.true_peak_dbtp = -1.5
    project.mix.stem_asset_ids = ["old-stem"]
    project.timeline.gallery_removed_at = None

    reopen_picture_for_retake(project)

    assert project.mix.state == MixLockState.OPEN
    assert project.mix.measured_lufs is None
    assert project.mix.true_peak_dbtp is None
    assert project.mix.stem_asset_ids == []
    assert project.timeline.gallery_removed_at is not None


def test_mix_receipt_rejects_missing_stem_bytes(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("DIONY_VAULT", str(tmp_path))
    monkeypatch.setenv("DIONY_PRODUCER_VAULT", str(tmp_path))
    project = demo_project()
    project.mix.stem_asset_ids = ["missing-stem"]
    project.mix.measured_lufs = -24.0
    project.mix.true_peak_dbtp = -2.0

    assert "Audio stem evidence is invalid." in mix_evidence_blockers(project)


@pytest.mark.skipif(not shutil.which("ffmpeg") or not shutil.which("ffprobe"), reason="FFmpeg is required")
def test_mix_refuses_a_flat_green_picture_master(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("DIONY_DRY_RUN", raising=False)
    monkeypatch.delenv("DIONY_FINISH_FAKE", raising=False)
    monkeypatch.setenv("DIONY_VAULT", str(tmp_path))
    monkeypatch.setenv("DIONY_PRODUCER_VAULT", str(tmp_path))
    project = demo_project()
    project.timeline.preview_path = (
        f"projects/{project.id}/program/{project.timeline.id}/master_1080.mov"
    )
    master = tmp_path / project.timeline.preview_path
    master.parent.mkdir(parents=True)
    subprocess.run(
        [
            shutil.which("ffmpeg"),
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "color=c=green:s=1920x1080:r=24:d=0.5",
            "-an",
            "-c:v",
            "prores_ks",
            "-profile:v",
            "3",
            str(master),
        ],
        check=True,
    )

    with pytest.raises(MixError, match="visual QC.*flat color"):
        bounce_mix(project)
