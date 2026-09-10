from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from diony_contracts.launch import (
    AuditEvent,
    CostAllocationEntry,
    CostBreakdown,
    CostCenter,
    CostGovernance,
    CostRateCard,
    Department,
    GenerationJob,
    JobStatus,
    ProgressEvent,
    ProviderCapability,
    RenderVersion,
    UsageEvent,
    WorkflowRun,
    WorkflowStage,
    WorkflowStageState,
)


def test_generation_job_carries_cost_forecast_lineage_and_retry_state() -> None:
    job = GenerationJob(
        id="job-video-1",
        run_id="run-1",
        workspace_id="ws-studio",
        project_id="project-1",
        stage=WorkflowStage.VIDEO,
        capability=ProviderCapability.VIDEO,
        provider_id="diony.wan22",
        model="Wan2.2-T2V-A14B",
        idempotency_key="project-1:shot-10:video:v1",
        forecast_internal_credits=120,
        input_lineage=["shot-10", "prompt-v3", "still-lead-v2"],
    )
    assert job.status == JobStatus.QUEUED
    assert job.attempt == 0
    assert job.max_attempts >= 1
    assert job.provider_id == "diony.wan22"
    assert job.forecast_internal_credits == 120
    assert job.input_lineage[-1] == "still-lead-v2"


def test_workflow_requires_one_ordered_state_per_stage() -> None:
    run = WorkflowRun.production_default(
        id="run-1",
        workspace_id="ws-studio",
        project_id="project-1",
        created_by="usr-rohn",
    )
    assert [state.stage for state in run.stages] == list(WorkflowStage.production_order())
    assert run.stages[0].status == JobStatus.QUEUED
    assert all(isinstance(state, WorkflowStageState) for state in run.stages)
    assert run.progress_percent == 0
    order = [state.stage for state in run.stages]
    assert order.index(WorkflowStage.SAFETY_INPUT) < order.index(WorkflowStage.VIDEO)
    assert order.index(WorkflowStage.SAFETY_OUTPUT) < order.index(WorkflowStage.PACKAGE)


def test_progress_percentage_is_bounded_and_errors_are_actionable() -> None:
    with pytest.raises(ValidationError):
        ProgressEvent(
            id="evt-bad",
            run_id="run-1",
            workspace_id="ws-studio",
            project_id="project-1",
            stage=WorkflowStage.VIDEO,
            percent=101,
            message="Too far",
        )
    event = ProgressEvent(
        id="evt-fail",
        run_id="run-1",
        workspace_id="ws-studio",
        project_id="project-1",
        stage=WorkflowStage.VIDEO,
        percent=42,
        message="Wan lost the GPU worker.",
        level="error",
        actions=["retry", "edit_prompt", "switch_provider"],
    )
    assert "retry" in event.actions


def test_usage_cost_allocation_audit_and_render_models_are_immutable_records() -> None:
    now = datetime.now(UTC)
    usage = UsageEvent(
        id="use-1",
        workspace_id="ws-studio",
        project_id="project-1",
        run_id="run-1",
        job_id="job-video-1",
        capability=ProviderCapability.VIDEO,
        provider_id="diony.wan22",
        model="Wan2.2-T2V-A14B",
        quantity_microunits=5_000_000,
        unit="video_second",
        department_id="dept-picture",
        team_id="team-camera",
        cost_center_id="cc-show-1",
        internal_credits=120,
        cost_breakdown=CostBreakdown(compute_usd_micros=140_000),
        occurred_at=now,
    )
    allocation = CostAllocationEntry(
        id="allocation-1",
        workspace_id="ws-studio",
        usage_event_id=usage.id,
        project_id=usage.project_id,
        job_id=usage.job_id,
        department_id=usage.department_id,
        team_id=usage.team_id,
        cost_center_id=usage.cost_center_id,
        internal_credits=usage.internal_credits,
        cost_usd_micros=usage.cost_breakdown.total_usd_micros,
    )
    audit = AuditEvent(
        id="aud-1",
        workspace_id="ws-studio",
        actor_user_id="usr-rohn",
        action="workflow.job.cost_allocated",
        object_type="generation_job",
        object_id=usage.job_id,
        correlation_id="corr-1",
    )
    render = RenderVersion(
        id="render-1",
        workspace_id="ws-studio",
        project_id="project-1",
        version=1,
        parent_version_id=None,
        asset_ids=["master-1080"],
        source_job_ids=[usage.job_id],
        source_take_ids=["take-1"],
        sha256="a" * 64,
    )
    assert allocation.internal_credits == 120
    assert allocation.cost_usd_micros == 140_000
    assert audit.correlation_id == "corr-1"
    assert render.source_job_ids == ["job-video-1"]


def test_cost_governance_models_use_fixed_point_budgets_and_explicit_admin_scope() -> None:
    department = Department(
        id="dept-picture",
        workspace_id="ws-studio",
        name="Picture",
        code="PIC",
        manager_user_ids=["usr-producer"],
        team_ids=["team-camera"],
    )
    center = CostCenter(
        id="cc-show-1",
        workspace_id="ws-studio",
        department_id=department.id,
        name="Show One",
        code="SHOW-001",
        team_ids=["team-camera"],
        internal_credit_budget=10_000,
        cost_budget_usd_micros=25_000_000,
    )
    governance = CostGovernance(
        id="cost-governance",
        workspace_id="ws-studio",
        admin_user_id="usr-admin",
        admin_team_id="team-finops",
        default_cost_center_id=center.id,
    )
    rate = CostRateCard(
        id="rate-wan-v1",
        workspace_id="ws-studio",
        capability=ProviderCapability.VIDEO,
        provider_id="diony.wan22",
        model="Wan2.2-T2V-A14B",
        unit="video_second",
        internal_credits_per_unit=25,
        cost_usd_micros_per_unit=80_000,
        source="Leased GPU amortization plus energy",
    )
    assert governance.admin_team_id == "team-finops"
    assert center.monitoring_mode == "monitor_only"
    assert rate.cost_usd_micros_per_unit == 80_000
