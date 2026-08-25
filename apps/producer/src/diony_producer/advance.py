"""Picture and mix close themselves during the Production run. Bible and distribution stay human."""

from __future__ import annotations

from datetime import datetime, timezone

from diony_contracts.enums import GateName, MixLockState, PictureLockState, ProjectStatus, QCVerdict
from diony_contracts.live import inference_live
from diony_contracts.models import Gate, Project, Take
from diony_producer.camera import live_camera
from diony_producer.farm import (
    farm_status,
    qc_contract_valid,
    refresh_take_measurement,
    take_has_live_negative,
)
from diony_producer.finish import (
    enter_finish,
    finish_status,
    live_finish,
    selected_masters_ready,
    start_finish,
)
from diony_producer.gpu import should_proxy, signed_gpu_site, wizard_pack_on_gpu
from diony_producer.mix import mix_evidence_blockers, mix_status, reset_mix, start_mix
from diony_producer.vault import persist_project


def _gate(project: Project, name: GateName) -> Gate:
    for gate in project.gates:
        if gate.name == name:
            return gate
    raise ValueError(f"missing gate {name.value}")


def _close(gate: Gate, locked_by: str) -> None:
    gate.open = False
    gate.locked_by = locked_by
    gate.locked_at = datetime.now(timezone.utc)


def _selected_take(project: Project, shot_id: str) -> Take | None:
    shot = next((item for item in project.shots if item.id == shot_id), None)
    if shot and shot.selected_take_id:
        try:
            return project.take(shot.selected_take_id)
        except KeyError:
            return None
    takes = project.takes_for(shot_id)
    return takes[-1] if takes else None


def rundown_ready(project: Project) -> bool:
    if not project.shots:
        return False
    return all(_selected_take(project, shot.id) is not None for shot in project.shots)


def farm_ready_for_finish(project: Project, *, live: bool) -> bool:
    if not rundown_ready(project):
        return False
    for shot in project.shots:
        take = _selected_take(project, shot.id)
        if take is None:
            return False
        if live:
            # A stale measurement version re-measures once instead of stalling.
            refresh_take_measurement(project, take)
        if take.qc and take.qc.verdict == QCVerdict.FAIL:
            return False
        if live and (take.qc is None or take.qc.verdict != QCVerdict.PASS):
            return False
        if live and not qc_contract_valid(project, take):
            return False
        if live and not take_has_live_negative(take, project):
            return False
    return True


def apply_picture_lock(project: Project, locked_by: str = "producer") -> None:
    """Close picture and start 1080 finish when `diony_sr_v1` is live."""
    gate = _gate(project, GateName.PICTURE)
    if not gate.open:
        return
    if any(_selected_take(project, shot.id) is None for shot in project.shots):
        raise ValueError("picture lock needs a selected take on every shot")
    for shot in project.shots:
        take = _selected_take(project, shot.id)
        if take is not None:
            refresh_take_measurement(project, take)
    failed = [
        (shot.id, take.id)
        for shot in project.shots
        if (take := _selected_take(project, shot.id)) is not None
        and take.qc is not None
        and take.qc.verdict == QCVerdict.FAIL
    ]
    if failed:
        evidence = ", ".join(f"{shot_id}/{take_id}" for shot_id, take_id in failed)
        raise ValueError(f"picture lock refuses selected Takes that failed AutoQC: {evidence}")
    incomplete = [
        (shot.id, take.id)
        for shot in project.shots
        if (take := _selected_take(project, shot.id)) is not None
        and take.qc is not None
        and take.qc.verdict == QCVerdict.PASS
        and not qc_contract_valid(project, take)
    ]
    if incomplete:
        evidence = ", ".join(f"{shot_id}/{take_id}" for shot_id, take_id in incomplete)
        raise ValueError(
            "picture lock refuses selected Takes without current reference/continuity QC: "
            f"{evidence}"
        )
    if inference_live() and not live_finish() and not wizard_pack_on_gpu():
        raise ValueError("picture lock needs diony_sr_v1. Studio App pulls RealESRGAN_x2.pth.")
    _close(gate, locked_by)
    if project.timeline:
        project.timeline.picture_lock = PictureLockState.LOCKED
    project.status = ProjectStatus.PICTURE_LOCKED
    project.fleet_mode = "FINISH"
    if live_finish() and not should_proxy():
        enter_finish()
        selected: list[Take] = []
        for shot in project.shots:
            take = _selected_take(project, shot.id)
            if take:
                selected.append(take)
        if selected:
            start_finish(project, selected)
    persist_project(project)


