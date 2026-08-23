from __future__ import annotations

import json


def _operator_evidence() -> dict:
    return {
        "gpu_name": "NVIDIA B200",
        "compute_capability": "10.0",
        "torch_version": "2.8.0+cu128",
        "cuda_version": "12.8",
        "cudnn_version": 91002,
        "shape": [1, 40, 75600, 128],
        "dtype": "bfloat16",
        "baseline_backend": "flash_sdpa",
        "candidate_backend": "cudnn_sdpa",
        "baseline_ms": [285.85, 285.39, 285.37],
        "candidate_ms": [92.08, 91.19, 91.18],
        "finite": True,
        "max_abs_delta": 0.000244140625,
        "mean_abs_delta": 0.0000111,
    }


def test_operator_receipt_is_run_scoped_until_quality_canary_passes(tmp_path) -> None:
    from diony_wan.attention_qualification import (
        attention_decision,
        build_operator_receipt,
        promote_quality_canary,
    )

    receipt = build_operator_receipt(
        _operator_evidence(),
        scope_project_id="studio-123",
        approved_by="Diony B200 qualification",
    )
    path = tmp_path / "receipt.json"
    path.write_text(json.dumps(receipt))

    held = attention_decision(path, project_id="studio-123")
    assert held.enabled is False
    assert "canary" in held.reason.casefold()

    promoted = promote_quality_canary(
        receipt,
        {
            "take_id": "sh-010-t2",
            "autoqc_passed": True,
            "reference_verified": True,
            "temporal_verified": True,
            "master_1080_verified": True,
            "quality_evidence_sha256": "a" * 64,
        },
    )
    path.write_text(json.dumps(promoted))
    approved = attention_decision(path, project_id="studio-123")
    assert approved.enabled is True
    assert approved.backend == "cudnn_sdpa"
    assert attention_decision(path, project_id="another-project").enabled is False


def test_operator_receipt_rejects_slow_or_numerically_divergent_kernel() -> None:
    from diony_wan.attention_qualification import QualificationError, build_operator_receipt

    slow = _operator_evidence()
    slow["candidate_ms"] = [250.0, 250.0, 250.0]
    try:
        build_operator_receipt(slow, scope_project_id="studio-123", approved_by="Diony")
    except QualificationError as exc:
        assert "speedup" in str(exc).casefold()
    else:
        raise AssertionError("slow attention candidate was accepted")

    divergent = _operator_evidence()
    divergent["max_abs_delta"] = 0.01
    try:
        build_operator_receipt(divergent, scope_project_id="studio-123", approved_by="Diony")
    except QualificationError as exc:
        assert "delta" in str(exc).casefold()
    else:
        raise AssertionError("divergent attention candidate was accepted")


def test_operator_evidence_can_be_rebound_to_one_new_project_canary() -> None:
    from diony_wan.attention_qualification import (
        QualificationError,
        bind_project_canary,
        build_operator_receipt,
        promote_quality_canary,
    )

    prior = build_operator_receipt(
        _operator_evidence(),
        scope_project_id="studio-prior",
        approved_by="Diony B200 qualification",
    )
    rebound = bind_project_canary(
        prior,
        project_id="studio-current",
        take_id="sh-010-t1",
    )

    assert rebound["scope_project_id"] == "studio-current"
    assert rebound["quality_canary_take_id"] == "sh-010-t1"
    assert rebound["stage"] == "operator_passed_quality_canary_required"
    assert rebound["operator_evidence_sha256"] == prior["operator_evidence_sha256"]

    try:
        promote_quality_canary(
            rebound,
            {
                "take_id": "sh-020-t1",
                "autoqc_passed": True,
                "reference_verified": True,
                "temporal_verified": True,
                "master_1080_verified": True,
                "quality_evidence_sha256": "a" * 64,
            },
        )
    except QualificationError as exc:
        assert "selected immutable Take" in str(exc)
    else:
        raise AssertionError("a different Take promoted the project canary")
