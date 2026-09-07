from __future__ import annotations

import json
from pathlib import Path

from diony_contracts.director import CostEstimate, DirectorOp
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
from diony_contracts.fixture import demo_project
from diony_contracts.models import (
    Asset,
    Bible,
    Brief,
    ClipEdit,
    ClipComposition,
    ClusterSite,
    Deliverable,
    GenerationExecutionReceipt,
    MixLock,
    Note,
    Project,
    ProductionInfrastructureRecovery,
    ProductionPauseReceipt,
    ProductionRestartReceipt,
    ProductionStopReceipt,
    QCReport,
    Shot,
    Take,
    Timeline,
    WanPromptPlan,
    SpeechClipPackage,
    SpeechQCReceipt,
    VoiceCasting,
    WanSpeechRequest,
)
from diony_contracts.montage import Decision, FinalReview, ProductionSizing, RundownReview
from diony_contracts.score import BeatGrid, ScoreAlignment
from diony_contracts.edl import EditDecisionList
from diony_contracts.launch import (
    InvitationEmailDelivery,
    AuditEvent,
    BootstrapContext,
    CostAllocationEntry,
    CostBreakdown,
    CostCenter,
    CostGovernance,
    CostRateCard,
    Department,
    GenerationJob,
    Membership,
    ProgressEvent,
    ProviderConfig,
    RenderVersion,
    StudioInvitation,
    StudioJoinRequest,
    StoredEvent,
    Team,
    UsageEvent,
    User,
    WorkflowRun,
    Workspace,
)


def schema_root() -> Path:
    return Path(__file__).resolve().parent.parent.parent


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
    root = schema_root()
    for path in write_schemas(root / "jsonschema"):
        print(path)
    examples = root / "examples"
    examples.mkdir(parents=True, exist_ok=True)
    pilot = examples / "pilot.json"
    pilot.write_text(json.dumps(demo_project().model_dump(mode="json"), indent=2) + "\n")
    print(pilot)


def exported_models() -> dict[str, type]:
    models = {
        "project": Project,
        "production_pause_receipt": ProductionPauseReceipt,
        "production_infrastructure_recovery": ProductionInfrastructureRecovery,
        "production_restart_receipt": ProductionRestartReceipt,
        "production_stop_receipt": ProductionStopReceipt,
        "bible": Bible,
        "brief": Brief,
        "asset": Asset,
        "clip_edit": ClipEdit,
        "clip_composition": ClipComposition,
        "cluster_site": ClusterSite,
        "shot": Shot,
        "take": Take,
        "generation_execution_receipt": GenerationExecutionReceipt,
        "qc_report": QCReport,
        "timeline": Timeline,
        "wan_prompt_plan": WanPromptPlan,
        "voice_casting": VoiceCasting,
        "wan_speech_request": WanSpeechRequest,
        "speech_clip_package": SpeechClipPackage,
        "speech_qc_receipt": SpeechQCReceipt,
        "mix_lock": MixLock,
        "deliverable": Deliverable,
        "note": Note,
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
        "bootstrap_context": BootstrapContext,
        "workflow_run": WorkflowRun,
        "generation_job": GenerationJob,
        "progress_event": ProgressEvent,
        "usage_event": UsageEvent,
        "cost_allocation_entry": CostAllocationEntry,
        "audit_event": AuditEvent,
        "provider_config": ProviderConfig,
        "render_version": RenderVersion,
        "stored_event": StoredEvent,
        "decision": Decision,
        "rundown_review": RundownReview,
        "final_review": FinalReview,
        "production_sizing": ProductionSizing,
        "beat_grid": BeatGrid,
        "score_alignment": ScoreAlignment,
        "edit_decision_list": EditDecisionList,
        "cost_estimate": CostEstimate,
        "director_op": DirectorOp,
        "assembly_map": AssemblyMap,
        "character_profile": CharacterProfile,
        "clip_review": ClipReview,
        "clip_unit": ClipUnit,
        "production_package": ProductionPackage,
        "master_prompt": MasterPrompt,
        "look_bible": LookBible,
        "identity_card": IdentityCard,
        "script": Script,
        "setting_profile": SettingProfile,
        "voice_profile": VoiceProfile,
    }
    return models


if __name__ == "__main__":
    main()
