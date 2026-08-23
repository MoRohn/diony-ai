from __future__ import annotations

import json
import zipfile
from pathlib import Path

from diony_producer.control_plane import ControlPlane
from diony_producer.portable import export_project_package, inspect_project_package
from diony_producer.vault import persist_project
from diony_contracts.studio import blank_studio


def test_project_export_is_portable_hashed_and_secret_free(tmp_path: Path, monkeypatch) -> None:
    vault = tmp_path / "vault"
    monkeypatch.setenv("DIONY_VAULT", str(vault))
    monkeypatch.setenv("DIONY_PRODUCER_VAULT", str(vault))
    project = blank_studio()
    project.id = "project-portable"
    project.workspace_id = "local-workspace"
    project.owner_user_id = "local-user"
    asset_path = vault / "assets" / "still.txt"
    asset_path.parent.mkdir(parents=True, exist_ok=True)
    asset_path.write_text("creator-owned still metadata\n")
    persist_project(project)
    plane = ControlPlane(vault / ".control" / "diony.sqlite3")
    plane.bootstrap_local()
    plane.put_document(
        "provider_secret",
        "secret-1",
        "local-workspace",
        {"ciphertext": "must-never-export"},
        project_id=project.id,
    )
    package = export_project_package(project, plane=plane, extra_paths=[asset_path])
    report = inspect_project_package(package)
    assert report["ok"] is True
    with zipfile.ZipFile(package) as zf:
        names = set(zf.namelist())
        assert "manifest.json" in names
        assert "project/project.json" in names
        assert "checksums.sha256" in names
        assert "lineage/cost_allocations.json" in names
        assert "lineage/cost_dimensions.json" in names
        assert any(name.startswith("media/") for name in names)
        blob = b"".join(zf.read(name) for name in names if not name.endswith("/"))
        assert b"must-never-export" not in blob
        manifest = json.loads(zf.read("manifest.json"))
        assert manifest["philosophy"] == "creator-owned, portable, transparent"
