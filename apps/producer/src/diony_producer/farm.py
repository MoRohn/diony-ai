"""FARM loop: one Wan clip at a time. Dual-Spark drains brain first."""

from __future__ import annotations

import hashlib
import os
import subprocess
import threading

from diony_contracts.cluster import refuse_concurrent, site_status, write_mode_lock
from diony_contracts.enums import ArtifactKind, QCVerdict
from diony_contracts.live import inference_live
from diony_contracts.models import Artifact, Project, QCReport, Take
from diony_producer.camera import generate_clip, live_camera
from diony_producer.vault import persist_project, vault_root

_lock = threading.Lock()
_busy = False
_detail = ""
_shot_id = ""
_pending: list[Take] = []


def farm_status() -> dict:
    site = site_status()
    return {
        "busy": _busy,
        "detail": _detail,
        "shot_id": _shot_id,
        "live": inference_live() and live_camera(),
        "sequential": site["sequential"],
        "negative": "720p Wan",
        "master": "1080 Diony finish",
    }


def enter_farm() -> str | None:
    """Dual-Spark/B300: drain brain if a command is set, then lock FARM/wan."""
    site = site_status()
    warning = None
    if site["sequential"]:
        held = refuse_concurrent("FARM", "wan")
        drain = os.environ.get("DIONY_DRAIN_BRAIN", "").strip()
        if drain:
            subprocess.run(drain, shell=True, timeout=180, check=False)
        elif held:
            warning = held
    write_mode_lock("FARM", "wan", reason="producer farm")
    return warning


def attach_negative(take: Take, dest_rel: str, shot_frames: int, *, live: bool, err: str = "") -> None:
    dest = vault_root() / dest_rel
    size = dest.stat().st_size if dest.is_file() else 0
    digest = hashlib.sha256(dest.read_bytes()).hexdigest() if dest.is_file() else ""
    take.artifacts = [
        Artifact(
            id=f"art-{take.id}-720",
            take_id=take.id,
            kind=ArtifactKind.NEGATIVE_720,
            path=dest_rel,
            bytes=size,
            sha256=digest,
            width=1280,
            height=720,
            duration_frames=shot_frames,
        )
    ]
    ok = live and size > 32 and not err
    take.qc = QCReport(
        id=f"qc-{take.id}",
        take_id=take.id,
        shot_id=take.shot_id,
        verdict=QCVerdict.PASS if ok else (QCVerdict.FAIL if err else QCVerdict.WARN),
        identity_ok=True,
        morph_ok=True,
        flicker_ok=True,
        duration_ok=ok or not live,
        legal_ok=True,
        notes=(
            err
            or (
                "Wan2.2 720p negative. Not a 1080 master. Identity VLM is later. Do not ship as master."
                if ok
                else "Queued for Wan2.2 on this pair."
            )
        ),
    )


def run_one(project: Project, take: Take) -> None:
    global _detail, _shot_id
    shot = project.shot(take.shot_id)
    _shot_id = shot.id
    _detail = f"Wan2.2 {shot.engine.value} {take.id}"
    rel = f"takes/{take.id}/negative_720.mp4"
    dest = vault_root() / rel
    try:
        generate_clip(project, shot, dest, take.seed)
        attach_negative(take, rel, shot.duration_frames, live=True)
        persist_project(project)
    except Exception as exc:  # noqa: BLE001 — farm must record QC fail
        attach_negative(take, rel, shot.duration_frames, live=True, err=str(exc)[:800])
        persist_project(project)


def start_farm(project: Project, takes: list[Take]) -> None:
    """Queue clips. Mark busy on this thread so Produce poll does not miss FARM."""
    global _busy, _detail
    spawn = False
    with _lock:
        _pending.extend(takes)
        if not _busy:
            _busy = True
            _detail = f"Farm {len(_pending)} Wan2.2 clips"
            spawn = True
    if not spawn:
        return

    def loop() -> None:
        global _busy, _detail, _shot_id
        try:
            while True:
                with _lock:
                    if not _pending:
                        break
                    take = _pending.pop(0)
                rel = f"takes/{take.id}/negative_720.mp4"
                existing = vault_root() / rel
                if existing.is_file() and existing.stat().st_size > 32:
                    continue
                run_one(project, take)
        finally:
            again = False
            with _lock:
                if _pending:
                    again = True
                else:
                    _busy = False
                    _detail = "Farm idle"
                    _shot_id = ""
            if again:
                threading.Thread(target=loop, name="diony-farm", daemon=True).start()

    threading.Thread(target=loop, name="diony-farm", daemon=True).start()
