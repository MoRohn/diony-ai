from __future__ import annotations

from pathlib import Path

import pytest

from diony_contracts.enums import AssetKind
from diony_contracts.models import Asset
from diony_contracts.studio import blank_studio
from diony_producer.control_plane import ControlPlane
from diony_producer.privacy import erase_project, project_erasure_plan
from diony_producer.vault import persist_project


def test_erasure_is_scoped_confirmed_and_removes_project_lineage(
    tmp_path: Path, monkeypatch
) -> None:
    vault = tmp_path / "vault"
    monkeypatch.setenv("DIONY_VAULT", str(vault))
    monkeypatch.setenv("DIONY_PRODUCER_VAULT", str(vault))
    project = blank_studio()
    project.id = "project-erase"
    project.assets.append(
        Asset(
            id="asset-erase",
            kind=AssetKind.DOCUMENT,
            role="reference",
            filename="owned.txt",
            mime="text/plain",
            path="assets/owned.txt",
            bytes=len("creator-owned"),
        )
    )
    media = vault / "assets" / "owned.txt"
    media.parent.mkdir(parents=True)
    media.write_text("creator-owned")
    persist_project(project)
    # Erasure is refused while a project is the active in-memory/vault pointer.
    another = blank_studio()
    another.id = "project-current"
    persist_project(another)

    plane = ControlPlane()
    plane.bootstrap_local()
    plane.put_document(
        "project", project.id, project.workspace_id, project.model_dump(mode="json"), project_id=project.id
    )
    plan = project_erasure_plan(project)
    assert str(media) in plan["local_files"]
    with pytest.raises(ValueError, match="ERASE project-erase"):
        erase_project(project, plane=plane, confirmation="wrong", actor_user_id="local-user")
    assert media.exists()

    result = erase_project(
        project,
        plane=plane,
        confirmation="ERASE project-erase",
        actor_user_id="local-user",
    )
    assert result["local_files_deleted"] >= 2
    assert not media.exists()
    assert not (vault / "projects" / project.id / "project.json").exists()
    assert plane.list_documents("project", project.workspace_id, project_id=project.id) == []
    assert plane.audit_log(project.workspace_id)[0].action == "project.erased"
