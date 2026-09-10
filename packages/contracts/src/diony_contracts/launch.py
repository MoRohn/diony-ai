"""Launch control-plane contracts.

These models make Diony's production system durable, portable, and
multi-workspace ready without changing the Director's contracts. They are
intentionally provider-neutral and JSON serializable.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator


def utc_now() -> datetime:
    return datetime.now(UTC)


class MembershipRole(StrEnum):
    VIEWER = "viewer"
    CREATOR = "creator"
    PRODUCER = "producer"
    ADMIN = "admin"
    OWNER = "owner"

    @property
    def rank(self) -> int:
        return {
            MembershipRole.VIEWER: 10,
            MembershipRole.CREATOR: 20,
            MembershipRole.PRODUCER: 30,
            MembershipRole.ADMIN: 40,
            MembershipRole.OWNER: 50,
        }[self]


class AdmissionPolicy(StrEnum):
    INVITE_ADMITS = "invite_admits"
    ADMIN_APPROVAL = "admin_approval"
    CLOSED = "closed"


class MembershipStatus(StrEnum):
    ACTIVE = "active"
    SUSPENDED = "suspended"
    REMOVED = "removed"


class InvitationStatus(StrEnum):
    DRAFT = "draft"
    ACTIVE = "active"
    PARTIALLY_USED = "partially_used"
    USED = "used"
    EXPIRED = "expired"
    REVOKED = "revoked"


class EmailDeliveryStatus(StrEnum):
    QUEUED = "queued"
    SENT = "sent"
    DELIVERED = "delivered"
    DELIVERY_UNCONFIRMED = "delivery_unconfirmed"
    BOUNCED = "bounced"
    COMPLAINED = "complained"
    FAILED = "failed"


class JoinRequestStatus(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    DENIED = "denied"
    WITHDRAWN = "withdrawn"


class User(BaseModel):
    schema_version: int = 1
    id: str
    email: str = ""
    display_name: str
    active: bool = True
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)
    deleted_at: datetime | None = None


class Workspace(BaseModel):
    schema_version: int = 1
    id: str
    name: str
    slug: str
    owner_user_id: str = ""
    local_first: bool = True
    admission_policy: AdmissionPolicy = AdmissionPolicy.INVITE_ADMITS
    controller_origin: str = ""
    controller_fingerprint: str = ""
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)
    deleted_at: datetime | None = None

    @field_validator("slug")
    @classmethod
    def valid_slug(cls, value: str) -> str:
        clean = value.strip().lower()
        if not clean or any(ch not in "abcdefghijklmnopqrstuvwxyz0123456789-" for ch in clean):
            raise ValueError("workspace slug uses lowercase letters, numbers, and hyphens")
        return clean


class Team(BaseModel):
    schema_version: int = 1
    id: str
    workspace_id: str
    name: str
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)
    deleted_at: datetime | None = None


class Department(BaseModel):
    schema_version: int = 1
    id: str
    workspace_id: str
    name: str
    code: str
    manager_user_ids: list[str] = Field(default_factory=list)
    team_ids: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)
    deleted_at: datetime | None = None


class CostCenter(BaseModel):
    schema_version: int = 1
    id: str
    workspace_id: str
    department_id: str
    name: str
    code: str
    team_ids: list[str] = Field(default_factory=list)
    owner_membership_id: str = ""
    internal_credit_budget: int = Field(default=0, ge=0)
    cost_budget_usd_micros: int = Field(default=0, ge=0)
    warning_threshold_basis_points: int = Field(default=8000, ge=1, le=10000)
    monitoring_mode: Literal["monitor_only"] = "monitor_only"
    active: bool = True
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)
    deleted_at: datetime | None = None


class CostGovernance(BaseModel):
    schema_version: int = 1
    id: str
    workspace_id: str
    admin_user_id: str
    admin_team_id: str
    default_cost_center_id: str
    currency: Literal["USD"] = "USD"
    cost_model_version: str = "diony-cost-v1"
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class Membership(BaseModel):
    schema_version: int = 1
    id: str
    user_id: str
    workspace_id: str
    team_ids: list[str] = Field(default_factory=list)
    role: MembershipRole = MembershipRole.CREATOR
    profile_name: str = ""
    profile_name_key: str = ""
    personal_cost_center_id: str = ""
    assigned_cost_center_ids: list[str] = Field(default_factory=list)
    status: MembershipStatus = MembershipStatus.ACTIVE
    created_at: datetime = Field(default_factory=utc_now)
    joined_at: datetime = Field(default_factory=utc_now)
    removed_at: datetime | None = None
    deleted_at: datetime | None = None

    @property
    def can_manage(self) -> bool:
        return (
            self.status == MembershipStatus.ACTIVE and self.role.rank >= MembershipRole.ADMIN.rank
        )


class StudioInvitation(BaseModel):
    schema_version: int = 1
    id: str
    workspace_id: str
    token_hash: str
    email: str
    email_key: str
    role: MembershipRole = MembershipRole.CREATOR
    proposed_profile_name: str = ""
    note: str = ""
    admission_policy: AdmissionPolicy = AdmissionPolicy.INVITE_ADMITS
    status: InvitationStatus = InvitationStatus.ACTIVE
    use_limit: int = Field(default=1, ge=1, le=100)
    use_count: int = Field(default=0, ge=0)
    created_by: str
    expires_at: datetime
    accepted_user_id: str = ""
    accepted_membership_id: str = ""
    join_request_id: str = ""
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)
    revoked_at: datetime | None = None


class InvitationEmailDelivery(BaseModel):
    schema_version: int = 1
    id: str
    workspace_id: str
    invitation_id: str
    kind: str = "studio_invitation"
    recipient_mask: str
    subject: str
    status: EmailDeliveryStatus = EmailDeliveryStatus.QUEUED
    transport: str = ""
    provider_message_id: str = ""
    attempts: int = Field(default=0, ge=0)
    error: str = ""
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)
    sent_at: datetime | None = None


class StudioJoinRequest(BaseModel):
    schema_version: int = 1
    id: str
    workspace_id: str
    invitation_id: str
    user_id: str
    email_key: str
    requested_profile_name: str
    requested_profile_name_key: str
    requested_role: MembershipRole = MembershipRole.CREATOR
    status: JoinRequestStatus = JoinRequestStatus.PENDING
    maker_note: str = ""
    admin_note: str = ""
    decided_by: str = ""
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)
    decided_at: datetime | None = None


class ProviderCapability(StrEnum):
    LLM = "llm"
    IMAGE = "image"
    VIDEO = "video"
    TTS = "tts"
    AUDIO = "audio"
    ASSEMBLY = "assembly"
    FINISH = "finish"
    SAFETY = "safety"
    PACKAGE = "package"


class WorkflowStage(StrEnum):
    INTAKE = "intake"
    SCRIPT = "script"
    BREAKDOWN = "breakdown"
    CONSISTENCY = "consistency"
    SAFETY_INPUT = "safety_input"
    IMAGE = "image"
    VIDEO = "video"
    VOICE = "voice"
    SOUND = "sound"
    ASSEMBLY = "assembly"
    FINISH = "finish"
    SAFETY_OUTPUT = "safety_output"
    PACKAGE = "package"

    @classmethod
    def production_order(cls) -> tuple[WorkflowStage, ...]:
        return (
            cls.INTAKE,
            cls.SCRIPT,
            cls.BREAKDOWN,
            cls.CONSISTENCY,
            cls.SAFETY_INPUT,
            cls.IMAGE,
            cls.VIDEO,
            cls.VOICE,
            cls.SOUND,
            cls.ASSEMBLY,
            cls.FINISH,
            cls.SAFETY_OUTPUT,
            cls.PACKAGE,
        )

    @property
    def capability(self) -> ProviderCapability:
        return {
            WorkflowStage.INTAKE: ProviderCapability.LLM,
            WorkflowStage.SCRIPT: ProviderCapability.LLM,
            WorkflowStage.BREAKDOWN: ProviderCapability.LLM,
            WorkflowStage.CONSISTENCY: ProviderCapability.LLM,
            WorkflowStage.SAFETY_INPUT: ProviderCapability.SAFETY,
            WorkflowStage.IMAGE: ProviderCapability.IMAGE,
            WorkflowStage.VIDEO: ProviderCapability.VIDEO,
            WorkflowStage.VOICE: ProviderCapability.TTS,
            WorkflowStage.SOUND: ProviderCapability.AUDIO,
            WorkflowStage.ASSEMBLY: ProviderCapability.ASSEMBLY,
            WorkflowStage.FINISH: ProviderCapability.FINISH,
            WorkflowStage.SAFETY_OUTPUT: ProviderCapability.SAFETY,
            WorkflowStage.PACKAGE: ProviderCapability.PACKAGE,
        }[self]


class JobStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    WAITING_PROVIDER = "waiting_provider"
    RETRY_WAIT = "retry_wait"
    PAUSED = "paused"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELED = "canceled"
    SKIPPED = "skipped"

    @property
    def terminal(self) -> bool:
        return self in {self.SUCCEEDED, self.FAILED, self.CANCELED, self.SKIPPED}


class WorkflowStageState(BaseModel):
    stage: WorkflowStage
    status: JobStatus = JobStatus.QUEUED
    progress_percent: float = 0
    job_ids: list[str] = Field(default_factory=list)
    started_at: datetime | None = None
    completed_at: datetime | None = None
    error_code: str = ""
    error_message: str = ""
    output_asset_ids: list[str] = Field(default_factory=list)

    @field_validator("progress_percent")
    @classmethod
    def bounded_progress(cls, value: float) -> float:
        if not 0 <= value <= 100:
            raise ValueError("progress_percent must be between 0 and 100")
        return value


class WorkflowRun(BaseModel):
    schema_version: int = 1
    id: str
    workspace_id: str
    project_id: str
    created_by: str
    correlation_id: str = ""
    status: JobStatus = JobStatus.QUEUED
    stages: list[WorkflowStageState]
    progress_percent: float = 0
    forecast_internal_credits: int = 0
    current_stage: WorkflowStage | None = None
    config: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)
    started_at: datetime | None = None
    completed_at: datetime | None = None
    deleted_at: datetime | None = None

    @classmethod
    def production_default(
        cls,
        *,
        id: str,
        workspace_id: str,
        project_id: str,
        created_by: str,
        correlation_id: str = "",
    ) -> WorkflowRun:
        return cls(
            id=id,
            workspace_id=workspace_id,
            project_id=project_id,
            created_by=created_by,
            correlation_id=correlation_id,
            stages=[WorkflowStageState(stage=stage) for stage in WorkflowStage.production_order()],
        )

    @model_validator(mode="after")
    def stages_are_unique_and_ordered(self) -> WorkflowRun:
        stages = [state.stage for state in self.stages]
        if len(stages) != len(set(stages)):
            raise ValueError("workflow stages must be unique")
        order = {stage: index for index, stage in enumerate(WorkflowStage.production_order())}
        if stages != sorted(stages, key=order.__getitem__):
            raise ValueError("workflow stages must follow production order")
        if not 0 <= self.progress_percent <= 100:
            raise ValueError("progress_percent must be between 0 and 100")
        return self


class GenerationJob(BaseModel):
    schema_version: int = 1
    id: str
    run_id: str
    workspace_id: str
    project_id: str
    stage: WorkflowStage
    capability: ProviderCapability
    provider_id: str
    model: str
    status: JobStatus = JobStatus.QUEUED
    idempotency_key: str
    attempt: int = 0
    max_attempts: int = 3
    progress_percent: float = 0
    forecast_internal_credits: int = 0
    actual_internal_credits: int = 0
    actual_cost_usd_micros: int = 0
    external_job_id: str = ""
    input_lineage: list[str] = Field(default_factory=list)
    output_asset_ids: list[str] = Field(default_factory=list)
    error_code: str = ""
    error_message: str = ""
    next_attempt_at: datetime | None = None
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)
    started_at: datetime | None = None
    completed_at: datetime | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("max_attempts")
    @classmethod
    def attempts_positive(cls, value: int) -> int:
        if value < 1:
            raise ValueError("max_attempts must be >= 1")
        return value

    @field_validator("progress_percent")
    @classmethod
    def progress_bounded(cls, value: float) -> float:
        if not 0 <= value <= 100:
            raise ValueError("progress_percent must be between 0 and 100")
        return value


class ProgressEvent(BaseModel):
    schema_version: int = 1
    id: str
    seq: int = 0
    run_id: str
    workspace_id: str
    project_id: str
    stage: WorkflowStage | None = None
    job_id: str | None = None
    kind: str = "progress"
    percent: float = 0
    message: str
    level: Literal["info", "live", "success", "warning", "error"] = "info"
    preview_asset_id: str | None = None
    estimated_seconds_remaining: int | None = None
    actions: list[str] = Field(default_factory=list)
    payload: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=utc_now)

    @field_validator("percent")
    @classmethod
    def percent_bounded(cls, value: float) -> float:
        if not 0 <= value <= 100:
            raise ValueError("percent must be between 0 and 100")
        return value


class CostBreakdown(BaseModel):
    compute_usd_micros: int = Field(default=0, ge=0)
    energy_usd_micros: int = Field(default=0, ge=0)
    storage_usd_micros: int = Field(default=0, ge=0)
    network_usd_micros: int = Field(default=0, ge=0)
    license_usd_micros: int = Field(default=0, ge=0)
    other_usd_micros: int = Field(default=0, ge=0)
    total_usd_micros: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def total_reconciles(self) -> CostBreakdown:
        component_total = (
            self.compute_usd_micros
            + self.energy_usd_micros
            + self.storage_usd_micros
            + self.network_usd_micros
            + self.license_usd_micros
            + self.other_usd_micros
        )
        if self.total_usd_micros == 0:
            self.total_usd_micros = component_total
        elif self.total_usd_micros != component_total:
            raise ValueError("cost breakdown total must equal its fixed-point components")
        return self


class CostRateCard(BaseModel):
    schema_version: int = 1
    id: str
    workspace_id: str
    capability: ProviderCapability
    provider_id: str
    model: str
    unit: str
    internal_credits_per_unit: int = Field(ge=0)
    cost_usd_micros_per_unit: int = Field(ge=0)
    source: str
    effective_from: datetime = Field(default_factory=utc_now)
    effective_to: datetime | None = None
    active: bool = True


class UsageEvent(BaseModel):
    schema_version: int = 1
    id: str
    workspace_id: str
    project_id: str
    run_id: str
    job_id: str
    department_id: str
    team_id: str
    cost_center_id: str
    capability: ProviderCapability
    provider_id: str
    model: str
    quantity_microunits: int = Field(ge=0)
    unit: str
    internal_credits: int = Field(default=0, ge=0)
    cost_breakdown: CostBreakdown = Field(default_factory=CostBreakdown)
    rate_card_id: str = ""
    measurement: Literal["actual", "rate_card", "unrated"] = "unrated"
    metadata: dict[str, Any] = Field(default_factory=dict)
    occurred_at: datetime = Field(default_factory=utc_now)


class CostAllocationEntry(BaseModel):
    schema_version: int = 1
    id: str
    workspace_id: str
    usage_event_id: str
    project_id: str
    job_id: str
    department_id: str
    team_id: str
    cost_center_id: str
    internal_credits: int = Field(ge=0)
    cost_usd_micros: int = Field(ge=0)
    created_at: datetime = Field(default_factory=utc_now)


class AuditEvent(BaseModel):
    schema_version: int = 1
    id: str
    workspace_id: str
    actor_user_id: str
    action: str
    object_type: str
    object_id: str
    correlation_id: str
    before_hash: str = ""
    after_hash: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=utc_now)


class ProviderConfig(BaseModel):
    schema_version: int = 1
    id: str
    workspace_id: str
    provider_id: str
    label: str
    capabilities: list[ProviderCapability] = Field(default_factory=list)
    preferred_models: dict[str, str] = Field(default_factory=dict)
    enabled: bool = True
    byok: bool = False
    secret_names: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)
    deleted_at: datetime | None = None


class RenderVersion(BaseModel):
    schema_version: int = 1
    id: str
    workspace_id: str
    project_id: str
    version: int
    parent_version_id: str | None = None
    asset_ids: list[str] = Field(default_factory=list)
    source_job_ids: list[str] = Field(default_factory=list)
    source_take_ids: list[str] = Field(default_factory=list)
    sha256: str
    created_at: datetime = Field(default_factory=utc_now)
    deleted_at: datetime | None = None


class StoredEvent(BaseModel):
    seq: int
    workspace_id: str
    run_id: str
    project_id: str
    kind: str
    payload: dict[str, Any]
    created_at: datetime = Field(default_factory=utc_now)


class BootstrapContext(BaseModel):
    user: User
    workspace: Workspace
    membership: Membership
