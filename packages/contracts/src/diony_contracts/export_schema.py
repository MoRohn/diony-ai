from __future__ import annotations

import json
from pathlib import Path

from diony_contracts.fixture import demo_project
from diony_contracts.models import (
    Asset,
    Bible,
    Brief,
    ClipEdit,
    ClipComposition,
    ClusterSite,
    Deliverable,
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
)
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


def main() -> None:
    root = Path(__file__).resolve().parent.parent.parent
    out = root / "jsonschema"
    out.mkdir(parents=True, exist_ok=True)
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
        "qc_report": QCReport,
        "timeline": Timeline,
        "wan_prompt_plan": WanPromptPlan,
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
    }
    for name, model in models.items():
        path = out / f"{name}.schema.json"
        path.write_text(json.dumps(model.model_json_schema(), indent=2) + "\n")
        print(path)
    examples = root / "examples"
    examples.mkdir(parents=True, exist_ok=True)
    pilot = examples / "pilot.json"
    pilot.write_text(json.dumps(demo_project().model_dump(mode="json"), indent=2) + "\n")
    print(pilot)


if __name__ == "__main__":
    main()
