"""Durable Studio invitation, email verification, and admission service."""

from __future__ import annotations

import hashlib
import hmac
import os
import re
import secrets
import unicodedata
import uuid
from datetime import UTC, datetime, timedelta
from email import policy
from email.parser import BytesParser
from pathlib import Path

from pydantic import BaseModel

from diony_contracts.launch import (
    AdmissionPolicy,
    CostCenter,
    Department,
    InvitationEmailDelivery,
    InvitationStatus,
    JoinRequestStatus,
    Membership,
    MembershipRole,
    MembershipStatus,
    StudioInvitation,
    StudioJoinRequest,
    Team,
    User,
    Workspace,
    utc_now,
)
from diony_producer.control_plane import ControlPlane, DocumentNotFound
from diony_producer.security import issue_token
from diony_producer.studio_email import (
    StudioMailer,
    recipient_mask,
    render_access_ready_email,
    render_invitation_email,
    render_membership_status_email,
    render_verification_email,
)


class InvitationCreated(BaseModel):
    invitation: StudioInvitation
    delivery: InvitationEmailDelivery
    token: str
    accept_url: str


class VerificationRequested(BaseModel):
    delivery: InvitationEmailDelivery
    recipient_mask: str
    expires_in_seconds: int = 600


class VerificationConfirmed(BaseModel):
    verification_ticket: str
    expires_in_seconds: int = 900


class JoinOutcome(BaseModel):
    status: str
    studio_name: str
    membership: Membership | None = None
    cost_center: CostCenter | None = None
    request: StudioJoinRequest | None = None
    session_token: str = ""


class EmailVerification(BaseModel):
    id: str
    workspace_id: str
    invitation_id: str
    code_hash: str
    attempts_remaining: int = 5
    expires_at: datetime
    ticket_hash: str = ""
    ticket_expires_at: datetime | None = None
    verified_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


def _email_key(value: str) -> str:
    cleaned = value.strip()
    if len(cleaned) > 320 or cleaned.count("@") != 1:
        raise ValueError("Enter a complete email address.")
    local, domain = cleaned.rsplit("@", 1)
    if not local or not domain or "." not in domain or any(ch.isspace() for ch in cleaned):
        raise ValueError("Enter a complete email address.")
    return f"{local}@{domain.lower()}".casefold()


def _profile_name(value: str) -> tuple[str, str]:
    clean = unicodedata.normalize("NFKC", value)
    clean = re.sub(r"\s+", " ", clean).strip()
    if not 2 <= len(clean) <= 60:
        raise ValueError("Profile Name must be between 2 and 60 characters.")
    if any(unicodedata.category(ch) in {"Cc", "Cf"} for ch in clean):
        raise ValueError("Profile Name cannot contain control characters.")
    if not any(ch.isalpha() or ch.isdigit() for ch in clean):
        raise ValueError("Profile Name needs at least one letter or number.")
    if re.search(r"https?://|www\.", clean, flags=re.IGNORECASE):
        raise ValueError("Profile Name cannot be a web address.")
    key = clean.casefold()
    reserved = {"diony", "diony ai", "system", "producer", "admin"}
    if key in reserved:
        raise ValueError("That Profile Name is reserved by the Studio.")
    return clean, key


