"""FINISH loop: 720 negative → 1080 master during Production. Dual-Spark drains Wan first."""

from __future__ import annotations

import hashlib
import os
import subprocess
import threading
import time
from pathlib import Path

import httpx

from diony_contracts.cluster import refuse_concurrent, site_status, write_mode_lock
from diony_contracts.enums import ArtifactKind, QCVerdict
from diony_contracts.live import finish_base, finish_ready
from diony_contracts.models import Artifact, Project, Take
from diony_contracts.stitch import assemble_program, program_master_rel
from diony_producer.farm import negative_lineage_valid, negative_rel, qc_contract_valid
from diony_producer.performance import PerformanceJournal
from diony_producer.vault import persist_project, record_activity, vault_root

try:
    from diony_finish.pipeline import (
        FinishError,
        PostSRQCError,
        finish_clip,
        finished_media_has_picture,
        media_frame_count,
        stitch_masters,
        verify_finished_media,
        verify_post_sr,
    )
except ImportError:  # producer installed without finish package
    FinishError = RuntimeError  # type: ignore[misc, assignment]

    class PostSRQCError(RuntimeError):  # type: ignore[no-redef]
        pass

    def finish_clip(**kwargs):  # type: ignore[no-untyped-def]
        raise FinishError("diony-finish is not installed")

    def media_frame_count(path):  # type: ignore[no-untyped-def]
        return 0

    def verify_post_sr(negative, master, *, label="post-SR QC"):  # type: ignore[no-untyped-def]
        raise FinishError(f"{label} cannot run because diony-finish is not installed")

    def stitch_masters(**kwargs):  # type: ignore[no-untyped-def]
        raise FinishError("diony-finish is not installed")

    def finished_media_has_picture(path):  # type: ignore[no-untyped-def]
        return False

    def verify_finished_media(path, *, label="1080 finish"):  # type: ignore[no-untyped-def]
        raise FinishError(f"{label} cannot be verified because diony-finish is not installed")


_lock = threading.Lock()
_busy = False
_detail = ""
_error = ""
_pending: list[Take] = []
_cancel = False
_generation = 0
_current_take_id = ""
_frame_done = 0
_frame_total = 0
_done = 0
_total = 0
_PERFORMANCE = PerformanceJournal()


def _visual_qc_required() -> bool:
    disabled = {"1", "true", "yes", "on"}
    return not (
        os.environ.get("DIONY_DRY_RUN", "").strip().lower() in disabled
        or os.environ.get("DIONY_FINISH_FAKE", "").strip().lower() in disabled
    )


def finish_cancelled(token: int | None = None) -> bool:
    with _lock:
        if token is None:
            return _cancel
        return _cancel or token != _generation


def reset_finish() -> None:
    global _busy, _detail, _error, _pending, _cancel
    global _current_take_id, _frame_done, _frame_total, _done, _total
    with _lock:
        _busy = False
        _detail = ""
        _error = ""
        _pending = []
        _cancel = False
        _current_take_id = ""
        _frame_done = 0
        _frame_total = 0
        _done = 0
        _total = 0


def abort_finish() -> None:
    """Drop the 1080 queue. Do not start mix on a discarded show."""
    global _cancel, _generation, _pending, _busy, _detail, _error
    global _current_take_id, _frame_done, _frame_total, _done, _total
    with _lock:
        _generation += 1
        _cancel = True
        _pending = []
        _busy = False
        _detail = ""
        _error = ""
        _current_take_id = ""
        _frame_done = 0
        _frame_total = 0
        _done = 0
        _total = 0


def live_finish() -> bool:
    """True when this box has diony_sr_v1, or Studio already packed it on the signed GPU.

    Desk `DIONY_DRY_RUN` does not hide a Studio App pack. Pytest stays on local `finish_ready()`.
    """
    if finish_ready():
        return True
    try:
        from diony_producer.gpu import gpu_finish_live, in_pytest, wizard_pack_on_gpu
    except ImportError:
        return False
    if in_pytest():
        return False
    if gpu_finish_live():
        return True
    return wizard_pack_on_gpu()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def finish_rel(project: Project, take: Take) -> str:
    return f"projects/{project.id}/takes/{take.id}/finish_1080.mov"


def _negative_artifact(project: Project, take: Take) -> Artifact | None:
    expected = negative_rel(project, take)
    return next(
        (
            art
            for art in take.artifacts
            if art.kind == ArtifactKind.NEGATIVE_720 and art.path == expected
        ),
        None,
    )


