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


def test_sampling_density_scales_with_runtime() -> None:
    from diony_contracts.montage import program_sample_count, shots_at_frames

    assert program_sample_count(81, 24.0) == 5
    assert program_sample_count(24 * 60 * 10, 24.0) == 10
    assert program_sample_count(24 * 3600, 24.0) == 32
    assert sample_frame_indices(81, 5) == [0, 20, 40, 60, 80]
    assert sample_frame_indices(1000, 3) == [0, 500, 999]
    project = expand_idea(Brief(idea="A keeper repairs the lamp before the storm.", customer_kind=CustomerKind.SERIES, target_minutes=0.5))
    first = project.timeline.events[0]
    second = project.timeline.events[1]
    assert shots_at_frames(project, [first.start_frame]) == [first.shot_id]
    assert shots_at_frames(project, [second.start_frame + 1, first.start_frame]) == [first.shot_id, second.shot_id]
    assert shots_at_frames(project, [10**9]) == [project.timeline.events[-1].shot_id]


def test_transcript_normalisation_and_missed_words() -> None:
    from diony_producer.speech_transcript import normalize_words, number_words, score_transcript

    assert number_words(0) == ["zero"] and number_words(21) == ["twenty", "one"] and number_words(305) == ["three", "hundred", "five"]
    assert normalize_words("We're leaving at 21, don't wait") == ["we", "are", "leaving", "at", "twenty", "one", "do", "not", "wait"]
    accuracy, last_ok, missed = score_transcript("We move now, Marcus, before the tide.", "we move now marcus before the")
    assert missed == ["tide"] and not last_ok and 0.8 < accuracy < 1.0


@needs_ffmpeg
def test_failed_final_review_retakes_the_offending_print(tmp_path: Path, monkeypatch) -> None:
    from diony_contracts.enums import GateName, QCVerdict
    from diony_contracts.models import MixLock
    from diony_producer import advance as advance_module
    from diony_producer.advance import remediate_final_review
    from diony_producer.final_review import run_final_review

    project = expand_idea(Brief(idea="A keeper repairs the lamp before the storm.", customer_kind=CustomerKind.SERIES, target_minutes=0.5))
    # Land a judged print on every shot so the timeline has selected takes.
    from diony_producer.farm import new_generation_take

    for shot in project.shots:
        take = new_generation_take(project, shot, live=False)
        take.qc = take.qc.model_copy(update={"verdict": QCVerdict.PASS, "identity_ok": True})
        project.takes.append(take)
        shot.selected_take_id = take.id
    from diony_contracts.stitch import assemble_program

    assemble_program(project)
    program_dir = vault_root() / "projects" / project.id / "program" / project.timeline.id
    program_dir.mkdir(parents=True, exist_ok=True)
    master = _lavfi(program_dir / "master_1080.mov", "color=c=black:s=1920x1080:r=24", seconds=1.0, audio=True)
    project.timeline.preview_path = str(master.relative_to(vault_root()))
    project.timeline.program_sha256 = "c" * 64
    project.mix = MixLock(id="mix-1", measured_lufs=-24.0, true_peak_dbtp=-2.0)
    for gate in project.gates:
        if gate.name in (GateName.PICTURE,):
            gate.open = False
    review = run_final_review(project, live=True)
    assert review.status == ReviewStatus.FAIL and review.recommended_action.value == "retake"
    assert review.offending_shot_ids and review.offending_shot_ids[0] == project.shots[0].id
    monkeypatch.setattr(advance_module, "start_farm", lambda project, takes: None)
    monkeypatch.setattr(advance_module, "enter_farm", lambda: None)
    before = {shot.id: shot.selected_take_id for shot in project.shots}
    retries = remediate_final_review(project, live=False)
    assert retries, "the offending print is retaken within the cap"
    assert {retry.shot_id for retry in retries} <= set(review.offending_shot_ids)
    for retry in retries:
        assert project.shot(retry.shot_id).selected_take_id == retry.id != before[retry.shot_id]
        assert retry.attempt == 2
    assert project.final_review is None  # picture reopened; measured again after the retake
    assert any(d.subject == "final review remediation" and d.selected == "retake" for d in project.decisions)
    picture = next(gate for gate in project.gates if gate.name == GateName.PICTURE)
    assert picture.open