def apply_mix_lock(project: Project, locked_by: str = "producer") -> None:
    """Close mix after picture. Live finish still needs 1080 masters."""
    pic = _gate(project, GateName.PICTURE)
    gate = _gate(project, GateName.MIX)
    if pic.open:
        raise ValueError("mix lock requires picture lock")
    if not gate.open:
        return
    if live_finish() and not should_proxy() and not selected_masters_ready(project):
        raise ValueError("mix lock needs 1080 masters on every selected take")
    if live_finish() and not should_proxy():
        blockers = mix_evidence_blockers(project, verify_program=True)
        if blockers:
            raise ValueError("mix lock refused: " + " ".join(blockers))
    _close(gate, locked_by)
    if project.mix:
        project.mix.state = MixLockState.LOCKED
        if project.timeline:
            project.mix.picture_lock_timeline_id = project.timeline.id
    project.status = ProjectStatus.MIX_LOCKED
    project.mark_production_end()
    persist_project(project)


def reopen_picture_for_retake(project: Project) -> None:
    """A retake reopens picture and mix. Production re-runs 1080 and mix."""
    pic = _gate(project, GateName.PICTURE)
    mix = _gate(project, GateName.MIX)
    pic.open = True
    pic.locked_by = None
    pic.locked_at = None
    mix.open = True
    mix.locked_by = None
    mix.locked_at = None
    if project.timeline:
        project.timeline.picture_lock = PictureLockState.OPEN
        project.timeline.gallery_removed_at = datetime.now(timezone.utc)
    if project.mix:
        project.mix.state = MixLockState.OPEN
        project.mix.picture_lock_timeline_id = None
        project.mix.measured_lufs = None
        project.mix.true_peak_dbtp = None
        project.mix.stem_asset_ids = []
    reset_mix()
    project.status = ProjectStatus.SHOOTING
    project.production_ended_at = None
    persist_project(project)


def maybe_advance_run(project: Project) -> None:
    """Close picture then mix when the run is ready. No-op while farm/finish is busy."""
    if project.production_stop is not None or project.production_pause is not None:
        return
    finish = finish_status()
    mix_work = mix_status()
    if farm_status()["busy"] or finish["busy"] or finish.get("error"):
        return
    live = live_camera()
    if signed_gpu_site() and not live:
        return
    if not farm_ready_for_finish(project, live=live):
        return
    pic = _gate(project, GateName.PICTURE)
    mix = _gate(project, GateName.MIX)
    if live_finish() and not should_proxy() and not mix.open:
        blockers = mix_evidence_blockers(project, verify_program=True)
        if blockers:
            mix.open = True
            mix.locked_by = None
            mix.locked_at = None
            if project.mix:
                project.mix.state = MixLockState.OPEN
                project.mix.picture_lock_timeline_id = None
            project.status = ProjectStatus.PICTURE_LOCKED
            project.production_ended_at = None
            persist_project(project)
    if mix_work["busy"] or mix_work["error"]:
        return
    if pic.open:
        if live and not live_finish() and inference_live() and not wizard_pack_on_gpu():
            return
        try:
            apply_picture_lock(project, "producer")
        except ValueError:
            return
        finish = finish_status()
        if finish["busy"] or finish.get("error"):
            return
    elif live_finish() and not selected_masters_ready(project) and not should_proxy():
        enter_finish()
        selected: list[Take] = []
        for shot in project.shots:
            take = _selected_take(project, shot.id)
            if take:
                selected.append(take)
        if selected:
            start_finish(project, selected)
        return
    if mix.open and not pic.open:
        if live_finish() and not should_proxy() and mix_evidence_blockers(project, verify_program=True):
            start_mix(project)
            return
        try:
            apply_mix_lock(project, "producer")
        except ValueError:
            return
