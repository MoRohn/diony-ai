"""Versioned launch API for Diony's durable, creator-owned control plane."""

from __future__ import annotations

import asyncio
import json
import os
import time
import uuid
from collections import defaultdict, deque
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, status
from fastapi.responses import PlainTextResponse, StreamingResponse
from pydantic import BaseModel, Field

from diony_contracts.launch import (
    CostBreakdown,
    CostCenter,
    CostGovernance,
    CostRateCard,
    Department,
    Membership,
    MembershipRole,
    ProviderCapability,
    Team,
    User,
    WorkflowStage,
)
from diony_producer.control_plane import (
    ControlPlane,
    DocumentNotFound,
    WorkspaceAccessDenied,
)
from diony_producer.costs import CostAnalyticsService
from diony_producer.providers import ProviderRegistry
from diony_producer.security import Actor, SecretVault, verify_token
from diony_producer.studio_invites import StudioInvitationService
from diony_producer.workflow import DurableWorkflowEngine

router = APIRouter(prefix="/api/v1", tags=["launch-control-plane"])


class WorkflowCreate(BaseModel):
    project_id: str = Field(min_length=1, max_length=160)
    stages: list[WorkflowStage] | None = None
    config: dict[str, Any] = Field(default_factory=dict)


class SecretWrite(BaseModel):
    value: str = Field(min_length=1, max_length=16_384)


class CheckpointWrite(BaseModel):
    output_asset_ids: list[str] = Field(default_factory=list, max_length=10_000)
    actual_internal_credits: int | None = Field(default=None, ge=0)
    cost_breakdown: CostBreakdown | None = None
    quantity_microunits: int = Field(default=1_000_000, ge=0)
    unit: str = Field(default="operation", min_length=1, max_length=80)
    metadata: dict[str, Any] = Field(default_factory=dict)


class TeamCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)


class DepartmentCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    code: str = Field(min_length=1, max_length=40)
    manager_user_ids: list[str] = Field(default_factory=list, max_length=100)
    team_ids: list[str] = Field(default_factory=list, max_length=100)


class CostCenterCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    code: str = Field(min_length=1, max_length=40)
    department_id: str = Field(min_length=1, max_length=160)
    team_ids: list[str] = Field(default_factory=list, max_length=100)
    internal_credit_budget: int = Field(default=0, ge=0)
    cost_budget_usd_micros: int = Field(default=0, ge=0)
    warning_threshold_basis_points: int = Field(default=8000, ge=1, le=10000)


class CostCenterRename(BaseModel):
    name: str = Field(min_length=1, max_length=120)


class MemberCostCentersWrite(BaseModel):
    cost_center_ids: list[str] = Field(min_length=1, max_length=100)


class CostRateCardCreate(BaseModel):
    capability: ProviderCapability
    provider_id: str = Field(min_length=1, max_length=160)
    model: str = Field(min_length=1, max_length=240)
    unit: str = Field(min_length=1, max_length=80)
    internal_credits_per_unit: int = Field(ge=0)
    cost_usd_micros_per_unit: int = Field(ge=0)
    source: str = Field(min_length=1, max_length=240)


class CostGovernanceWrite(BaseModel):
    admin_user_id: str = Field(min_length=1, max_length=160)
    admin_team_id: str = Field(min_length=1, max_length=160)
    default_cost_center_id: str = Field(min_length=1, max_length=160)
    cost_model_version: str = Field(min_length=1, max_length=120)


class MemberWrite(BaseModel):
    display_name: str = Field(min_length=1, max_length=160)
    email: str = Field(default="", max_length=320)
    role: MembershipRole = MembershipRole.CREATOR
    team_ids: list[str] = Field(default_factory=list, max_length=100)


class StudioInvitationCreate(BaseModel):
    email: str = Field(min_length=3, max_length=320)
    role: MembershipRole = MembershipRole.CREATOR
    proposed_profile_name: str = Field(default="", max_length=60)
    note: str = Field(default="", max_length=500)
    expires_hours: int = Field(default=72, ge=1, le=720)


class JoinDecisionBody(BaseModel):
    note: str = Field(default="", max_length=500)


class MemberRemoveBody(BaseModel):
    confirmation: str = Field(min_length=1, max_length=160)


class _RateWindow:
    """Process-local abuse brake; cluster ingress remains the outer rate-limit layer."""

    def __init__(self) -> None:
        self._hits: dict[str, deque[float]] = defaultdict(deque)

    def admit(self, key: str) -> bool:
        limit = max(10, int(os.environ.get("DIONY_API_REQUESTS_PER_MINUTE", "240")))
        now = time.monotonic()
        hits = self._hits[key]
        while hits and hits[0] < now - 60:
            hits.popleft()
        if len(hits) >= limit:
            return False
        hits.append(now)
        return True


