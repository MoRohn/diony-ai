"""Picture and mix close themselves during the Production run. Bible and distribution stay human."""

from __future__ import annotations

from datetime import datetime, timezone

from diony_contracts.enums import (
    ContinuityMode,
    GateName,
    MixLockState,
    PictureLockState,
    ProjectStatus,
    QCVerdict,
)
from diony_contracts.live import inference_live
from diony_contracts.models import Gate, Project, Take
from diony_producer.camera import live_camera
from diony_producer.farm import (
    auto_retake_for_qc_failure,
    enter_farm,
    farm_status,
    new_generation_take,
    start_farm,
    qc_contract_valid,
    refresh_take_measurement,
    refresh_voice_measurement,
    take_has_live_negative,
)
from diony_producer.final_review import final_review_refuses_current_master, run_final_review
from diony_contracts.cluster import RETAKE_CAP
from diony_contracts.enums import TakeOrigin
from diony_contracts.montage import RecommendedAction, ReviewStatus
from diony_producer.decisions import record_decision
from diony_contracts.montage import DecisionCategory, DecisionOption
from diony_producer.finish import (
    enter_finish,
    finish_status,
    live_finish,
    selected_masters_ready,
    start_finish,
)
from diony_producer.gpu import should_proxy, signed_gpu_site, wizard_pack_on_gpu
from diony_producer.mix import mix_evidence_blockers, mix_status, reset_mix, start_mix
from diony_producer.vault import persist_project, record_activity


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


def stale_continuity(project: Project) -> list[tuple[object, Take, str]]:
    """Selected prints whose previous-frame chain names a print that is no longer the previous Shot's.

    sh-020-t1 (2026-08-28) rolled while sh-010 had no passing print, chained
    its evidence to sh-010-t1, and passed; sh-010 later printed on t4. The
    contract then refused sh-020 at picture lock on every tick, silently,
    for two hours: the rundown was complete and nothing could move.
    """
    rows: list[tuple[object, Take, str]] = []
    for index, shot in enumerate(project.shots):
        if index == 0 or shot.continuity_mode != ContinuityMode.PREVIOUS_FRAME:
            continue
        take = _selected_take(project, shot.id)
        qc = take.qc if take is not None else None
        if take is None or qc is None or qc.verdict not in {QCVerdict.PASS, QCVerdict.WARN}:
            continue
        if qc.continuity_waived or not qc.continuity_from_take_id:
            continue
        previous = project.shots[index - 1]
        expected = previous.selected_take_id or ""
        if not expected or qc.continuity_from_take_id == expected:
            continue
        prior = _selected_take(project, previous.id)
        if prior is None or prior.qc is None or prior.qc.verdict == QCVerdict.FAIL:
            continue
        rows.append((shot, take, expected))
    return rows


