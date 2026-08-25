import shutil
import subprocess
from datetime import datetime, timezone

import pytest

from diony_contracts.enums import GateName, MixLockState
from diony_contracts.fixture import demo_project
from diony_producer.advance import reopen_picture_for_retake
from diony_producer.mix import (
    MixError,
    _mux_program,
    bounce_mix,
    mix_evidence_blockers,
)


@pytest.mark.skipif(not shutil.which("ffmpeg") or not shutil.which("ffprobe"), reason="FFmpeg is required")
def test_bounce_mix_refuses_a_noise_only_soundtrack(tmp_path, monkeypatch) -> None:
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

    with pytest.raises(MixError, match="refusing a noise-only mix"):
        bounce_mix(project)

    assert project.mix.state == MixLockState.OPEN
    assert project.mix.stem_asset_ids == []
    assert project.timeline.gallery_removed_at is not None


def test_required_dialogue_never_falls_back_to_room_tone(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("DIONY_VAULT", str(tmp_path))
    monkeypatch.setenv("DIONY_PRODUCER_VAULT", str(tmp_path))
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

    with pytest.raises(MixError, match="Required dialogue.*voice package"):
        bounce_mix(project)

    assert project.mix.stem_asset_ids == []


def test_mix_assembles_verified_per_clip_voice_packages_in_timeline_order(
    tmp_path, monkeypatch
) -> None:
    from diony_producer.voice import DeterministicVoiceTestBackend, VoicePerformancePipeline

    monkeypatch.setenv("DIONY_VAULT", str(tmp_path))
    monkeypatch.setenv("DIONY_PRODUCER_VAULT", str(tmp_path))
    project = demo_project()
    for shot in project.shots[1:]:
        shot.audio_line = None
    shot = project.shots[0]
    take = project.take(shot.selected_take_id)
    take.voice_package = None
    backend = DeterministicVoiceTestBackend()
    pipeline = VoicePerformancePipeline(
        vault=tmp_path,
        synthesizer=backend,
        embedder=backend,
        aligner=backend,
        audio_processor=backend,
        audiovisual_qc=backend,
    )
    pipeline.prepare_take(project, take)
    negative = tmp_path / "voice-negative.mp4"
    negative.write_bytes(b"video evidence" * 8)
    pipeline.verify_animation(project, take, negative)
    project.timeline.preview_path = (
        f"projects/{project.id}/program/{project.timeline.id}/master_1080.mov"
    )
    master = tmp_path / project.timeline.preview_path
    master.parent.mkdir(parents=True)
    master.write_bytes(b"picture" * 16)
    written_sources: list[tuple[str, float]] = []
    from diony_producer import mix as mix_module

    original_filters = mix_module._voice_rail_filters

    def capture_filters(rows):
        written_sources.extend((row.package.take_id, row.start_seconds) for row in rows)
        return original_filters(rows)

    def fake_rail(destination, **_kwargs):
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(b"RIFF" + b"locked-voice" * 8)

    monkeypatch.setattr("diony_producer.mix._visual_qc_required", lambda: False)
    monkeypatch.setattr("diony_producer.mix._tool", lambda name: name)
    monkeypatch.setattr("diony_producer.mix._duration_seconds", lambda *_args: 20.0)
    monkeypatch.setattr("diony_producer.mix._voice_rail_filters", capture_filters)
    monkeypatch.setattr("diony_producer.mix._write_rail", fake_rail)
    monkeypatch.setattr("diony_producer.mix._write_program_mixdown", fake_rail)
    monkeypatch.setattr("diony_producer.mix._measure_stem", lambda *_args, **_kwargs: (-24.0, -1.2))
    monkeypatch.setattr("diony_producer.mix._mux_program", lambda *_args, **_kwargs: None)
    monkeypatch.setattr("diony_producer.mix.program_master_has_audio", lambda *_args: True)
    monkeypatch.setattr("diony_producer.mix.gallery_program_preview_path", lambda *_args: tmp_path / "preview.mp4")

    bounce_mix(project)

    assert written_sources == [(take.id, 0.0)]
    stem = project.asset(project.mix.stem_asset_ids[0])
    assert stem.role == "dialogue_stem"
    assert stem.metadata["voice_package_count"] == 1
    assert stem.metadata["voice_pipeline"] == "locked_per_clip"
    program = project.asset(project.mix.stem_asset_ids[-1])
    assert program.role == "program_mix_stem"
    assert program.metadata["rails"] == "dialogue"


def test_multi_turn_dialogue_counts_as_required_dialogue() -> None:
    from diony_contracts.models import DialogueTurn
    from diony_producer.mix import _dialogue_script

    project = demo_project()
    for shot in project.shots:
        shot.audio_line = None
    project.shots[0].dialogue_turns = [
        DialogueTurn(
            id="turn-1",
            character_id="char-mara",
            text="Stop the presses.",
        )
    ]

    assert _dialogue_script(project) == "Stop the presses."


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


def test_voice_rail_filters_fade_across_picture_junctions() -> None:
    from pathlib import Path

    from diony_producer.mix import VoiceRailRow, _voice_rail_filters

    rows = [
        VoiceRailRow(Path("a.wav"), 0.0, None, 3.378, 0.0, 0.3337),
        VoiceRailRow(Path("b.wav"), 3.044, None, 3.378, 0.3337, 0.0),
    ]
    filters, labels = _voice_rail_filters(rows)
    assert labels == ["[voice0]", "[voice1]"]
    # Outgoing clip fades out over the junction blend; incoming fades in.
    assert "afade=t=out:st=3.044300:d=0.333700" in filters[0]
    assert "afade=t=in" not in filters[0]
    assert "afade=t=in:st=0:d=0.333700" in filters[1]
    assert "adelay=3044|3044" in filters[1]


@pytest.mark.skipif(not shutil.which("ffmpeg") or not shutil.which("ffprobe"), reason="FFmpeg is required")
def test_program_mixdown_ducks_score_under_dialogue(tmp_path) -> None:
    from diony_producer.mix import _write_program_mixdown

    ffmpeg = shutil.which("ffmpeg")
    rails = {}
    for role, frequency in (("dialogue", 300), ("score", 220), ("foley", 800)):
        rail = tmp_path / f"{role}.wav"
        subprocess.run(
            [
                ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
                "-f", "lavfi",
                "-i", f"sine=frequency={frequency}:sample_rate=48000:duration=3",
                "-af", "volume=0.2,aformat=channel_layouts=stereo",
                "-c:a", "pcm_s16le", str(rail),
            ],
            check=True,
        )
        rails[role] = rail

    destination = tmp_path / "program-mix.wav"
    _write_program_mixdown(
        destination,
        ffmpeg=ffmpeg,
        rails=rails,
        duration=3.0,
        target=-24.0,
        peak=-1.0,
    )
    assert destination.is_file() and destination.stat().st_size > 44
    probe = subprocess.run(
        [
            shutil.which("ffprobe"), "-v", "error",
            "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1", str(destination),
        ],
        capture_output=True, text=True, check=True,
    )
    assert abs(float(probe.stdout.strip()) - 3.0) < 0.1