def _take_1080(
    project: Project,
    take: Take,
    *,
    verify_hash: bool = False,
) -> Artifact | None:
    expected = finish_rel(project, take)
    source = _negative_artifact(project, take)
    if source is None or not source.sha256:
        return None
    for art in take.artifacts:
        if art.kind != ArtifactKind.FINISH_1080:
            continue
        if art.path != expected:
            continue
        if art.project_id != project.id or art.shot_id != take.shot_id:
            continue
        if art.source_sha256 != source.sha256:
            continue
        dest = vault_root() / art.path
        if dest.is_file() and dest.stat().st_size > 32:
            if verify_hash and (not art.sha256 or _sha256_file(dest) != art.sha256):
                continue
            if _visual_qc_required() and not finished_media_has_picture(dest):
                continue
            return art
    return None


def selected_masters_ready(project: Project) -> bool:
    for shot in project.shots:
        if not shot.selected_take_id:
            return False
        try:
            take = project.take(shot.selected_take_id)
        except KeyError:
            return False
        if not negative_lineage_valid(project, take):
            return False
        if _take_1080(project, take) is None:
            return False
    return bool(project.shots)


def finish_status() -> dict:
    site = site_status()
    with _lock:
        busy = _busy
        detail = _detail
        error = _error
        current_take_id = _current_take_id
        frame_done = _frame_done
        frame_total = _frame_total
        done = _done
        total = _total
        queued = len(_pending)
    progress = {}
    if frame_total:
        progress = {
            "done": frame_done,
            "total": frame_total,
            "pct": min(100, max(0, round(frame_done / frame_total * 100))),
        }
    current_shot_id = current_take_id
    if current_take_id.rsplit("-t", 1)[-1].isdigit():
        current_shot_id = current_take_id.rsplit("-t", 1)[0]
    return {
        "busy": busy,
        "detail": detail,
        "error": error,
        "current_take_id": current_take_id,
        "current_shot_id": current_shot_id,
        "progress": progress,
        "done": done,
        "total": total,
        "queued": queued,
        "live": live_finish(),
        "sequential": site["sequential"],
        "master": "1920x1080 Rec.709",
        "lanczos": False,
    }


def enter_finish() -> str | None:
    site = site_status()
    warning = None
    if site["sequential"]:
        held = refuse_concurrent("FINISH", "finish")
        drain = os.environ.get("DIONY_DRAIN_WAN", "").strip()
        if drain:
            subprocess.run(drain, shell=True, timeout=180, check=False)
        elif held:
            warning = held
    write_mode_lock("FINISH", "finish", reason="production 1080 finish")
    return warning


def _finish_one(project: Project, take: Take) -> None:
    global _detail, _frame_done, _frame_total
    if take.qc is not None and take.qc.verdict == QCVerdict.FAIL:
        raise FinishError(f"finish refuses {take.id}: selected Take failed AutoQC")
    settled = take.qc is not None and take.qc.settled_by_policy
    if not settled and not qc_contract_valid(project, take):
        raise FinishError(
            f"finish refuses {take.id}: selected Take lacks current reference/continuity QC"
        )
    _detail = f"diony_sr_v1 {take.id}"
    if not negative_lineage_valid(project, take, verify_hash=True):
        raise FinishError(f"finish refuses {take.id}: 720 negative lineage is invalid")
    source_artifact = _negative_artifact(project, take)
    if source_artifact is None:
        raise FinishError(f"finish refuses {take.id}: 720 negative receipt is missing")
    src_rel = negative_rel(project, take)
    src = vault_root() / src_rel
    rel = finish_rel(project, take)
    dest = vault_root() / rel
    shot = project.shot(take.shot_id)
    grain = project.bible.grain.value if project.bible.grain else "none"
    record_activity("sr", f"{shot.id} · 1080 finish", level="live", shot_id=shot.id)
    timeout = float(os.environ.get("DIONY_FINISH_TIMEOUT", "7200"))
    want_http = bool(os.environ.get("DIONY_FINISH_URL", "").strip())

    def report_progress(done: int, total: int) -> None:
        global _detail, _frame_done, _frame_total
        _frame_done = done
        _frame_total = total
        _detail = f"diony_sr_v1 {take.id} · frame {done}/{total}"

    if want_http:
        try:
            res = httpx.post(
                finish_base() + "/v1/finish",
                json={
                    "src": str(src),
                    "dest": str(dest),
                    "fps": shot.fps,
                    "grain": grain,
                },
                timeout=timeout,
            )
            if res.status_code >= 400:
                raise FinishError(str(res.json().get("detail") or res.text))
        except httpx.HTTPError:
            finish_clip(src, dest, fps=shot.fps, grain=grain)
    else:
        finish_clip(src, dest, fps=shot.fps, grain=grain, progress=report_progress)
    size = dest.stat().st_size if dest.is_file() else 0
    if size < 32:
        raise FinishError(f"1080 master missing for {take.id}")
    if _visual_qc_required():
        verify_finished_media(dest, label=f"1080 finish for {take.id}")
        # Blueprint 06/19: the finish may not add identity drift or temporal
        # boil the negative does not have. A violation holds this take only.
        try:
            verify_post_sr(src, dest, label=f"post-SR QC for {take.id}")
        except PostSRQCError:
            # A rejected master must not linger at the canonical path where the
            # next pass's adoption branch would attach it.
            dest.unlink(missing_ok=True)
            raise
    digest = _sha256_file(dest)
    measured_frames = media_frame_count(dest) if _visual_qc_required() else 0
    _attach_1080(
        project,
        take,
        rel,
        size,
        source_artifact=source_artifact,
        digest=digest,
        duration_frames=measured_frames or shot.duration_frames,
    )
    persist_project(project)
    record_activity("sr", f"{shot.id} · 1080 master", shot_id=shot.id)


