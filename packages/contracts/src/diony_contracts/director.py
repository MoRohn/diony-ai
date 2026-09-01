"""Director service contracts (Blueprints 25/26).

Cloud generation is conducted by the Director Agent: every priced operation
carries a locked cost estimate before it may exist, and estimates reconcile
their totals the same way ``CostBreakdown`` does — money never floats and a
stated total that disagrees with its lines is refused, not repaired.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class EngineMode(StrEnum):
    """Which generation engine the platform runs on. Cloud is primary."""

    CLOUD = "cloud"
    LOCAL_LEGACY = "local_legacy"


class CloudProvider(StrEnum):
    GROK = "grok"
    GOOGLE_FLOW = "google-flow"


class CloudGenerationMode(StrEnum):
    T2V = "t2v"
    I2V = "i2v"
    R2V = "r2v"
    FIRST_LAST = "first_last"
    V2V_EDIT = "v2v_edit"
    EXTENSION = "extension"
    UPSCALE = "upscale"
    IMAGE = "image"


class DirectorOpType(StrEnum):
    GENERATE = "generate"
    EDIT = "edit"
    EXTEND = "extend"
    REIMAGE = "reimage"
    RECAST = "recast"
    SWAP_PROVIDER = "swap_provider"
    UPSCALE = "upscale"
    ASSEMBLE = "assemble"
    ACCEPT = "accept"
    HOLD = "hold"


#: Operation types that spend provider money and therefore demand a locked
#: estimate, a provider, and a model before the op object may even exist.
PRICED_OPS: frozenset[DirectorOpType] = frozenset(
    {
        DirectorOpType.GENERATE,
        DirectorOpType.EDIT,
        DirectorOpType.EXTEND,
        DirectorOpType.REIMAGE,
        DirectorOpType.RECAST,
        DirectorOpType.UPSCALE,
    }
)


class CostEstimateLine(BaseModel):
    label: str
    provider_id: str
    model: str
    unit: str
    unit_rate_usd_micros: int = Field(default=0, ge=0)
    quantity_microunits: int = Field(default=0, ge=0)
    internal_credits: int = Field(default=0, ge=0)
    usd_micros: int = Field(ge=0)
    rate_card_id: str = ""


class CostEstimate(BaseModel):
    schema_version: int = 1
    id: str
    workspace_id: str
    project_id: str = ""
    op_id: str = ""
    currency: Literal["USD"] = "USD"
    lines: list[CostEstimateLine] = Field(default_factory=list)
    revision_allowance_usd_micros: int = Field(default=0, ge=0)
    total_usd_micros: int = Field(default=0, ge=0)
    rate_cards_stale: bool = False
    created_at: datetime = Field(default_factory=_utc_now)

    @model_validator(mode="after")
    def total_reconciles(self) -> "CostEstimate":
        computed = sum(line.usd_micros for line in self.lines)
        computed += self.revision_allowance_usd_micros
        if self.total_usd_micros == 0:
            self.total_usd_micros = computed
        elif self.total_usd_micros != computed:
            raise ValueError(
                "cost estimate total does not reconcile with its lines: "
                f"stated {self.total_usd_micros}, computed {computed}"
            )
        return self


class DirectorOp(BaseModel):
    schema_version: int = 1
    id: str
    session_id: str
    op: DirectorOpType
    project_id: str = ""
    shot_id: str = ""
    clip_id: str = ""
    provider: CloudProvider | None = None
    model: str = ""
    mode: CloudGenerationMode | None = None
    prompt: str = ""
    prompt_version: str = ""
    duration_s: float = Field(default=0, ge=0)
    resolution: str = "720p"
    artifact_refs: dict[str, Any] = Field(default_factory=dict)
    estimate: CostEstimate | None = None
    rationale: str = ""
    requires_confirmation: bool = True
    # "submitting" is the transient double-spend claim: held only while the
    # provider call is in flight; a persisted snapshot releases it back to
    # "proposed" on rehydrate (the claiming thread died with the process).
    status: Literal[
        "proposed", "approved", "submitting", "submitted", "completed", "failed", "discarded"
    ] = "proposed"
    job_id: str = ""
    created_at: datetime = Field(default_factory=_utc_now)

    @model_validator(mode="after")
    def priced_ops_are_locked(self) -> "DirectorOp":
        if self.op in PRICED_OPS:
            if self.estimate is None:
                raise ValueError(
                    f"a {self.op.value} op spends money and requires a locked cost estimate"
                )
            if self.provider is None or not self.model:
                raise ValueError(
                    f"a {self.op.value} op requires an explicit provider and model"
                )
        return self
