"""Creator-owned Diony project package export and verification."""

from __future__ import annotations

import hashlib
import json
import uuid
import zipfile
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Iterable

from diony_contracts.models import Project
from diony_producer.control_plane import ControlPlane
from diony_producer.vault import vault_root


PACKAGE_VERSION = 1


def _json_bytes(value: object) -> bytes:
    return (json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n").encode()


def _sha(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _safe_archive_name(name: str) -> str:
    path = PurePosixPath(name)
    if path.is_absolute() or ".." in path.parts or not path.parts:
        raise ValueError(f"Unsafe package path: {name}")
    return str(path)


def _project_media_paths(project: Project) -> list[Path]:
    root = vault_root()
    rows: list[Path] = []
    for asset in project.assets:
        if asset.path:
            rows.append(root / asset.path)
        if asset.thumbnail_path:
            rows.append(root / asset.thumbnail_path)
        if asset.proxy_path:
            rows.append(root / asset.proxy_path)
    for take in project.takes:
        rows.extend(root / artifact.path for artifact in take.artifacts if artifact.path)
    rows.extend(root / deliverable.path for deliverable in project.deliverables if deliverable.path)
    if project.timeline and project.timeline.preview_path:
        master = root / project.timeline.preview_path
        rows.append(master)
        # Blueprint 24 evidence beside the master: final review, QC packet, subtitles.
        rows.extend(
            master.parent / name
            for name in ("final-review.json", "qc-packet.json", "master_1080.srt", "master_1080.vtt")
        )
    rows.append(root / "projects" / project.id / "decision-log.jsonl")
    return rows


def export_project_package(
    project: Project,
    *,
    plane: ControlPlane,
    extra_paths: Iterable[Path] = (),
) -> Path:
    """Write one verifiable `.diony.zip` without credentials or controller secrets."""

    root = vault_root().resolve()
    export_dir = root / "exports" / project.id
    export_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    target = export_dir / f"{project.id}-{stamp}-{uuid.uuid4().hex[:8]}.diony.zip"

    files: dict[str, bytes] = {
        "project/project.json": (project.model_dump_json(indent=2) + "\n").encode(),
        "README.md": (
            "# Diony project package\n\n"
            "This package belongs to its creator. It includes the project contract, "
            "generation lineage, cost/usage records, audit history, media, and SHA-256 checksums.\n\n"
            "Provider secrets, session tokens, and controller keys are never included.\n"
        ).encode(),
    }
    runs = plane.runs(project.workspace_id, project_id=project.id)
    jobs = [job for run in runs for job in plane.jobs(project.workspace_id, run.id)]
    usage = plane.usage(project.workspace_id, project_id=project.id)
    usage_ids = {event.id for event in usage}
    allocations = [
        row
        for row in plane.cost_allocations(project.workspace_id)
        if row.usage_event_id in usage_ids
    ]
    cost_center_ids = {row.cost_center_id for row in allocations}
    department_ids = {row.department_id for row in allocations}
    team_ids = {row.team_id for row in allocations}
    rate_card_ids = {row.rate_card_id for row in usage if row.rate_card_id}
    cost_dimensions = {
        "cost_centers": [
            row
            for row in plane.list_documents("cost_center", project.workspace_id)
            if row["id"] in cost_center_ids
        ],
        "departments": [
            row
            for row in plane.list_documents("department", project.workspace_id)
            if row["id"] in department_ids
        ],
        "teams": [
            row
            for row in plane.list_documents("team", project.workspace_id)
            if row["id"] in team_ids
        ],
        "rate_cards": [
            row
            for row in plane.list_documents("cost_rate_card", project.workspace_id)
            if row["id"] in rate_card_ids
        ],
    }
    audits = [
        event
        for event in plane.audit_log(project.workspace_id, limit=2000)
        if event.object_id in {project.id, *(run.id for run in runs), *(job.id for job in jobs)}
    ]
    events = [
        event
        for run in runs
        for event in plane.events(project.workspace_id, run.id, limit=2000)
    ]
    files["lineage/workflow_runs.json"] = _json_bytes(
        [run.model_dump(mode="json") for run in runs]
    )
    files["lineage/generation_jobs.json"] = _json_bytes(
        [job.model_dump(mode="json") for job in jobs]
    )
    files["lineage/progress_events.json"] = _json_bytes(
        [event.model_dump(mode="json") for event in events]
    )
    files["lineage/usage.json"] = _json_bytes(
        [event.model_dump(mode="json") for event in usage]
    )
    files["lineage/cost_allocations.json"] = _json_bytes(
        [row.model_dump(mode="json") for row in allocations]
    )
    files["lineage/cost_dimensions.json"] = _json_bytes(cost_dimensions)
    files["lineage/audit.json"] = _json_bytes(
        [event.model_dump(mode="json") for event in audits]
    )

    seen: set[Path] = set()
    for source in [*_project_media_paths(project), *list(extra_paths)]:
        try:
            resolved = source.resolve(strict=True)
        except OSError:
            continue
        if resolved in seen or (resolved != root and root not in resolved.parents):
            continue
        if not resolved.is_file():
            continue
        seen.add(resolved)
        relative = resolved.relative_to(root).as_posix()
        archive_name = _safe_archive_name(f"media/{relative}")
        files[archive_name] = resolved.read_bytes()

    checksums = {name: _sha(payload) for name, payload in sorted(files.items())}
    manifest = {
        "schema_version": PACKAGE_VERSION,
        "package_id": f"package-{uuid.uuid4().hex}",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "philosophy": "creator-owned, portable, transparent",
        "project_id": project.id,
        "workspace_id": project.workspace_id,
        "project_version": project.version,
        "production_contract": {
            "brain": "deepseek-ai/DeepSeek-V4-Flash-0731",
            "camera": "Wan2.2 native 720p",
            "finish": "diony_sr_v1 1920x1080 Rec.709",
            "human_gates": ["bible", "distribution"],
        },
        "files": [
            {"path": name, "sha256": digest, "bytes": len(files[name])}
            for name, digest in checksums.items()
        ],
    }
    files["manifest.json"] = _json_bytes(manifest)
    files["checksums.sha256"] = (
        "".join(f"{digest}  {name}\n" for name, digest in checksums.items())
    ).encode()

    with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for name, payload in sorted(files.items()):
            archive.writestr(_safe_archive_name(name), payload)
    return target


def inspect_project_package(path: Path) -> dict[str, object]:
    failures: list[str] = []
    with zipfile.ZipFile(path) as archive:
        names = archive.namelist()
        for name in names:
            try:
                _safe_archive_name(name)
            except ValueError as exc:
                failures.append(str(exc))
        required = {"manifest.json", "project/project.json", "checksums.sha256"}
        missing = required.difference(names)
        if missing:
            failures.append(f"Missing {', '.join(sorted(missing))}")
            return {"ok": False, "failures": failures}
        manifest = json.loads(archive.read("manifest.json"))
        for row in manifest.get("files", []):
            name = str(row.get("path") or "")
            if name not in names:
                failures.append(f"Missing payload {name}")
                continue
            actual = _sha(archive.read(name))
            if actual != row.get("sha256"):
                failures.append(f"Checksum mismatch {name}")
        try:
            Project.model_validate_json(archive.read("project/project.json"))
        except Exception as exc:  # noqa: BLE001
            failures.append(f"Invalid project contract: {exc}")
    return {
        "ok": not failures,
        "failures": failures,
        "project_id": manifest.get("project_id"),
        "workspace_id": manifest.get("workspace_id"),
        "files": len(manifest.get("files", [])),
    }