def test_production_standard_reports_a_failed_final_review() -> None:
    from diony_contracts.montage import (
        AudioSpotcheck,
        PromisePreservation,
        SubtitleCheck,
        TechnicalProbe,
        VisualSpotcheck,
        evaluate_final_review,
    )
    from diony_producer.run import production_standard

    project = expand_idea(Brief(idea="A keeper repairs the lamp before the storm.", customer_kind=CustomerKind.SERIES, target_minutes=0.5))
    project.timeline.program_sha256 = "d" * 64
    project.final_review = evaluate_final_review(
        timeline_id=project.timeline.id, master_path="m.mov", master_sha256="d" * 64,
        technical=TechnicalProbe(valid_container=True, has_audio=True, codec="prores"),
        visual=VisualSpotcheck(frames_sampled=5, black_frames=1), audio=AudioSpotcheck(),
        promise=PromisePreservation(), subtitles=SubtitleCheck(), offending_shot_ids=["sh-010"],
    )
    standard = production_standard(project, clips=[], camera="live")
    assert standard["status"] == "QC HOLD" and standard["master_ready"] is False
    assert any(item.startswith("Final review failed:") for item in standard["blockers"])
    assert standard["final_review"]["offending_shot_ids"] == ["sh-010"] and standard["final_review"]["current"]
    assert standard["rundown_review"]["verdict"] in {"strong", "acceptable", "revise", "fail"}


def test_stop_clears_final_review_and_deliverables() -> None:
    from diony_contracts.enums import ProductionStopDisposition
    from diony_contracts.models import Deliverable
    from diony_contracts.montage import skipped_final_review
    from diony_producer.production_stop import record_production_stop

    project = expand_idea(Brief(idea="A keeper repairs the lamp before the storm.", customer_kind=CustomerKind.SERIES, target_minutes=0.5))
    project.final_review = skipped_final_review("tl", "m.mov", "test")
    project.deliverables = [Deliverable(id="dlv-x", kind="qc_packet", path="x.json")]
    record_production_stop(project, ProductionStopDisposition.KEEP)
    assert project.final_review is None and project.deliverables == []


def test_deliverables_api_lists_and_serves_only_project_files(tmp_path: Path) -> None:
    import importlib

    from fastapi.testclient import TestClient

    from diony_contracts.models import Deliverable

    app_module = importlib.import_module("diony_producer.app")
    client = TestClient(app_module.app)
    client.post("/api/reset")
    project = app_module._project
    inside = vault_root() / "projects" / project.id / "program" / "tl" / "master_1080.srt"
    inside.parent.mkdir(parents=True, exist_ok=True)
    inside.write_text("1\n00:00:00,000 --> 00:00:01,000\nhello\n", encoding="utf-8")
    outside = vault_root() / "secret.txt"
    outside.write_text("no", encoding="utf-8")
    project.deliverables = [
        Deliverable(id="dlv-srt", kind="subtitles", path=str(inside.relative_to(vault_root()))),
        Deliverable(id="dlv-out", kind="qc_packet", path=str(outside.relative_to(vault_root()))),
    ]
    listing = client.get("/api/program/deliverables").json()
    assert [row["id"] for row in listing["deliverables"]] == ["dlv-srt", "dlv-out"]
    assert listing["deliverables"][0]["url"] == "/api/deliverables/dlv-srt/file"
    served = client.get("/api/deliverables/dlv-srt/file")
    assert served.status_code == 200 and served.text.startswith("1\n")
    assert client.get("/api/deliverables/dlv-out/file").status_code == 404
    assert client.get("/api/deliverables/missing/file").status_code == 404


