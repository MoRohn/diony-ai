"""Convert paired isolated benchmark evidence into a resident-Wan receipt."""

from __future__ import annotations

import math
import hashlib
import json
import statistics
from collections import defaultdict
from typing import Any, Iterable

from diony_wan.resident import (
    EAGER_KERNEL_MODE,
    KERNEL_CANDIDATES,
    QUALITY_CONTRACT_SHA256,
    RESIDENT_TASKS,
)


class ValidationError(ValueError):
    pass


def _p95(values: list[float]) -> float:
    ordered = sorted(values)
    return ordered[max(0, math.ceil(0.95 * len(ordered)) - 1)]


def build_validation_receipt(
    rows: Iterable[dict[str, Any]],
    *,
    approved_by: str,
    max_canary_percent: int = 10,
    kernel_mode: str = EAGER_KERNEL_MODE,
) -> dict[str, Any]:
    approver = approved_by.strip()
    if not approver:
        raise ValidationError("A named validation approver is required.")
    if max_canary_percent < 1 or max_canary_percent > 100:
        raise ValidationError("max_canary_percent must be from 1 through 100.")
    if kernel_mode not in KERNEL_CANDIDATES:
        raise ValidationError("kernel_mode must be eager or regional_compile.")
    grouped: dict[str, dict[str, dict[str, Any]]] = defaultdict(dict)
    for row in rows:
        clip_id = str(row.get("clip_id") or "").strip()
        lane = str(row.get("lane") or "").strip()
        if not clip_id or lane not in {"baseline", "candidate"}:
            raise ValidationError("Every benchmark row needs a clip_id and baseline/candidate lane.")
        if lane in grouped[clip_id]:
            raise ValidationError(f"Duplicate {lane} benchmark row for {clip_id}.")
        grouped[clip_id][lane] = dict(row)
    if len(grouped) < 30:
        raise ValidationError("Resident validation requires at least 30 paired clips.")
    baseline: list[float] = []
    candidate: list[float] = []
    baseline_retries = 0
    candidate_retries = 0
    candidate_ooms = 0
    quality_evidence: list[dict[str, str]] = []
    qualified_tasks: set[str] = set()
    for clip_id, pair in grouped.items():
        if set(pair) != {"baseline", "candidate"}:
            raise ValidationError(f"Benchmark clip {clip_id} is not paired.")
        left, right = pair["baseline"], pair["candidate"]
        task = str(left.get("task") or "").strip()
        if task not in RESIDENT_TASKS or str(right.get("task") or "").strip() != task:
            raise ValidationError(f"Benchmark clip {clip_id} changed or omitted its resident task.")
        qualified_tasks.add(task)
        if left.get("input_sha256") != right.get("input_sha256"):
            raise ValidationError(f"Benchmark clip {clip_id} changed its input lineage.")
        if left.get("quality_contract_sha256") != QUALITY_CONTRACT_SHA256 or right.get(
            "quality_contract_sha256"
        ) != QUALITY_CONTRACT_SHA256:
            raise ValidationError(f"Benchmark clip {clip_id} changed the quality contract.")
        if left.get("quality_passed") is not True or right.get("quality_passed") is not True:
            raise ValidationError(f"Benchmark clip {clip_id} did not pass quality equivalence.")
        for lane, evidence in (("baseline", left), ("candidate", right)):
            if evidence.get("autoqc_passed") is not True:
                raise ValidationError(f"Benchmark clip {clip_id} {lane} did not pass AutoQC.")
            if evidence.get("master_verified") is not True:
                raise ValidationError(f"Benchmark clip {clip_id} {lane} has no verified 1080 master.")
            if evidence.get("blind_review_passed") is not True:
                raise ValidationError(f"Benchmark clip {clip_id} {lane} did not pass blind review.")
            evidence_sha = str(evidence.get("quality_evidence_sha256") or "")
            if len(evidence_sha) != 64:
                raise ValidationError(f"Benchmark clip {clip_id} {lane} has no quality evidence hash.")
        if left.get("decoded_frame_sha256") != right.get("decoded_frame_sha256"):
            raise ValidationError(f"Benchmark clip {clip_id} changed decoded frame bytes.")
        quality_evidence.append(
            {
                "clip_id": clip_id,
                "baseline": str(left["quality_evidence_sha256"]),
                "candidate": str(right["quality_evidence_sha256"]),
                "decoded_frame_sha256": str(right["decoded_frame_sha256"]),
            }
        )
        try:
            left_seconds = float(left["duration_seconds"])
            right_seconds = float(right["duration_seconds"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ValidationError(f"Benchmark clip {clip_id} has invalid timing.") from exc
        if left_seconds <= 0 or right_seconds <= 0:
            raise ValidationError(f"Benchmark clip {clip_id} has non-positive timing.")
        baseline.append(left_seconds)
        candidate.append(right_seconds)
        baseline_retries += int(left.get("retries") or 0)
        candidate_retries += int(right.get("retries") or 0)
        candidate_ooms += int(bool(right.get("oom")))
    baseline_p50 = statistics.median(baseline)
    candidate_p50 = statistics.median(candidate)
    baseline_p95 = _p95(baseline)
    candidate_p95 = _p95(candidate)
    improvement = (baseline_p50 - candidate_p50) / baseline_p50
    if improvement < 0.15:
        raise ValidationError("Candidate p50 improvement is below the required 15%.")
    if candidate_p95 > baseline_p95:
        raise ValidationError("Candidate p95 latency regressed.")
    if candidate_ooms:
        raise ValidationError("Candidate recorded an out-of-memory failure.")
    if candidate_retries > baseline_retries:
        raise ValidationError("Candidate retry count regressed.")
    evidence_digest = hashlib.sha256(
        json.dumps(quality_evidence, separators=(",", ":"), sort_keys=True).encode("utf-8")
    ).hexdigest()
    return {
        "schema_version": 1,
        "candidate": KERNEL_CANDIDATES[kernel_mode],
        "quality_contract_sha256": QUALITY_CONTRACT_SHA256,
        "sample_count": len(grouped),
        "baseline_p50_seconds": round(baseline_p50, 6),
        "candidate_p50_seconds": round(candidate_p50, 6),
        "baseline_p95_seconds": round(baseline_p95, 6),
        "candidate_p95_seconds": round(candidate_p95, 6),
        "baseline_retries": baseline_retries,
        "candidate_retries": candidate_retries,
        "candidate_ooms": candidate_ooms,
        "quality_passed": True,
        "quality_evidence_sha256": evidence_digest,
        "qualified_tasks": sorted(qualified_tasks),
        "max_canary_percent": max_canary_percent,
        "approved_by": approver,
    }