_rate_window = _RateWindow()


def _plane() -> ControlPlane:
    return ControlPlane()


Plane = Annotated[ControlPlane, Depends(_plane)]


def _actor(
    request: Request,
    plane: Plane,
    authorization: Annotated[str | None, Header()] = None,
) -> Actor:
    mode = os.environ.get("DIONY_AUTH_MODE", "local").strip().lower()
    if mode == "local":
        context = plane.bootstrap_local()
        actor = Actor(
            user_id=context.user.id,
            workspace_id=context.workspace.id,
            role=context.membership.role,
            auth_mode="local",
        )
    elif mode == "token":
        if not authorization or not authorization.lower().startswith("bearer "):
            raise HTTPException(
                status.HTTP_401_UNAUTHORIZED,
                "A signed Diony workspace token is required.",
                headers={"WWW-Authenticate": "Bearer"},
            )
        try:
            actor = verify_token(authorization.split(" ", 1)[1].strip())
            membership = plane.authorize(actor.user_id, actor.workspace_id, MembershipRole.VIEWER)
            actor.role = membership.role
        except (ValueError, WorkspaceAccessDenied) as exc:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, str(exc)) from exc
    else:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "DIONY_AUTH_MODE must be local or token before Producer can accept control-plane requests.",
        )
    if not _rate_window.admit(f"{actor.workspace_id}:{actor.user_id}"):
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            "This maker is moving faster than the control plane can safely record. Wait a moment and retry.",
            headers={"Retry-After": "1"},
        )
    request.state.actor = actor
    return actor


ActorDep = Annotated[Actor, Depends(_actor)]


def _require(plane: ControlPlane, actor: Actor, role: MembershipRole) -> None:
    try:
        plane.authorize(actor.user_id, actor.workspace_id, role)
    except WorkspaceAccessDenied as exc:
        raise HTTPException(status.HTTP_403_FORBIDDEN, str(exc)) from exc


def _membership(plane: ControlPlane, actor: Actor) -> Membership:
    try:
        return plane.authorize(actor.user_id, actor.workspace_id, MembershipRole.VIEWER)
    except WorkspaceAccessDenied as exc:
        raise HTTPException(status.HTTP_403_FORBIDDEN, str(exc)) from exc


def _require_cost_admin(plane: ControlPlane, actor: Actor) -> Membership:
    membership = _membership(plane, actor)
    if not CostAnalyticsService(plane).is_organization_admin(membership):
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "Organization cost totals are restricted to the configured Cost Center admin account or team.",
        )
    return membership


def _known_ids(plane: ControlPlane, workspace_id: str, kind: str) -> set[str]:
    return {str(row["id"]) for row in plane.list_documents(kind, workspace_id)}


def _engine(plane: ControlPlane) -> DurableWorkflowEngine:
    return DurableWorkflowEngine(plane, ProviderRegistry.with_diony_defaults())


def _correlation(request: Request) -> str:
    return str(getattr(request.state, "correlation_id", "") or "")


def _run_or_http(plane: ControlPlane, actor: Actor, run_id: str):
    try:
        return plane.run(actor.workspace_id, run_id)
    except DocumentNotFound as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    except WorkspaceAccessDenied as exc:
        raise HTTPException(status.HTTP_403_FORBIDDEN, str(exc)) from exc


@router.get("/session")
def session(actor: ActorDep, plane: Plane) -> dict[str, Any]:
    membership = _membership(plane, actor)
    return {
        **actor.model_dump(mode="json"),
        "cost_scope": (
            "organization"
            if CostAnalyticsService(plane).is_organization_admin(membership)
            else "assigned"
        ),
        "message": "Your studio, your assets, your lineage.",
    }


@router.get("/providers")
def providers(actor: ActorDep) -> dict[str, Any]:
    return {
        "providers": ProviderRegistry.with_diony_defaults().catalog(),
        "policy": "local-first; hosted adapters require an explicit workspace choice",
        "models_unchanged": True,
    }


@router.get("/teams")
def teams(actor: ActorDep, plane: Plane) -> dict[str, Any]:
    return {"teams": plane.list_documents("team", actor.workspace_id)}


