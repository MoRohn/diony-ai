from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from diony_contracts.launch import CostBreakdown, JobStatus, ProviderCapability, WorkflowStage
from diony_producer.control_plane import ControlPlane
from diony_producer.providers import ProviderRegistry, ProviderResult, ProviderSpec
from diony_producer.worker import run_once
from diony_producer.workflow import DurableWorkflowEngine


class RecordingProvider:
    def __init__(
        self, provider_id: str, capability: ProviderCapability, fail_times: int = 0
    ) -> None:
        self.spec = ProviderSpec(
            id=provider_id,
            label=provider_id,
            capabilities=[capability],
            models=["test-model"],
            local=True,
            async_provider=False,
            enabled=True,
            priority=10,
            internal_credits_per_unit=10,
        )
        self.fail_times = fail_times
        self.calls = 0

    def invoke(self, job, payload):
        self.calls += 1
        if self.calls <= self.fail_times:
            raise RuntimeError("worker warming up")
        return ProviderResult(
            external_job_id=f"external-{job.id}",
            output_asset_ids=[f"asset-{job.id}"],
            cost_breakdown=CostBreakdown(compute_usd_micros=25_000),
            internal_credits_used=10,
            metadata={"transparent": True},
        )

    def cancel(self, external_job_id: str) -> None:
        return None


def test_registry_prefers_local_and_uses_explicit_fallback_chain() -> None:
    registry = ProviderRegistry()
    primary = RecordingProvider("local.primary", ProviderCapability.LLM)
    fallback = RecordingProvider("local.fallback", ProviderCapability.LLM)
    fallback.spec.priority = 20
    registry.register(primary)
    registry.register(fallback)
    selected = registry.select(ProviderCapability.LLM)
    assert selected.spec.id == "local.primary"
    chain = registry.chain(ProviderCapability.LLM, preferred="local.primary")
    assert [provider.spec.id for provider in chain] == ["local.primary", "local.fallback"]


def test_durable_workflow_retries_resumes_and_never_repeats_completed_stage(tmp_path: Path) -> None:
    plane = ControlPlane(tmp_path / "control.sqlite3")
    plane.bootstrap_local()
    registry = ProviderRegistry()
    provider = RecordingProvider("local.llm", ProviderCapability.LLM, fail_times=1)
    registry.register(provider)
    engine = DurableWorkflowEngine(plane, registry)
    run = engine.create_run(
        workspace_id="local-workspace",
        project_id="project-1",
        created_by="local-user",
        stages=[WorkflowStage.INTAKE],
    )
    first = engine.advance(run.id, now=datetime.now(UTC))
    assert first.status == JobStatus.RETRY_WAIT
    retry_job = plane.jobs("local-workspace", run.id)[0]
    assert retry_job.attempt == 1
    due = (retry_job.next_attempt_at or datetime.now(UTC)) + timedelta(seconds=1)
    second = engine.advance(run.id, now=due)
    assert second.status == JobStatus.SUCCEEDED
    third = engine.advance(run.id, now=due + timedelta(seconds=1))
    assert third.status == JobStatus.SUCCEEDED
    assert provider.calls == 2
    assert plane.usage("local-workspace", project_id="project-1")[0].internal_credits == 10
    assert (
        plane.usage("local-workspace", project_id="project-1")[0].cost_center_id
        == "local-production"
    )


def test_pause_resume_cancel_and_progress_events_are_durable(tmp_path: Path) -> None:
    plane = ControlPlane(tmp_path / "control.sqlite3")
    plane.bootstrap_local()
    registry = ProviderRegistry.with_diony_defaults()
    engine = DurableWorkflowEngine(plane, registry)
    run = engine.create_run(
        workspace_id="local-workspace",
        project_id="project-1",
        created_by="local-user",
        stages=[WorkflowStage.INTAKE, WorkflowStage.SCRIPT],
    )
    paused = engine.pause(run.id, actor_user_id="local-user")
    assert paused.status == JobStatus.PAUSED
    assert engine.advance(run.id).status == JobStatus.PAUSED
    resumed = engine.resume(run.id, actor_user_id="local-user")
    assert resumed.status == JobStatus.QUEUED
    canceled = engine.cancel(run.id, actor_user_id="local-user")
    assert canceled.status == JobStatus.CANCELED
    events = plane.events("local-workspace", run.id)
    assert [event.kind for event in events][-3:] == [
        "workflow.paused",
        "workflow.resumed",
        "workflow.canceled",
    ]


def test_worker_advances_queued_runs_one_durable_checkpoint(tmp_path: Path) -> None:
    plane = ControlPlane(tmp_path / "control.sqlite3")
    plane.bootstrap_local()
    registry = ProviderRegistry.with_diony_defaults()
    run = DurableWorkflowEngine(plane, registry).create_run(
        workspace_id="local-workspace",
        project_id="project-worker",
        created_by="local-user",
        stages=[WorkflowStage.INTAKE, WorkflowStage.SCRIPT],
        config={
            "stage_payloads": {
                "intake": {"checkpoint_complete": True, "metadata": {"source": "brain"}}
            }
        },
    )
    assert run_once(plane, registry, workspace_id="local-workspace") == 1
    updated = plane.run("local-workspace", run.id)
    assert updated.stages[0].status == JobStatus.SUCCEEDED
    assert updated.stages[1].status == JobStatus.QUEUED


def test_local_checkpoint_provider_never_claims_unproved_engine_work(tmp_path: Path) -> None:
    plane = ControlPlane(tmp_path / "control.sqlite3")
    plane.bootstrap_local()
    engine = DurableWorkflowEngine(plane, ProviderRegistry.with_diony_defaults())
    run = engine.create_run(
        workspace_id="local-workspace",
        project_id="project-proof",
        created_by="local-user",
        stages=[WorkflowStage.VIDEO],
    )
    waiting = engine.advance(run.id)
    assert waiting.status == JobStatus.WAITING_PROVIDER
    assert plane.jobs("local-workspace", run.id)[0].output_asset_ids == []
