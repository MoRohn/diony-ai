"""Explicit, workspace-scoped creator data erasure."""

from __future__ import annotations

import json
import os
import uuid
from pathlib import Path
from typing import Any

from diony_contracts.models import Project
from diony_producer.control_plane import ControlPlane
from diony_producer.media_storage import S3CompatibleStorage
from diony_producer.portable import _project_media_paths
from diony_producer.vault import list_vault_projects, vault_root


def _inside(root: Path, path: Path) -> Path | None:
    try:
        resolved = path.resolve()
    except OSError:
        return None
    if resolved == root or root not in resolved.parents:
        return None
    return resolved


def _current_project_id(root: Path) -> str:
    pointer = root / ".cluster" / "current.json"
    try:
        return str(json.loads(pointer.read_text()).get("project_id") or "")
    except (OSError, ValueError):
        return ""


def project_erasure_plan(project: Project) -> dict[str, Any]:
    root = vault_root().resolve()
    candidates = [
        *_project_media_paths(project),
        root / "projects" / project.id / "project.json",
    ]
    export_dir = root / "exports" / project.id
    if export_dir.is_dir():
        candidates.extend(path for path in export_dir.rglob("*") if path.is_file())

    shared: set[Path] = set()
    for other in list_vault_projects():
        if other.id == project.id:
            continue
        for path in _project_media_paths(other):
            resolved = _inside(root, path)
            if resolved:
                shared.add(resolved)

    local_files: list[str] = []
    skipped_shared: list[str] = []
    for candidate in candidates:
        resolved = _inside(root, candidate)
        if not resolved or not resolved.is_file():
            continue
        if resolved in shared:
            skipped_shared.append(str(resolved))
        else:
            local_files.append(str(resolved))
    remote_keys = sorted({asset.storage_key for asset in project.assets if asset.storage_key})
    return {
        "project_id": project.id,
        "workspace_id": project.workspace_id,
        "confirmation": f"ERASE {project.id}",
        "active_project": _current_project_id(root) == project.id,
        "local_files": sorted(set(local_files)),
        "shared_files_retained": sorted(set(skipped_shared)),
        "remote_keys": remote_keys,
        "warning": "Erasure is permanent. Export first if the creator wants an archive.",
    }


def _object_storage() -> S3CompatibleStorage | None:
    bucket = os.environ.get("DIONY_OBJECT_BUCKET", "").strip()
    if not bucket:
        return None
    return S3CompatibleStorage(
        bucket=bucket,
        endpoint_url=os.environ.get("AWS_ENDPOINT_URL") or None,
        region=os.environ.get("AWS_REGION", "us-east-1"),
    )


def erase_project(
    project: Project,
    *,
    plane: ControlPlane,
    confirmation: str,
    actor_user_id: str,
    correlation_id: str = "",
) -> dict[str, Any]:
    plan = project_erasure_plan(project)
    if confirmation != plan["confirmation"]:
        raise ValueError(f"Type {plan['confirmation']} to erase this project")
    if plan["active_project"]:
        raise ValueError("Start a new project before erasing the active production")

    storage = _object_storage()
    remote_keys = list(plan["remote_keys"])
    if remote_keys and storage is None:
        raise RuntimeError(
            "This project has mirrored objects but object storage is not configured for erasure"
        )
    remote_deleted = storage.delete_many(remote_keys) if storage else 0

    deleted = 0
    root = vault_root().resolve()
    for raw in plan["local_files"]:
        target = _inside(root, Path(raw))
        if target and target.is_file():
            target.unlink()
            deleted += 1
    for folder in [root / "projects" / project.id, root / "exports" / project.id]:
        if folder.is_dir():
            for candidate in sorted(folder.rglob("*"), reverse=True):
                if candidate.is_dir():
                    try:
                        candidate.rmdir()
                    except OSError:
                        pass
            try:
                folder.rmdir()
            except OSError:
                pass

    receipt = {
        "local_files_deleted": deleted,
        "remote_objects_deleted": remote_deleted,
        "shared_files_retained": len(plan["shared_files_retained"]),
    }
    plane.erase_project_records(
        project.workspace_id,
        project.id,
        actor_user_id=actor_user_id,
        correlation_id=correlation_id or f"erase-{uuid.uuid4().hex}",
        metadata=receipt,
    )
    return {"project_id": project.id, "erased": True, **receipt}
