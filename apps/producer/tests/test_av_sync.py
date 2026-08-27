from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from diony_producer.av_sync import (
    BACKEND_NAME,
    FaceTrack,
    choose_speaker,
    combined_aperture,
    delivered_span_ms,
    envelope_per_frame,
    measure_sync,
)


def _speech_envelope(frames: int, fps: float, seed: int = 3) -> np.ndarray:
    """Syllable-rate bursts for the first 60% of the clip, then silence."""
    rng = np.random.default_rng(seed)
    envelope = np.zeros(frames)
    spoken = int(frames * 0.6)
    syllable = max(2, int(round(fps / 4)))
    for start in range(0, spoken, syllable):
        envelope[start : start + max(1, syllable // 2)] = 0.4 + 0.6 * rng.random()
    return envelope


def _mouth_from(envelope: np.ndarray, *, lag: int = 0, smile_tail: bool = False, seed: int = 5) -> np.ndarray:
    rng = np.random.default_rng(seed)
    mouth = 0.02 + 0.3 * envelope + 0.01 * rng.random(envelope.size)
    if lag:
        mouth = np.roll(mouth, lag)
    if smile_tail:
        spoken = int(np.flatnonzero(envelope > 0).max()) + 1
        tail = envelope.size - spoken
        # A held smile opens the jaw slowly and steadily; it must not read as talking.
        mouth[spoken:] = 0.02 + 0.15 * np.linspace(0, 1, tail)
    return mouth


def test_measure_sync_qualifies_a_synced_performance_and_reads_the_offset() -> None:
    fps = 16.0
    envelope = _speech_envelope(55, fps)
    mouth = _mouth_from(envelope)

    result = measure_sync(mouth, envelope, fps)

    assert result["disqualifiers"] == []
    assert result["lip_sync_confidence"] >= 0.90
    assert result["audiovisual_offset_ms_p95"] <= 40.0
    assert result["performance_consistency"] >= 0.90
    assert result["frame_period_ms"] == 62.5

    late = measure_sync(_mouth_from(envelope, lag=2), envelope, fps)
    assert late["disqualifiers"] == []
    # Two frames at 16 fps is 125 ms; the measurement must see it so the farm
    # can act on the offset gate instead of hiding it.
    assert 100.0 <= late["audiovisual_offset_ms_p95"] <= 150.0


def test_measure_sync_scores_a_held_smile_as_silence_not_talking() -> None:
    fps = 16.0
    envelope = _speech_envelope(55, fps)
    smiling = measure_sync(_mouth_from(envelope, smile_tail=True), envelope, fps)
    assert smiling["disqualifiers"] == []
    assert smiling["true_negative_rate"] >= 0.85
    assert smiling["performance_consistency"] >= 0.90


def test_absent_performance_is_a_qualified_failure_not_a_hold() -> None:
    """A tracked, audible clip whose mouth never moves is measured, not degenerate."""
    fps = 24.0
    envelope = _speech_envelope(72, fps)
    still = measure_sync(np.full(72, 0.003), envelope, fps)
    assert still["disqualifiers"] == []
    assert any("still" in reason for reason in still["defects"])
    assert still["lip_sync_confidence"] <= 0.75

    rng = np.random.default_rng(9)
    unrelated = measure_sync(0.1 + 0.1 * rng.random(72), envelope, fps)
    assert unrelated["disqualifiers"] == []
    assert unrelated["defects"], "chance-level correlation is a defect"
    assert unrelated["lip_sync_confidence"] < 0.90


def test_measure_sync_stays_unqualified_only_on_degenerate_evidence() -> None:
    fps = 24.0
    envelope = _speech_envelope(72, fps)
    silent = measure_sync(_mouth_from(envelope), np.zeros(72), fps)
    assert any("active audio" in reason for reason in silent["disqualifiers"])
    short = measure_sync(_mouth_from(envelope)[:8], envelope[:8], fps)
    assert any("comparable frames" in reason for reason in short["disqualifiers"])


def test_choose_speaker_prefers_the_face_whose_mouth_follows_the_line() -> None:
    fps = 16.0
    frames = 55
    envelope = _speech_envelope(frames, fps)
    speaker = FaceTrack(
        frames=list(range(frames)),
        jaw=list(_mouth_from(envelope)),
        gap=list(0.5 * _mouth_from(envelope)),
        centers=[(870.0, 180.0)] * frames,
    )
    listener = FaceTrack(
        frames=list(range(frames)),
        jaw=[0.001] * frames,
        gap=[0.0005] * frames,
        centers=[(300.0, 200.0)] * frames,
    )
    glimpse = FaceTrack(frames=[0, 1, 2], jaw=[0.3, 0.1, 0.3], gap=[0.2, 0.1, 0.2], centers=[(600.0, 190.0)] * 3)

    chosen, result, notes = choose_speaker([listener, glimpse, speaker], envelope, fps, frames)

    assert chosen is speaker
    assert result is not None and result["disqualifiers"] == []
    assert any("covered 5%" in note for note in notes)


def test_combined_aperture_fills_missed_frames_without_inventing_motion() -> None:
    track = FaceTrack(frames=[0, 2, 4], jaw=[0.1, float("nan"), 0.3], gap=[0.05, 0.1, 0.15], centers=[(0, 0)] * 3)
    series = combined_aperture(5, track)
    assert series.shape == (5,)
    assert np.all(np.isfinite(series))
    assert series[0] < series[4]


def test_envelope_per_frame_bins_at_the_clip_frame_rate() -> None:
    sample_rate = 16_000
    samples = np.zeros(sample_rate)
    samples[sample_rate // 2 :] = 0.5
    envelope = envelope_per_frame(samples, sample_rate, 16.0, 16)
    assert envelope[:8].max() == 0.0
    assert envelope[8:].min() > 0.4


def test_delivered_span_reads_the_script_pacing_target(tmp_path: Path) -> None:
    metadata = tmp_path / "performance.json"
    metadata.write_text(json.dumps({"script": {"pacing_target_ms": 3378, "lines": []}}))
    assert delivered_span_ms(metadata) == 3378.0
    metadata.write_text(
        json.dumps({"script": {"lines": [{"start_ms": 200, "target_duration_ms": 1500}, {"start_ms": 0, "target_duration_ms": 900}]}})
    )
    assert delivered_span_ms(metadata) == 1700.0
    metadata.write_text("{}")
    assert delivered_span_ms(metadata) is None


def test_backend_name_is_the_mouth_sync_v3_identity() -> None:
    assert BACKEND_NAME == "diony-local-mouth-sync-v3"


def test_choose_speaker_never_prefers_a_still_face_over_a_moving_one() -> None:
    """A listener's z-scored jitter can out-correlate the real speaker by chance."""
    fps = 16.0
    frames = 55
    envelope = _speech_envelope(frames, fps)
    rng = np.random.default_rng(21)
    # Real speaker: moves with the line but leads it by 5 frames (weak correlation at lag 0).
    early = FaceTrack(
        frames=list(range(frames)), jaw=list(np.roll(_mouth_from(envelope), -5)),
        gap=list(0.5 * np.roll(_mouth_from(envelope), -5)), centers=[(874.0, 183.0)] * frames,
    )
    # Still listener whose 1e-4 jitter happens to line up with the envelope.
    jitter = 0.0005 + 0.0002 * (envelope > 0) + 0.00005 * rng.random(frames)
    still = FaceTrack(frames=list(range(frames)), jaw=list(jitter), gap=list(jitter), centers=[(316.0, 210.0)] * frames)

    chosen, result, _notes = choose_speaker([still, early], envelope, fps, frames)

    assert chosen is early
    assert result is not None and result["aperture_std"] >= 0.02
    assert abs(result["best_lag_frames"]) >= 4