@router.post("/teams", status_code=status.HTTP_201_CREATED)
def create_team(body: TeamCreate, request: Request, actor: ActorDep, plane: Plane):
    _require(plane, actor, MembershipRole.ADMIN)
    team = Team(
        id=f"team-{uuid.uuid4().hex}",
        workspace_id=actor.workspace_id,
        name=body.name.strip(),
    )
    plane.put_document("team", team.id, actor.workspace_id, team.model_dump(mode="json"))
    plane.audit(
        workspace_id=actor.workspace_id,
        actor_user_id=actor.user_id,
        action="team.created",
        object_type="team",
        object_id=team.id,
        correlation_id=_correlation(request),
        after=team,
    )
    return team


@router.get("/members")
def members(actor: ActorDep, plane: Plane) -> dict[str, Any]:
    _require(plane, actor, MembershipRole.VIEWER)
    return {"members": plane.list_documents("membership", actor.workspace_id)}


@router.get("/studio")
def studio_context(actor: ActorDep, plane: Plane) -> dict[str, Any]:
    membership = _membership(plane, actor)
    workspace = plane.get_document("workspace", actor.workspace_id, actor.workspace_id)
    return {
        "studio": workspace,
        "membership": membership.model_dump(mode="json"),
        "can_manage_members": membership.can_manage,
        "cost_scope": (
            "organization"
            if CostAnalyticsService(plane).is_organization_admin(membership)
            else "assigned"
        ),
    }


@router.get("/studio/invitations")
def studio_invitations(actor: ActorDep, plane: Plane) -> dict[str, Any]:
    _require(plane, actor, MembershipRole.ADMIN)
    return {"invitations": StudioInvitationService(plane).list_invitations(actor.workspace_id)}


@router.post("/studio/invitations", status_code=status.HTTP_201_CREATED)
def create_studio_invitation(
    body: StudioInvitationCreate,
    request: Request,
    actor: ActorDep,
    plane: Plane,
):
    _require(plane, actor, MembershipRole.ADMIN)
    try:
        return StudioInvitationService(plane).create_invitation(
            workspace_id=actor.workspace_id,
            actor_user_id=actor.user_id,
            email=body.email,
            role=body.role,
            proposed_profile_name=body.proposed_profile_name,
            note=body.note,
            expires_hours=body.expires_hours,
            correlation_id=_correlation(request),
        )
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc


@router.post("/studio/invitations/{invitation_id}/revoke")
def revoke_studio_invitation(
    invitation_id: str,
    request: Request,
    actor: ActorDep,
    plane: Plane,
):
    _require(plane, actor, MembershipRole.ADMIN)
    try:
        return StudioInvitationService(plane).revoke_invitation(
            workspace_id=actor.workspace_id,
            invitation_id=invitation_id,
            actor_user_id=actor.user_id,
            correlation_id=_correlation(request),
        )
    except (ValueError, DocumentNotFound) as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc


@router.get("/studio/join-requests")
def studio_join_requests(actor: ActorDep, plane: Plane) -> dict[str, Any]:
    _require(plane, actor, MembershipRole.ADMIN)
    return {"requests": plane.list_documents("studio_join_request", actor.workspace_id)}


@router.post("/studio/join-requests/{request_id}/approve")
def approve_studio_join_request(
    request_id: str,
    request: Request,
    actor: ActorDep,
    plane: Plane,
):
    _require(plane, actor, MembershipRole.ADMIN)
    try:
        return StudioInvitationService(plane).approve_request(
            workspace_id=actor.workspace_id,
            request_id=request_id,
            actor_user_id=actor.user_id,
            correlation_id=_correlation(request),
        )
    except (ValueError, DocumentNotFound) as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc


@router.post("/studio/join-requests/{request_id}/deny")
def deny_studio_join_request(
    request_id: str,
    body: JoinDecisionBody,
    request: Request,
    actor: ActorDep,
    plane: Plane,
):
    _require(plane, actor, MembershipRole.ADMIN)
    try:
        return StudioInvitationService(plane).deny_request(
            workspace_id=actor.workspace_id,
            request_id=request_id,
            actor_user_id=actor.user_id,
            note=body.note,
            correlation_id=_correlation(request),
        )
    except (ValueError, DocumentNotFound) as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc


@router.post("/studio/members/{membership_id}/remove")
def remove_studio_member(
    membership_id: str,
    body: MemberRemoveBody,
    request: Request,
    actor: ActorDep,
    plane: Plane,
):
    _require(plane, actor, MembershipRole.ADMIN)
    try:
        return StudioInvitationService(plane).remove_member(
            workspace_id=actor.workspace_id,
            membership_id=membership_id,
            actor_user_id=actor.user_id,
            confirmation=body.confirmation,
            correlation_id=_correlation(request),
        )
    except (ValueError, DocumentNotFound) as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc


