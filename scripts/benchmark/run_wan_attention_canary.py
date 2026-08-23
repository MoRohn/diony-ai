#!/usr/bin/env python3
"""Run one held immutable Take through cuDNN SDPA, AutoQC, and 1080 Finish."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path

from diony_contracts.enums import ArtifactKind, QCVerdict
from diony_contracts.models import Project
from diony_producer.farm import run_one
from diony_producer.finish import _finish_one
from diony_producer.vault import vault_root
from diony_wan.attention_qualification import (
    attention_decision,
    promote_quality_canary,
)


def _load(project_id: str) -> Project:
    path = vault_root() / "projects" / project_id / "project.json"
    return Project.model_validate_json(path.read_text(encoding="utf-8"))


def _digest(payload: object) -> str:
    return hashlib.sha256(
        json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    ).hexdigest()


def run_canary(*, project_id: str, take_id: str, receipt_path: Path) -> dict:
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    held = attention_decision(receipt_path, project_id=project_id)
    if held.enabled or "canary" not in held.reason.casefold():
        raise RuntimeError("Attention receipt is not at the operator-passed canary boundary.")
    project = _load(project_id)
    if project.production_stop is not None:
        raise RuntimeError("Infrastructure recovery must clear STOP before the canary.")
    if project.production_pause is None:
        raise RuntimeError("Attention canary requires a durable Production hold.")
    take = project.take(take_id)
    if project.shot(take.shot_id).selected_take_id != take.id:
        raise RuntimeError("Attention canary must reuse a selected immutable Take.")

    os.environ["DIONY_WAN_ATTENTION_BACKEND"] = "cudnn_sdpa"
    os.environ.setdefault("DIONY_MEASURED_QC_REQUIRED", "1")
    run_one(project, take)
    project = _load(project_id)
    take = project.take(take_id)
    qc = take.qc
    if qc is None or qc.verdict != QCVerdict.PASS:
        raise RuntimeError(f"Attention quality canary failed AutoQC: {(qc.notes if qc else 'missing QC')}")
    reference_verified = qc.reference_verified is True
    temporal_verified = bool(
        qc.measurement_version
        and qc.measured_frame_count > 1
        and qc.morph_ok
        and qc.flicker_ok
    )
    if not reference_verified or not temporal_verified:
        raise RuntimeError("Attention quality canary lacks reference or temporal evidence.")

    _finish_one(project, take)
    project = _load(project_id)
    take = project.take(take_id)
    negative = next(
        (artifact for artifact in take.artifacts if artifact.kind == ArtifactKind.NEGATIVE_720),
        None,
    )
    master = next(
        (artifact for artifact in take.artifacts if artifact.kind == ArtifactKind.FINISH_1080),
        None,
    )
    master_verified = bool(
        master
        and master.width == 1920
        and master.height == 1080
        and master.bytes > 32
        and len(master.sha256) == 64
        and (vault_root() / master.path).is_file()
    )
    if negative is None or not master_verified:
        raise RuntimeError("Attention canary did not land a verified 1080 master.")
    quality_payload = {
        "project_id": project.id,
        "take_id": take.id,
        "negative_sha256": negative.sha256,
        "master_sha256": master.sha256,
        "qc": take.qc.model_dump(mode="json") if take.qc else {},
        "operator_evidence_sha256": receipt["operator_evidence_sha256"],
    }
    evidence = {
        "take_id": take.id,
        "autoqc_passed": True,
        "reference_verified": reference_verified,
        "temporal_verified": temporal_verified,
        "master_1080_verified": master_verified,
        "quality_evidence_sha256": _digest(quality_payload),
    }
    promoted = promote_quality_canary(receipt, evidence)
    pending = receipt_path.with_suffix(receipt_path.suffix + ".part")
    pending.write_text(json.dumps(promoted, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    pending.replace(receipt_path)
    return {
        "project_id": project.id,
        "take_id": take.id,
        "stage": promoted["stage"],
        "negative_sha256": negative.sha256,
        "master_sha256": master.sha256,
        "quality_evidence_sha256": evidence["quality_evidence_sha256"],
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-id", required=True)
    parser.add_argument("--take-id", required=True)
    parser.add_argument("--receipt", type=Path, required=True)
    args = parser.parse_args()
    print(
        json.dumps(
            run_canary(
                project_id=args.project_id,
                take_id=args.take_id,
                receipt_path=args.receipt,
            ),
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
