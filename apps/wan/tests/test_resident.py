from __future__ import annotations

import json


def _passing_receipt(
    contract_sha256: str,
    *,
    candidate: str = "persistent-bf16-resident-v1",
) -> dict:
    return {
        "schema_version": 1,
        "candidate": candidate,
        "quality_contract_sha256": contract_sha256,
        "sample_count": 30,
        "baseline_p50_seconds": 900.0,
        "candidate_p50_seconds": 750.0,
        "baseline_p95_seconds": 930.0,
        "candidate_p95_seconds": 920.0,
        "baseline_retries": 0,
        "candidate_retries": 0,
        "candidate_ooms": 0,
        "quality_passed": True,
        "quality_evidence_sha256": "a" * 64,
        "max_canary_percent": 10,
        "approved_by": "Diony performance qualification",
    }


def test_resident_wan_requires_a_complete_validation_receipt(tmp_path, monkeypatch) -> None:
    from diony_wan.resident import QUALITY_CONTRACT_SHA256, resident_decision

    monkeypatch.setenv("DIONY_WAN_RESIDENT", "validated")
    missing = resident_decision(task="i2v-A14B", offload=False)
    assert missing.enabled is False
    assert "receipt" in missing.reason.casefold()

    receipt = tmp_path / "resident-validation.json"
    receipt.write_text(json.dumps(_passing_receipt(QUALITY_CONTRACT_SHA256)))
    monkeypatch.setenv("DIONY_WAN_RESIDENT_RECEIPT", str(receipt))

    approved = resident_decision(task="i2v-A14B", offload=False)
    assert approved.enabled is True
    assert approved.receipt_sha256


def test_regional_compile_requires_its_own_matching_receipt(tmp_path, monkeypatch) -> None:
    from diony_wan.resident import (
        QUALITY_CONTRACT_SHA256,
        REGIONAL_COMPILE_CANDIDATE,
        resident_decision,
        resident_job_payload,
    )

    monkeypatch.setenv("DIONY_WAN_RESIDENT", "validated")
    monkeypatch.setenv("DIONY_WAN_KERNEL_MODE", "regional_compile")
    receipt = tmp_path / "resident-validation.json"
    monkeypatch.setenv("DIONY_WAN_RESIDENT_RECEIPT", str(receipt))
    receipt.write_text(json.dumps(_passing_receipt(QUALITY_CONTRACT_SHA256)))

    wrong_candidate = resident_decision(task="i2v-A14B", offload=False)
    assert wrong_candidate.enabled is False
    assert "candidate" in wrong_candidate.reason.casefold()

    receipt.write_text(
        json.dumps(
            _passing_receipt(
                QUALITY_CONTRACT_SHA256,
                candidate=REGIONAL_COMPILE_CANDIDATE,
            )
        )
    )
    approved = resident_decision(task="i2v-A14B", offload=False)
    assert approved.enabled is True
    assert approved.kernel_mode == "regional_compile"

    payload = resident_job_payload(
        task="i2v-A14B",
        checkpoint=tmp_path / "Wan2.2-I2V-A14B",
        prompt="The exact supplied subject crosses the studio.",
        negative_prompt="identity drift, flicker",
        seed=10401,
        save_file=tmp_path / "negative.mp4",
        frame_num=81,
        image=tmp_path / "locked.png",
        log_path=tmp_path / "negative.wan.log",
    )
    assert payload["kernel_mode"] == "regional_compile"


def test_regional_compile_marks_every_repeated_wan_block() -> None:
    from diony_wan.resident_worker import _apply_kernel_mode

    class Block:
        def __init__(self) -> None:
            self.calls: list[dict] = []

        def compile(self, **kwargs) -> None:
            self.calls.append(kwargs)

    class Model:
        def __init__(self) -> None:
            self.blocks = [Block(), Block()]

    class Pipeline:
        def __init__(self) -> None:
            self.low_noise_model = Model()
            self.high_noise_model = Model()

    pipeline = Pipeline()
    _apply_kernel_mode(pipeline, "regional_compile")

    calls = [
        call
        for model in (pipeline.low_noise_model, pipeline.high_noise_model)
        for block in model.blocks
        for call in block.calls
    ]
    assert calls == [{"fullgraph": True, "dynamic": False}] * 4