@router.put("/studio/members/{membership_id}/cost-centers")
def assign_member_cost_centers(
    membership_id: str,
    body: MemberCostCentersWrite,
    request: Request,
    actor: ActorDep,
    plane: Plane,
):
    _require_cost_admin(plane, actor)
    try:
        membership = Membership.model_validate(
            plane.get_document("membership", membership_id, actor.workspace_id)
        )
    except DocumentNotFound as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc

    center_ids = list(dict.fromkeys(body.cost_center_ids))
    if membership.personal_cost_center_id not in center_ids:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "A member's personal Cost Center must remain assigned.",
        )
    known_centers = {
        str(row["id"]): CostCenter.model_validate(row)
        for row in plane.list_documents("cost_center", actor.workspace_id)
    }
    unknown = set(center_ids).difference(known_centers)
    if unknown:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"Unknown Cost Center IDs: {', '.join(sorted(unknown))}",
        )
    inactive = [center_id for center_id in center_ids if not known_centers[center_id].active]
    if inactive:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"Inactive Cost Centers cannot be assigned: {', '.join(sorted(inactive))}",
        )

    before = membership.model_copy(deep=True)
    membership.assigned_cost_center_ids = center_ids
    membership.team_ids = list(
        dict.fromkeys(
            [
                *membership.team_ids,
                *(
                    team_id
                    for center_id in center_ids
                    for team_id in known_centers[center_id].team_ids
                ),
            ]
        )
    )
    plane.put_document(
        "membership",
        membership.id,
        actor.workspace_id,
        membership.model_dump(mode="json"),
    )
    plane.audit(
        workspace_id=actor.workspace_id,
        actor_user_id=actor.user_id,
        action="membership.cost_centers_assigned",
        object_type="membership",
        object_id=membership.id,
        correlation_id=_correlation(request),
        before=before,
        after=membership,
    )
    return membership


@router.put("/members/{user_id}")
def put_member(
    user_id: str,
    body: MemberWrite,
    request: Request,
    actor: ActorDep,
    plane: Plane,
):
    _require(plane, actor, MembershipRole.ADMIN)
    known_team_ids = {str(row["id"]) for row in plane.list_documents("team", actor.workspace_id)}
    unknown = set(body.team_ids).difference(known_team_ids)
    if unknown:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"Unknown team IDs: {', '.join(sorted(unknown))}",
        )
    user = User(id=user_id, display_name=body.display_name.strip(), email=body.email.strip())
    membership = Membership(
        id=f"{actor.workspace_id}:{user_id}",
        user_id=user_id,
        workspace_id=actor.workspace_id,
        team_ids=body.team_ids,
        role=body.role,
    )
    plane.put_document("user", user.id, actor.workspace_id, user.model_dump(mode="json"))
    plane.put_document(
        "membership",
        membership.id,
        actor.workspace_id,
        membership.model_dump(mode="json"),
    )
    plane.audit(
        workspace_id=actor.workspace_id,
        actor_user_id=actor.user_id,
        action="membership.updated",
        object_type="membership",
        object_id=membership.id,
        correlation_id=_correlation(request),
        after=membership,
    )
    return membership


@router.get("/departments")
def departments(actor: ActorDep, plane: Plane) -> dict[str, Any]:
    membership = _membership(plane, actor)
    analytics = CostAnalyticsService(plane)
    rows = plane.list_documents("department", actor.workspace_id)
    if not analytics.is_organization_admin(membership):
        rows = [
            row
            for row in rows
            if actor.user_id in row.get("manager_user_ids", [])
            or set(row.get("team_ids", [])).intersection(membership.team_ids)
        ]
    return {"departments": rows}


@router.post("/departments", status_code=status.HTTP_201_CREATED)
def create_department(body: DepartmentCreate, request: Request, actor: ActorDep, plane: Plane):
    _require_cost_admin(plane, actor)
    unknown = set(body.team_ids).difference(_known_ids(plane, actor.workspace_id, "team"))
    if unknown:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"Unknown team IDs: {', '.join(sorted(unknown))}",
        )
    department = Department(
        id=f"department-{uuid.uuid4().hex}",
        workspace_id=actor.workspace_id,
        name=body.name.strip(),
        code=body.code.strip().upper(),
        manager_user_ids=body.manager_user_ids,
        team_ids=body.team_ids,
    )
    CostAnalyticsService(plane).save_department(department)
    plane.audit(
        workspace_id=actor.workspace_id,
        actor_user_id=actor.user_id,
        action="department.created",
        object_type="department",
        object_id=department.id,
        correlation_id=_correlation(request),
        after=department,
    )
    return department


