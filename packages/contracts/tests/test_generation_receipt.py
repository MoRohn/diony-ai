import pytest
from pydantic import ValidationError

from diony_contracts.enums import Engine
from diony_contracts.models import GenerationExecutionReceipt, Take


def _receipt(**changes):
    payload = {
        "status": "succeeded",
        "backend": "native",
        "selected_lane": "wan22_quality_cli",
        "task": "i2v-A14B",
        "native_width": 1280,
        "native_height": 720,
        "frame_num": 81,
        "sample_steps": 24,
        "sample_solver": "unipc",
        "precision": "bfloat16",
        "negative_conditioning": True,
        "finish_required": True,
        "quality_contract_sha256": "a" * 64,
        "quality_contract_matched": True,
        "attention_backend": "auto",
        "elapsed_seconds": 901.25,
        "output_sha256": "b" * 64,
        "output_bytes": 1024,
    }
    payload.update(changes)
    return GenerationExecutionReceipt(**payload)


def test_take_persists_the_actual_generation_lane_and_quality_contract() -> None:
    receipt = _receipt(
        resident_reason="Resident Wan validation receipt is not configured.",
        fallback_reason="Resident Wan validation receipt is not configured.",
    )
    take = Take(
        id="sh-010-t1",
        shot_id="sh-010",
        attempt=1,
        engine=Engine.I2V_A14B,
        seed=10401,
        generation_receipt=receipt,
    )

    restored = Take.model_validate_json(take.model_dump_json())

    assert restored.generation_receipt is not None
    assert restored.generation_receipt.selected_lane == "wan22_quality_cli"
    assert restored.generation_receipt.quality_contract_matched is True
    assert "receipt" in restored.generation_receipt.fallback_reason.casefold()


def test_succeeded_generation_receipt_requires_addressed_output_and_exact_quality_shape() -> None:
    with pytest.raises(ValidationError, match="addressed output"):
        _receipt(output_sha256="", output_bytes=0)

    with pytest.raises(ValidationError, match="quality contract"):
        _receipt(sample_steps=4)


def test_s2v_quality_receipt_requires_one_84_frame_inference_window() -> None:
    receipt = _receipt(task="s2v-14B", inference_frames_per_pass=84, sample_steps=40, effective_denoise_steps=40)

    assert receipt.quality_contract_matched is True
    assert receipt.effective_denoise_steps == 40

    with pytest.raises(ValidationError, match="quality contract"):
        _receipt(task="s2v-14B", inference_frames_per_pass=88, sample_steps=40, effective_denoise_steps=40)


def test_quality_contract_follows_the_booking_not_the_lane_cap() -> None:
    from diony_contracts.models import quality_contract_satisfied, s2v_inference_frames, wan_lane_frames_valid

    short = _receipt(frame_num=49, inference_frames_per_pass=49)
    assert short.quality_contract_matched is True
    assert quality_contract_satisfied(short)
    assert wan_lane_frames_valid(49) and wan_lane_frames_valid(17) and wan_lane_frames_valid(81)
    assert not wan_lane_frames_valid(50) and not wan_lane_frames_valid(85)

    with pytest.raises(ValidationError):
        _receipt(frame_num=50, inference_frames_per_pass=50)
    with pytest.raises(ValidationError):
        # Delivered fewer frames than the booking asked for.
        _receipt(frame_num=77, inference_frames_per_pass=81)

    spoken = _receipt(task="s2v-14B", frame_num=49, inference_frames_per_pass=52, sample_steps=40, effective_denoise_steps=40)
    assert spoken.quality_contract_matched is True
    assert s2v_inference_frames(49) == 52 and s2v_inference_frames(81) == 84
    with pytest.raises(ValidationError):
        _receipt(task="s2v-14B", frame_num=49, inference_frames_per_pass=84, sample_steps=40, effective_denoise_steps=40)


def test_s2v_books_native_rate_frames_for_the_booked_seconds() -> None:
    from diony_contracts.live import s2v_frame_num, wan_frame_num

    # 81 frames at 23.976 fps is 3.378 s: 55 native frames + 1 margin → 57 (one 60-frame window).
    assert wan_frame_num(81, 23.976) == 81
    assert s2v_frame_num(81, 23.976) == 57
    assert s2v_frame_num(49, 23.976) == 37   # 2.04 s → 33.7 + 1 → 37
    assert s2v_frame_num(17, 23.976) == 17   # never below the lane floor
    assert s2v_frame_num(120, 24.0) == 81    # never above the lane cap
    for frames in (57, 37, 17, 81):
        assert (frames - 1) % 4 == 0


def test_s2v_lead_is_part_of_the_rendered_contract() -> None:
    """57 delivered + 7 lead = 65 rendered frames in one 68-frame S2V window."""
    receipt = _receipt(task="s2v-14B", frame_num=58, lead_frames=7, inference_frames_per_pass=68, sample_steps=40, effective_denoise_steps=40)
    assert receipt.quality_contract_matched is True
    with pytest.raises(ValidationError):
        _receipt(task="s2v-14B", frame_num=58, lead_frames=7, inference_frames_per_pass=60, sample_steps=40, effective_denoise_steps=40)


def test_s2v_lane_denoises_in_forty_steps_and_the_receipt_knows() -> None:
    from diony_contracts.models import lane_sample_steps

    assert lane_sample_steps("i2v-A14B") == 24 and lane_sample_steps("t2v-A14B") == 24
    assert lane_sample_steps("s2v-14B") == 40
    spoken = _receipt(task="s2v-14B", frame_num=65, inference_frames_per_pass=68, sample_steps=40, effective_denoise_steps=40)
    assert spoken.quality_contract_matched is True
    # A receipt delivered under the earlier 24-step S2V lane remains valid evidence.
    legacy = _receipt(task="s2v-14B", frame_num=65, inference_frames_per_pass=68, sample_steps=24, effective_denoise_steps=24)
    assert legacy.quality_contract_matched is True
    with pytest.raises(ValidationError):
        _receipt(task="s2v-14B", frame_num=65, inference_frames_per_pass=68, sample_steps=30, effective_denoise_steps=30)
    held = _receipt(task="s2v-14B", frame_num=65, inference_frames_per_pass=68, sample_steps=40, effective_denoise_steps=40, head_hold_frames=5)
    assert held.head_hold_frames == 5


def test_only_the_spoken_lane_dispatches_a_guide_scale_and_receipts_carry_it() -> None:
    from diony_contracts.models import GenerationExecutionReceipt, lane_guide_scale

    assert lane_guide_scale("s2v-14B") == 5.5
    assert lane_guide_scale("i2v-A14B") is None
    assert lane_guide_scale("") is None
    fields = GenerationExecutionReceipt.model_fields
    assert fields["sample_guide_scale"].default is None
