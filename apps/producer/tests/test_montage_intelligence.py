"""Blueprint 24 on the producer side: sampled-frame QC, chain drift, decision
log, final review + sidecars, sizing, transcript scoring, network guard."""

from __future__ import annotations

import json
import shutil
import socket
import subprocess
from pathlib import Path

import numpy as np
import pytest

from diony_contracts import Brief, CustomerKind, expand_idea
from diony_contracts.montage import DecisionCategory, DecisionOption, ReviewStatus
from diony_producer.decisions import decision_log_path, read_decision_log, record_decision
from diony_producer.quality import (
    chain_color_drift,
    channel_stats,
    frame_channel_stats,
    measure_frame_quality,
    measure_frame_quality_frames,
    sample_frame_indices,
)
from diony_producer.sizing import current_sizing
from diony_producer.speech_transcript import transcribe_line, word_accuracy
from diony_producer.vault import vault_root

FFMPEG = shutil.which("ffmpeg")
needs_ffmpeg = pytest.mark.skipif(not FFMPEG, reason="ffmpeg is required")


def _lavfi(path: Path, source: str, *, seconds: float = 0.5, audio: bool = False) -> Path:
    args = [FFMPEG, "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i", f"{source}:d={seconds}"]
    if audio:
        args += ["-f", "lavfi", "-i", f"sine=frequency=440:sample_rate=48000:duration={seconds}", "-c:a", "aac"]
    else:
        args += ["-an"]
    args += ["-c:v", "libx264", "-pix_fmt", "yuv420p", str(path)]
    subprocess.run(args, check=True)
    return path


def test_frame_quality_thresholds_on_synthetic_frames() -> None:
    rng = np.random.default_rng(7)
    textured = rng.integers(0, 255, (360, 640, 3)).astype(np.uint8)
    flat = np.full((360, 640, 3), 128, np.uint8)
    black = np.zeros((360, 640, 3), np.uint8)
    good = measure_frame_quality_frames([textured, textured])
    assert good.measured and good.sharpness_ok and good.exposure_ok and good.black_frames == 0
    soft = measure_frame_quality_frames([flat])
    assert soft.measured and not soft.sharpness_ok and not soft.contrast_ok
    dark = measure_frame_quality_frames([black, textured])
    assert dark.black_frames == 1 and not dark.exposure_ok
    assert "soft sampled frame" in " ".join(soft.advisory_notes)
    assert sample_frame_indices(81) == [0, 20, 40, 60, 80]


@needs_ffmpeg
def test_frame_quality_and_drift_measure_real_media(tmp_path: Path) -> None:
    clip = _lavfi(tmp_path / "test.mp4", "testsrc2=s=1280x720:r=24")
    receipt = measure_frame_quality(clip, frame_count=12)
    assert receipt.measured and receipt.frame_count == 5 and receipt.black_frames == 0
    dark = _lavfi(tmp_path / "black.mp4", "color=c=black:s=1280x720:r=24")
    assert measure_frame_quality(dark, frame_count=12).black_frames == 5
    first = frame_channel_stats(clip)
    last = frame_channel_stats(clip, last=True)
    assert first is not None and last is not None and len(first[0]) == 3
    grey = _lavfi(tmp_path / "grey.mp4", "color=c=gray:s=1280x720:r=24")
    drift = chain_color_drift(frame_channel_stats(grey), frame_channel_stats(dark))
    assert 0.4 < drift < 0.6
    assert chain_color_drift(channel_stats(np.zeros((4, 4, 3), np.uint8)), channel_stats(np.zeros((4, 4, 3), np.uint8))) == 0.0


def test_decision_log_appends_to_project_and_file() -> None:
    project = expand_idea(Brief(idea="A keeper repairs the lamp before the storm.", customer_kind=CustomerKind.SERIES, target_minutes=0.5))
    before = len(project.decisions)
    decision = record_decision(
        project,
        stage="camera",
        category=DecisionCategory.RETAKE,
        subject="sh-010 retake 2",
        options=[DecisionOption(id="directed", label="directed"), DecisionOption(id="repaired", label="repaired")],
        selected="repaired",
        reason="brain unreachable",
        confidence=1.5,
        shot_id="sh-010",
    )
    assert decision.confidence == 0.99 and project.decisions[-1].id == decision.id
    assert len(project.decisions) == before + 1
    rows = read_decision_log(project.id)
    assert rows and rows[-1]["selected"] == "repaired"
    again = record_decision(project, stage="camera", category=DecisionCategory.SEED_SELECTION, subject="seed",
                            options=[DecisionOption(id="a", label="a")], selected="a", reason="r")
    assert int(again.id.split("-")[1]) == int(decision.id.split("-")[1]) + 1
    assert decision_log_path(project.id).is_file()