@router.get("/cost-centers")
def cost_centers(actor: ActorDep, plane: Plane) -> dict[str, Any]:
    membership = _membership(plane, actor)
    analytics = CostAnalyticsService(plane)
    rows = plane.list_documents("cost_center", actor.workspace_id)
    if not analytics.is_organization_admin(membership):
        managed = {
            str(row["id"])
            for row in plane.list_documents("department", actor.workspace_id)
            if actor.user_id in row.get("manager_user_ids", [])
        }
        rows = [
            row
            for row in rows
            if row.get("department_id") in managed
            or set(row.get("team_ids", [])).intersection(membership.team_ids)
        ]
    return {"cost_centers": rows}


@router.post("/cost-centers", status_code=status.HTTP_201_CREATED)
def create_cost_center(body: CostCenterCreate, request: Request, actor: ActorDep, plane: Plane):
    _require_cost_admin(plane, actor)
    unknown = set(body.team_ids).difference(_known_ids(plane, actor.workspace_id, "team"))
    if unknown:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"Unknown team IDs: {', '.join(sorted(unknown))}",
        )
    center = CostCenter(
        id=f"cost-center-{uuid.uuid4().hex}",
        workspace_id=actor.workspace_id,
        department_id=body.department_id,
        name=body.name.strip(),
        code=body.code.strip().upper(),
        team_ids=body.team_ids,
        internal_credit_budget=body.internal_credit_budget,
        cost_budget_usd_micros=body.cost_budget_usd_micros,
        warning_threshold_basis_points=body.warning_threshold_basis_points,
    )
    try:
        CostAnalyticsService(plane).save_cost_center(center)
    except (DocumentNotFound, ValueError) as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    plane.audit(
        workspace_id=actor.workspace_id,
        actor_user_id=actor.user_id,
        action="cost_center.created",
        object_type="cost_center",
        object_id=center.id,
        correlation_id=_correlation(request),
        after=center,
    )
    return center


@router.patch("/cost-centers/{center_id}")
def rename_cost_center(
    center_id: str,
    body: CostCenterRename,
    request: Request,
    actor: ActorDep,
    plane: Plane,
):
    _require_cost_admin(plane, actor)
    try:
        center = CostCenter.model_validate(
            plane.get_document("cost_center", center_id, actor.workspace_id)
        )
    except DocumentNotFound as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    before = center.model_copy(deep=True)
    center.name = body.name.strip()
    center.updated_at = datetime.now(UTC)
    CostAnalyticsService(plane).save_cost_center(center)
    plane.audit(
        workspace_id=actor.workspace_id,
        actor_user_id=actor.user_id,
        action="cost_center.renamed",
        object_type="cost_center",
        object_id=center.id,
        correlation_id=_correlation(request),
        before=before,
        after=center,
    )
    return center


@router.get("/cost-rate-cards")
def cost_rate_cards(actor: ActorDep, plane: Plane) -> dict[str, Any]:
    _require_cost_admin(plane, actor)
    return {"rate_cards": plane.list_documents("cost_rate_card", actor.workspace_id)}


@router.get("/cost-governance")
def cost_governance(actor: ActorDep, plane: Plane) -> CostGovernance:
    _require_cost_admin(plane, actor)
    return CostAnalyticsService(plane).governance(actor.workspace_id)


@router.put("/cost-governance")
def update_cost_governance(
    body: CostGovernanceWrite, request: Request, actor: ActorDep, plane: Plane
) -> CostGovernance:
    _require_cost_admin(plane, actor)
    member_user_ids = {
        str(row["user_id"]) for row in plane.list_documents("membership", actor.workspace_id)
    }
    if body.admin_user_id not in member_user_ids:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "The Cost Center admin account must be a workspace member.",
        )
    if body.admin_team_id not in _known_ids(plane, actor.workspace_id, "team"):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Unknown Cost Center admin team.")
    if body.default_cost_center_id not in _known_ids(plane, actor.workspace_id, "cost_center"):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Unknown default Cost Center.")
    before = CostAnalyticsService(plane).governance(actor.workspace_id)
    governance = CostGovernance(
        id="cost-governance",
        workspace_id=actor.workspace_id,
        **body.model_dump(),
    )
    plane.put_document(
        "cost_governance",
        governance.id,
        actor.workspace_id,
        governance.model_dump(mode="json"),
    )
    plane.audit(
        workspace_id=actor.workspace_id,
        actor_user_id=actor.user_id,
        action="cost_governance.updated",
        object_type="cost_governance",
        object_id=governance.id,
        correlation_id=_correlation(request),
        before=before,
        after=governance,
    )
    return governance


