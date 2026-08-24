"""Run-scoped qualification for the B200 cuDNN SDPA attention lane."""

from __future__ import annotations

import hashlib
import json
import statistics
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from diony_wan.resident import QUALITY_CONTRACT_SHA256


CANDIDATE = "pytorch-cudnn-sdpa-b200-v1"
BACKEND = "cudnn_sdpa"
WAN_ATTENTION_SHAPE = [1, 40, 75600, 128]
MAX_ABS_DELTA = 5e-4
MAX_MEAN_ABS_DELTA = 5e-5
MIN_SPEEDUP = 1.5


class QualificationError(ValueError):
    pass


@dataclass(frozen=True)
class AttentionDecision:
    enabled: bool
    reason: str
    backend: str = "auto"
    receipt_sha256: str = ""


def _sha256(payload: Any) -> str:
    return hashlib.sha256(
        json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    ).hexdigest()


def _valid_digest(value: object) -> bool:
    text = str(value or "").lower()
    return len(text) == 64 and all(char in "0123456789abcdef" for char in text)


def build_operator_receipt(
    evidence: dict[str, Any],
    *,
    scope_project_id: str,
    approved_by: str,
) -> dict[str, Any]:
    project_id = scope_project_id.strip()
    approver = approved_by.strip()
    if not project_id or not approver:
        raise QualificationError("A Project scope and named approver are required.")
    if evidence.get("gpu_name") != "NVIDIA B200" or evidence.get("compute_capability") != "10.0":
        raise QualificationError("The attention candidate must be measured on the target B200.")
    if evidence.get("shape") != WAN_ATTENTION_SHAPE or evidence.get("dtype") != "bfloat16":
        raise QualificationError("The operator probe must use the exact Wan BF16 attention shape.")
    if evidence.get("candidate_backend") != BACKEND or evidence.get("finite") is not True:
        raise QualificationError("The cuDNN attention candidate did not produce finite output.")
    try:
        baseline = [float(value) for value in evidence["baseline_ms"]]
        candidate = [float(value) for value in evidence["candidate_ms"]]
        max_delta = float(evidence["max_abs_delta"])
        mean_delta = float(evidence["mean_abs_delta"])
    except (KeyError, TypeError, ValueError) as exc:
        raise QualificationError("The attention operator evidence is incomplete.") from exc
    if len(baseline) < 3 or len(candidate) < 3 or min(baseline + candidate) <= 0:
        raise QualificationError("At least three positive timings per attention backend are required.")
    baseline_median = statistics.median(baseline)
    candidate_median = statistics.median(candidate)
    speedup = baseline_median / candidate_median
    if speedup < MIN_SPEEDUP:
        raise QualificationError("The cuDNN attention speedup is below 1.5x.")
    if max_delta > MAX_ABS_DELTA or mean_delta > MAX_MEAN_ABS_DELTA:
        raise QualificationError("The cuDNN attention numeric delta exceeds the BF16 equivalence limit.")
    normalized = dict(evidence)
    return {
        "schema_version": 1,
        "candidate": CANDIDATE,
        "backend": BACKEND,
        "scope_project_id": project_id,
        "quality_contract_sha256": QUALITY_CONTRACT_SHA256,
        "stage": "operator_passed_quality_canary_required",
        "operator_speedup": round(speedup, 4),
        "operator_evidence": normalized,
        "operator_evidence_sha256": _sha256(normalized),
        "quality_canary": None,
        "approved_by": approver,
    }


def bind_project_canary(
    receipt: dict[str, Any],
    *,
    project_id: str,
    take_id: str,
) -> dict[str, Any]:
    """Reuse measured B200 operator proof for exactly one Project/Take canary."""
    clean_project_id = project_id.strip()
    clean_take_id = take_id.strip()
    if not clean_project_id or not clean_take_id:
        raise QualificationError("A Project and selected immutable Take are required.")
    if receipt.get("candidate") != CANDIDATE or receipt.get("backend") != BACKEND:
        raise QualificationError("The source receipt names another attention candidate.")
    if receipt.get("quality_contract_sha256") != QUALITY_CONTRACT_SHA256:
        raise QualificationError("The source receipt changed the pinned Wan quality contract.")
    if receipt.get("stage") not in {
        "operator_passed_quality_canary_required",
        "run_scoped_production",
    }:
        raise QualificationError("The source receipt has no reusable B200 operator proof.")
    existing_take_id = str(receipt.get("quality_canary_take_id") or "").strip()
    if (
        receipt.get("scope_project_id") == clean_project_id
        and existing_take_id
        and existing_take_id != clean_take_id
        and receipt.get("stage") != "run_scoped_production"
    ):
        raise QualificationError("Another selected immutable Take owns this Project canary.")
    rebound = build_operator_receipt(
        dict(receipt.get("operator_evidence") or {}),
        scope_project_id=clean_project_id,
        approved_by=str(receipt.get("approved_by") or "Diony B200 qualification"),
    )
    rebound["quality_canary_take_id"] = clean_take_id
    rebound["source_receipt_sha256"] = _sha256(receipt)
    return rebound


