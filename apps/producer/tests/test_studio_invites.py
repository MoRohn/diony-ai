from __future__ import annotations

import base64
from pathlib import Path

from fastapi.testclient import TestClient

from diony_contracts.launch import AdmissionPolicy, InvitationStatus, MembershipRole
from diony_producer.app import app
from diony_producer.control_plane import ControlPlane
from diony_producer.security import issue_token
from diony_producer.studio_invites import StudioInvitationService


def _configure(tmp_path: Path, monkeypatch) -> Path:
    vault = tmp_path / "vault"
    monkeypatch.setenv("DIONY_VAULT", str(vault))
    monkeypatch.setenv("DIONY_PRODUCER_VAULT", str(vault))
    monkeypatch.setenv("DIONY_AUTH_MODE", "token")
    monkeypatch.setenv("DIONY_AUTH_SECRET", "studio-auth-secret-that-is-long-enough")
    monkeypatch.setenv("DIONY_INVITE_SECRET", "studio-invite-secret-that-is-long-enough")
    monkeypatch.setenv(
        "DIONY_KEY_ENCRYPTION_KEY",
        base64.urlsafe_b64encode(b"k" * 32).decode(),
    )
    monkeypatch.setenv("DIONY_EMAIL_TRANSPORT", "capture")
    monkeypatch.setenv("DIONY_EMAIL_FROM", "Northlight via Diony AI <invites@northlight.test>")
    monkeypatch.setenv("DIONY_EMAIL_REPLY_TO", "studio@northlight.test")
    monkeypatch.setenv("DIONY_PRODUCER_PUBLIC_URL", "https://producer.northlight.test")
    return vault


def test_email_invitation_acceptance_creates_one_membership_and_cost_center(
    tmp_path: Path, monkeypatch
) -> None:
    vault = _configure(tmp_path, monkeypatch)
    plane = ControlPlane()
    context = plane.bootstrap_local()
    service = StudioInvitationService(plane)

    created = service.create_invitation(
        workspace_id=context.workspace.id,
        actor_user_id=context.user.id,
        email="maya@example.com",
        role=MembershipRole.CREATOR,
        proposed_profile_name="Maya Chen",
        note="Bring the midnight cut.",
        expires_hours=72,
        correlation_id="corr-invite",
    )

    assert created.invitation.status == InvitationStatus.ACTIVE
    assert created.invitation.token_hash not in created.accept_url
    assert created.token not in created.invitation.model_dump_json()
    assert created.delivery.status.value == "sent"
    captured = list((vault / ".control" / "mail-capture").glob("*.eml"))
    assert len(captured) == 1
    body = captured[0].read_text()
    assert "YOU'VE BEEN" in body
    assert "WHAT DIONY AI IS" in body
    assert "personal Cost Center" in body
    assert "Review and accept invitation" in body

    passport = service.discover(created.token)
    assert passport["studio_name"] == "Local Studio"
    assert passport["recipient_mask"] == "m•••@example.com"
    verification = service.request_email_verification(created.token)
    assert verification.delivery.status.value == "sent"
    code_mail = sorted((vault / ".control" / "mail-capture").glob("*.eml"))[-1]
    code = service.extract_capture_code(code_mail)
    verified = service.confirm_email_verification(created.token, code)

    joined = service.accept_invitation(
        token=created.token,
        verification_ticket=verified.verification_ticket,
        profile_name="Maya Chen",
        correlation_id="corr-accept",
    )
    again = service.accept_invitation(
        token=created.token,
        verification_ticket=verified.verification_ticket,
        profile_name="Maya Chen",
        correlation_id="corr-accept-retry",
    )
    assert joined.status == "accepted"
    assert joined.membership.id == again.membership.id
    assert joined.cost_center.id == again.cost_center.id
    assert joined.membership.profile_name == "Maya Chen"
    assert joined.membership.personal_cost_center_id == joined.cost_center.id
    members = plane.list_documents("membership", context.workspace.id)
    centers = plane.list_documents("cost_center", context.workspace.id)
    assert sum(row["user_id"] == joined.membership.user_id for row in members) == 1
    assert sum(row["id"] == joined.cost_center.id for row in centers) == 1
    all_mail = list((vault / ".control" / "mail-capture").glob("*.eml"))
    assert len(all_mail) == 3
    assert any("You're now part of Local Studio" in path.read_text() for path in all_mail)


