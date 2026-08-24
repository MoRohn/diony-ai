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
    receipt = _receipt(task="s2v-14B", inference_frames_per_pass=84)

    assert receipt.quality_contract_matched is True
    assert receipt.effective_denoise_steps == 24

    with pytest.raises(ValidationError, match="quality contract"):
        _receipt(task="s2v-14B", inference_frames_per_pass=88)