def rebind_failed_project_canary(
    receipt: dict[str, Any],
    *,
    project_id: str,
    failed_take_id: str,
    retry_take_id: str,
) -> dict[str, Any]:
    """Move an unpromoted canary only to the failed Take's immutable retake."""
    clean_project_id = project_id.strip()
    clean_failed_id = failed_take_id.strip()
    clean_retry_id = retry_take_id.strip()
    if not clean_project_id or not clean_failed_id or not clean_retry_id:
        raise QualificationError("Project, failed Take, and retry Take are required.")
    if clean_failed_id == clean_retry_id:
        raise QualificationError("The failed canary and immutable retry must differ.")
    if receipt.get("stage") != "operator_passed_quality_canary_required":
        raise QualificationError("Only an unpromoted Project canary may be rebound.")
    if receipt.get("scope_project_id") != clean_project_id:
        raise QualificationError("Another Project owns this attention canary.")
    if str(receipt.get("quality_canary_take_id") or "").strip() != clean_failed_id:
        raise QualificationError("Another selected immutable Take owns this Project canary.")
    rebound = build_operator_receipt(
        dict(receipt.get("operator_evidence") or {}),
        scope_project_id=clean_project_id,
        approved_by=str(receipt.get("approved_by") or "Diony B200 qualification"),
    )
    rebound["quality_canary_take_id"] = clean_retry_id
    rebound["previous_failed_canary_take_id"] = clean_failed_id
    rebound["source_receipt_sha256"] = _sha256(receipt)
    return rebound


def promote_quality_canary(
    receipt: dict[str, Any],
    evidence: dict[str, Any],
) -> dict[str, Any]:
    if receipt.get("candidate") != CANDIDATE or receipt.get("stage") != "operator_passed_quality_canary_required":
        raise QualificationError("Only a passing operator receipt can be promoted.")
    required = (
        "autoqc_passed",
        "reference_verified",
        "temporal_verified",
        "master_1080_verified",
    )
    if any(evidence.get(field) is not True for field in required):
        raise QualificationError("The same-model quality canary did not clear every picture gate.")
    evidence_take_id = str(evidence.get("take_id") or "").strip()
    expected_take_id = str(receipt.get("quality_canary_take_id") or "").strip()
    if expected_take_id and evidence_take_id != expected_take_id:
        raise QualificationError("Only the selected immutable Take may promote this Project canary.")
    if not evidence_take_id or not _valid_digest(
        evidence.get("quality_evidence_sha256")
    ):
        raise QualificationError("The quality canary evidence is incomplete.")
    promoted = dict(receipt)
    promoted["stage"] = "run_scoped_production"
    promoted["quality_canary"] = dict(evidence)
    promoted["quality_canary_sha256"] = _sha256(evidence)
    return promoted


def attention_decision(path: Path, *, project_id: str) -> AttentionDecision:
    try:
        raw = path.read_bytes()
        receipt = json.loads(raw)
    except (OSError, json.JSONDecodeError, TypeError):
        return AttentionDecision(False, "cuDNN SDPA qualification receipt is missing or unreadable.")
    if not isinstance(receipt, dict) or receipt.get("schema_version") != 1:
        return AttentionDecision(False, "cuDNN SDPA qualification receipt is invalid.")
    if receipt.get("candidate") != CANDIDATE or receipt.get("backend") != BACKEND:
        return AttentionDecision(False, "cuDNN SDPA qualification receipt names another candidate.")
    if receipt.get("quality_contract_sha256") != QUALITY_CONTRACT_SHA256:
        return AttentionDecision(False, "cuDNN SDPA receipt changed the pinned Wan quality contract.")
    if receipt.get("scope_project_id") != project_id.strip():
        return AttentionDecision(False, "cuDNN SDPA receipt is scoped to another Project.")
    if receipt.get("stage") != "run_scoped_production":
        return AttentionDecision(False, "cuDNN SDPA operator passed; the 1080 quality canary is still required.")
    evidence = receipt.get("operator_evidence") or {}
    try:
        speedup = float(receipt.get("operator_speedup") or 0)
        max_delta = float(evidence.get("max_abs_delta"))
        mean_delta = float(evidence.get("mean_abs_delta"))
    except (TypeError, ValueError):
        return AttentionDecision(False, "cuDNN SDPA receipt has incomplete operator evidence.")
    canary = receipt.get("quality_canary") or {}
    if (
        evidence.get("shape") != WAN_ATTENTION_SHAPE
        or evidence.get("finite") is not True
        or speedup < MIN_SPEEDUP
        or max_delta > MAX_ABS_DELTA
        or mean_delta > MAX_MEAN_ABS_DELTA
        or not _valid_digest(canary.get("quality_evidence_sha256"))
    ):
        return AttentionDecision(False, "cuDNN SDPA qualification evidence does not clear activation gates.")
    return AttentionDecision(
        True,
        "Run-scoped B200 cuDNN SDPA lane approved.",
        BACKEND,
        hashlib.sha256(raw).hexdigest(),
    )