def _attach_1080(
    project: Project,
    take: Take,
    rel: str,
    size: int,
    *,
    source_artifact: Artifact,
    digest: str = "",
    duration_frames: int = 0,
) -> None:
    shot = project.shot(take.shot_id)
    take.artifacts = [a for a in take.artifacts if a.kind != ArtifactKind.FINISH_1080] + [
        Artifact(
            id=f"art-{take.id}-1080",
            take_id=take.id,
            kind=ArtifactKind.FINISH_1080,
            path=rel,
            project_id=project.id,
            shot_id=take.shot_id,
            bytes=size,
            sha256=digest,
            source_sha256=source_artifact.sha256,
            prompt_sha256=source_artifact.prompt_sha256,
            reference_asset_ids=list(source_artifact.reference_asset_ids),
            reference_sha256=source_artifact.reference_sha256,
            continuity_from_take_id=source_artifact.continuity_from_take_id,
            continuity_sha256=source_artifact.continuity_sha256,
            generation_task="diony_sr_v1",
            width=1920,
            height=1080,
            duration_frames=duration_frames or shot.duration_frames,
        )
    ]


def _selected_take(project: Project, shot) -> Take | None:
    if shot.selected_take_id:
        try:
            return project.take(shot.selected_take_id)
        except KeyError:
            return None
    takes = project.takes_for(shot.id)
    return takes[-1] if takes else None


def _adopted_master_post_sr_ok(source_artifact: Artifact, existing: Path, take: Take) -> bool:
    """An adopted pre-existing master must clear the same post-SR gate a fresh
    finish would — a previously rejected file may not re-enter through adoption."""
    negative = vault_root() / source_artifact.path
    if not negative.is_file():
        return False
    try:
        verify_post_sr(negative, existing, label=f"adopted post-SR QC for {take.id}")
        return True
    except FinishError:
        return False


def _stitch_program(project: Project) -> None:
    assemble_program(project)
    if not project.timeline:
        raise FinishError("selected takes are missing 1080 masters")
    # Migration for pre-lane projects: a Shot that still books more frames than
    # its landed master decodes would make the trim-only conform refuse the
    # whole stitch. Reconcile such bookings to camera truth (the same rule the
    # farm applies at attach time) and rebuild the timeline once, visibly.
    reconciled = 0
    for shot in project.shots:
        take = _selected_take(project, shot)
        art = _take_1080(project, take, verify_hash=False) if take else None
        if art is None:
            continue
        measured = media_frame_count(vault_root() / art.path)
        if 0 < measured < shot.duration_frames - 1:
            shot.duration_frames = max(17, measured)
            reconciled += 1
    if reconciled:
        assemble_program(project)
        persist_project(project)
        record_activity(
            "sr",
            f"Reconciled {reconciled} legacy over-booked shot(s) to measured camera truth before stitch.",
        )
    clips: list[tuple[Path, int, int]] = []
    source_hashes: list[str] = []
    for event, shot in zip(project.timeline.events, project.shots, strict=True):
        if not event.take_id:
            raise FinishError(f"no print for {shot.id}")
        take = project.take(event.take_id)
        art = _take_1080(project, take, verify_hash=True)
        if art is None:
            raise FinishError(f"no 1080 for {take.id}")
        clips.append((vault_root() / art.path, event.handle_in, shot.duration_frames))
        source_hashes.append(art.sha256)
    rel = program_master_rel(project)
    dest = vault_root() / rel
    stitch_masters(clips, dest, fps=project.bible.fps)
    if _visual_qc_required():
        verify_finished_media(dest, label="1080 program master")
    project.timeline.preview_path = rel
    project.timeline.picture_sha256 = _sha256_file(dest)
    project.timeline.program_sha256 = ""
    project.timeline.source_artifact_sha256s = source_hashes
    project.timeline.gallery_removed_at = None
    persist_project(project)