def test_admin_email_invite_api_and_public_accept_flow(tmp_path: Path, monkeypatch) -> None:
    vault = _configure(tmp_path, monkeypatch)
    plane = ControlPlane()
    context = plane.bootstrap_local()
    owner_token = issue_token(context.user.id, context.workspace.id, MembershipRole.OWNER)
    client = TestClient(app)
    headers = {"Authorization": f"Bearer {owner_token}"}

    invitation = client.post(
        "/api/v1/studio/invitations",
        headers=headers,
        json={
            "email": "maya@example.com",
            "role": "creator",
            "proposed_profile_name": "Maya Chen",
            "note": "Bring the midnight cut.",
            "expires_hours": 72,
        },
    )
    assert invitation.status_code == 201, invitation.text
    invite_body = invitation.json()
    assert invite_body["delivery"]["status"] == "sent"
    assert invite_body["accept_url"].startswith("https://producer.northlight.test/#invite=")

    discovered = client.post("/api/studio-entry/discover", json={"token": invite_body["token"]})
    assert discovered.status_code == 200, discovered.text
    assert discovered.json()["studio_name"] == "Local Studio"

    requested = client.post(
        "/api/studio-entry/verification/request",
        json={"token": invite_body["token"]},
    )
    assert requested.status_code == 202, requested.text
    code_mail = sorted((vault / ".control" / "mail-capture").glob("*.eml"))[-1]
    code = StudioInvitationService(plane).extract_capture_code(code_mail)
    confirmed = client.post(
        "/api/studio-entry/verification/confirm",
        json={"token": invite_body["token"], "code": code},
    )
    assert confirmed.status_code == 200, confirmed.text
    accepted = client.post(
        "/api/studio-entry/join",
        json={
            "token": invite_body["token"],
            "verification_ticket": confirmed.json()["verification_ticket"],
            "profile_name": "Maya Chen",
        },
    )
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["status"] == "accepted"
    assert accepted.json()["session_token"].startswith("diony1.")
    studio_session = client.get(
        "/api/v1/session",
        headers={"Authorization": f"Bearer {accepted.json()['session_token']}"},
    )
    assert studio_session.status_code == 200
    assert studio_session.json()["user_id"] == accepted.json()["membership"]["user_id"]
    workflow = client.post(
        "/api/v1/workflows",
        headers={"Authorization": f"Bearer {accepted.json()['session_token']}"},
        json={"project_id": "joined-maker-project", "stages": ["intake"]},
    )
    assert workflow.status_code == 201, workflow.text
    assert workflow.json()["config"]["cost_center_id"] == accepted.json()["cost_center"]["id"]

    member = accepted.json()["membership"]
    shared = client.post(
        "/api/v1/cost-centers",
        headers=headers,
        json={
            "name": "Midnight Picture",
            "code": "MIDNIGHT-PIC",
            "department_id": "local-department",
            "team_ids": member["team_ids"],
            "internal_credit_budget": 500,
            "cost_budget_usd_micros": 2_000_000,
        },
    )
    assert shared.status_code == 201, shared.text
    assigned = client.put(
        f"/api/v1/studio/members/{member['id']}/cost-centers",
        headers=headers,
        json={"cost_center_ids": [member["personal_cost_center_id"], shared.json()["id"]]},
    )
    assert assigned.status_code == 200, assigned.text
    assert shared.json()["id"] in assigned.json()["assigned_cost_center_ids"]
    denied_assignment = client.put(
        f"/api/v1/studio/members/{member['id']}/cost-centers",
        headers={"Authorization": f"Bearer {accepted.json()['session_token']}"},
        json={"cost_center_ids": [member["personal_cost_center_id"]]},
    )
    assert denied_assignment.status_code == 403
    renamed = client.patch(
        f"/api/v1/cost-centers/{shared.json()['id']}",
        headers=headers,
        json={"name": "Midnight Camera Unit"},
    )
    assert renamed.status_code == 200, renamed.text
    assert renamed.json()["name"] == "Midnight Camera Unit"
    shared_workflow = client.post(
        "/api/v1/workflows",
        headers={"Authorization": f"Bearer {accepted.json()['session_token']}"},
        json={
            "project_id": "joined-maker-shared-project",
            "stages": ["intake"],
            "config": {"cost_center_id": shared.json()["id"]},
        },
    )
    assert shared_workflow.status_code == 201, shared_workflow.text
    assert shared_workflow.json()["config"]["cost_center_id"] == shared.json()["id"]


