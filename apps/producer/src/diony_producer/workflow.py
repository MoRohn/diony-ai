"""Durable, retryable production workflow orchestration."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from diony_contracts.launch import (
    GenerationJob,
    JobStatus,
    WorkflowRun,
    WorkflowStage,
    WorkflowStageState,
)
from diony_producer.control_plane import ControlPlane
from diony_producer.costs import CostAnalyticsService, UsageSample
from diony_producer.observability import log_event
from diony_producer.providers import ProviderPending, ProviderRegistry, ProviderUnavailable


OPTIONAL_STAGES = {WorkflowStage.IMAGE, WorkflowStage.VOICE, WorkflowStage.SOUND}


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class DurableWorkflowEngine:
    """One-step-at-a-time engine safe to resume after process restarts.

    Every state transition is persisted before the next stage is eligible. The
    caller may use a local worker loop, an API action, or an external scheduler;
    repeated `advance` calls are idempotent once a stage succeeds.
    """

    def __init__(self, plane: ControlPlane, registry: ProviderRegistry) -> None:
        self.plane = plane
        self.registry = registry
        self.worker_id = f"worker-{uuid.uuid4().hex}"

    def create_run(
        self,
        *,
        workspace_id: str,
        project_id: str,
        created_by: str,
        stages: list[WorkflowStage] | None = None,
        correlation_id: str = "",
        config: dict[str, Any] | None = None,
    ) -> WorkflowRun:
        selected = stages or list(WorkflowStage.production_order())
        estimates = 0
        for stage in selected:
            try:
                estimates += self.registry.select(stage.capability).spec.internal_credits_per_unit
            except ProviderUnavailable:
                if stage not in OPTIONAL_STAGES:
                    # Preserve the missing capability in the run; advance emits the actionable error.
                    estimates += 0
        run = WorkflowRun(
            id=f"run-{uuid.uuid4().hex}",
            workspace_id=workspace_id,
            project_id=project_id,
            created_by=created_by,
            correlation_id=correlation_id or f"corr-{uuid.uuid4().hex}",
            stages=[WorkflowStageState(stage=stage) for stage in selected],
            forecast_internal_credits=estimates,
            config=config or {},
        )
        self.plane.save_run(run)
        self.plane.append_event(
            workspace_id=workspace_id,
            run_id=run.id,
            project_id=project_id,
            kind="workflow.created",
            payload={
                "message": "Production run created. Every stage, model, cost, and output stays visible.",
                "percent": 0,
                "stages": [state.stage.value for state in run.stages],
                "forecast_internal_credits": estimates,
                "correlation_id": run.correlation_id,
            },
        )
        self.plane.audit(
            workspace_id=workspace_id,
            actor_user_id=created_by,
            action="workflow.created",
            object_type="workflow_run",
            object_id=run.id,
            correlation_id=run.correlation_id,
            after=run,
        )
        return run

    def _run(self, run_id: str, workspace_id: str) -> WorkflowRun:
        return self.plane.run(workspace_id, run_id)

    @staticmethod
    def _next_state(run: WorkflowRun) -> WorkflowStageState | None:
        for state in run.stages:
            if state.status not in {JobStatus.SUCCEEDED, JobStatus.SKIPPED}:
                return state
        return None

    @staticmethod
    def _recompute(run: WorkflowRun) -> None:
        complete = sum(
            1 for state in run.stages if state.status in {JobStatus.SUCCEEDED, JobStatus.SKIPPED}
        )
        run.progress_percent = round((complete / max(1, len(run.stages))) * 100, 2)
        if complete == len(run.stages):
            run.status = JobStatus.SUCCEEDED
            run.progress_percent = 100
            run.current_stage = None
            run.completed_at = run.completed_at or utc_now()

    def _job_for_stage(self, run: WorkflowRun, stage: WorkflowStage) -> GenerationJob | None:
        return next(
            (job for job in self.plane.jobs(run.workspace_id, run.id) if job.stage == stage),
            None,
        )

    def _event(
        self,
        run: WorkflowRun,
        kind: str,
        *,
        message: str,
        stage: WorkflowStage | None = None,
        job: GenerationJob | None = None,
        level: str = "info",
        actions: list[str] | None = None,
        extra: dict[str, Any] | None = None,
    ) -> None:
        payload: dict[str, Any] = {
            "message": message,
            "percent": run.progress_percent,
            "stage": stage.value if stage else None,
            "job_id": job.id if job else None,
            "level": level,
            "actions": actions or [],
            "correlation_id": run.correlation_id,
        }
        payload.update(extra or {})
        self.plane.append_event(
            workspace_id=run.workspace_id,
            run_id=run.id,
            project_id=run.project_id,
            kind=kind,
            payload=payload,
        )
        log_event(
            kind,
            level="warning" if level in {"warning", "error"} else "info",
            correlation_id=run.correlation_id,
            workspace_id=run.workspace_id,
            project_id=run.project_id,
            run_id=run.id,
            stage=stage.value if stage else "",
            job_id=job.id if job else "",
            status=run.status.value,
            message=message,
        )

    def advance(
        self,
        run_id: str,
        *,
        workspace_id: str = "local-workspace",
        now: datetime | None = None,
    ) -> WorkflowRun:
        if not self.plane.claim_run(run_id, owner_id=self.worker_id, lease_seconds=600):
            return self._run(run_id, workspace_id)
        try:
            return self._advance_claimed(run_id, workspace_id=workspace_id, now=now)
        finally:
            self.plane.release_run(run_id, owner_id=self.worker_id)

    def _advance_claimed(
        self,
        run_id: str,
        *,
        workspace_id: str = "local-workspace",
        now: datetime | None = None,
    ) -> WorkflowRun:
        moment = now or utc_now()
        run = self._run(run_id, workspace_id)
        if run.status in {JobStatus.PAUSED, JobStatus.CANCELED, JobStatus.SUCCEEDED}:
            return run
        state = self._next_state(run)
        if state is None:
            self._recompute(run)
            return self.plane.save_run(run)
        if state.status == JobStatus.FAILED:
            return run
        job = self._job_for_stage(run, state.stage)
        if job and job.status == JobStatus.RETRY_WAIT:
            if job.next_attempt_at and moment < job.next_attempt_at:
                run.status = JobStatus.RETRY_WAIT
                return self.plane.save_run(run)
        chain = self.registry.chain(
            state.stage.capability,
            preferred=(job.provider_id if job else None),
            require_local=bool(run.config.get("require_local", True)),
        )
        if not chain:
            if state.stage in OPTIONAL_STAGES:
                state.status = JobStatus.SKIPPED
                state.progress_percent = 100
                state.completed_at = moment
                self._recompute(run)
                if run.status != JobStatus.SUCCEEDED:
                    run.status = JobStatus.QUEUED
                self.plane.save_run(run)
                self._event(
                    run,
                    "workflow.stage.skipped",
                    message=f"{state.stage.value.title()} is optional and no provider is configured. The run keeps moving.",
                    stage=state.stage,
                    level="warning",
                    actions=["configure_provider"],
                )
                return run
            state.status = JobStatus.FAILED
            state.error_code = "PROVIDER_NOT_CONFIGURED"
            state.error_message = (
                f"No {state.stage.capability.value} provider is configured on this signed site."
            )
            run.status = JobStatus.FAILED
            run.current_stage = state.stage
            self.plane.save_run(run)
            self._event(
                run,
                "workflow.stage.failed",
                message=state.error_message,
                stage=state.stage,
                level="error",
                actions=["configure_provider", "retry"],
            )
            return run

        provider = chain[0]
        stage_payload = dict(run.config.get("stage_payloads", {}).get(state.stage.value, {}))
        if job and job.status == JobStatus.WAITING_PROVIDER and not stage_payload.get(
            "checkpoint_complete"
        ):
            return run
        if job is None:
            job = GenerationJob(
                id=f"job-{run.id}-{state.stage.value}",
                run_id=run.id,
                workspace_id=run.workspace_id,
                project_id=run.project_id,
                stage=state.stage,
                capability=state.stage.capability,
                provider_id=provider.spec.id,
                model=provider.spec.models[0] if provider.spec.models else "",
                idempotency_key=f"{run.id}:{state.stage.value}:v1",
                forecast_internal_credits=provider.spec.internal_credits_per_unit,
                input_lineage=list(run.config.get("input_lineage") or [run.project_id]),
            )
            self.plane.save_job(job)
            state.job_ids.append(job.id)

        job.attempt += 1
        job.status = JobStatus.RUNNING
        job.started_at = job.started_at or moment
        job.progress_percent = max(job.progress_percent, 1)
        job.next_attempt_at = None
        state.status = JobStatus.RUNNING
        state.started_at = state.started_at or moment
        state.progress_percent = max(state.progress_percent, 1)
        run.status = JobStatus.RUNNING
        run.current_stage = state.stage
        run.started_at = run.started_at or moment
        self.plane.save_job(job)
        self.plane.save_run(run)
        self._event(
            run,
            "workflow.stage.started",
            message=f"{state.stage.value.title()} started on {job.provider_id}.",
            stage=state.stage,
            job=job,
            level="live",
            extra={"provider_id": job.provider_id, "model": job.model, "attempt": job.attempt},
        )

        error: Exception | None = None
        pending: ProviderPending | None = None
        result = None
        for candidate in chain:
            job.provider_id = candidate.spec.id
            job.model = candidate.spec.models[0] if candidate.spec.models else job.model
            try:
                result = candidate.invoke(job, stage_payload)
                provider = candidate
                error = None
                break
            except ProviderPending as exc:
                pending = exc
                error = None
                break
            except Exception as exc:  # noqa: BLE001
                error = exc
                self._event(
                    run,
                    "workflow.provider.failed",
                    message=f"{candidate.spec.label} could not finish this attempt: {exc}",
                    stage=state.stage,
                    job=job,
                    level="warning",
                    actions=["retry", "switch_provider"],
                    extra={"provider_id": candidate.spec.id},
                )

        if pending is not None:
            job.attempt = max(0, job.attempt - 1)
            job.status = JobStatus.WAITING_PROVIDER
            job.progress_percent = max(job.progress_percent, 1)
            job.error_code = "ENGINE_CHECKPOINT_PENDING"
            job.error_message = str(pending)
            state.status = JobStatus.WAITING_PROVIDER
            state.error_code = job.error_code
            state.error_message = job.error_message
            run.status = JobStatus.WAITING_PROVIDER
            self.plane.save_job(job)
            self.plane.save_run(run)
            self._event(
                run,
                "workflow.stage.waiting_provider",
                message=f"{state.stage.value.title()} is waiting for signed-site engine proof. No output was invented.",
                stage=state.stage,
                job=job,
                level="live",
                actions=["attach_checkpoint", "cancel"],
            )
            return run

        if result is None:
            job.error_code = "PROVIDER_ATTEMPT_FAILED"
            job.error_message = str(error or "Provider failed")[:500]
            state.error_code = job.error_code
            state.error_message = job.error_message
            if job.attempt < job.max_attempts:
                delay = min(300, 5 * (2 ** max(0, job.attempt - 1)))
                job.status = JobStatus.RETRY_WAIT
                job.next_attempt_at = moment + timedelta(seconds=delay)
                state.status = JobStatus.RETRY_WAIT
                run.status = JobStatus.RETRY_WAIT
                self._event(
                    run,
                    "workflow.stage.retry_wait",
                    message=f"{state.stage.value.title()} will retry in {delay}s. Nothing completed will repeat.",
                    stage=state.stage,
                    job=job,
                    level="warning",
                    actions=["retry_now", "edit_prompt", "switch_provider", "cancel"],
                    extra={"retry_at": job.next_attempt_at.isoformat()},
                )
            else:
                job.status = JobStatus.FAILED
                job.completed_at = moment
                state.status = JobStatus.FAILED
                state.completed_at = moment
                run.status = JobStatus.FAILED
                self._event(
                    run,
                    "workflow.stage.failed",
                    message=f"{state.stage.value.title()} stopped after {job.attempt} attempts: {job.error_message}",
                    stage=state.stage,
                    job=job,
                    level="error",
                    actions=["retry", "edit_prompt", "switch_provider"],
                )
            self.plane.save_job(job)
            return self.plane.save_run(run)

        job.status = JobStatus.SUCCEEDED
        job.provider_id = provider.spec.id
        job.external_job_id = result.external_job_id
        job.output_asset_ids = result.output_asset_ids
        job.actual_cost_usd_micros = (
            result.cost_breakdown.total_usd_micros if result.cost_breakdown else 0
        )
        job.actual_internal_credits = result.internal_credits_used or 0
        job.progress_percent = 100
        job.completed_at = moment
        job.metadata.update(result.metadata)
        job.error_code = ""
        job.error_message = ""
        state.status = JobStatus.SUCCEEDED
        state.progress_percent = 100
        state.output_asset_ids = list(result.output_asset_ids)
        state.error_code = ""
        state.error_message = ""
        state.completed_at = moment
        recorded = CostAnalyticsService(self.plane).record(UsageSample(
            id=f"usage-{job.id}",
            workspace_id=run.workspace_id,
            project_id=run.project_id,
            run_id=run.id,
            job_id=job.id,
            capability=job.capability,
            provider_id=job.provider_id,
            model=job.model,
            quantity_microunits=int(stage_payload.get("quantity_microunits") or 1_000_000),
            unit=str(stage_payload.get("unit") or "stage"),
            cost_center_id=str(run.config.get("cost_center_id") or ""),
            department_id=str(run.config.get("department_id") or ""),
            team_id=str(run.config.get("team_id") or ""),
            actual_internal_credits=result.internal_credits_used,
            actual_cost_breakdown=result.cost_breakdown,
            metadata={"run_id": run.id, **result.metadata},
        ))
        self.plane.save_job(job)
        self._recompute(run)
        if run.status != JobStatus.SUCCEEDED:
            run.status = JobStatus.QUEUED
            run.current_stage = None
        self.plane.save_run(run)
        self._event(
            run,
            "workflow.stage.succeeded",
            message=f"{state.stage.value.title()} is checkpointed. Lineage and cost are recorded.",
            stage=state.stage,
            job=job,
            level="success",
            extra={
                "provider_id": job.provider_id,
                "model": job.model,
                "output_asset_ids": job.output_asset_ids,
                "internal_credits": recorded.usage.internal_credits,
                "cost_usd_micros": recorded.usage.cost_breakdown.total_usd_micros,
                "cost_center_id": recorded.usage.cost_center_id,
            },
        )
        if run.status == JobStatus.SUCCEEDED:
            self._event(
                run,
                "workflow.succeeded",
                message="Production is complete. The portable package and full lineage belong to this workspace.",
                level="success",
            )
        return run

    def pause(
        self, run_id: str, *, actor_user_id: str, workspace_id: str = "local-workspace"
    ) -> WorkflowRun:
        run = self._run(run_id, workspace_id)
        if run.status.terminal:
            return run
        before = run.model_dump(mode="json")
        run.status = JobStatus.PAUSED
        self.plane.save_run(run)
        self._event(run, "workflow.paused", message="Production paused at a durable checkpoint.")
        self.plane.audit(
            workspace_id=workspace_id,
            actor_user_id=actor_user_id,
            action="workflow.paused",
            object_type="workflow_run",
            object_id=run.id,
            correlation_id=run.correlation_id,
            before=before,
            after=run,
        )
        return run

    def resume(
        self, run_id: str, *, actor_user_id: str, workspace_id: str = "local-workspace"
    ) -> WorkflowRun:
        run = self._run(run_id, workspace_id)
        if run.status != JobStatus.PAUSED:
            return run
        before = run.model_dump(mode="json")
        run.status = JobStatus.QUEUED
        self.plane.save_run(run)
        self._event(
            run,
            "workflow.resumed",
            message="Production resumed. Completed stages will not run twice.",
        )
        self.plane.audit(
            workspace_id=workspace_id,
            actor_user_id=actor_user_id,
            action="workflow.resumed",
            object_type="workflow_run",
            object_id=run.id,
            correlation_id=run.correlation_id,
            before=before,
            after=run,
        )
        return run

    def cancel(
        self, run_id: str, *, actor_user_id: str, workspace_id: str = "local-workspace"
    ) -> WorkflowRun:
        run = self._run(run_id, workspace_id)
        if run.status == JobStatus.CANCELED:
            return run
        before = run.model_dump(mode="json")
        for job in self.plane.jobs(workspace_id, run.id):
            if job.status.terminal:
                continue
            if job.external_job_id:
                try:
                    self.registry.get(job.provider_id).cancel(job.external_job_id)
                except ProviderUnavailable:
                    pass
            job.status = JobStatus.CANCELED
            job.completed_at = utc_now()
            self.plane.save_job(job)
        run.status = JobStatus.CANCELED
        run.completed_at = utc_now()
        self.plane.save_run(run)
        self._event(
            run,
            "workflow.canceled",
            message="Production canceled. Completed assets and lineage remain yours.",
            level="warning",
        )
        self.plane.audit(
            workspace_id=workspace_id,
            actor_user_id=actor_user_id,
            action="workflow.canceled",
            object_type="workflow_run",
            object_id=run.id,
            correlation_id=run.correlation_id,
            before=before,
            after=run,
        )
        return run

    def retry(
        self, run_id: str, *, actor_user_id: str, workspace_id: str = "local-workspace"
    ) -> WorkflowRun:
        run = self._run(run_id, workspace_id)
        state = self._next_state(run)
        if state is None:
            return run
        before = run.model_dump(mode="json")
        job = self._job_for_stage(run, state.stage)
        if job:
            job.status = JobStatus.RETRY_WAIT
            job.next_attempt_at = utc_now()
            job.error_code = ""
            job.error_message = ""
            self.plane.save_job(job)
        state.status = JobStatus.RETRY_WAIT
        state.error_code = ""
        state.error_message = ""
        run.status = JobStatus.RETRY_WAIT
        self.plane.save_run(run)
        self._event(
            run,
            "workflow.retry.requested",
            message=f"{state.stage.value.title()} is ready to retry from its last checkpoint.",
            stage=state.stage,
        )
        self.plane.audit(
            workspace_id=workspace_id,
            actor_user_id=actor_user_id,
            action="workflow.retry_requested",
            object_type="workflow_run",
            object_id=run.id,
            correlation_id=run.correlation_id,
            before=before,
            after=run,
        )
        return run