def start_finish(project: Project, takes: list[Take]) -> None:
    global _busy, _detail, _error, _cancel, _total
    spawn = False
    with _lock:
        _cancel = False
        _pending.extend(takes)
        _total = max(_total, _done + len(_pending))
        if not _busy:
            _busy = True
            _error = ""
            _detail = f"1080 finish · {len(_pending)} masters"
            spawn = True
    if not spawn:
        return
    record_activity("sr", _detail, level="live")
    token = _generation

    def loop() -> None:
        global _busy, _detail, _error
        global _current_take_id, _frame_done, _frame_total, _done
        try:
            while True:
                with _lock:
                    if _cancel or not _pending:
                        break
                    take = _pending.pop(0)
                    _current_take_id = take.id
                    _frame_done = 0
                    _frame_total = 0
                if finish_cancelled(token):
                    break
                rel = finish_rel(project, take)
                existing = vault_root() / rel
                if existing.is_file() and existing.stat().st_size > 32:
                    source_artifact = _negative_artifact(project, take)
                    if source_artifact is not None and negative_lineage_valid(
                        project, take, verify_hash=True
                    ) and (
                        not _visual_qc_required()
                        or (
                            finished_media_has_picture(existing)
                            and _adopted_master_post_sr_ok(source_artifact, existing, take)
                        )
                    ):
                        _attach_1080(
                            project,
                            take,
                            rel,
                            existing.stat().st_size,
                            source_artifact=source_artifact,
                            digest=_sha256_file(existing),
                            duration_frames=(
                                media_frame_count(existing)
                                or project.shot(take.shot_id).duration_frames
                            ),
                        )
                        persist_project(project)
                        with _lock:
                            _done += 1
                        continue
                    existing.unlink(missing_ok=True)
                try:
                    finish_started = time.monotonic()
                    _finish_one(project, take)
                    _PERFORMANCE.record(
                        stage="finish_1080",
                        project_id=project.id,
                        take_id=take.id,
                        duration_seconds=time.monotonic() - finish_started,
                        outcome="succeeded",
                    )
                    with _lock:
                        _done += 1
                except Exception as exc:  # noqa: BLE001
                    _PERFORMANCE.record(
                        stage="finish_1080",
                        project_id=project.id,
                        take_id=take.id,
                        duration_seconds=time.monotonic() - finish_started,
                        outcome="failed",
                    )
                    if finish_cancelled(token):
                        break
                    message = str(exc).strip()[:800] or "1080 finish failed"
                    if isinstance(exc, PostSRQCError):
                        # A per-take quality verdict, not a model failure: this
                        # take stays unfinished and blocks final readiness, but
                        # the rest of the queue keeps finishing.
                        with _lock:
                            _done += 1
                            _error = message
                            _detail = message
                        persist_project(project)
                        record_activity("sr", message, level="fail", shot_id=take.shot_id)
                        continue
                    with _lock:
                        _error = message
                        _detail = message
                        # Model/checkpoint failures affect every selected take. Stop once,
                        # retain the 720 negatives, and never let Mix advance on partial output.
                        _pending.clear()
                    persist_project(project)
                    record_activity("sr", message, level="fail")
                    break
                finally:
                    with _lock:
                        _current_take_id = ""
                        _frame_done = 0
                        _frame_total = 0
        finally:
            cancelled = finish_cancelled(token)
            try:
                with _lock:
                    leftover = list(_pending)
                    failed = bool(_error)
                if not leftover and not cancelled and not failed:
                    with _lock:
                        _detail = "Verifying 1080 program master"
                    record_activity("sr", _detail, level="live")
                    _stitch_program(project)
            except Exception as exc:  # noqa: BLE001
                message = f"1080 program stitch failed: {exc}"[:800]
                with _lock:
                    _error = message
                    _detail = message
                persist_project(project)
            with _lock:
                _busy = False if not _pending else True
                failed = bool(_error)
                _current_take_id = ""
                _frame_done = 0
                _frame_total = 0
                if not _pending and not failed:
                    _detail = "Finish idle"
            if not _pending and not cancelled and not failed:
                from diony_producer.advance import maybe_advance_run

                maybe_advance_run(project)

    threading.Thread(target=loop, name="diony-finish", daemon=True).start()