def test_resident_wan_rejects_quality_or_reliability_regression(tmp_path, monkeypatch) -> None:
    from diony_wan.resident import QUALITY_CONTRACT_SHA256, resident_decision

    monkeypatch.setenv("DIONY_WAN_RESIDENT", "validated")
    receipt = tmp_path / "resident-validation.json"
    monkeypatch.setenv("DIONY_WAN_RESIDENT_RECEIPT", str(receipt))

    failing = _passing_receipt(QUALITY_CONTRACT_SHA256)
    failing["candidate_p95_seconds"] = 940.0
    receipt.write_text(json.dumps(failing))
    decision = resident_decision(task="i2v-A14B", offload=False)
    assert decision.enabled is False
    assert "p95" in decision.reason

    failing = _passing_receipt(QUALITY_CONTRACT_SHA256)
    failing["quality_passed"] = False
    receipt.write_text(json.dumps(failing))
    decision = resident_decision(task="i2v-A14B", offload=False)
    assert decision.enabled is False
    assert "quality" in decision.reason.casefold()

    passing = _passing_receipt(QUALITY_CONTRACT_SHA256)
    receipt.write_text(json.dumps(passing))
    monkeypatch.setenv("DIONY_WAN_RESIDENT_CANARY_PERCENT", "25")
    decision = resident_decision(task="i2v-A14B", offload=False)
    assert decision.enabled is False
    assert "canary" in decision.reason.casefold()


def test_resident_wan_never_changes_the_quality_lane_or_runs_on_offload_sites(
    tmp_path, monkeypatch
) -> None:
    from diony_wan.resident import QUALITY_CONTRACT, QUALITY_CONTRACT_SHA256, resident_decision

    assert QUALITY_CONTRACT == {
        "native_size": "1280*720",
        "frame_num": 81,
        "sample_steps": 24,
        "sample_solver": "unipc",
        "precision": "bfloat16",
        "negative_conditioning": True,
        "prompt_extension": False,
        "finish_required": True,
    }
    receipt = tmp_path / "resident-validation.json"
    receipt.write_text(json.dumps(_passing_receipt(QUALITY_CONTRACT_SHA256)))
    monkeypatch.setenv("DIONY_WAN_RESIDENT", "validated")
    monkeypatch.setenv("DIONY_WAN_RESIDENT_RECEIPT", str(receipt))

    assert resident_decision(task="t2v-A14B", offload=False).enabled is True
    assert resident_decision(task="i2v-A14B", offload=False).enabled is True
    assert resident_decision(task="s2v-14B", offload=False).enabled is False
    assert resident_decision(task="i2v-A14B", offload=True).enabled is False


def test_pipeline_cache_reuses_only_the_exact_task_and_checkpoint() -> None:
    from diony_wan.resident_worker import PipelineCache

    created: list[tuple[str, str, str]] = []

    def factory(task: str, checkpoint: str, kernel_mode: str):
        created.append((task, checkpoint, kernel_mode))
        return {"task": task, "checkpoint": checkpoint, "kernel_mode": kernel_mode}

    cache = PipelineCache(factory=factory)
    first, first_reused = cache.get("i2v-A14B", "/models/i2v")
    again, again_reused = cache.get("i2v-A14B", "/models/i2v")
    compiled, compiled_reused = cache.get(
        "i2v-A14B", "/models/i2v", "regional_compile"
    )
    changed, changed_reused = cache.get("t2v-A14B", "/models/t2v")

    assert first is again
    assert first_reused is False
    assert again_reused is True
    assert compiled is not first
    assert compiled_reused is False
    assert changed is not first
    assert changed_reused is False
    assert created == [
        ("i2v-A14B", "/models/i2v", "eager"),
        ("i2v-A14B", "/models/i2v", "regional_compile"),
        ("t2v-A14B", "/models/t2v", "eager"),
    ]


def test_resident_job_payload_preserves_native_generation_inputs(tmp_path) -> None:
    from diony_wan.resident import resident_job_payload

    still = tmp_path / "locked.png"
    still.write_bytes(b"image")
    payload = resident_job_payload(
        task="i2v-A14B",
        checkpoint=tmp_path / "Wan2.2-I2V-A14B",
        prompt="The locked subject crosses the room.",
        negative_prompt="identity drift, flicker",
        seed=10401,
        save_file=tmp_path / "negative.mp4",
        frame_num=81,
        image=still,
        log_path=tmp_path / "negative.wan.log",
    )

    assert payload["task"] == "i2v-A14B"
    assert payload["size"] == "1280*720"
    assert payload["frame_num"] == 81
    assert payload["sample_steps"] == 24
    assert payload["sample_solver"] == "unipc"
    assert payload["seed"] == 10401
    assert payload["prompt"] == "The locked subject crosses the room."
    assert payload["negative_prompt"] == "identity drift, flicker"
    assert payload["image"] == str(still)
    assert payload["offload_model"] is False
    assert payload["init_on_cpu"] is False