def _click_track(seconds: float = 20.0, bpm: float = 120.0, sample_rate: int = 22_050):
    """Kick on every downbeat, hi-hat on every beat: an unambiguous pulse."""
    rng = np.random.default_rng(3)
    total = int(seconds * sample_rate)
    signal = np.zeros(total, dtype=np.float32)
    beat = 60.0 / bpm
    index = 0
    t = 0.0
    while t < seconds:
        start = int(t * sample_rate)
        length = min(int(0.05 * sample_rate), total - start)
        if length <= 0:
            break
        env = np.exp(-np.linspace(0, 8, length)).astype(np.float32)
        if index % 4 == 0:
            tone = np.sin(2 * np.pi * 60.0 * np.arange(length) / sample_rate).astype(np.float32)
            signal[start : start + length] += 0.9 * env * tone
        else:
            signal[start : start + length] += 0.4 * env * rng.standard_normal(length).astype(np.float32)
        t += beat
        index += 1
    return signal


def test_beat_grid_measures_a_click_track_and_distrusts_a_pad() -> None:
    from diony_producer.beat_grid import analyze_signal

    grid = analyze_signal(_click_track())
    assert grid.rhythmic and grid.bpm is not None and abs(grid.bpm - 120.0) < 2.5, grid.bpm
    assert len(grid.downbeat_seconds) >= 8
    gaps = np.diff(np.asarray(grid.downbeat_seconds))
    assert np.all(np.abs(gaps - 2.0) < 0.1), gaps
    assert grid.energy_phases and grid.duration_seconds == 20.0
    pad = np.sin(2 * np.pi * 220.0 * np.arange(20 * 22_050) / 22_050).astype(np.float32) * 0.3
    calm = analyze_signal(pad)
    assert not calm.rhythmic and calm.downbeat_seconds == []


@needs_ffmpeg
def test_mix_aligns_a_score_cue_to_hero_cuts(tmp_path: Path) -> None:
    import wave

    from diony_producer.mix import _cue_rail_filters, align_score_cues, hero_cut_seconds
    from diony_producer.soundtrack import PlannedCue

    project = expand_idea(Brief(idea="A keeper repairs the lamp before the storm.", customer_kind=CustomerKind.SERIES, target_minutes=0.5))
    cuts = hero_cut_seconds(project)
    assert cuts and cuts[0] > 0
    cue_path = tmp_path / "score.wav"
    signal = _click_track(seconds=60.0)
    with wave.open(str(cue_path), "wb") as stream:
        stream.setnchannels(1)
        stream.setsampwidth(2)
        stream.setframerate(22_050)
        stream.writeframes((signal * 32767).astype(np.int16).tobytes())
    cue = PlannedCue(cue_id="score/click", path=str(cue_path), role="score", start_seconds=0.0, duration_seconds=25.0, gain_db=-12.0)
    ambience = PlannedCue(cue_id="ambience/room", path=str(cue_path), role="ambience", start_seconds=0.0, duration_seconds=25.0, gain_db=-30.0)
    aligned, alignments = align_score_cues(project, [cue, ambience])
    assert len(alignments) == 1 and alignments[0].cue_id == "score/click"
    assert alignments[0].mode.value == "beat_cut", alignments[0].reason
    assert 0.0 <= aligned[0].source_offset_seconds < 2.0 and aligned[1].source_offset_seconds == 0.0
    assert alignments[0].aligned_cuts and alignments[0].aligned_cuts[0].error_ms <= 60.0
    filters, labels = _cue_rail_filters(aligned)
    assert f"atrim={aligned[0].source_offset_seconds:.6f}:" in filters[0] and "asetpts=PTS-STARTPTS" in filters[0]
    assert labels == ["[cue0]", "[cue1]"]
    assert project.decisions[-1].subject == "score alignment to hero cuts"
    project.bible.score_cuts_on_downbeats = False
    same, none = align_score_cues(project, [cue])
    assert none == [] and same[0].source_offset_seconds == 0.0


