"""Same-Project Production restart with immutable take and stop provenance."""

from __future__ import annotations

from diony_contracts.enums import (
    GateName,
    MixLockState,
    ProjectStatus,
)
from diony_contracts.models import ProductionRestartReceipt, Project, Take
from diony_contracts.stitch import assemble_program
from diony_contracts.studio import refresh_reference_shot
from diony_contracts.wan_prompt import WAN22_PROMPT_STRATEGY_VERSION
from diony_producer.farm import new_generation_take


def recalibrate_stopped_project(
    project: Project,
    *,
    reason: str = "reference_and_performance_recalibration",
) -> list[Take]:
    """Compile today's rundown and create replacement Takes without deleting history."""
    if project.production_stop is None:
        raise ValueError("Production must be durably stopped before it can be restarted.")
    clean_reason = reason.strip()
    if not clean_reason:
        raise ValueError("Production restart requires a reason.")

    previous_stop = project.production_stop.model_copy(deep=True)
    previous_version = project.version
    preserved_take_ids = [take.id for take in project.takes]

    for shot in project.shots:
        refresh_reference_shot(project, shot)
        shot.selected_take_id = None

    project.production_stop = None
    project.production_started_at = None
    project.production_ended_at = None
    project.version += 1
    project.deliverables = []
    project.final_review = None
    project.timeline = None
    if project.mix is not None:
        project.mix.state = MixLockState.OPEN
        project.mix.measured_lufs = None
        project.mix.true_peak_dbtp = None
        project.mix.stem_asset_ids = []
        project.mix.picture_lock_timeline_id = None
    for gate in project.gates:
        if gate.name == GateName.BIBLE:
            continue
        gate.open = True
        gate.locked_by = None
        gate.locked_at = None
    project.status = (
        ProjectStatus.BIBLE_LOCKED if project.bible.locked else ProjectStatus.BIBLE_PENDING
    )
    project.fleet_mode = "SPLIT"

    replacements: list[Take] = []
    for shot in project.shots:
        take = new_generation_take(project, shot)
        project.takes.append(take)
        shot.selected_take_id = take.id
        replacements.append(take)

    reference_asset_ids = list(
        dict.fromkeys(
            asset_id
            for shot in project.shots
            for asset_id in shot.keyframe_asset_ids
        )
    )
    project.production_restarts.append(
        ProductionRestartReceipt(
            reason=clean_reason,
            previous_project_version=previous_version,
            new_project_version=project.version,
            previous_stop=previous_stop,
            preserved_take_ids=preserved_take_ids,
            replacement_take_ids=[take.id for take in replacements],
            prompt_strategy_version=WAN22_PROMPT_STRATEGY_VERSION,
            reference_asset_ids=reference_asset_ids,
        )
    )
    assemble_program(project)
    return replacements