def test_resident_canary_is_deterministic_and_has_hard_zero_and_full_boundaries(monkeypatch) -> None:
    from diony_wan.resident import resident_canary_selected

    monkeypatch.setenv("DIONY_WAN_RESIDENT_CANARY_PERCENT", "0")
    assert resident_canary_selected("project/take-one") is False
    monkeypatch.setenv("DIONY_WAN_RESIDENT_CANARY_PERCENT", "100")
    assert resident_canary_selected("project/take-one") is True
    monkeypatch.setenv("DIONY_WAN_RESIDENT_CANARY_PERCENT", "10")
    assert resident_canary_selected("project/take-one") == resident_canary_selected(
        "project/take-one"
    )


def test_resident_failure_opens_a_receipt_scoped_rollback_circuit(tmp_path, monkeypatch) -> None:
    from diony_wan.resident import (
        QUALITY_CONTRACT_SHA256,
        clear_resident_suppression,
        resident_decision,
        resident_suppression_path,
        suppress_resident,
    )

    monkeypatch.setenv("DIONY_VAULT", str(tmp_path / "vault"))
    receipt = tmp_path / "resident-validation.json"
    receipt.write_text(json.dumps(_passing_receipt(QUALITY_CONTRACT_SHA256)))
    monkeypatch.setenv("DIONY_WAN_RESIDENT", "validated")
    monkeypatch.setenv("DIONY_WAN_RESIDENT_RECEIPT", str(receipt))
    clear_resident_suppression()
    approved = resident_decision(task="i2v-A14B", offload=False)
    assert approved.enabled is True

    suppress_resident(approved.receipt_sha256, "worker OOM")
    assert resident_suppression_path().is_file()
    rolled_back = resident_decision(task="i2v-A14B", offload=False)
    assert rolled_back.enabled is False
    assert "rollback" in rolled_back.reason.casefold()

    updated = _passing_receipt(QUALITY_CONTRACT_SHA256)
    updated["approved_by"] = "new isolated qualification"
    receipt.write_text(json.dumps(updated))
    assert resident_decision(task="i2v-A14B", offload=False).enabled is True
    clear_resident_suppression()


def test_validation_receipt_builder_requires_paired_quality_passed_clips() -> None:
    from diony_wan.resident import QUALITY_CONTRACT_SHA256, REGIONAL_COMPILE_CANDIDATE
    from diony_wan.validation import ValidationError, build_validation_receipt

    rows = []
    for index in range(30):
        shared = {
            "clip_id": f"clip-{index:02d}",
            "input_sha256": f"input-{index:02d}",
            "quality_contract_sha256": QUALITY_CONTRACT_SHA256,
            "quality_passed": True,
            "autoqc_passed": True,
            "master_verified": True,
            "blind_review_passed": True,
            "decoded_frame_sha256": f"{index:064x}",
            "quality_evidence_sha256": f"{index + 100:064x}",
            "oom": False,
            "retries": 0,
        }
        rows.append({**shared, "lane": "baseline", "duration_seconds": 900 + index})
        rows.append({**shared, "lane": "candidate", "duration_seconds": 740 + index})

    receipt = build_validation_receipt(rows, approved_by="Diony isolated B200 qualification")

    assert receipt["sample_count"] == 30
    assert receipt["quality_passed"] is True
    assert receipt["candidate_ooms"] == 0
    assert receipt["candidate_p50_seconds"] < receipt["baseline_p50_seconds"]
    assert receipt["max_canary_percent"] == 10
    assert len(receipt["quality_evidence_sha256"]) == 64

    compiled = build_validation_receipt(
        rows,
        approved_by="Diony isolated B200 qualification",
        kernel_mode="regional_compile",
    )
    assert compiled["candidate"] == REGIONAL_COMPILE_CANDIDATE

    rows[-1]["quality_passed"] = False
    try:
        build_validation_receipt(rows, approved_by="Diony isolated B200 qualification")
        raise AssertionError("expected quality validation failure")
    except ValidationError as exc:
        assert "quality" in str(exc).casefold()

    for row in rows:
        row["quality_passed"] = True
        if row["lane"] == "candidate":
            row["duration_seconds"] = 910
    try:
        build_validation_receipt(rows, approved_by="Diony isolated B200 qualification")
        raise AssertionError("expected performance validation failure")
    except ValidationError as exc:
        assert "15%" in str(exc) or "p95" in str(exc)