def refresh_stale_continuity(project: Project, *, live: bool) -> bool:
    """Re-roll a following clip from the replaced print, or waive when the budget is spent.

    Returns True when retakes were queued (the caller waits for the farm).
    """
    rows = stale_continuity(project)
    if not rows:
        return False
    retakes: list[Take] = []
    for shot, take, expected in rows:
        assert take.qc is not None
        generations = [item for item in project.takes_for(shot.id) if item.origin == TakeOrigin.GENERATION]
        stale_from = take.qc.continuity_from_take_id
        if live and len(generations) < RETAKE_CAP:
            retry = new_generation_take(project, shot, live=True)
            project.takes.append(retry)
            shot.selected_take_id = retry.id
            record_decision(
                project,
                stage="camera",
                category=DecisionCategory.RETAKE,
                subject=f"{shot.id} continuity refresh {retry.attempt}",
                options=[
                    DecisionOption(
                        id="reroll",
                        label=f"chain {shot.id} from {expected}'s last frame",
                        reason=f"previous print {stale_from} was replaced by {expected} after {take.id} rolled",
                    ),
                    DecisionOption(
                        id="waive",
                        label="keep the print and waive the chain",
                        rejected_because="retake budget remains",
                    ),
                ],
                selected="reroll",
                reason=f"previous-frame continuity: {take.id} chained to {stale_from}, now replaced by {expected}",
                confidence=0.8,
                shot_id=shot.id,
                take_id=retry.id,
            )
            record_activity(
                "qc",
                (
                    f"{shot.id} · previous print {stale_from} was replaced by {expected} after {take.id} rolled; "
                    f"re-rolling chained from its last frame (continuity refresh, attempt {retry.attempt} of {RETAKE_CAP})"
                ),
                level="live",
                shot_id=shot.id,
            )
            retakes.append(retry)
        else:
            take.qc.continuity_waived = (
                f"previous print {stale_from} was replaced by {expected} after this clip rolled; "
                + ("retake budget spent" if live else "camera not live")
            )
            record_decision(
                project,
                stage="camera",
                category=DecisionCategory.SETTLE,
                subject=f"{shot.id} continuity waiver",
                options=[
                    DecisionOption(
                        id="waive",
                        label=f"keep {take.id}; chain evidence names {stale_from}",
                        reason=take.qc.continuity_waived,
                    ),
                    DecisionOption(
                        id="reroll",
                        label=f"chain {shot.id} from {expected}",
                        rejected_because="retake budget spent" if live else "camera not live",
                    ),
                ],
                selected="waive",
                reason=take.qc.continuity_waived,
                confidence=0.6,
                shot_id=shot.id,
                take_id=take.id,
            )
            record_activity(
                "qc",
                f"{shot.id} · {take.id} keeps its print; {take.qc.continuity_waived} (continuity waived)",
                level="warn",
                shot_id=shot.id,
            )
    persist_project(project)
    if retakes:
        project.status = ProjectStatus.SHOOTING
        project.production_ended_at = None
        persist_project(project)
        enter_farm()
        start_farm(project, retakes)
        return True
    return False


_last_lock_refusal = ""


def _report_lock_refusal(gate: str, exc: BaseException) -> None:
    """A refused lock is a state the feed must show, not a silent tick."""
    global _last_lock_refusal
    message = f"{gate} lock is waiting: {exc}"
    if message == _last_lock_refusal:
        return
    _last_lock_refusal = message
    record_activity("qc", message, level="warn")


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
            refresh_voice_measurement(project, take)
        if take.qc and take.qc.verdict == QCVerdict.FAIL:
            return False
        settled = take.qc is not None and take.qc.settled_by_policy
        if live and not settled and (take.qc is None or take.qc.verdict != QCVerdict.PASS):
            return False
        if live and not settled and not qc_contract_valid(project, take):
            return False
        if live and not take_has_live_negative(take, project):
            return False
    return True


def finish_ready_takes_early(project: Project, *, live: bool) -> list[str]:
    """1080-finish every passed clip while a held Shot waits on a human.

    Measured 2026-08-27: nine passed negatives sat behind one voice hold for
    hours with "1080 finish · blocked by AutoQC". A hold is a decision the
    rundown waits for; the masters that are ready do not have to.
    """
    from diony_producer.farm import farm_has_pending_work, farm_status
    from diony_producer.finish import _take_1080, enter_finish, finish_status, start_finish

    if not live or not live_finish() or should_proxy():
        return []
    if farm_status()["busy"] or farm_has_pending_work(project):
        return []
    finish = finish_status()
    if finish["busy"] or finish.get("error"):
        return []
    ready: list[Take] = []
    waiting = 0
    for shot in project.shots:
        take = _selected_take(project, shot.id)
        if take is None or take.qc is None:
            waiting += 1
            continue
        settled = take.qc.settled_by_policy
        passed = take.qc.verdict == QCVerdict.PASS
        if not (passed or settled):
            waiting += 1
            continue
        if not settled and not qc_contract_valid(project, take):
            continue
        if not take_has_live_negative(take, project) or _take_1080(project, take) is not None:
            continue
        ready.append(take)
    if not ready or not waiting:
        return []
    record_activity(
        "sr",
        f"finishing {len(ready)} passed clip{'s' if len(ready) != 1 else ''} at 1080 while "
        f"{waiting} Shot{'s' if waiting != 1 else ''} wait{'s' if waiting == 1 else ''} for a decision",
        level="live",
    )
    enter_finish()
    start_finish(project, ready)
    return [take.id for take in ready]


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
            refresh_voice_measurement(project, take)
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
    live = live_finish() and not should_proxy()
    if live:
        blockers = mix_evidence_blockers(project, verify_program=True)
        if blockers:
            raise ValueError("mix lock refused: " + " ".join(blockers))
    # Blueprint 24: the mixed master is measured before it is called done. A
    # failed review refuses the lock with its evidence; revise locks on record.
    review = run_final_review(project, live=live)
    if review.status == ReviewStatus.FAIL:
        persist_project(project)
        raise ValueError("mix lock refused by final review: " + "; ".join(review.issues_found))
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
    # The final review and the deliverables described the master a retake
    # just invalidated; they are measured again when the next mix locks.
    project.final_review = None
    project.deliverables = []
    project.status = ProjectStatus.SHOOTING
    project.production_ended_at = None
    persist_project(project)