class StudioInvitationService:
    def __init__(self, plane: ControlPlane | None = None) -> None:
        self.plane = plane or ControlPlane()
        self.mailer = StudioMailer(self.plane)

    @staticmethod
    def _digest(value: str) -> str:
        signing = (
            os.environ.get("DIONY_INVITE_SECRET", "") or os.environ.get("DIONY_AUTH_SECRET", "")
        ).encode()
        if len(signing) < 24:
            raise RuntimeError("DIONY_INVITE_SECRET or DIONY_AUTH_SECRET must be at least 24 bytes")
        return hmac.new(signing, value.encode(), hashlib.sha256).hexdigest()

    def _workspace(self, workspace_id: str) -> Workspace:
        return Workspace.model_validate(
            self.plane.get_document("workspace", workspace_id, workspace_id)
        )

    def _inviter_name(self, workspace_id: str, user_id: str) -> str:
        try:
            membership = Membership.model_validate(
                self.plane.get_document("membership", f"{workspace_id}:{user_id}", workspace_id)
            )
            if membership.profile_name:
                return membership.profile_name
        except DocumentNotFound:
            pass
        try:
            user = User.model_validate(self.plane.get_document("user", user_id, workspace_id))
            return user.display_name
        except DocumentNotFound:
            return "A Studio Admin"

    @staticmethod
    def _site_label() -> str:
        return "Diony Studio (cloud production on Grok Imagine and Google Flow)"

    def create_invitation(
        self,
        *,
        workspace_id: str,
        actor_user_id: str,
        email: str,
        role: MembershipRole,
        proposed_profile_name: str,
        note: str,
        expires_hours: int,
        correlation_id: str,
    ) -> InvitationCreated:
        workspace = self._workspace(workspace_id)
        if workspace.admission_policy == AdmissionPolicy.CLOSED:
            raise ValueError(f"{workspace.name} is not accepting joins right now.")
        normalized_email = _email_key(email)
        if role in {MembershipRole.OWNER, MembershipRole.ADMIN}:
            raise ValueError("Owner and Admin access require the separate authority flow.")
        proposed = ""
        if proposed_profile_name.strip():
            proposed, _ = _profile_name(proposed_profile_name)
        hours = max(1, min(int(expires_hours), 24 * 30))
        now = utc_now()
        raw = secrets.token_urlsafe(32)
        invitation = StudioInvitation(
            id=f"invite-{uuid.uuid4().hex}",
            workspace_id=workspace_id,
            token_hash=self._digest(raw),
            email=email.strip(),
            email_key=normalized_email,
            role=role,
            proposed_profile_name=proposed,
            note=note.strip()[:500],
            admission_policy=workspace.admission_policy,
            created_by=actor_user_id,
            expires_at=now + timedelta(hours=hours),
        )
        inviter = self._inviter_name(workspace_id, actor_user_id)
        subject = f"{inviter} invited you to {workspace.name} in Diony AI"
        delivery = InvitationEmailDelivery(
            id=f"mail-{uuid.uuid4().hex}",
            workspace_id=workspace_id,
            invitation_id=invitation.id,
            recipient_mask=recipient_mask(invitation.email),
            subject=subject,
        )
        self.plane.put_documents_atomic(
            [
                (
                    "studio_invitation",
                    invitation.id,
                    workspace_id,
                    invitation.model_dump(mode="json"),
                ),
                (
                    "invitation_email_delivery",
                    delivery.id,
                    workspace_id,
                    delivery.model_dump(mode="json"),
                ),
            ]
        )
        self.plane.audit(
            workspace_id=workspace_id,
            actor_user_id=actor_user_id,
            action="studio.invitation.created",
            object_type="studio_invitation",
            object_id=invitation.id,
            correlation_id=correlation_id,
            after=invitation,
            metadata={"recipient": recipient_mask(invitation.email), "role": role.value},
        )
        base = (
            workspace.controller_origin
            or os.environ.get("DIONY_PRODUCER_PUBLIC_URL", "http://producer.diony.local")
        ).rstrip("/")
        accept_url = f"{base}/#invite={raw}"
        rendered = render_invitation_email(
            workspace=workspace,
            invitation=invitation,
            inviter_profile_name=inviter,
            accept_url=accept_url,
            site_label=self._site_label(),
        )
        delivery = self.mailer.deliver(delivery, rendered)
        return InvitationCreated(
            invitation=invitation,
            delivery=delivery,
            token=raw,
            accept_url=accept_url,
        )

    def _invitation(self, token: str, *, allow_used: bool = False) -> StudioInvitation:
        if len(token.strip()) < 24:
            raise ValueError("This is not a Diony Studio invitation.")
        body = self.plane.find_document_by_field(
            "studio_invitation", "token_hash", self._digest(token.strip())
        )
        if not body:
            raise ValueError("This is not a Diony Studio invitation.")
        invitation = StudioInvitation.model_validate(body)
        if invitation.status == InvitationStatus.REVOKED:
            raise ValueError("This Studio closed the invitation.")
        if invitation.expires_at <= datetime.now(UTC):
            if invitation.status == InvitationStatus.ACTIVE:
                invitation.status = InvitationStatus.EXPIRED
                invitation.updated_at = utc_now()
                self.plane.put_document(
                    "studio_invitation",
                    invitation.id,
                    invitation.workspace_id,
                    invitation.model_dump(mode="json"),
                )
            raise ValueError(
                f"This invitation expired on {invitation.expires_at.strftime('%B %d, %Y')}."
            )
        if invitation.status == InvitationStatus.USED and allow_used:
            return invitation
        if invitation.status not in {
            InvitationStatus.ACTIVE,
            InvitationStatus.PARTIALLY_USED,
        }:
            raise ValueError("This one-use invitation has already brought in a maker.")
        return invitation

    def discover(self, token: str) -> dict[str, str | bool]:
        invitation = self._invitation(token)
        workspace = self._workspace(invitation.workspace_id)
        return {
            "studio_id": workspace.id,
            "studio_name": workspace.name,
            "role": invitation.role.value,
            "site_label": self._site_label(),
            "controller_origin": workspace.controller_origin,
            "controller_fingerprint": workspace.controller_fingerprint,
            "recipient_mask": recipient_mask(invitation.email),
            "proposed_profile_name": invitation.proposed_profile_name,
            "approval_required": invitation.admission_policy == AdmissionPolicy.ADMIN_APPROVAL,
            "expires_at": invitation.expires_at.isoformat(),
        }

    def request_email_verification(self, token: str) -> VerificationRequested:
        invitation = self._invitation(token)
        workspace = self._workspace(invitation.workspace_id)
        code = f"{secrets.randbelow(1_000_000):06d}"
        now = utc_now()
        verification = EmailVerification(
            id=f"verify-{invitation.id}",
            workspace_id=invitation.workspace_id,
            invitation_id=invitation.id,
            code_hash=self._digest(f"{invitation.id}:{code}"),
            expires_at=now + timedelta(minutes=10),
            created_at=now,
            updated_at=now,
        )
        delivery = InvitationEmailDelivery(
            id=f"mail-{uuid.uuid4().hex}",
            workspace_id=invitation.workspace_id,
            invitation_id=invitation.id,
            kind="email_verification",
            recipient_mask=recipient_mask(invitation.email),
            subject=f"Your {workspace.name} verification code",
        )
        self.plane.put_documents_atomic(
            [
                (
                    "studio_email_verification",
                    verification.id,
                    invitation.workspace_id,
                    verification.model_dump(mode="json"),
                ),
                (
                    "invitation_email_delivery",
                    delivery.id,
                    invitation.workspace_id,
                    delivery.model_dump(mode="json"),
                ),
            ]
        )
        delivery = self.mailer.deliver(
            delivery,
            render_verification_email(workspace=workspace, invitation=invitation, code=code),
        )
        return VerificationRequested(
            delivery=delivery, recipient_mask=recipient_mask(invitation.email)
        )

    def confirm_email_verification(self, token: str, code: str) -> VerificationConfirmed:
        invitation = self._invitation(token)
        verification = EmailVerification.model_validate(
            self.plane.get_document(
                "studio_email_verification",
                f"verify-{invitation.id}",
                invitation.workspace_id,
            )
        )
        now = utc_now()
        if verification.expires_at <= now:
            raise ValueError("That verification code expired. Send a new code.")
        if verification.attempts_remaining <= 0:
            raise ValueError("Too many code attempts. Send a new code.")
        expected = self._digest(f"{invitation.id}:{code.strip()}")
        if not hmac.compare_digest(expected, verification.code_hash):
            verification.attempts_remaining -= 1
            verification.updated_at = now
            self.plane.put_document(
                "studio_email_verification",
                verification.id,
                invitation.workspace_id,
                verification.model_dump(mode="json"),
            )
            raise ValueError("That verification code does not match.")
        ticket = secrets.token_urlsafe(32)
        verification.ticket_hash = self._digest(ticket)
        verification.ticket_expires_at = now + timedelta(minutes=15)
        verification.verified_at = now
        verification.updated_at = now
        self.plane.put_document(
            "studio_email_verification",
            verification.id,
            invitation.workspace_id,
            verification.model_dump(mode="json"),
        )
        return VerificationConfirmed(verification_ticket=ticket)

    def _verified_invitation(
        self, token: str, verification_ticket: str
    ) -> tuple[StudioInvitation, EmailVerification]:
        invitation = self._invitation(token, allow_used=True)
        verification = EmailVerification.model_validate(
            self.plane.get_document(
                "studio_email_verification",
                f"verify-{invitation.id}",
                invitation.workspace_id,
            )
        )
        if (
            not verification.verified_at
            or not verification.ticket_expires_at
            or verification.ticket_expires_at <= utc_now()
            or not hmac.compare_digest(verification.ticket_hash, self._digest(verification_ticket))
        ):
            raise ValueError("Verify the invitation email before joining.")
        return invitation, verification

    def accept_invitation(
        self,
        *,
        token: str,
        verification_ticket: str,
        profile_name: str,
        correlation_id: str,
    ) -> JoinOutcome:
        invitation, _ = self._verified_invitation(token, verification_ticket)
        workspace = self._workspace(invitation.workspace_id)
        if invitation.accepted_membership_id:
            membership = Membership.model_validate(
                self.plane.get_document(
                    "membership",
                    invitation.accepted_membership_id,
                    invitation.workspace_id,
                )
            )
            center = CostCenter.model_validate(
                self.plane.get_document(
                    "cost_center",
                    membership.personal_cost_center_id,
                    invitation.workspace_id,
                )
            )
            return JoinOutcome(
                status="accepted",
                studio_name=workspace.name,
                membership=membership,
                cost_center=center,
                session_token=issue_token(membership.user_id, workspace.id, membership.role),
            )

        clean_name, profile_key = _profile_name(profile_name)
        for row in self.plane.list_documents("membership", workspace.id):
            existing = Membership.model_validate(row)
            if (
                existing.status == MembershipStatus.ACTIVE
                and existing.profile_name_key == profile_key
            ):
                raise ValueError("That Profile Name is already in use here.")
        user_id = f"user-{hashlib.sha256(invitation.email_key.encode()).hexdigest()[:20]}"

        if invitation.admission_policy == AdmissionPolicy.ADMIN_APPROVAL:
            request_id = invitation.join_request_id or f"join-{uuid.uuid4().hex}"
            request = StudioJoinRequest(
                id=request_id,
                workspace_id=workspace.id,
                invitation_id=invitation.id,
                user_id=user_id,
                email_key=invitation.email_key,
                requested_profile_name=clean_name,
                requested_profile_name_key=profile_key,
                requested_role=invitation.role,
            )
            invitation.join_request_id = request.id
            invitation.status = InvitationStatus.USED
            invitation.updated_at = utc_now()
            user = User(id=user_id, email=invitation.email, display_name=clean_name)
            self.plane.put_documents_atomic(
                [
                    ("user", user.id, workspace.id, user.model_dump(mode="json")),
                    (
                        "studio_join_request",
                        request.id,
                        workspace.id,
                        request.model_dump(mode="json"),
                    ),
                    (
                        "studio_invitation",
                        invitation.id,
                        workspace.id,
                        invitation.model_dump(mode="json"),
                    ),
                ]
            )
            self._send_membership_status(
                workspace=workspace,
                invitation_id=invitation.id,
                to=invitation.email,
                profile_name=clean_name,
                status_kind="approval_pending",
            )
            return JoinOutcome(status="pending", studio_name=workspace.name, request=request)

        return self._admit(
            invitation=invitation,
            workspace=workspace,
            user_id=user_id,
            profile_name=clean_name,
            profile_key=profile_key,
            actor_user_id=user_id,
            correlation_id=correlation_id,
        )

    def _admit(
        self,
        *,
        invitation: StudioInvitation,
        workspace: Workspace,
        user_id: str,
        profile_name: str,
        profile_key: str,
        actor_user_id: str,
        correlation_id: str,
    ) -> JoinOutcome:
        membership_id = f"{workspace.id}:{user_id}"
        team_id = f"personal-{hashlib.sha256(membership_id.encode()).hexdigest()[:12]}"
        center_id = f"cc-{hashlib.sha256(membership_id.encode()).hexdigest()[:16]}"
        departments = self.plane.list_documents("department", workspace.id)
        if not departments:
            raise ValueError("The Studio membership Cost Center is not configured.")
        department = Department.model_validate(departments[0])
        if team_id not in department.team_ids:
            department.team_ids.append(team_id)
        team = Team(id=team_id, workspace_id=workspace.id, name=f"{profile_name} · Personal")
        membership = Membership(
            id=membership_id,
            user_id=user_id,
            workspace_id=workspace.id,
            team_ids=[team.id],
            role=invitation.role,
            profile_name=profile_name,
            profile_name_key=profile_key,
            personal_cost_center_id=center_id,
            assigned_cost_center_ids=[center_id],
        )
        center = CostCenter(
            id=center_id,
            workspace_id=workspace.id,
            department_id=department.id,
            name=f"{profile_name} · Personal",
            code=f"PERSONAL-{hashlib.sha256(membership_id.encode()).hexdigest()[:8].upper()}",
            team_ids=[team.id],
            owner_membership_id=membership.id,
        )
        user = User(id=user_id, email=invitation.email, display_name=profile_name)
        invitation.status = InvitationStatus.USED
        invitation.use_count = 1
        invitation.accepted_user_id = user.id
        invitation.accepted_membership_id = membership.id
        invitation.updated_at = utc_now()
        self.plane.put_documents_atomic(
            [
                ("user", user.id, workspace.id, user.model_dump(mode="json")),
                ("team", team.id, workspace.id, team.model_dump(mode="json")),
                (
                    "department",
                    department.id,
                    workspace.id,
                    department.model_dump(mode="json"),
                ),
                (
                    "membership",
                    membership.id,
                    workspace.id,
                    membership.model_dump(mode="json"),
                ),
                (
                    "cost_center",
                    center.id,
                    workspace.id,
                    center.model_dump(mode="json"),
                ),
                (
                    "studio_invitation",
                    invitation.id,
                    workspace.id,
                    invitation.model_dump(mode="json"),
                ),
            ]
        )
        self.plane.audit(
            workspace_id=workspace.id,
            actor_user_id=actor_user_id,
            action="membership.joined",
            object_type="membership",
            object_id=membership.id,
            correlation_id=correlation_id,
            after=membership,
            metadata={"cost_center_id": center.id, "profile_name": profile_name},
        )
        self._send_access_receipt(workspace, invitation, membership, center)
        return JoinOutcome(
            status="accepted",
            studio_name=workspace.name,
            membership=membership,
            cost_center=center,
            session_token=issue_token(user.id, workspace.id, membership.role),
        )

    def _send_access_receipt(
        self,
        workspace: Workspace,
        invitation: StudioInvitation,
        membership: Membership,
        center: CostCenter,
    ) -> InvitationEmailDelivery:
        delivery = InvitationEmailDelivery(
            id=f"mail-{uuid.uuid4().hex}",
            workspace_id=workspace.id,
            invitation_id=invitation.id,
            kind="access_ready",
            recipient_mask=recipient_mask(invitation.email),
            subject=f"You're now part of {workspace.name}",
        )
        self.plane.put_document(
            "invitation_email_delivery",
            delivery.id,
            workspace.id,
            delivery.model_dump(mode="json"),
        )
        producer_url = (
            workspace.controller_origin
            or os.environ.get("DIONY_PRODUCER_PUBLIC_URL", "http://producer.diony.local")
        ).rstrip("/")
        return self.mailer.deliver(
            delivery,
            render_access_ready_email(
                workspace=workspace,
                invitation=invitation,
                profile_name=membership.profile_name,
                cost_center_code=center.code,
                producer_url=producer_url,
            ),
        )

    def _send_membership_status(
        self,
        *,
        workspace: Workspace,
        invitation_id: str,
        to: str,
        profile_name: str,
        status_kind: str,
        note: str = "",
    ) -> InvitationEmailDelivery | None:
        if not to.strip():
            return None
        producer_url = (
            workspace.controller_origin
            or os.environ.get("DIONY_PRODUCER_PUBLIC_URL", "http://producer.diony.local")
        ).rstrip("/")
        rendered = render_membership_status_email(
            to=to,
            workspace=workspace,
            profile_name=profile_name,
            status_kind=status_kind,
            producer_url=producer_url,
            note=note,
        )
        delivery = InvitationEmailDelivery(
            id=f"mail-{uuid.uuid4().hex}",
            workspace_id=workspace.id,
            invitation_id=invitation_id,
            kind=f"membership_{status_kind}",
            recipient_mask=recipient_mask(to),
            subject=rendered.subject,
        )
        self.plane.put_document(
            "invitation_email_delivery",
            delivery.id,
            workspace.id,
            delivery.model_dump(mode="json"),
        )
        return self.mailer.deliver(delivery, rendered)

    def approve_request(
        self,
        *,
        workspace_id: str,
        request_id: str,
        actor_user_id: str,
        correlation_id: str,
    ) -> JoinOutcome:
        request = StudioJoinRequest.model_validate(
            self.plane.get_document("studio_join_request", request_id, workspace_id)
        )
        invitation = StudioInvitation.model_validate(
            self.plane.get_document("studio_invitation", request.invitation_id, workspace_id)
        )
        workspace = self._workspace(workspace_id)
        if request.status == JoinRequestStatus.APPROVED and invitation.accepted_membership_id:
            membership = Membership.model_validate(
                self.plane.get_document(
                    "membership", invitation.accepted_membership_id, workspace_id
                )
            )
            center = CostCenter.model_validate(
                self.plane.get_document(
                    "cost_center", membership.personal_cost_center_id, workspace_id
                )
            )
            return JoinOutcome(
                status="accepted",
                studio_name=workspace.name,
                membership=membership,
                cost_center=center,
            )
        if request.status != JoinRequestStatus.PENDING:
            raise ValueError("This join request is no longer pending.")
        for row in self.plane.list_documents("membership", workspace_id):
            existing = Membership.model_validate(row)
            if (
                existing.status == MembershipStatus.ACTIVE
                and existing.profile_name_key == request.requested_profile_name_key
            ):
                raise ValueError("That Profile Name is already in use here.")
        outcome = self._admit(
            invitation=invitation,
            workspace=workspace,
            user_id=request.user_id,
            profile_name=request.requested_profile_name,
            profile_key=request.requested_profile_name_key,
            actor_user_id=actor_user_id,
            correlation_id=correlation_id,
        )
        request.status = JoinRequestStatus.APPROVED
        request.decided_by = actor_user_id
        request.decided_at = utc_now()
        request.updated_at = utc_now()
        self.plane.put_document(
            "studio_join_request",
            request.id,
            workspace_id,
            request.model_dump(mode="json"),
        )
        return outcome.model_copy(update={"session_token": ""})

    def deny_request(
        self,
        *,
        workspace_id: str,
        request_id: str,
        actor_user_id: str,
        note: str,
        correlation_id: str,
    ) -> StudioJoinRequest:
        request = StudioJoinRequest.model_validate(
            self.plane.get_document("studio_join_request", request_id, workspace_id)
        )
        if request.status != JoinRequestStatus.PENDING:
            raise ValueError("This join request is no longer pending.")
        request.status = JoinRequestStatus.DENIED
        request.admin_note = note.strip()[:500]
        request.decided_by = actor_user_id
        request.decided_at = utc_now()
        request.updated_at = utc_now()
        self.plane.put_document(
            "studio_join_request",
            request.id,
            workspace_id,
            request.model_dump(mode="json"),
        )
        self.plane.audit(
            workspace_id=workspace_id,
            actor_user_id=actor_user_id,
            action="studio.join_request.denied",
            object_type="studio_join_request",
            object_id=request.id,
            correlation_id=correlation_id,
            after=request,
        )
        invitation = StudioInvitation.model_validate(
            self.plane.get_document("studio_invitation", request.invitation_id, workspace_id)
        )
        self._send_membership_status(
            workspace=self._workspace(workspace_id),
            invitation_id=invitation.id,
            to=invitation.email,
            profile_name=request.requested_profile_name,
            status_kind="denied",
            note=request.admin_note,
        )
        return request

    def remove_member(
        self,
        *,
        workspace_id: str,
        membership_id: str,
        actor_user_id: str,
        confirmation: str,
        correlation_id: str,
    ) -> Membership:
        membership = Membership.model_validate(
            self.plane.get_document("membership", membership_id, workspace_id)
        )
        if membership.role == MembershipRole.OWNER:
            raise ValueError("Transfer Studio ownership before removing this member.")
        expected = f"REMOVE {membership.profile_name}"
        if confirmation != expected:
            raise ValueError(f"Type {expected} to remove this member.")
        center = CostCenter.model_validate(
            self.plane.get_document("cost_center", membership.personal_cost_center_id, workspace_id)
        )
        membership.status = MembershipStatus.REMOVED
        membership.removed_at = utc_now()
        center.active = False
        center.updated_at = utc_now()
        self.plane.put_documents_atomic(
            [
                (
                    "membership",
                    membership.id,
                    workspace_id,
                    membership.model_dump(mode="json"),
                ),
                (
                    "cost_center",
                    center.id,
                    workspace_id,
                    center.model_dump(mode="json"),
                ),
            ]
        )
        self.plane.audit(
            workspace_id=workspace_id,
            actor_user_id=actor_user_id,
            action="membership.removed",
            object_type="membership",
            object_id=membership.id,
            correlation_id=correlation_id,
            after=membership,
            metadata={"personal_cost_center_id": center.id},
        )
        user = User.model_validate(
            self.plane.get_document("user", membership.user_id, workspace_id)
        )
        invitation_id = next(
            (
                str(row["id"])
                for row in self.plane.list_documents("studio_invitation", workspace_id)
                if row.get("accepted_membership_id") == membership.id
            ),
            f"membership:{membership.id}",
        )
        self._send_membership_status(
            workspace=self._workspace(workspace_id),
            invitation_id=invitation_id,
            to=user.email,
            profile_name=membership.profile_name,
            status_kind="removed",
        )
        return membership

    def revoke_invitation(
        self,
        *,
        workspace_id: str,
        invitation_id: str,
        actor_user_id: str,
        correlation_id: str,
    ) -> StudioInvitation:
        invitation = StudioInvitation.model_validate(
            self.plane.get_document("studio_invitation", invitation_id, workspace_id)
        )
        if invitation.status == InvitationStatus.USED:
            raise ValueError("An accepted invitation cannot be revoked.")
        invitation.status = InvitationStatus.REVOKED
        invitation.revoked_at = utc_now()
        invitation.updated_at = utc_now()
        self.plane.put_document(
            "studio_invitation",
            invitation.id,
            workspace_id,
            invitation.model_dump(mode="json"),
        )
        self.plane.audit(
            workspace_id=workspace_id,
            actor_user_id=actor_user_id,
            action="studio.invitation.revoked",
            object_type="studio_invitation",
            object_id=invitation.id,
            correlation_id=correlation_id,
            after=invitation,
        )
        return invitation

    def list_invitations(self, workspace_id: str) -> list[dict]:
        deliveries = {
            row["invitation_id"]: row
            for row in self.plane.list_documents("invitation_email_delivery", workspace_id)
            if row.get("kind") == "studio_invitation"
        }
        return [
            {**row, "delivery": deliveries.get(row["id"])}
            for row in self.plane.list_documents("studio_invitation", workspace_id)
        ]

    @staticmethod
    def extract_capture_code(path: Path) -> str:
        message = BytesParser(policy=policy.default).parsebytes(path.read_bytes())
        body = message.get_body(preferencelist=("plain",))
        text = body.get_content() if body else message.get_content()
        match = re.search(r"\b(\d{6})\b", text)
        if not match:
            raise ValueError("No verification code was found in captured email.")
        return match.group(1)
