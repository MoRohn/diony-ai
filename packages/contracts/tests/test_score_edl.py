"""Blueprint 24 §12–13: score alignment math and the edit decision list."""

from __future__ import annotations

from diony_contracts import Brief, CustomerKind, expand_idea
from diony_contracts.edl import EDL_VERSION, build_edl, frames_to_timecode, render_cmx3600, render_edl_json
from diony_contracts.score import AlignmentMode, BeatGrid, align_cue_to_cuts


def _grid(bpm: float = 120.0, seconds: float = 60.0) -> BeatGrid:
    beat = 60.0 / bpm
    beats = [round(i * beat, 4) for i in range(int(seconds / beat))]
    return BeatGrid(duration_seconds=seconds, bpm=bpm, rhythmic=True, confidence=0.9, beat_seconds=beats, downbeat_seconds=beats[::4])


def test_alignment_shifts_the_in_point_onto_the_hero_cuts() -> None:
    # Bar = 2.0 s at 120 BPM. A hero cut 3.3 s into the cue needs a 0.7 s shift
    # (source 4.0 s is a downbeat; 3.3 + 0.7 = 4.0).
    alignment = align_cue_to_cuts(_grid(), cue_id="score/a", cue_start_seconds=10.0, cue_duration_seconds=30.0, cut_seconds=[13.3], source_duration_seconds=60.0)
    assert alignment.mode == AlignmentMode.BEAT_CUT
    assert abs(alignment.source_shift_seconds - 0.7) < 1e-6
    assert alignment.aligned_cuts[0].error_ms < 1.0 and alignment.hero_cut_seconds == [13.3]
    # Two cuts a bar apart both land; two cuts 1.3 s apart cannot both land within tolerance.
    both = align_cue_to_cuts(_grid(), cue_id="c", cue_start_seconds=0.0, cue_duration_seconds=30.0, cut_seconds=[4.5, 6.5], source_duration_seconds=60.0)
    assert both.mode == AlignmentMode.BEAT_CUT and len(both.aligned_cuts) == 2
    partial = align_cue_to_cuts(_grid(), cue_id="c", cue_start_seconds=0.0, cue_duration_seconds=30.0, cut_seconds=[4.5, 5.8], source_duration_seconds=60.0)
    assert partial.mode == AlignmentMode.BEAT_CUT and len(partial.aligned_cuts) == 1 and partial.missed_cuts == 1
    assert "1 missed" in partial.reason and partial.max_error_ms is not None and partial.max_error_ms > 60
    # A cut on a beat (not a bar line) counts as a beat hit.
    on_beat = align_cue_to_cuts(_grid(), cue_id="c", cue_start_seconds=0.0, cue_duration_seconds=30.0, cut_seconds=[4.5, 5.0], source_duration_seconds=60.0)
    assert sorted(row.grid for row in on_beat.aligned_cuts) == ["beat", "downbeat"] and on_beat.missed_cuts == 0


def test_alignment_respects_the_source_length_and_the_trust_boundary() -> None:
    capped = align_cue_to_cuts(_grid(), cue_id="c", cue_start_seconds=0.0, cue_duration_seconds=30.0, cut_seconds=[3.3], source_duration_seconds=30.2)
    assert capped.source_shift_seconds <= 0.2 + 1e-9
    pad = BeatGrid(duration_seconds=30.0, bpm=None, rhythmic=False)
    flow = align_cue_to_cuts(pad, cue_id="c", cue_start_seconds=0.0, cue_duration_seconds=30.0, cut_seconds=[3.3])
    assert flow.mode == AlignmentMode.PHRASE_FLOW and "no stable pulse" in flow.reason
    empty = align_cue_to_cuts(_grid(), cue_id="c", cue_start_seconds=0.0, cue_duration_seconds=30.0, cut_seconds=[0.1, 40.0])
    assert empty.mode == AlignmentMode.PHRASE_FLOW and "no hero cut" in empty.reason


def test_edl_builds_from_the_timeline_with_reasons_and_transitions() -> None:
    project = expand_idea(Brief(idea='MARA: "We leave at dawn." A keeper repairs the lamp before the storm.', customer_kind=CustomerKind.SERIES, target_minutes=0.5))
    edl = build_edl(project)
    assert edl.version == EDL_VERSION and len(edl.decisions) == len(project.timeline.events)
    assert all(item.reason for item in edl.decisions)
    assert edl.decisions[0].record_in == 0 and edl.decisions[0].transition_in.kind == "cut"
    assert set(edl.transition_families) <= {"cut", "dissolve"} and len(edl.transition_families) <= 4
    dialogue = [item for item in edl.decisions if item.role == "dialogue"]
    assert dialogue and dialogue[0].speaker == "Mara"
    assert any(item.hero for item in edl.decisions)
    assert edl.decisions[-1].record_out == project.timeline.total_frames
    assert "every cut carries a reason" in edl.notes


def test_cmx3600_and_timecode() -> None:
    assert frames_to_timecode(0, 23.976) == "00:00:00:00"
    assert frames_to_timecode(24 * 3600 + 24 * 61 + 5, 24.0) == "01:01:01:05"
    project = expand_idea(Brief(idea="A keeper repairs the lamp before the storm.", customer_kind=CustomerKind.SERIES, target_minutes=0.5))
    text = render_cmx3600(build_edl(project))
    assert text.startswith("TITLE: ") and "FCM: NON-DROP FRAME" in text
    assert "001  " in text and " V     C        " in text
    assert " V     D    008 " in text  # eight-frame temporal blends are dissolves
    assert "* DIONY REASON: " in text and "* FROM CLIP NAME: " in text
    assert render_edl_json(build_edl(project)).startswith("{")