@needs_ffmpeg
def test_final_review_delivers_the_edl(tmp_path: Path) -> None:
    from diony_contracts.models import MixLock
    from diony_producer.final_review import run_final_review

    project = expand_idea(Brief(idea='MARA: "We leave at dawn." A keeper repairs the lamp.', customer_kind=CustomerKind.SERIES, target_minutes=0.5))
    program_dir = vault_root() / "projects" / project.id / "program" / project.timeline.id
    program_dir.mkdir(parents=True, exist_ok=True)
    master = _lavfi(program_dir / "master_1080.mov", "testsrc2=s=1920x1080:r=24", seconds=1.0, audio=True)
    project.timeline.preview_path = str(master.relative_to(vault_root()))
    project.mix = MixLock(id="mix-1", measured_lufs=-24.0, true_peak_dbtp=-2.0)
    run_final_review(project, live=True)
    edl = program_dir / "master_1080.edl"
    assert edl.is_file() and edl.read_text().startswith("TITLE: ")
    assert (program_dir / "master_1080.edl.json").is_file()
    assert {item.kind.value for item in project.deliverables} >= {"edl", "subtitles", "qc_packet", "prores_hq"}
    packet = json.loads((program_dir / "qc-packet.json").read_text())
    assert packet["edl"]["decisions"]


def test_face_identity_is_unmeasured_without_staged_models(monkeypatch, tmp_path: Path) -> None:
    from diony_producer.face_identity import face_models, face_threshold, measure_face_identity

    monkeypatch.delenv("DIONY_FACE_ID_DETECTOR_MODEL", raising=False)
    monkeypatch.delenv("DIONY_FACE_ID_RECOGNIZER_MODEL", raising=False)
    assert face_models() is None
    receipt = measure_face_identity(tmp_path / "n.mp4", tmp_path / "ref.png", frame_count=81)
    assert not receipt.measured and "not staged" in receipt.error
    monkeypatch.setenv("DIONY_FACE_ID_SIMILARITY_MIN", "0.5")
    assert face_threshold() == 0.5
    monkeypatch.setenv("DIONY_FACE_ID_DETECTOR_MODEL", str(tmp_path / "missing.onnx"))
    monkeypatch.setenv("DIONY_FACE_ID_RECOGNIZER_MODEL", str(tmp_path / "missing2.onnx"))
    assert face_models() is None


def test_qc_report_refuses_pass_on_a_measured_face_failure() -> None:
    from diony_contracts.enums import QCVerdict
    from diony_contracts.models import QCReport

    with pytest.raises(ValueError):
        QCReport(id="qc", take_id="t", shot_id="s", verdict=QCVerdict.PASS, identity_ok=True, morph_ok=True,
                 flicker_ok=True, duration_ok=True, legal_ok=True, face_verified=False)
    ok = QCReport(id="qc", take_id="t", shot_id="s", verdict=QCVerdict.FAIL, identity_ok=True, morph_ok=True,
                  flicker_ok=True, duration_ok=True, legal_ok=True, face_verified=False, face_similarity_min=0.21)
    assert ok.face_verified is False


def test_attempt_score_counts_face_identity_when_measured() -> None:
    from diony_contracts.enums import QCVerdict
    from diony_contracts.models import QCReport
    from diony_producer.farm import attempt_score, new_generation_take

    project = expand_idea(Brief(idea="A keeper repairs the lamp before the storm.", customer_kind=CustomerKind.SERIES, target_minutes=0.5))
    take = new_generation_take(project, project.shots[0], live=False)
    base = dict(id="qc", take_id=take.id, shot_id=take.shot_id, verdict=QCVerdict.WARN, identity_ok=True, morph_ok=True,
                flicker_ok=True, duration_ok=True, legal_ok=True, reference_similarity=0.9, temporal_residual_p95=0.02)
    take.qc = QCReport(**base)
    without = attempt_score(take)
    take.qc = QCReport(**base, face_similarity_min=0.6, face_verified=True)
    strong = attempt_score(take)
    take.qc = QCReport(**base, face_similarity_min=0.25, face_verified=False)
    weak = attempt_score(take)
    assert strong >= without > weak


def test_bible_audio_layout_is_validated() -> None:
    from diony_contracts.models import Bible

    Bible(title="t", logline="l", visual_grammar="g", world="w", audio_layout="stereo+5.1")
    with pytest.raises(ValueError):
        Bible(title="t", logline="l", visual_grammar="g", world="w", audio_layout="7.1")