@router.post("/cost-rate-cards", status_code=status.HTTP_201_CREATED)
def create_cost_rate_card(
    body: CostRateCardCreate, request: Request, actor: ActorDep, plane: Plane
):
    _require_cost_admin(plane, actor)
    rate = CostRateCard(
        id=f"cost-rate-{uuid.uuid4().hex}",
        workspace_id=actor.workspace_id,
        **body.model_dump(),
    )
    CostAnalyticsService(plane).save_rate_card(rate)
    plane.audit(
        workspace_id=actor.workspace_id,
        actor_user_id=actor.user_id,
        action="cost_rate_card.created",
        object_type="cost_rate_card",
        object_id=rate.id,
        correlation_id=_correlation(request),
        after=rate,
    )
    return rate


@router.post("/workflows", status_code=status.HTTP_201_CREATED)
def create_workflow(body: WorkflowCreate, request: Request, actor: ActorDep, plane: Plane):
    _require(plane, actor, MembershipRole.CREATOR)
    membership = _membership(plane, actor)
    analytics = CostAnalyticsService(plane)
    is_cost_admin = analytics.is_organization_admin(membership)
    center_id = str(
        body.config.get("cost_center_id")
        or membership.personal_cost_center_id
        or analytics.governance(actor.workspace_id).default_cost_center_id
    )
    try:
        center = CostCenter.model_validate(
            plane.get_document("cost_center", center_id, actor.workspace_id)
        )
    except DocumentNotFound as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    allowed_center_ids = {
        membership.personal_cost_center_id,
        *membership.assigned_cost_center_ids,
    }
    if not is_cost_admin and center.id not in allowed_center_ids:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "That Cost Center is not assigned to this maker.",
        )
    requested_department = str(body.config.get("department_id") or center.department_id)
    if requested_department != center.department_id:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "The workflow Department must match its Cost Center.",
        )
    requested_team = str(body.config.get("team_id") or "")
    eligible_teams = (
        center.team_ids
        if is_cost_admin
        else [team_id for team_id in center.team_ids if team_id in membership.team_ids]
    )
    if requested_team and requested_team not in eligible_teams:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "That Team is not assigned to this maker and Cost Center.",
        )
    if not requested_team:
        requested_team = eligible_teams[0] if eligible_teams else ""
    if not requested_team:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "Choose a Cost Center assigned to this maker's team before production.",
        )
    resolved_config = {
        **body.config,
        "cost_center_id": center.id,
        "department_id": center.department_id,
        "team_id": requested_team,
    }
    return _engine(plane).create_run(
        workspace_id=actor.workspace_id,
        project_id=body.project_id,
        created_by=actor.user_id,
        stages=body.stages,
        config={"require_local": True, **resolved_config},
        correlation_id=_correlation(request),
    )


@router.get("/workflows")
def list_workflows(
    actor: ActorDep, plane: Plane, project_id: str | None = Query(default=None)
) -> dict[str, Any]:
    return {
        "runs": [
            run.model_dump(mode="json")
            for run in plane.runs(actor.workspace_id, project_id=project_id)
        ]
    }


@router.get("/workflows/{run_id}")
def get_workflow(run_id: str, actor: ActorDep, plane: Plane):
    return _run_or_http(plane, actor, run_id)


@router.post("/workflows/{run_id}/advance")
def advance_workflow(run_id: str, actor: ActorDep, plane: Plane):
    _require(plane, actor, MembershipRole.CREATOR)
    _run_or_http(plane, actor, run_id)
    return _engine(plane).advance(run_id, workspace_id=actor.workspace_id)


