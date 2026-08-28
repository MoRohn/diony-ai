"""Measured production sizing on the activity feed (Blueprint 24).

The Production rail never fabricates an ETA (Blueprint 19). Sizing therefore
comes only from this site's own performance journal: the measured p50 of
successful Wan camera clips and 1080 finishes, times the takes queued, with a
1.3× retake allowance on the high end. Fewer than three measured clips means
no numbers at all.
"""

from __future__ import annotations

from diony_contracts.models import Project
from diony_contracts.montage import DecisionCategory, DecisionOption, ProductionSizing, format_sizing, size_production
from diony_producer.decisions import record_decision
from diony_producer.performance import PerformanceJournal
from diony_producer.vault import record_activity


def current_sizing(project: Project, *, planned_takes: int | None = None) -> ProductionSizing:
    snapshot = PerformanceJournal().snapshot()
    camera = snapshot.get("wan_camera", {})
    finish = snapshot.get("finish_1080", {})
    shot_count = len(project.shots)
    hero = sum(1 for shot in project.shots if shot.hero)
    sizing = size_production(
        shot_count=shot_count,
        hero_count=hero,
        camera_p50_seconds=float(camera.get("latency_seconds_p50") or 0) or None,
        finish_p50_seconds=float(finish.get("latency_seconds_p50") or 0) or None,
        measured_clips=int(camera.get("completed") or 0),
    )
    if planned_takes is not None and sizing.basis == "measured" and sizing.camera_p50_seconds:
        per_take = sizing.camera_p50_seconds + (sizing.finish_p50_seconds or 0.0)
        low = planned_takes * per_take
        sizing = sizing.model_copy(
            update={
                "planned_takes": int(planned_takes),
                "estimated_seconds_low": round(low, 1),
                "estimated_seconds_high": round(low * sizing.retake_allowance, 1),
            }
        )
    return sizing


def announce_sizing(project: Project, *, planned_takes: int) -> ProductionSizing:
    """Record the sizing decision and, when measured, say it on the feed."""
    sizing = current_sizing(project, planned_takes=planned_takes)
    line = format_sizing(sizing)
    record_decision(
        project,
        stage="roll",
        category=DecisionCategory.SIZING,
        subject="production sizing",
        options=[
            DecisionOption(
                id="measured",
                label="measured p50 × takes × retake allowance",
                reason=line,
                rejected_because="" if sizing.basis == "measured" else "fewer than three measured clips on this site",
            ),
            DecisionOption(
                id="unmeasured",
                label="no estimate (Blueprint 19: never a fabricated ETA)",
                rejected_because="measured clips available" if sizing.basis == "measured" else "",
            ),
        ],
        selected=sizing.basis,
        reason=line or f"{planned_takes} takes queued; sizing waits for measured clips",
        confidence=0.6 if sizing.basis == "measured" else 0.9,
    )
    if line:
        record_activity("plan", line)
    return sizing


__all__ = ["announce_sizing", "current_sizing"]