@needs_ffmpeg
def test_surround_mixdown_and_mux_deliver_a_5_1_track(tmp_path: Path) -> None:
    from diony_producer.mix import _mux_program, _surround_graph, _write_surround_mixdown

    def rail(name: str, source: str) -> Path:
        path = tmp_path / f"{name}.wav"
        subprocess.run([FFMPEG, "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i", f"{source}:d=2", "-ar", "48000", "-ac", "2", "-c:a", "pcm_s24le", str(path)], check=True)
        return path

    rails = {
        "dialogue": rail("dialogue", "sine=frequency=300:sample_rate=48000"),
        "score": rail("score", "sine=frequency=110:sample_rate=48000"),
        "ambience": rail("ambience", "anoisesrc=color=pink:sample_rate=48000:amplitude=0.1"),
    }
    graph = _surround_graph(["dialogue", "ambience", "score"], 2.0)
    assert "join=inputs=6:channel_layout=5.1" in graph and "sidechaincompress" in graph and "lowpass=f=120" in graph
    out = tmp_path / "program-mix-51-v1.wav"
    _write_surround_mixdown(out, ffmpeg=FFMPEG, rails=rails, duration=2.0, target=-24.0, peak=-2.0)
    probe = subprocess.run([shutil.which("ffprobe"), "-v", "error", "-select_streams", "a:0", "-show_entries", "stream=channels,channel_layout", "-of", "csv=p=0", str(out)], capture_output=True, text=True, check=True)
    assert probe.stdout.strip().startswith("6,5.1")
    master = _lavfi(tmp_path / "master.mov", "testsrc2=s=320x180:r=24", seconds=2.0)
    stereo = rail("program", "sine=frequency=440:sample_rate=48000")
    _mux_program(master, stereo, ffmpeg=FFMPEG, surround=out)
    streams = subprocess.run([shutil.which("ffprobe"), "-v", "error", "-select_streams", "a", "-show_entries", "stream=channels", "-of", "csv=p=0", str(master)], capture_output=True, text=True, check=True)
    assert [line.split(",")[0] for line in streams.stdout.strip().splitlines()] == ["2", "6"]


def test_face_pack_and_surround_reach_the_scripts_and_rail() -> None:
    from diony_producer.gpu import _gpu_producer_start_script
    from diony_wizard.apply import FACE_PACK, remote_setup_script

    script = remote_setup_script("/opt/diony-ai")
    assert "FACE_PACK_START" in script and "FACE_PACK_OPTIONAL_FAIL" in script
    for url, dest, sha, _bytes in FACE_PACK:
        assert url in script and dest in script and sha in script
    start = _gpu_producer_start_script("stamp-face")
    assert "export DIONY_FACE_ID_DETECTOR_MODEL=" in start and 'grep -Fqx "DIONY_FACE_ID_RECOGNIZER_MODEL=' in start
    from diony_producer.run import production_standard

    project = expand_idea(Brief(idea="A keeper repairs the lamp before the storm.", customer_kind=CustomerKind.SERIES, target_minutes=0.5))
    project.bible.audio_layout = "stereo+5.1"
    assert production_standard(project, clips=[], camera="stub")["audio"]["layout"] == "stereo+5.1"


def test_transcript_gate_only_judges_lines_long_enough_to_measure() -> None:
    from diony_contracts.voice import SpeechQCReceipt

    base = dict(measurement_version="v", audio_present=True, speech_presence_score=0.5, speech_tail_ms=300,
                integrated_lufs=-24.0, true_peak_dbtp=-2.0, window_ms=3000)
    short = SpeechQCReceipt(**base, transcript_word_accuracy=0.25, transcript_word_count=4)
    assert not [f for f in short.failures() if "authored line" in f]
    long = SpeechQCReceipt(**base, transcript_word_accuracy=0.25, transcript_word_count=9)
    assert any("authored line" in f for f in long.failures())