@router.post("/workflows/{run_id}/stages/{stage}/checkpoint")
def attach_stage_checkpoint(
    run_id: str,
    stage: WorkflowStage,
    body: CheckpointWrite,
    request: Request,
    actor: ActorDep,
    plane: Plane,
):
    """Attach signed-site engine proof; the next advance consumes it idempotently."""
    _require(plane, actor, MembershipRole.PRODUCER)
    run = _run_or_http(plane, actor, run_id)
    if stage not in {state.stage for state in run.stages}:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "That stage is not part of this run.")
    before = run.model_dump(mode="json")
    stage_payloads = dict(run.config.get("stage_payloads") or {})
    stage_payloads[stage.value] = {
        "checkpoint_complete": True,
        "output_asset_ids": body.output_asset_ids,
        "actual_internal_credits": body.actual_internal_credits,
        "cost_breakdown": (
            body.cost_breakdown.model_dump(mode="json") if body.cost_breakdown else None
        ),
        "quantity_microunits": body.quantity_microunits,
        "unit": body.unit,
        "metadata": body.metadata,
    }
    run.config["stage_payloads"] = stage_payloads
    plane.save_run(run)
    plane.append_event(
        workspace_id=actor.workspace_id,
        run_id=run.id,
        project_id=run.project_id,
        kind="workflow.stage.checkpoint_attached",
        payload={
            "stage": stage.value,
            "output_asset_ids": body.output_asset_ids,
            "message": "Signed-site engine proof attached. The durable worker may advance this stage.",
            "correlation_id": _correlation(request),
        },
    )
    plane.audit(
        workspace_id=actor.workspace_id,
        actor_user_id=actor.user_id,
        action="workflow.stage_checkpoint_attached",
        object_type="workflow_run",
        object_id=run.id,
        correlation_id=_correlation(request),
        before=before,
        after=run,
        metadata={"stage": stage.value},
    )
    return run


@router.post("/workflows/{run_id}/pause")
def pause_workflow(run_id: str, actor: ActorDep, plane: Plane):
    _require(plane, actor, MembershipRole.CREATOR)
    _run_or_http(plane, actor, run_id)
    return _engine(plane).pause(
        run_id, workspace_id=actor.workspace_id, actor_user_id=actor.user_id
    )


@router.post("/workflows/{run_id}/resume")
def resume_workflow(run_id: str, actor: ActorDep, plane: Plane):
    _require(plane, actor, MembershipRole.CREATOR)
    _run_or_http(plane, actor, run_id)
    return _engine(plane).resume(
        run_id, workspace_id=actor.workspace_id, actor_user_id=actor.user_id
    )


@router.post("/workflows/{run_id}/cancel")
def cancel_workflow(run_id: str, actor: ActorDep, plane: Plane):
    _require(plane, actor, MembershipRole.PRODUCER)
    _run_or_http(plane, actor, run_id)
    return _engine(plane).cancel(
        run_id, workspace_id=actor.workspace_id, actor_user_id=actor.user_id
    )


@router.post("/workflows/{run_id}/retry")
def retry_workflow(run_id: str, actor: ActorDep, plane: Plane):
    _require(plane, actor, MembershipRole.CREATOR)
    _run_or_http(plane, actor, run_id)
    return _engine(plane).retry(
        run_id, workspace_id=actor.workspace_id, actor_user_id=actor.user_id
    )


@router.get("/workflows/{run_id}/events")
def workflow_events(
    run_id: str,
    actor: ActorDep,
    plane: Plane,
    after: int = Query(default=0, ge=0),
    limit: int = Query(default=500, ge=1, le=2000),
) -> dict[str, Any]:
    _run_or_http(plane, actor, run_id)
    events = plane.events(actor.workspace_id, run_id, after_seq=after, limit=limit)
    return {"events": [event.model_dump(mode="json") for event in events]}


@router.get("/workflows/{run_id}/events/stream")
async def workflow_event_stream(
    run_id: str,
    request: Request,
    actor: ActorDep,
    plane: Plane,
    after: int = Query(default=0, ge=0),
) -> StreamingResponse:
    _run_or_http(plane, actor, run_id)

    async def generate() -> AsyncIterator[str]:
        cursor = after
        while not await request.is_disconnected():
            rows = plane.events(actor.workspace_id, run_id, after_seq=cursor, limit=200)
            for event in rows:
                cursor = event.seq
                payload = json.dumps(event.model_dump(mode="json"), separators=(",", ":"))
                yield f"id: {event.seq}\nevent: {event.kind}\ndata: {payload}\n\n"
            run = plane.run(actor.workspace_id, run_id)
            if run.status.terminal and not rows:
                yield 'event: end\ndata: {"terminal":true}\n\n'
                break
            if not rows:
                yield ": diony-heartbeat\n\n"
            await asyncio.sleep(1)

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
    )


@router.get("/costs/usage")
def cost_usage(actor: ActorDep, plane: Plane, project_id: str | None = None) -> dict[str, Any]:
    membership = _membership(plane, actor)
    analytics = CostAnalyticsService(plane)
    if analytics.is_organization_admin(membership):
        rows = plane.usage(actor.workspace_id, project_id=project_id)
    else:
        managed = {
            str(row["id"])
            for row in plane.list_documents("department", actor.workspace_id)
            if actor.user_id in row.get("manager_user_ids", [])
        }
        rows = [
            row
            for row in plane.usage(actor.workspace_id, project_id=project_id)
            if row.team_id in membership.team_ids or row.department_id in managed
        ]
    return {"events": [row.model_dump(mode="json") for row in rows]}


