from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from diony_contracts.launch import Membership, MembershipRole
from diony_producer.app import app
from diony_producer.control_plane import ControlPlane
from diony_producer.security import issue_token


def test_launch_api_bootstrap_workflow_progress_controls_and_metrics(tmp_path: Path, monkeypatch) -> None:
    vault = tmp_path / "vault"
    monkeypatch.setenv("DIONY_VAULT", str(vault))
    monkeypatch.setenv("DIONY_PRODUCER_VAULT", str(vault))
    monkeypatch.setenv("DIONY_AUTH_MODE", "local")
    client = TestClient(app)
    session = client.get("/api/v1/session")
    assert session.status_code == 200
    assert session.json()["workspace_id"] == "local-workspace"
    assert session.headers["X-Correlation-ID"]
    providers = client.get("/api/v1/providers")
    assert providers.status_code == 200
    assert any(row["id"] == "diony.wan22" for row in providers.json()["providers"])
    reset_project = client.post("/api/reset")
    assert reset_project.status_code == 200
    projects = client.get("/api/v1/projects")
    assert projects.status_code == 200
    assert reset_project.json()["id"] in {row["id"] for row in projects.json()["projects"]}
    team = client.post("/api/v1/teams", json={"name": "Picture Unit"})
    assert team.status_code == 201
    member = client.put(
        "/api/v1/members/maker-two",
        json={
            "display_name": "Maker Two",
            "role": "creator",
            "team_ids": [team.json()["id"]],
        },
    )
    assert member.status_code == 200
    members = client.get("/api/v1/members")
    assert {row["user_id"] for row in members.json()["members"]} >= {"local-user", "maker-two"}
    department = client.post(
        "/api/v1/departments",
        json={"name": "Picture", "code": "PIC", "team_ids": [team.json()["id"]]},
    )
    assert department.status_code == 201
    center = client.post(
        "/api/v1/cost-centers",
        json={
            "name": "Pilot Picture",
            "code": "PILOT-PIC",
            "department_id": department.json()["id"],
            "team_ids": [team.json()["id"]],
            "internal_credit_budget": 100,
            "cost_budget_usd_micros": 1_000_000,
        },
    )
    assert center.status_code == 201
    governance = client.get("/api/v1/cost-governance")
    assert governance.status_code == 200
    assert governance.json()["admin_user_id"] == "local-user"
    assert governance.json()["admin_team_id"] == "local-admin-team"
    created = client.post(
        "/api/v1/workflows",
        json={
            "project_id": "project-launch",
            "stages": ["intake", "script"],
            "config": {
                "cost_center_id": center.json()["id"],
                "department_id": department.json()["id"],
                "team_id": team.json()["id"],
            },
        },
        headers={"X-Correlation-ID": "corr-api-test"},
    )
    assert created.status_code == 201, created.text
    run_id = created.json()["id"]
    paused = client.post(f"/api/v1/workflows/{run_id}/pause")
    assert paused.status_code == 200
    resumed = client.post(f"/api/v1/workflows/{run_id}/resume")
    assert resumed.status_code == 200
    checkpoint = client.post(
        f"/api/v1/workflows/{run_id}/stages/intake/checkpoint",
        json={
            "output_asset_ids": ["asset-intake-proof"],
            "actual_internal_credits": 12,
            "cost_breakdown": {"compute_usd_micros": 125_000},
            "metadata": {"engine": "brain"},
        },
    )
    assert checkpoint.status_code == 200
    advanced = client.post(f"/api/v1/workflows/{run_id}/advance")
    assert advanced.status_code == 200
    assert advanced.json()["stages"][0]["status"] == "succeeded"
    events = client.get(f"/api/v1/workflows/{run_id}/events")
    assert events.status_code == 200
    assert events.json()["events"]
    health = client.get("/api/v1/health")
    assert health.status_code == 200
    assert health.json()["database"] == "ready"
    assert health.json()["cost_center"] == "ready"
    assert health.json()["object_storage"] == "local-vault"
    organization = client.get("/api/v1/costs/organization")
    assert organization.status_code == 200
    assert organization.json()["totals"]["internal_credits"] == 12
    assert organization.json()["totals"]["cost_usd_micros"] == 125_000
    assert organization.json()["reconciliation"]["difference_usd_micros"] == 0
    metrics = client.get("/metrics")
    assert metrics.status_code == 200
    assert "diony_workflow_runs" in metrics.text
    assert "diony_workflow_success_rate" in metrics.text
    assert "diony_cost_per_video_minute_usd_micros" in metrics.text
    assert "diony_internal_credits 12" in metrics.text
    schema_paths = client.get("/openapi.json").json()["paths"]
    assert not any("billing" in path or "checkout" in path for path in schema_paths)


def test_token_auth_mode_rejects_unsigned_launch_requests(tmp_path: Path, monkeypatch) -> None:
    vault = tmp_path / "vault"
    monkeypatch.setenv("DIONY_VAULT", str(vault))
    monkeypatch.setenv("DIONY_PRODUCER_VAULT", str(vault))
    monkeypatch.setenv("DIONY_AUTH_MODE", "token")
    monkeypatch.setenv("DIONY_AUTH_SECRET", "test-secret-that-is-long-enough")
    client = TestClient(app)
    assert client.get("/api/v1/session").status_code == 401
    assert client.get("/api/project").status_code == 200
    assert client.post("/api/reset").status_code == 401


def test_token_auth_mode_accepts_persisted_workspace_membership(tmp_path: Path, monkeypatch) -> None:
    vault = tmp_path / "vault"
    monkeypatch.setenv("DIONY_VAULT", str(vault))
    monkeypatch.setenv("DIONY_PRODUCER_VAULT", str(vault))
    monkeypatch.setenv("DIONY_AUTH_MODE", "token")
    monkeypatch.setenv("DIONY_AUTH_SECRET", "test-secret-that-is-long-enough")
    ControlPlane().bootstrap_local()
    token = issue_token("local-user", "local-workspace", MembershipRole.OWNER)
    response = TestClient(app).get(
        "/api/v1/session", headers={"Authorization": f"Bearer {token}"}
    )
    assert response.status_code == 200
    assert response.json()["auth_mode"] == "token"


def test_generic_admin_cannot_read_organization_cost_rollup(tmp_path: Path, monkeypatch) -> None:
    vault = tmp_path / "vault"
    monkeypatch.setenv("DIONY_VAULT", str(vault))
    monkeypatch.setenv("DIONY_PRODUCER_VAULT", str(vault))
    monkeypatch.setenv("DIONY_AUTH_MODE", "token")
    monkeypatch.setenv("DIONY_AUTH_SECRET", "test-secret-that-is-long-enough")
    plane = ControlPlane()
    plane.bootstrap_local()
    membership = Membership(
        id="local-workspace:generic-admin",
        user_id="generic-admin",
        workspace_id="local-workspace",
        role=MembershipRole.ADMIN,
        team_ids=[],
    )
    plane.put_document(
        "membership",
        membership.id,
        membership.workspace_id,
        membership.model_dump(mode="json"),
    )
    token = issue_token("generic-admin", "local-workspace", MembershipRole.ADMIN)
    response = TestClient(app).get(
        "/api/v1/costs/organization",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 403
    assert "Cost Center admin" in response.json()["detail"]
