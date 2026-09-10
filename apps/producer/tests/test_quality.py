from __future__ import annotations


def test_temporal_quality_accepts_stable_motion_and_rejects_full_frame_flicker() -> None:
    import numpy as np

    from diony_producer.quality import measure_temporal_frames

    stable = []
    for offset in range(12):
        frame = np.full((36, 64, 3), 72, dtype=np.uint8)
        frame[10:24, offset : offset + 12] = [190, 130, 80]
        stable.append(frame)
    stable_receipt = measure_temporal_frames(stable)
    assert stable_receipt.measured is True
    assert stable_receipt.flicker_ok is True
    assert stable_receipt.morph_ok is True
    assert stable_receipt.motion_ok is True
    assert stable_receipt.frame_count == 12

    flicker = [
        np.full((36, 64, 3), 30 if index % 2 == 0 else 220, dtype=np.uint8) for index in range(12)
    ]
    flicker_receipt = measure_temporal_frames(flicker)
    assert flicker_receipt.measured is True
    assert flicker_receipt.flicker_ok is False
    assert flicker_receipt.flicker_luma_p95 > stable_receipt.flicker_luma_p95


def test_temporal_quality_rejects_a_frozen_plate_without_secondary_motion() -> None:
    import numpy as np

    from diony_producer.quality import measure_temporal_frames

    frames = [np.full((36, 64, 3), 96, dtype=np.uint8) for _ in range(12)]

    receipt = measure_temporal_frames(frames)

    assert receipt.measured is True
    assert receipt.morph_ok is True
    assert receipt.flicker_ok is True
    assert receipt.motion_ok is False


def test_temporal_quality_rejects_an_isolated_incoherent_frame() -> None:
    import numpy as np

    from diony_producer.quality import measure_temporal_frames

    rng = np.random.default_rng(10401)
    frames = [np.full((36, 64, 3), 100, dtype=np.uint8) for _ in range(12)]
    frames[6] = rng.integers(0, 256, size=(36, 64, 3), dtype=np.uint8)

    receipt = measure_temporal_frames(frames)

    assert receipt.measured is True
    assert receipt.morph_ok is False
    assert receipt.temporal_residual_p95 > 0.2
