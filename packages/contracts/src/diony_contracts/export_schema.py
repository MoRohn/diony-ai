"""Export every contract's JSON Schema to ``packages/contracts/jsonschema``.

``diony-export-schemas`` regenerates the committed documents; the schema sync
test refuses a model change that forgot to.
"""

from __future__ import annotations

import json
from pathlib import Path

from diony_contracts.director import CostEstimate, DirectorOp
from diony_contracts.launch import (
    AuditEvent,
    BootstrapContext,
    CostAllocationEntry,
    CostBreakdown,
    CostCenter,
    CostGovernance,
    CostRateCard,
    Department,
    GenerationJob,
    InvitationEmailDelivery,
    Membership,
    ProgressEvent,
    ProviderConfig,
    RenderVersion,
    StoredEvent,
    StudioInvitation,
    StudioJoinRequest,
    Team,
    UsageEvent,
    User,
    WorkflowRun,
    Workspace,
)
from diony_contracts.production import (
    AssemblyMap,
    CharacterProfile,
    ClipReview,
    ClipUnit,
    IdentityCard,
    LookBible,
    MasterPrompt,
    ProductionPackage,
    Script,
    SettingProfile,
    VoiceProfile,
)


def schema_root() -> Path:
    return Path(__file__).resolve().parent.parent.parent


def exported_models() -> dict[str, type]:
    return {
        "user": User,
        "workspace": Workspace,
        "team": Team,
        "department": Department,
        "cost_center": CostCenter,
        "cost_governance": CostGovernance,
        "cost_breakdown": CostBreakdown,
        "cost_rate_card": CostRateCard,
        "membership": Membership,
        "studio_invitation": StudioInvitation,
        "invitation_email_delivery": InvitationEmailDelivery,
        "studio_join_request": StudioJoinRequest,
        "workflow_run": WorkflowRun,
        "generation_job": GenerationJob,
        "progress_event": ProgressEvent,
        "usage_event": UsageEvent,
        "cost_allocation_entry": CostAllocationEntry,
        "audit_event": AuditEvent,
        "provider_config": ProviderConfig,
        "render_version": RenderVersion,
        "stored_event": StoredEvent,
        "bootstrap_context": BootstrapContext,
        "director_op": DirectorOp,
        "cost_estimate": CostEstimate,
        "script": Script,
        "character_profile": CharacterProfile,
        "identity_card": IdentityCard,
        "setting_profile": SettingProfile,
        "voice_profile": VoiceProfile,
        "clip_unit": ClipUnit,
        "clip_review": ClipReview,
        "look_bible": LookBible,
        "master_prompt": MasterPrompt,
        "assembly_map": AssemblyMap,
        "production_package": ProductionPackage,
    }


def schema_documents() -> dict[str, dict]:
    """Every exported contract's JSON Schema, keyed by file stem."""
    return {name: model.model_json_schema() for name, model in exported_models().items()}


def write_schemas(out: Path) -> list[Path]:
    out.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for name, schema in schema_documents().items():
        path = out / f"{name}.schema.json"
        path.write_text(json.dumps(schema, indent=2) + "\n")
        written.append(path)
    return written


def main() -> None:
    for path in write_schemas(schema_root() / "jsonschema"):
        print(path)
