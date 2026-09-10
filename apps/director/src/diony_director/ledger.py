"""One ledger, no private math.

The Director writes and reads the same ``CostAnalyticsService`` store the UI
renders. Estimates commit on submit (as job forecasts), actuals reconcile on
completion, and a failed job that burned money stays visible — never hidden.
"""

from __future__ import annotations

import functools
import threading
import uuid
from datetime import UTC, datetime

from diony_contracts.director import CloudProvider, DirectorOp
from diony_contracts.launch import (
    CostBreakdown,
    GenerationJob,
    JobStatus,
    ProviderCapability,
    WorkflowRun,
    WorkflowStage,
)
from diony_director import ratecards
from diony_producer.control_plane import ControlPlane, DocumentNotFound
from diony_producer.costs import CostAnalyticsService, UsageSample

DIRECTOR_PROJECT_ID = "director"

# The advance endpoint and the batch-submitter worker hit the control plane
# concurrently. Two same-process sqlite connections on that file intermittently
# wedged each other for the full 30s busy timeout per call (faulthandler dumps:
# one thread mid-commit_job, the other mid-reconcile_completion, both stalled
# inside sqlite). Every ledger call is milliseconds of local I/O, so one
# process-wide lock removes the contention outright; cross-process safety is
# still sqlite's own locking.
_PLANE_LOCK = threading.RLock()


def _serialized(fn):
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        with _PLANE_LOCK:
            return fn(*args, **kwargs)

    return wrapper


@_serialized
def seed_rate_cards(plane: ControlPlane, workspace_id: str) -> int:
    service = CostAnalyticsService(plane)
    cards = ratecards.contract_rate_cards(workspace_id)
    for card in cards:
        service.save_rate_card(card)
    return len(cards)


@_serialized
def ensure_run(
    plane: ControlPlane, workspace_id: str, created_by: str, session_id: str
) -> WorkflowRun:
    run_id = f"director-{session_id}"
    try:
        return plane.run(workspace_id, run_id)
    except DocumentNotFound:
        run = WorkflowRun.production_default(
            id=run_id,
            workspace_id=workspace_id,
            project_id=DIRECTOR_PROJECT_ID,
            created_by=created_by,
        )
        return plane.save_run(run)


@_serialized
def commit_job(
    plane: ControlPlane,
    workspace_id: str,
    created_by: str,
    op: DirectorOp,
    external_job_id: str,
) -> GenerationJob:
    run = ensure_run(plane, workspace_id, created_by, op.session_id)
    estimate = op.estimate
    forecast_credits = sum(line.internal_credits for line in estimate.lines) if estimate else 0
    job = GenerationJob(
        id=f"job-{uuid.uuid4().hex[:12]}",
        run_id=run.id,
        workspace_id=workspace_id,
        project_id=op.project_id or DIRECTOR_PROJECT_ID,
        stage=WorkflowStage.VIDEO,
        capability=ProviderCapability.VIDEO,
        provider_id=op.provider.value if op.provider else "",
        model=op.model,
        status=JobStatus.RUNNING,
        idempotency_key=op.id,
        external_job_id=external_job_id,
        forecast_internal_credits=forecast_credits,
        metadata={
            "op_id": op.id,
            "estimate_usd_micros": estimate.total_usd_micros if estimate else 0,
            "resolution": op.resolution,
            "duration_s": op.duration_s,
        },
    )
    return plane.save_job(job)


def _grok_actuals(op: DirectorOp, seconds: int) -> tuple[CostBreakdown, int, str]:
    """Metered actuals from what the request really carried: output seconds,
    plus the image inputs that actually attached at submit, plus input-video
    seconds on edit/extend — never a paper figure copied off the estimate."""
    files = ratecards.load_files()
    rate = ratecards.resolve(files, CloudProvider.GROK.value, op.model, f"second:{op.resolution}")
    usd = seconds * rate.usd_micros_per_unit
    refs = op.artifact_refs or {}
    image_count = len(refs.get("references") or []) or (1 if refs.get("start_image") else 0)
    if image_count:
        img = ratecards.resolve(files, CloudProvider.GROK.value, op.model, "image_input")
        usd += image_count * img.usd_micros_per_unit
    if refs.get("source_video"):
        vid = ratecards.resolve(files, CloudProvider.GROK.value, op.model, "video_input_second")
        usd += int(op.duration_s or 0) * vid.usd_micros_per_unit
    return CostBreakdown(compute_usd_micros=usd), 0, rate.unit


