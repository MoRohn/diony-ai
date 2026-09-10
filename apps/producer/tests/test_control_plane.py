from __future__ import annotations

import base64
from pathlib import Path

import pytest

from diony_contracts.launch import MembershipRole
from diony_producer.control_plane import ControlPlane, WorkspaceAccessDenied
from diony_producer.security import SecretVault


def test_bootstrap_is_idempotent_and_workspace_isolation_is_enforced(tmp_path: Path) -> None:
    plane = ControlPlane(tmp_path / "control.sqlite3")
    first = plane.bootstrap_local()
    again = plane.bootstrap_local()
    assert first.workspace.id == again.workspace.id == "local-workspace"
    assert (
        plane.get_document("cost_governance", "cost-governance", "local-workspace")["admin_team_id"]
        == "local-admin-team"
    )
    plane.put_document("project", "project-1", "local-workspace", {"title": "Owned here"})
    assert plane.get_document("project", "project-1", "local-workspace")["title"] == "Owned here"
    with pytest.raises(WorkspaceAccessDenied):
        plane.get_document("project", "project-1", "another-workspace")


def test_membership_roles_gate_management(tmp_path: Path) -> None:
    plane = ControlPlane(tmp_path / "control.sqlite3")
    plane.bootstrap_local()
    plane.add_membership("usr-viewer", "local-workspace", MembershipRole.VIEWER)
    assert (
        plane.authorize("local-user", "local-workspace", MembershipRole.OWNER).role
        == MembershipRole.OWNER
    )
    with pytest.raises(WorkspaceAccessDenied):
        plane.authorize("usr-viewer", "local-workspace", MembershipRole.PRODUCER)


def test_events_and_audit_are_monotonic_and_filter_by_workspace(tmp_path: Path) -> None:
    plane = ControlPlane(tmp_path / "control.sqlite3")
    plane.bootstrap_local()
    one = plane.append_event(
        workspace_id="local-workspace",
        run_id="run-1",
        project_id="project-1",
        kind="progress",
        payload={"message": "Script locked"},
    )
    two = plane.append_event(
        workspace_id="local-workspace",
        run_id="run-1",
        project_id="project-1",
        kind="progress",
        payload={"message": "Wan rolling"},
    )
    plane.audit(
        workspace_id="local-workspace",
        actor_user_id="local-user",
        action="workflow.started",
        object_type="workflow_run",
        object_id="run-1",
        correlation_id="corr-1",
    )
    assert two.seq > one.seq
    assert [event.payload["message"] for event in plane.events("local-workspace", "run-1")] == [
        "Script locked",
        "Wan rolling",
    ]
    assert plane.audit_log("local-workspace")[0].correlation_id == "corr-1"


def test_byok_keys_are_encrypted_with_workspace_bound_aad(tmp_path: Path, monkeypatch) -> None:
    key = base64.urlsafe_b64encode(b"k" * 32).decode()
    monkeypatch.setenv("DIONY_KEY_ENCRYPTION_KEY", key)
    plane = ControlPlane(tmp_path / "control.sqlite3")
    plane.bootstrap_local()
    secrets = SecretVault(plane)
    saved = secrets.put("local-workspace", "provider-x", "api_key", "super-secret-value")
    assert saved.masked == "supe…alue"
    raw = (tmp_path / "control.sqlite3").read_bytes()
    assert b"super-secret-value" not in raw
    assert secrets.get("local-workspace", "provider-x", "api_key") == "super-secret-value"
    with pytest.raises((ValueError, Exception)):
        secrets.decrypt_for_workspace("another-workspace", saved.ciphertext)


def test_shared_controller_requires_external_key_encryption_key(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("DIONY_AUTH_MODE", "token")
    monkeypatch.delenv("DIONY_KEY_ENCRYPTION_KEY", raising=False)
    with pytest.raises(RuntimeError, match="DIONY_KEY_ENCRYPTION_KEY"):
        SecretVault(ControlPlane(tmp_path / "control.sqlite3"))


def test_run_leases_prevent_two_workers_from_dispatching_the_same_stage(tmp_path: Path) -> None:
    plane = ControlPlane(tmp_path / "control.sqlite3")
    assert plane.claim_run("run-one", owner_id="worker-a", lease_seconds=30) is True
    assert plane.claim_run("run-one", owner_id="worker-b", lease_seconds=30) is False
    plane.release_run("run-one", owner_id="worker-a")
    assert plane.claim_run("run-one", owner_id="worker-b", lease_seconds=30) is True