def test_revoked_invitation_cannot_be_discovered(tmp_path: Path, monkeypatch) -> None:
    _configure(tmp_path, monkeypatch)
    plane = ControlPlane()
    context = plane.bootstrap_local()
    service = StudioInvitationService(plane)
    created = service.create_invitation(
        workspace_id=context.workspace.id,
        actor_user_id=context.user.id,
        email="maya@example.com",
        role=MembershipRole.CREATOR,
        proposed_profile_name="Maya",
        note="",
        expires_hours=72,
        correlation_id="corr-revoke",
    )
    service.revoke_invitation(
        workspace_id=context.workspace.id,
        invitation_id=created.invitation.id,
        actor_user_id=context.user.id,
        correlation_id="corr-revoked",
    )
    try:
        service.discover(created.token)
    except ValueError as exc:
        assert "closed the invitation" in str(exc)
    else:
        raise AssertionError("revoked invitation remained discoverable")


def test_approval_required_join_creates_no_cost_center_until_admin_approves(
    tmp_path: Path, monkeypatch
) -> None:
    _configure(tmp_path, monkeypatch)
    plane = ControlPlane()
    context = plane.bootstrap_local()
    workspace = context.workspace.model_copy(
        update={"admission_policy": AdmissionPolicy.ADMIN_APPROVAL}
    )
    plane.put_document("workspace", workspace.id, workspace.id, workspace.model_dump(mode="json"))
    service = StudioInvitationService(plane)
    created = service.create_invitation(
        workspace_id=workspace.id,
        actor_user_id=context.user.id,
        email="pending@example.com",
        role=MembershipRole.CREATOR,
        proposed_profile_name="Pending Maker",
        note="",
        expires_hours=72,
        correlation_id="corr-pending",
    )
    service.request_email_verification(created.token)
    capture = sorted((tmp_path / "vault" / ".control" / "mail-capture").glob("*.eml"))[-1]
    code = service.extract_capture_code(capture)
    verified = service.confirm_email_verification(created.token, code)
    pending = service.accept_invitation(
        token=created.token,
        verification_ticket=verified.verification_ticket,
        profile_name="Pending Maker",
        correlation_id="corr-request",
    )
    assert pending.status == "pending"
    assert pending.request is not None
    assert pending.membership is None
    pending_mail = list((tmp_path / "vault" / ".control" / "mail-capture").glob("*.eml"))
    assert any("YOUR REQUEST IS WITH THE STUDIO" in path.read_text() for path in pending_mail)
    assert not any(
        row.get("user_id") == pending.request.user_id
        for row in plane.list_documents("membership", workspace.id)
    )

    approved = service.approve_request(
        workspace_id=workspace.id,
        request_id=pending.request.id,
        actor_user_id=context.user.id,
        correlation_id="corr-approved",
    )
    assert approved.status == "accepted"
    assert approved.membership is not None
    assert approved.cost_center is not None
    request_row = plane.get_document("studio_join_request", pending.request.id, workspace.id)
    assert request_row["status"] == "approved"


def test_removing_member_revokes_access_and_closes_personal_cost_center(
    tmp_path: Path, monkeypatch
) -> None:
    _configure(tmp_path, monkeypatch)
    plane = ControlPlane()
    context = plane.bootstrap_local()
    service = StudioInvitationService(plane)
    created = service.create_invitation(
        workspace_id=context.workspace.id,
        actor_user_id=context.user.id,
        email="remove@example.com",
        role=MembershipRole.CREATOR,
        proposed_profile_name="Remove Maker",
        note="",
        expires_hours=72,
        correlation_id="corr-remove-invite",
    )
    service.request_email_verification(created.token)
    capture = sorted((tmp_path / "vault" / ".control" / "mail-capture").glob("*.eml"))[-1]
    verified = service.confirm_email_verification(
        created.token, service.extract_capture_code(capture)
    )
    joined = service.accept_invitation(
        token=created.token,
        verification_ticket=verified.verification_ticket,
        profile_name="Remove Maker",
        correlation_id="corr-joined",
    )
    removed = service.remove_member(
        workspace_id=context.workspace.id,
        membership_id=joined.membership.id,
        actor_user_id=context.user.id,
        confirmation="REMOVE Remove Maker",
        correlation_id="corr-removed",
    )
    assert removed.status.value == "removed"
    center = plane.get_document("cost_center", joined.cost_center.id, context.workspace.id)
    assert center["active"] is False
    removal_mail = list((tmp_path / "vault" / ".control" / "mail-capture").glob("*.eml"))
    assert any("YOUR STUDIO ACCESS HAS CLOSED" in path.read_text() for path in removal_mail)
    try:
        plane.authorize(
            joined.membership.user_id,
            context.workspace.id,
            MembershipRole.VIEWER,
        )
    except Exception as exc:
        assert "required" in str(exc)
    else:
        raise AssertionError("removed member retained Studio access")