@router.get("/costs/summary")
def cost_summary(actor: ActorDep, plane: Plane) -> dict[str, Any]:
    membership = _membership(plane, actor)
    analytics = CostAnalyticsService(plane)
    if analytics.is_organization_admin(membership):
        return analytics.summary(actor.workspace_id, organization_wide=True)
    managed_departments = [
        str(row["id"])
        for row in plane.list_documents("department", actor.workspace_id)
        if actor.user_id in row.get("manager_user_ids", [])
    ]
    return analytics.summary(
        actor.workspace_id,
        allowed_team_ids=membership.team_ids,
        department_ids=managed_departments,
    )


@router.get("/costs/organization")
def organization_costs(actor: ActorDep, plane: Plane) -> dict[str, Any]:
    _require_cost_admin(plane, actor)
    return CostAnalyticsService(plane).summary(actor.workspace_id, organization_wide=True)


@router.get("/audit")
def audit(actor: ActorDep, plane: Plane, limit: int = Query(default=200, ge=1, le=2000)):
    _require(plane, actor, MembershipRole.PRODUCER)
    return {
        "events": [
            row.model_dump(mode="json") for row in plane.audit_log(actor.workspace_id, limit=limit)
        ]
    }


@router.put("/providers/{provider_id}/secrets/{name}")
def put_provider_secret(
    provider_id: str,
    name: str,
    body: SecretWrite,
    request: Request,
    actor: ActorDep,
    plane: Plane,
) -> dict[str, Any]:
    _require(plane, actor, MembershipRole.ADMIN)
    stored = SecretVault(plane).put(actor.workspace_id, provider_id, name, body.value)
    plane.audit(
        workspace_id=actor.workspace_id,
        actor_user_id=actor.user_id,
        action="provider.secret.updated",
        object_type="provider_config",
        object_id=provider_id,
        correlation_id=_correlation(request),
        metadata={"name": name, "masked": stored.masked},
    )
    return {"provider_id": provider_id, "name": name, "masked": stored.masked}


@router.get("/provider-secrets")
def provider_secrets(actor: ActorDep, plane: Plane) -> dict[str, Any]:
    _require(plane, actor, MembershipRole.ADMIN)
    return {"secrets": plane.encrypted_secret_catalog(actor.workspace_id)}


@router.get("/health")
def health(actor: ActorDep, plane: Plane) -> dict[str, Any]:
    metrics = plane.metrics(actor.workspace_id)
    object_storage = "s3-compatible" if os.environ.get("DIONY_OBJECT_BUCKET") else "local-vault"
    return {
        "status": "ready",
        "database": "ready",
        "auth_mode": actor.auth_mode,
        "cost_center": "ready",
        "cost_model_version": CostAnalyticsService(plane)
        .governance(actor.workspace_id)
        .cost_model_version,
        "object_storage": object_storage,
        "providers": ProviderRegistry.with_diony_defaults().catalog(),
        "queue_depth": metrics["queue_depth"],
        "control_plane_schema": plane.SCHEMA_VERSION,
    }


@router.get("/admin/operations")
def operations(actor: ActorDep, plane: Plane) -> dict[str, Any]:
    _require(plane, actor, MembershipRole.ADMIN)
    runs = plane.runs(actor.workspace_id)
    return {
        "metrics": plane.metrics(actor.workspace_id),
        "attention": [
            run.model_dump(mode="json")
            for run in runs
            if run.status.value in {"failed", "retry_wait", "waiting_provider"}
        ],
        "provider_health": ProviderRegistry.with_diony_defaults().catalog(),
    }


def prometheus_metrics(request: Request) -> PlainTextResponse:
    """Unversioned scrape target; respects the same launch authentication mode."""
    plane = _plane()
    actor = _actor(request, plane, request.headers.get("authorization"))
    rows = plane.metrics(actor.workspace_id)
    lines = [
        "# HELP diony_workflow_runs Durable workflow runs in this workspace.",
        "# TYPE diony_workflow_runs gauge",
        f"diony_workflow_runs {rows['workflow_runs']}",
    ]
    for key, value in rows.items():
        if key == "workflow_runs":
            continue
        lines.append(f"diony_{key} {value}")
    return PlainTextResponse("\n".join(lines) + "\n", media_type="text/plain; version=0.0.4")