def _flow_actuals(op: DirectorOp, credits_used: int) -> tuple[CostBreakdown, int, str]:
    files = ratecards.load_files()
    per_credit = ratecards.usd_per_credit(files[ratecards.FLOW_PROVIDER_ID])
    captcha = ratecards.captcha_usd_micros(files)
    unit = op.estimate.lines[0].unit if op.estimate and op.estimate.lines else "generation"
    breakdown = CostBreakdown(
        compute_usd_micros=credits_used * per_credit,
        other_usd_micros=captcha,
    )
    return breakdown, credits_used, unit


def _image_actuals(op: DirectorOp) -> tuple[CostBreakdown, int, str]:
    files = ratecards.load_files()
    if op.provider is CloudProvider.GROK:
        unit = f"generation:{op.resolution}" if op.resolution in {"1k", "2k"} else "generation:2k"
        rate = ratecards.resolve(files, CloudProvider.GROK.value, op.model, unit)
    else:
        rate = ratecards.resolve(files, ratecards.FLOW_PROVIDER_ID, op.model, "generation")
    return (
        CostBreakdown(compute_usd_micros=rate.usd_micros_per_unit),
        rate.internal_credits_per_unit,
        rate.unit,
    )


@_serialized
def reconcile_completion(
    plane: ControlPlane,
    workspace_id: str,
    job: GenerationJob,
    op: DirectorOp,
    *,
    actual_duration_s: int | None = None,
    credits_used: int | None = None,
) -> GenerationJob:
    from diony_contracts.director import CloudGenerationMode

    if op.mode is CloudGenerationMode.IMAGE:
        breakdown, credits, unit = _image_actuals(op)
        quantity = 1_000_000
        basis = "metered" if op.provider is CloudProvider.GROK else "modeled_from_plan"
    elif op.provider is CloudProvider.GROK:
        seconds = int(actual_duration_s or op.duration_s or 1)
        breakdown, credits, unit = _grok_actuals(op, seconds)
        quantity = seconds * 1_000_000
        basis = "metered"
    else:
        credits = int(credits_used or job.forecast_internal_credits)
        breakdown, credits, unit = _flow_actuals(op, credits)
        quantity = 1_000_000
        basis = "modeled_from_plan"

    recorded = CostAnalyticsService(plane).record(
        UsageSample(
            id=f"usage-{job.id}",
            workspace_id=workspace_id,
            project_id=job.project_id,
            run_id=job.run_id,
            job_id=job.id,
            capability=ProviderCapability.VIDEO,
            provider_id=job.provider_id,
            model=job.model,
            quantity_microunits=quantity,
            unit=unit,
            actual_internal_credits=credits,
            actual_cost_breakdown=breakdown,
            metadata={"cost_basis": basis, "op_id": op.id, "external_job_id": job.external_job_id},
        )
    )
    job.status = JobStatus.SUCCEEDED
    job.actual_internal_credits = recorded.usage.internal_credits
    job.actual_cost_usd_micros = recorded.usage.cost_breakdown.total_usd_micros
    job.completed_at = datetime.now(UTC)
    return plane.save_job(job)


@_serialized
def record_failure(
    plane: ControlPlane,
    workspace_id: str,
    job: GenerationJob,
    op: DirectorOp,
    detail: str,
    *,
    credits_burned: int = 0,
) -> GenerationJob:
    if credits_burned > 0:
        breakdown, credits, unit = _flow_actuals(op, credits_burned)
        CostAnalyticsService(plane).record(
            UsageSample(
                id=f"usage-{job.id}",
                workspace_id=workspace_id,
                project_id=job.project_id,
                run_id=job.run_id,
                job_id=job.id,
                capability=ProviderCapability.VIDEO,
                provider_id=job.provider_id,
                model=job.model,
                quantity_microunits=1_000_000,
                unit=unit,
                actual_internal_credits=credits,
                actual_cost_breakdown=breakdown,
                metadata={"failed": True, "op_id": op.id, "detail": detail},
            )
        )
    job.status = JobStatus.FAILED
    job.error_message = detail
    job.completed_at = datetime.now(UTC)
    return plane.save_job(job)


