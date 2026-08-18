"""FINISH loop: 720 negative → 1080 master after picture lock. Dual-Spark drains Wan first."""

from __future__ import annotations

import hashlib
import os
import subprocess
import threading
from pathlib import Path

import httpx

from diony_contracts.cluster import refuse_concurrent, site_status, write_mode_lock
from diony_contracts.enums import ArtifactKind
from diony_contracts.live import finish_base, finish_ready
from diony_contracts.models import Artifact, Project, Take
from diony_contracts.stitch import assemble_program, program_master_rel
from diony_producer.vault import persist_project, vault_root

try:
    from diony_finish.pipeline import FinishError, finish_clip, stitch_masters
except ImportError:  # producer installed without finish package
    FinishError = RuntimeError  # type: ignore[misc, assignment]

    def finish_clip(**kwargs):  # type: ignore[no-untyped-def]
        raise FinishError("diony-finish is not installed")

    def stitch_masters(**kwargs):  # type: ignore[no-untyped-def]
        raise FinishError("diony-finish is not installed")


_lock = threading.Lock()
_busy = False
_detail = ""
_pending: list[Take] = []


def live_finish() -> bool:
    return finish_ready()


def _take_1080(take: Take) -> Artifact | None:
    for art in take.artifacts:
        if art.kind != ArtifactKind.FINISH_1080:
            continue
        if not art.path.endswith((".mov", ".mp4")):
            continue
        dest = vault_root() / art.path
        if dest.is_file() and dest.stat().st_size > 32:
            return art
    rel = f"takes/{take.id}/finish_1080.mov"
    dest = vault_root() / rel
    if dest.is_file() and dest.stat().st_size > 32:
        return Artifact(
            id=f"art-{take.id}-1080",
            take_id=take.id,
            kind=ArtifactKind.FINISH_1080,
            path=rel,
            bytes=dest.stat().st_size,
            width=1920,
            height=1080,
        )
    return None


def selected_masters_ready(project: Project) -> bool:
    for shot in project.shots:
        if not shot.selected_take_id:
            return False
        try:
            take = project.take(shot.selected_take_id)
        except KeyError:
            return False
        if _take_1080(take) is None:
            return False
    return bool(project.shots)


def finish_status() -> dict:
    site = site_status()
    return {
        "busy": _busy,
        "detail": _detail,
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
    write_mode_lock("FINISH", "finish", reason="picture lock")
    return warning


def _finish_one(project: Project, take: Take) -> None:
    global _detail
    _detail = f"diony_sr_v1 {take.id}"
    src_rel = next(
        (a.path for a in take.artifacts if a.path.endswith("negative_720.mp4")),
        None,
    ) or next(
        (
            a.path
            for a in take.artifacts
            if a.path.endswith((".mp4", ".mov")) and "finish_1080" not in a.path
        ),
        f"takes/{take.id}/negative_720.mp4",
    )
    src = vault_root() / src_rel
    rel = f"takes/{take.id}/finish_1080.mov"
    dest = vault_root() / rel
    shot = project.shot(take.shot_id)
    grain = project.bible.grain.value if project.bible.grain else "none"
    timeout = float(os.environ.get("DIONY_FINISH_TIMEOUT", "7200"))
    want_http = bool(os.environ.get("DIONY_FINISH_URL", "").strip())
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
        finish_clip(src, dest, fps=shot.fps, grain=grain)
    size = dest.stat().st_size if dest.is_file() else 0
    if size < 32:
        raise FinishError(f"1080 master missing for {take.id}")
    digest = hashlib.sha256(dest.read_bytes()).hexdigest()
    _attach_1080(project, take, rel, size, digest=digest, duration_frames=shot.duration_frames)
    persist_project(project)


def _attach_1080(
    project: Project,
    take: Take,
    rel: str,
    size: int,
    *,
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
            bytes=size,
            sha256=digest,
            width=1920,
            height=1080,
            duration_frames=duration_frames or shot.duration_frames,
        )
    ]


def _stitch_program(project: Project) -> None:
    assemble_program(project)
    if not selected_masters_ready(project) or not project.timeline:
        raise FinishError("selected takes are missing 1080 masters")
    clips: list[tuple[Path, int, int]] = []
    for event, shot in zip(project.timeline.events, project.shots, strict=True):
        if not event.take_id:
            raise FinishError(f"no print for {shot.id}")
        take = project.take(event.take_id)
        art = _take_1080(take)
        if art is None:
            raise FinishError(f"no 1080 for {take.id}")
        clips.append((vault_root() / art.path, event.handle_in, shot.duration_frames))
    rel = program_master_rel(project)
    dest = vault_root() / rel
    stitch_masters(clips, dest, fps=project.bible.fps)
    project.timeline.preview_path = rel
    persist_project(project)


def start_finish(project: Project, takes: list[Take]) -> None:
    global _busy, _detail
    spawn = False
    with _lock:
        _pending.extend(takes)
        if not _busy:
            _busy = True
            _detail = f"FINISH {len(_pending)} masters"
            spawn = True
    if not spawn:
        return

    def loop() -> None:
        global _busy, _detail
        try:
            while True:
                with _lock:
                    if not _pending:
                        break
                    take = _pending.pop(0)
                rel = f"takes/{take.id}/finish_1080.mov"
                existing = vault_root() / rel
                if existing.is_file() and existing.stat().st_size > 32:
                    _attach_1080(project, take, rel, existing.stat().st_size)
                    persist_project(project)
                    continue
                try:
                    _finish_one(project, take)
                except Exception as exc:  # noqa: BLE001
                    _detail = str(exc)[:400]
                    persist_project(project)
        finally:
            try:
                with _lock:
                    leftover = list(_pending)
                if not leftover:
                    _stitch_program(project)
            except Exception as exc:  # noqa: BLE001
                _detail = f"1080 program stitch failed: {exc}"[:400]
                persist_project(project)
            with _lock:
                _busy = False if not _pending else True
                if not _pending and "fail" not in str(_detail).lower():
                    _detail = "Finish idle"

    threading.Thread(target=loop, name="diony-finish", daemon=True).start()