def test_sizing_is_unmeasured_on_a_fresh_site() -> None:
    project = expand_idea(Brief(idea="A keeper repairs the lamp before the storm.", customer_kind=CustomerKind.SERIES, target_minutes=0.5))
    sizing = current_sizing(project, planned_takes=len(project.shots))
    assert sizing.basis == "unmeasured" and sizing.estimated_seconds_low is None


def test_transcript_word_accuracy_and_optional_model(monkeypatch, tmp_path: Path) -> None:
    accuracy, last_ok = word_accuracy("We move now, Marcus.", "we move now marcus")
    assert accuracy == 1.0 and last_ok
    accuracy, last_ok = word_accuracy("We move now, Marcus.", "we move")
    assert accuracy == 0.5 and not last_ok
    assert word_accuracy("", "anything") == (1.0, True)
    monkeypatch.delenv("DIONY_WHISPER_MODEL", raising=False)
    assert transcribe_line(tmp_path / "missing.wav", "line") is None


@needs_ffmpeg
def test_final_review_writes_sidecars_and_deliverables(tmp_path: Path, monkeypatch) -> None:
    from diony_contracts.models import MixLock
    from diony_producer.final_review import run_final_review

    project = expand_idea(
        Brief(idea='MARA: "We leave at dawn before the tide turns." TOM: "Then pack tonight."', customer_kind=CustomerKind.SERIES, target_minutes=0.5)
    )
    program_dir = vault_root() / "projects" / project.id / "program" / project.timeline.id
    program_dir.mkdir(parents=True, exist_ok=True)
    master = _lavfi(program_dir / "master_1080.mov", "testsrc2=s=1920x1080:r=24", seconds=1.0, audio=True)
    project.timeline.preview_path = str(master.relative_to(vault_root()))
    project.mix = MixLock(id="mix-1", measured_lufs=-24.2, true_peak_dbtp=-2.0)
    review = run_final_review(project, live=True)
    assert review.status in (ReviewStatus.PASS, ReviewStatus.REVISE), review.issues_found
    assert review.technical_probe.valid_container and review.technical_probe.width == 1920
    assert review.visual_spotcheck.frames_sampled == 5 and review.visual_spotcheck.black_frames == 0
    assert review.subtitle_check.expected and review.subtitle_check.present
    assert (program_dir / "master_1080.srt").read_text().startswith("1\n")
    assert (program_dir / "master_1080.vtt").read_text().startswith("WEBVTT")
    packet = json.loads((program_dir / "qc-packet.json").read_text())
    assert packet["final_review"]["status"] == review.status.value
    kinds = {item.kind.value for item in project.deliverables}
    assert {"prores_hq", "subtitles", "qc_packet"} <= kinds
    assert project.final_review is review and project.decisions[-1].category == DecisionCategory.FINAL_REVIEW
    skipped = run_final_review(project, live=False)
    assert skipped.status == ReviewStatus.SKIPPED


@needs_ffmpeg
def test_final_review_fails_a_black_master(tmp_path: Path) -> None:
    from diony_contracts.models import MixLock
    from diony_producer.final_review import run_final_review

    project = expand_idea(Brief(idea="A keeper repairs the lamp before the storm.", customer_kind=CustomerKind.SERIES, target_minutes=0.5))
    program_dir = vault_root() / "projects" / project.id / "program" / project.timeline.id
    program_dir.mkdir(parents=True, exist_ok=True)
    master = _lavfi(program_dir / "master_1080.mov", "color=c=black:s=1920x1080:r=24", seconds=1.0, audio=True)
    project.timeline.preview_path = str(master.relative_to(vault_root()))
    project.mix = MixLock(id="mix-1", measured_lufs=-24.0, true_peak_dbtp=-2.0)
    review = run_final_review(project, live=True)
    assert review.status == ReviewStatus.FAIL
    assert any("black" in issue for issue in review.issues_found)