@_serialized
def build_receipt(
    plane: ControlPlane,
    workspace_id: str,
    session_id: str,
    ops: list[DirectorOp],
    reviews: dict[str, str],
) -> dict:
    """Itemized receipt: every op, accepted and discarded alike, estimate vs actual."""

    run_id = f"director-{session_id}"
    usage = [u for u in plane.usage(workspace_id) if u.run_id == run_id]
    actual_by_job = {u.job_id: u.cost_breakdown.total_usd_micros for u in usage}
    flagged_failed = {u.job_id for u in usage if u.metadata.get("failed")}

    lines = []
    estimated_total = 0
    for op in ops:
        estimate = op.estimate.total_usd_micros if op.estimate else 0
        if op.status in {"submitting", "submitted", "completed", "failed"}:
            estimated_total += estimate
        lines.append(
            {
                "op_id": op.id,
                "clip_id": op.clip_id,
                "op": op.op.value,
                "provider": op.provider.value if op.provider else "",
                "model": op.model,
                "prompt_version": op.prompt_version,
                "duration_s": op.duration_s,
                "resolution": op.resolution,
                "status": op.status,
                "estimate_usd_micros": estimate,
                "actual_usd_micros": actual_by_job.get(op.job_id, 0),
                "billed_after_failure": op.job_id in flagged_failed,
                "review_verdict": reviews.get(op.id, ""),
            }
        )
    actual_total = sum(actual_by_job.values())
    return {
        "session_id": session_id,
        "lines": lines,
        "totals": {
            "estimated_usd_micros": estimated_total,
            "actual_usd_micros": actual_total,
            "variance_usd_micros": actual_total - estimated_total,
            "internal_credits": sum(u.internal_credits for u in usage),
        },
        "currency": "USD",
    }


@_serialized
def spent_today_by_provider(
    plane: ControlPlane, workspace_id: str, *, now: datetime | None = None
) -> dict[str, dict[str, int]]:
    """Cost and credits allocated to usage on the current local calendar day,
    per provider — Grok spends dollars, Google Flow spends plan credits, and
    the meter reads each in its own currency."""
    now = now if now is not None else datetime.now(UTC).astimezone()
    day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    usage_by_id = {row.id: row for row in plane.usage(workspace_id)}
    today: dict[str, dict[str, int]] = {}
    for allocation in plane.cost_allocations(workspace_id):
        usage = usage_by_id.get(allocation.usage_event_id)
        if usage is None:
            continue
        occurred = usage.occurred_at
        if occurred.tzinfo is None:
            occurred = occurred.replace(tzinfo=UTC)
        if occurred.astimezone(now.tzinfo) < day_start:
            continue
        bucket = today.setdefault(usage.provider_id, {"usd_micros": 0, "credits": 0})
        bucket["usd_micros"] += allocation.cost_usd_micros
        bucket["credits"] += int(usage.internal_credits)
    return today


def spent_today_usd_micros(
    plane: ControlPlane, workspace_id: str, *, now: datetime | None = None
) -> int:
    """Cost allocated to usage that occurred on the current local calendar day."""
    return sum(
        bucket["usd_micros"]
        for bucket in spent_today_by_provider(plane, workspace_id, now=now).values()
    )


def by_provider(
    plane: ControlPlane, workspace_id: str, summary: dict, *, now: datetime | None = None
) -> dict[str, dict[str, int]]:
    """Lifetime and today's spend per provider, in dollars AND credits."""
    today = spent_today_by_provider(plane, workspace_id, now=now)
    out: dict[str, dict[str, int]] = {}
    for row in summary["rollups"]["providers"]:
        provider_id = str(row["id"])
        day = today.get(provider_id, {"usd_micros": 0, "credits": 0})
        out[provider_id] = {
            "usd_micros": int(row["cost_usd_micros"]),
            "credits": int(row["internal_credits"]),
            "usage_events": int(row["usage_events"]),
            "today_usd_micros": day["usd_micros"],
            "today_credits": day["credits"],
        }
    return out


@_serialized
def snapshot(plane: ControlPlane, workspace_id: str, *, committed_usd_micros: int = 0) -> dict:
    summary = CostAnalyticsService(plane).summary(workspace_id, organization_wide=True)
    providers = by_provider(plane, workspace_id, summary)
    return {
        "totals": summary["totals"],
        "today_usd_micros": sum(row["today_usd_micros"] for row in providers.values()),
        "by_provider": providers,
        "flow_plan": ratecards.flow_plan(),
        "providers": summary["rollups"]["providers"],
        "models": summary["rollups"]["models"],
        "committed_usd_micros": committed_usd_micros,
        "currency": summary["currency"],
    }