BLACK_FRAME_MASTER_NOTE = "Black frame detected in the program master (final review sample)"


def remediate_final_review(project: Project, *, live: bool) -> list[Take]:
    """Retake the prints a failed final review named, within the four-Take cap.

    Runs at most once per reviewed master: the review records what was done
    (or why nothing could be), so the advance controller's next tick sees a
    remediated review and stops. A successful remediation reopens picture;
    the new negatives land, finish, stitch a new master, mix, and review
    again. A print already at the retake cap is left for a human with the
    evidence on the feed.
    """
    review = project.final_review
    if review is None or review.remediation or review.status != ReviewStatus.FAIL:
        return []
    if review.recommended_action != RecommendedAction.RETAKE or not review.offending_shot_ids:
        review.remediation = "no retake target: the failure is not tied to a print"
        persist_project(project)
        return []
    if project.production_stop is not None or project.production_pause is not None:
        review.remediation = "production stopped or paused; no automatic retake"
        persist_project(project)
        return []
    retries: list[Take] = []
    held: list[str] = []
    for shot_id in review.offending_shot_ids:
        take = _selected_take(project, shot_id)
        if take is None or take.qc is None:
            held.append(f"{shot_id} has no judged print")
            continue
        generations = [item for item in project.takes_for(shot_id) if item.origin == TakeOrigin.GENERATION]
        if len(generations) >= RETAKE_CAP:
            held.append(f"{shot_id} is at the retake cap ({RETAKE_CAP})")
            continue
        take.qc = take.qc.model_copy(
            update={"verdict": QCVerdict.FAIL, "notes": BLACK_FRAME_MASTER_NOTE, "settled_best_of": None, "settled_score": None}
        )
        retry = auto_retake_for_qc_failure(project, take)
        if retry is None:
            held.append(f"{shot_id} could not be retaken")
            continue
        retries.append(retry)
    summary = (
        ", ".join(f"{retry.shot_id} → {retry.id}" for retry in retries) or "no print could be retaken"
    ) + (f"; held: {'; '.join(held)}" if held else "")
    review.remediation = summary[:600]
    record_decision(
        project,
        stage="mix",
        category=DecisionCategory.RETAKE,
        subject="final review remediation",
        options=[
            DecisionOption(id="retake", label="retake the prints that sampled black", reason=summary if retries else "", rejected_because="" if retries else "no retake budget or no judged print"),
            DecisionOption(id="hold", label="hold the master for a human", reason="" if retries else summary, rejected_because="retake budget remained" if retries else ""),
        ],
        selected="retake" if retries else "hold",
        reason=summary,
        confidence=0.8,
    )
    record_activity(
        "qc",
        f"Final review remediation: {summary}",
        level="live",
        shot_id=retries[0].shot_id if retries else "",
    )
    if retries:
        # reopen_picture_for_retake clears the review; the remediation line
        # above and the decision log keep the record.
        reopen_picture_for_retake(project)
        if live:
            enter_farm()
            start_farm(project, retries)
    else:
        persist_project(project)
    return retries


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
    if refresh_stale_continuity(project, live=live):
        return
    if not farm_ready_for_finish(project, live=live):
        finish_ready_takes_early(project, live=live)
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
        except ValueError as exc:
            _report_lock_refusal("Picture", exc)
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
        if final_review_refuses_current_master(project):
            # The mixed master already failed its final review. When the review
            # names the prints that failed and they still have retake budget,
            # the run retakes them itself (once per master); otherwise the
            # verdict stays on the Project and the feed until a human acts.
            remediate_final_review(project, live=live)
            return
        try:
            apply_mix_lock(project, "producer")
        except ValueError as exc:
            _report_lock_refusal("Mix", exc)
            return