def test_network_guard_blocks_external_and_allows_loopback() -> None:
    with pytest.raises(RuntimeError, match="Blocked a network connection"):  # NetworkBlocked
        socket.create_connection(("93.184.216.34", 80), timeout=0.2)
    server = socket.socket()
    server.bind(("127.0.0.1", 0))
    server.listen(1)
    try:
        client = socket.socket()
        client.settimeout(1)
        client.connect(("127.0.0.1", server.getsockname()[1]))
        client.close()
    finally:
        server.close()


def test_transcript_matching_is_fuzzy_for_names() -> None:
    accuracy, last_ok = word_accuracy("We move now, Marcus.", "we move now markus")
    assert accuracy == 1.0 and last_ok
    accuracy, _ = word_accuracy("the lighthouse keeper", "the lighthouse keepers")
    assert accuracy == 1.0
    accuracy, _ = word_accuracy("cat sat", "bat mat")
    assert accuracy == 0.0


@needs_ffmpeg
def test_seek_sampling_reads_long_media(tmp_path: Path) -> None:
    from diony_producer.quality import decode_sampled_frames

    clip = _lavfi(tmp_path / "long.mp4", "testsrc2=s=320x180:r=24", seconds=30.0)
    frames = decode_sampled_frames(clip, [0, 360, 719], fps=24.0)
    assert len(frames) == 3 and frames[0].shape == (360, 640, 3)
    receipt = measure_frame_quality(clip, frame_count=720, fps=24.0)
    assert receipt.measured and receipt.frame_count == 5 and receipt.black_frames == 0


@needs_ffmpeg
def test_final_review_is_cached_per_master_and_refuses_advance(tmp_path: Path) -> None:
    from diony_contracts.models import MixLock
    from diony_producer.final_review import (
        final_review_current,
        final_review_refuses_current_master,
        run_final_review,
    )

    project = expand_idea(Brief(idea="A keeper repairs the lamp before the storm.", customer_kind=CustomerKind.SERIES, target_minutes=0.5))
    program_dir = vault_root() / "projects" / project.id / "program" / project.timeline.id
    program_dir.mkdir(parents=True, exist_ok=True)
    master = _lavfi(program_dir / "master_1080.mov", "color=c=black:s=1920x1080:r=24", seconds=1.0, audio=True)
    project.timeline.preview_path = str(master.relative_to(vault_root()))
    project.timeline.program_sha256 = "a" * 64
    project.mix = MixLock(id="mix-1", measured_lufs=-24.0, true_peak_dbtp=-2.0)
    first = run_final_review(project, live=True)
    assert first.status == ReviewStatus.FAIL and first.master_sha256 == "a" * 64
    decisions = len(project.decisions)
    second = run_final_review(project, live=True)
    assert second is first and len(project.decisions) == decisions
    assert final_review_current(project) is first
    assert final_review_refuses_current_master(project)
    # A re-mix (new program bytes) earns a fresh measurement.
    project.timeline.program_sha256 = "b" * 64
    assert final_review_current(project) is None
    third = run_final_review(project, live=True)
    assert third is not first and len(project.decisions) == decisions + 1


def test_retake_clears_final_review_and_deliverables() -> None:
    from diony_contracts.models import Deliverable
    from diony_contracts.montage import skipped_final_review
    from diony_producer.advance import reopen_picture_for_retake

    project = expand_idea(Brief(idea="A keeper repairs the lamp before the storm.", customer_kind=CustomerKind.SERIES, target_minutes=0.5))
    project.final_review = skipped_final_review("tl", "m.mov", "test")
    project.deliverables = [Deliverable(id="dlv-x", kind="qc_packet", path="x.json")]
    reopen_picture_for_retake(project)
    assert project.final_review is None and project.deliverables == []


def test_gpu_start_script_exports_optional_lanes_only_when_staged() -> None:
    from diony_producer.gpu import _gpu_producer_start_script

    script = _gpu_producer_start_script("stamp-montage")
    assert "_diony_whisper_ready" in script and "_diony_clip_ready" in script
    assert "export DIONY_WHISPER_MODEL=/models/audio/faster-whisper-large-v3" in script
    assert "export DIONY_CLIP_MODEL=/models/diony/clip-vit-base-patch32" in script
    assert 'grep -Fqx "DIONY_WHISPER_MODEL=$DIONY_WHISPER_MODEL"' in script
    assert "montage-overlay.txt" in script and "wan-tts-overlay.txt" in script
    assert "unset DIONY_WHISPER_MODEL DIONY_CLIP_MODEL" in script
