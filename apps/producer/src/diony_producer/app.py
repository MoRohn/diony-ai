"""Diony Producer: the Gallery and the launch control plane.

Producer holds the finished Director films with their spend (Blueprint 25),
the workspace governance API (Blueprint 16: members, invitations, Cost
Centers, BYOK secrets, workflows, the cost ledger), and the shared services
the Director imports (control plane, cost analytics, face identity, frame and
speech QC). Generation happens in the Director on the cloud engines; nothing
here renders a frame.
"""

from __future__ import annotations

import os
import re
import shutil
import threading
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from starlette.background import BackgroundTask
from starlette.requests import Request

from diony_contracts.cluster import (
    bind_host,
    cluster_root,
    engine_is_cloud,
    ffmpeg_install_hint,
    read_engine_choice,
    surface_url,
)
from diony_contracts.launch import MembershipRole
from diony_producer.control_plane import ControlPlane, WorkspaceAccessDenied
from diony_producer.gallery import (
    GalleryError,
    build_gallery_archive,
    gallery_archive_sources,
    gallery_item_details,
    gallery_media_path,
    gallery_payload,
    gallery_thumbnail_path,
    remove_gallery_item,
    remove_gallery_items,
    stitch_items,
)
from diony_producer.launch_api import prometheus_metrics
from diony_producer.launch_api import router as launch_router
from diony_producer.media_tools import resolve_media_tool
from diony_producer.observability import bind_correlation, log_event, reset_correlation
from diony_producer.security import verify_token

STATIC = Path(__file__).resolve().parent / "static"
_SESSION_ACTIVE = {"queued", "running", "waiting_provider", "retry_wait"}
_GALLERY_ARCHIVE_RETENTION_S = 24 * 60 * 60
_GALLERY_ARCHIVES: dict[str, dict] = {}
_GALLERY_ARCHIVES_LOCK = threading.Lock()

app = FastAPI(title="Diony Producer", version="0.9.0")
app.mount("/static", StaticFiles(directory=STATIC), name="static")
app.include_router(launch_router)
app.add_api_route("/metrics", prometheus_metrics, methods=["GET"], include_in_schema=False)


@app.middleware("http")
async def no_store_pages(request: Request, call_next):
    """The console and its API are never served from a browser's heuristic
    cache: an index cached across a desktop rebuild showed the previous UI."""
    response = await call_next(request)
    path = request.url.path
    if path == "/" or path.startswith("/static/") or path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-store, max-age=0"
        response.headers["Pragma"] = "no-cache"
    return response


@app.middleware("http")
async def correlation_context(request: Request, call_next):
    supplied = request.headers.get("X-Correlation-ID", "").strip()
    clean = re.sub(r"[^A-Za-z0-9._:-]", "-", supplied[:128])
    correlation_id = clean if clean else f"corr-{uuid.uuid4().hex}"
    request.state.correlation_id = correlation_id
    token = bind_correlation(correlation_id)
    started = time.monotonic()
    try:
        response = await call_next(request)
        response.headers["X-Correlation-ID"] = correlation_id
        log_event(
            "http.request.completed",
            method=request.method,
            path=request.url.path,
            status_code=response.status_code,
            duration_ms=round((time.monotonic() - started) * 1000, 2),
        )
        return response
    except Exception:
        log_event(
            "http.request.failed",
            level="error",
            method=request.method,
            path=request.url.path,
            duration_ms=round((time.monotonic() - started) * 1000, 2),
        )
        raise
    finally:
        reset_correlation(token)


@app.middleware("http")
async def mutation_auth(request: Request, call_next):
    """Shared controllers cannot mutate through an unsigned UI request."""
    path = request.url.path
    guarded = (
        os.environ.get("DIONY_AUTH_MODE", "local").strip().lower() == "token"
        and path.startswith("/api/")
        and not path.startswith("/api/v1/")
        and request.method not in {"GET", "HEAD", "OPTIONS"}
    )
    if guarded:
        authorization = request.headers.get("Authorization", "")
        if not authorization.lower().startswith("bearer "):
            return JSONResponse(
                {"detail": "A signed Diony workspace token is required."},
                status_code=401,
                headers={"WWW-Authenticate": "Bearer"},
            )
        try:
            actor = verify_token(authorization.split(" ", 1)[1].strip())
            ControlPlane().authorize(actor.user_id, actor.workspace_id, MembershipRole.CREATOR)
        except (ValueError, WorkspaceAccessDenied) as exc:
            return JSONResponse({"detail": str(exc)}, status_code=401)
    return await call_next(request)


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC / "index.html")


@app.get("/api/engine")
def engine_state() -> dict:
    """Which engine the Studio chose; Producer is the Gallery whatever the answer."""
    return {
        "cloud": engine_is_cloud(),
        "engine_choice": read_engine_choice(),
        "director_url": surface_url("director"),
        "studio_url": surface_url("studio"),
    }


def _director_session_stats() -> list[dict]:
    """Every Director run's job counts and spend from the shared plane —
    the receipt data each finished film card carries."""
    plane = ControlPlane()
    context = plane.bootstrap_local()
    sessions = []
    for run in plane.runs(context.workspace.id):
        if not run.id.startswith("director-"):
            continue
        jobs = plane.jobs(context.workspace.id, run.id)
        if not jobs:
            continue
        counts: dict[str, int] = {}
        actual = 0
        for job in jobs:
            counts[job.status.value] = counts.get(job.status.value, 0) + 1
            actual += job.actual_cost_usd_micros
        sessions.append(
            {
                "session": run.id.removeprefix("director-"),
                "jobs": len(jobs),
                "counts": counts,
                "actual_usd_micros": actual,
                "active": any(job.status.value in _SESSION_ACTIVE for job in jobs),
            }
        )
    sessions.sort(key=lambda item: item["active"], reverse=True)
    return sessions


@app.get("/api/cloud-production")
def cloud_production() -> dict:
    """Live cloud production status: every Director run's jobs from the shared plane."""
    return {"sessions": _director_session_stats()}


# --- the Finished Clips inventory -------------------------------------------


@app.get("/api/gallery")
def get_gallery() -> dict:
    """Every finished film and clip on this desk, with each film's receipt."""
    payload = gallery_payload()
    try:
        by_session = {row["session"]: row for row in _director_session_stats()}
    except Exception:
        by_session = {}
    for item in payload["items"]:
        row = by_session.get(item["session"], {})
        counts = row.get("counts", {})
        jobs = int(row.get("jobs", 0))
        printed = int(counts.get("succeeded", 0))
        failed = int(counts.get("failed", 0))
        item["spent_usd_micros"] = int(row.get("actual_usd_micros", 0))
        item["printed"] = printed
        item["failed"] = failed
        item["generating"] = max(0, jobs - printed - failed)
        item["active"] = bool(row.get("active", False))
    return payload


@app.get("/api/gallery/items/{item_id}/file")
def gallery_item_file(item_id: str, download: bool = False) -> FileResponse:
    try:
        path = gallery_media_path(item_id)
    except GalleryError as exc:
        raise HTTPException(404, str(exc)) from exc
    session, _, name = item_id.partition(":")
    filename = f"diony-{session}-{name}.mp4"
    return FileResponse(
        path,
        media_type="video/mp4",
        filename=filename if download else None,
        content_disposition_type="attachment" if download else "inline",
    )


@app.get("/api/gallery/items/{item_id}/thumbnail")
def gallery_item_thumbnail(item_id: str) -> FileResponse:
    try:
        path = gallery_thumbnail_path(item_id)
    except GalleryError as exc:
        raise HTTPException(404, str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(503, str(exc)) from exc
    return FileResponse(
        path,
        media_type="image/jpeg",
        headers={"Cache-Control": "public, max-age=31536000, immutable"},
    )


@app.get("/api/gallery/items/{item_id}/details")
def get_gallery_item_details(item_id: str) -> dict:
    try:
        return gallery_item_details(item_id)
    except GalleryError as exc:
        raise HTTPException(404, str(exc)) from exc


@app.delete("/api/gallery/items/{item_id}")
def delete_gallery_item(item_id: str) -> dict:
    try:
        return remove_gallery_item(item_id)
    except GalleryError as exc:
        raise HTTPException(404, str(exc)) from exc


class GalleryItemsBody(BaseModel):
    items: list[str]


def _gallery_archive_root() -> Path:
    return cluster_root() / ".control" / "gallery-archives"


def _gallery_archive_public(job: dict) -> dict:
    payload = {
        key: job[key]
        for key in (
            "archive_id",
            "state",
            "count",
            "completed",
            "source_bytes",
            "archive_bytes",
            "filename",
            "failure",
            "created_at",
        )
    }
    payload["status_url"] = f"/api/gallery/archive/{job['archive_id']}"
    payload["download_url"] = (
        f"/api/gallery/archive/{job['archive_id']}/file" if job["state"] == "ready" else ""
    )
    return payload


def _update_gallery_archive(archive_id: str, **changes) -> None:
    with _GALLERY_ARCHIVES_LOCK:
        job = _GALLERY_ARCHIVES.get(archive_id)
        if job is not None:
            job.update(changes)


def _build_gallery_archive(archive_id: str, sources: list[dict], destination: Path) -> None:
    _update_gallery_archive(archive_id, state="building")

    def progress(completed: int, _total: int) -> None:
        _update_gallery_archive(archive_id, completed=completed)

    try:
        result = build_gallery_archive(sources, destination, progress=progress)
    except Exception as exc:
        destination.unlink(missing_ok=True)
        _update_gallery_archive(
            archive_id,
            state="failed",
            failure=f"The ZIP could not be built: {exc}",
        )
        log_event("gallery.archive.failed", level="error", archive_id=archive_id, failure=str(exc))
        return
    _update_gallery_archive(
        archive_id,
        state="ready",
        completed=result["count"],
        source_bytes=result["source_bytes"],
        archive_bytes=result["archive_bytes"],
    )
    log_event(
        "gallery.archive.ready",
        archive_id=archive_id,
        items=result["count"],
        archive_bytes=result["archive_bytes"],
    )


def _discard_gallery_archive(archive_id: str) -> None:
    with _GALLERY_ARCHIVES_LOCK:
        job = _GALLERY_ARCHIVES.pop(archive_id, None)
    if not job:
        return
    root = Path(job["root"]).resolve()
    directory = Path(job["directory"]).resolve()
    if directory.parent == root:
        shutil.rmtree(directory, ignore_errors=True)


def _purge_stale_gallery_archives(root: Path) -> None:
    cutoff = time.time() - _GALLERY_ARCHIVE_RETENTION_S
    resolved_root = root.resolve()
    expired_directories: list[Path] = []
    with _GALLERY_ARCHIVES_LOCK:
        for archive_id, job in list(_GALLERY_ARCHIVES.items()):
            if (
                Path(job["root"]).resolve() == resolved_root
                and float(job.get("created_timestamp", 0)) < cutoff
            ):
                expired_directories.append(Path(job["directory"]))
                _GALLERY_ARCHIVES.pop(archive_id, None)
        active_ids = {
            archive_id
            for archive_id, job in _GALLERY_ARCHIVES.items()
            if Path(job["root"]).resolve() == resolved_root
        }
    for directory in expired_directories:
        resolved = directory.resolve()
        if resolved.parent == resolved_root:
            shutil.rmtree(resolved, ignore_errors=True)
    try:
        children = list(root.iterdir())
    except OSError:
        return
    for child in children:
        if (
            child.name in active_ids
            or not child.is_dir()
            or not re.fullmatch(r"[0-9a-f]{32}", child.name)
        ):
            continue
        try:
            stale = child.stat().st_mtime < cutoff
        except OSError:
            continue
        if stale:
            shutil.rmtree(child, ignore_errors=True)


@app.post("/api/gallery/archive", status_code=202)
def create_gallery_archive(body: GalleryItemsBody) -> dict:
    """Queue one local ZIP for the selection; MP4s are stored, never recompressed."""
    try:
        sources = gallery_archive_sources(body.items)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except GalleryError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    root = _gallery_archive_root()
    root.mkdir(parents=True, exist_ok=True)
    _purge_stale_gallery_archives(root)
    source_bytes = sum(int(source["bytes"]) for source in sources)
    reserve = 16 * 1024 * 1024
    if shutil.disk_usage(root).free < source_bytes + reserve:
        raise HTTPException(
            status_code=507,
            detail="There is not enough free disk space to build this ZIP.",
        )

    archive_id = uuid.uuid4().hex
    directory = root / archive_id
    directory.mkdir()
    filename = (
        f"diony-finished-clips-{len(sources)}-{datetime.now(UTC).strftime('%Y%m%d-%H%M%S')}.zip"
    )
    destination = directory / filename
    job = {
        "archive_id": archive_id,
        "state": "queued",
        "count": len(sources),
        "completed": 0,
        "source_bytes": source_bytes,
        "archive_bytes": 0,
        "filename": filename,
        "failure": "",
        "created_at": datetime.now(UTC).isoformat(),
        "created_timestamp": time.time(),
        "root": str(root.resolve()),
        "directory": str(directory.resolve()),
        "path": str(destination.resolve()),
    }
    with _GALLERY_ARCHIVES_LOCK:
        _GALLERY_ARCHIVES[archive_id] = job
    threading.Thread(
        target=_build_gallery_archive,
        args=(archive_id, sources, destination),
        name=f"gallery-archive-{archive_id[:8]}",
        daemon=True,
    ).start()
    with _GALLERY_ARCHIVES_LOCK:
        return _gallery_archive_public(_GALLERY_ARCHIVES[archive_id])


@app.get("/api/gallery/archive/{archive_id}")
def gallery_archive_status(archive_id: str) -> dict:
    _purge_stale_gallery_archives(_gallery_archive_root())
    with _GALLERY_ARCHIVES_LOCK:
        job = _GALLERY_ARCHIVES.get(archive_id)
        payload = _gallery_archive_public(job) if job else None
    if payload is None:
        raise HTTPException(status_code=404, detail="That ZIP is no longer available.")
    return payload


@app.get("/api/gallery/archive/{archive_id}/file")
def download_gallery_archive(archive_id: str) -> FileResponse:
    _purge_stale_gallery_archives(_gallery_archive_root())
    with _GALLERY_ARCHIVES_LOCK:
        job = dict(_GALLERY_ARCHIVES.get(archive_id) or {})
    if not job:
        raise HTTPException(status_code=404, detail="That ZIP is no longer available.")
    if job["state"] == "failed":
        raise HTTPException(status_code=409, detail=job["failure"])
    if job["state"] != "ready":
        raise HTTPException(status_code=409, detail="That ZIP is still being built.")
    path = Path(job["path"])
    if not path.is_file():
        _discard_gallery_archive(archive_id)
        raise HTTPException(status_code=404, detail="That ZIP is no longer available.")
    return FileResponse(
        path,
        media_type="application/zip",
        filename=job["filename"],
        content_disposition_type="attachment",
        background=BackgroundTask(_discard_gallery_archive, archive_id),
    )


@app.post("/api/gallery/delete")
def delete_gallery_items(body: GalleryItemsBody) -> dict:
    """Delete selected media in one request while keeping records and receipts."""
    try:
        return remove_gallery_items(body.items)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


class StitchItemsBody(BaseModel):
    items: list[str]
    title: str = ""


@app.post("/api/gallery/stitch")
def stitch_gallery_items(body: StitchItemsBody) -> dict:
    """Join selected clips and films, in the selection order, into a new film."""
    try:
        return stitch_items(body.items, title=body.title)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except GalleryError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


def _films_root() -> Path:
    return cluster_root() / "director"


def _safe_session(sid: str) -> str:
    return "".join(ch for ch in sid if ch.isalnum() or ch in "-_")


@app.get("/api/director-films")
def director_films() -> dict:
    """Finished Director cuts from the vault, newest first."""
    root = _films_root()
    films = []
    # each film carries its session's receipt: spend and job status from the
    # shared plane (a plane hiccup leaves the film list standing, costless)
    try:
        by_session = {row["session"]: row for row in _director_session_stats()}
    except Exception:
        by_session = {}
    if root.is_dir():
        # snapshot stats up front: a delete landing mid-listing drops that row
        # instead of 500ing the whole Gallery
        stats = []
        for film in root.glob("*/film.mp4"):
            try:
                stats.append((film, film.stat()))
            except OSError:
                continue
        for film, stat in sorted(stats, key=lambda item: item[1].st_mtime, reverse=True):
            session = film.parent.name
            plane_row = by_session.get(session, {})
            counts = plane_row.get("counts", {})
            jobs = int(plane_row.get("jobs", 0))
            printed = int(counts.get("succeeded", 0))
            failed = int(counts.get("failed", 0))
            films.append(
                {
                    "session": session,
                    "bytes": stat.st_size,
                    "modified": datetime.fromtimestamp(stat.st_mtime, tz=UTC).isoformat(),
                    "url": f"/api/director-films/{session}",
                    "spent_usd_micros": int(plane_row.get("actual_usd_micros", 0)),
                    "printed": printed,
                    "failed": failed,
                    "generating": max(0, jobs - printed - failed),
                    "active": bool(plane_row.get("active", False)),
                }
            )
    return {"films": films}


class StitchFilmsBody(BaseModel):
    sessions: list[str]
    title: str = ""


@app.post("/api/director-films/stitch")
def stitch_director_films(body: StitchFilmsBody) -> dict:
    """Join selected Director films into one cut.

    Films all come out of the Director's assembler with one raster/rate/codec,
    so a stream-copy concat is the fast path; a re-encode concat is the
    fallback when copy refuses. The join lands in the vault as its own
    `join-*` session, so it lists and serves like any film.
    """
    import subprocess

    if len(body.sessions) < 2:
        raise HTTPException(status_code=422, detail="select at least two films to stitch")
    ffmpeg = resolve_media_tool("ffmpeg")
    if not ffmpeg:
        raise HTTPException(
            status_code=409,
            detail="ffmpeg was not found — " + ffmpeg_install_hint(),
        )
    root = _films_root()
    sources: list[Path] = []
    for sid in body.sessions:
        safe = _safe_session(sid)
        path = root / safe / "film.mp4"
        if safe != sid or not path.is_file():
            raise HTTPException(status_code=404, detail=f"no film for session {sid}")
        sources.append(path)

    # Join in Gallery order — the Gallery lists newest FIRST and the selection
    # UI carries no ordering signal, so the top-to-bottom display order is the
    # only order the user can predict. A film deleted mid-request never 500s
    # the join; it is refused by name instead.
    def _mtime(path: Path) -> float:
        try:
            return path.stat().st_mtime
        except OSError:
            raise HTTPException(
                status_code=404, detail=f"a selected film vanished: {path.parent.name}"
            )

    sources.sort(key=_mtime, reverse=True)

    join_id = f"join-{uuid.uuid4().hex[:8]}"
    dest = root / join_id / "film.mp4"
    dest.parent.mkdir(parents=True, exist_ok=True)
    listing = dest.parent / "inputs.txt"
    listing.write_text(
        # ffmpeg concat demuxer: single quotes inside a quoted path are '\''
        "".join("file '" + str(src).replace("'", "'\\''") + "'\n" for src in sources),
        encoding="utf-8",
    )

    def _cleanup_partial() -> None:
        # never leave a truncated film.mp4 behind — it would list as real
        dest.unlink(missing_ok=True)
        listing.unlink(missing_ok=True)

    try:
        copy = subprocess.run(
            [
                ffmpeg,
                "-hide_banner",
                "-loglevel",
                "error",
                "-y",
                "-f",
                "concat",
                "-safe",
                "0",
                "-i",
                str(listing),
                "-c",
                "copy",
                str(dest),
            ],
            capture_output=True,
            timeout=600,
        )
    except subprocess.TimeoutExpired:
        _cleanup_partial()
        raise HTTPException(status_code=502, detail="stitch timed out on the copy pass")
    if copy.returncode != 0 or not dest.is_file() or dest.stat().st_size < 1024:
        chains = []
        concat_inputs = []
        cmd = [ffmpeg, "-hide_banner", "-loglevel", "error", "-y"]
        for src in sources:
            cmd += ["-i", str(src)]
        for index in range(len(sources)):
            chains.append(
                f"[{index}:v]scale=1280:720:force_original_aspect_ratio=decrease,"
                f"pad=1280:720:(ow-iw)/2:(oh-ih)/2,setsar=1,fps=24,format=yuv420p[v{index}]"
            )
            # per-film level match + declick before the join, loudness lock
            # after — a re-encoded stitch must not have audible level jumps
            chains.append(
                f"[{index}:a]aresample=48000,aformat=channel_layouts=stereo,"
                f"loudnorm=I=-16:TP=-1.5:LRA=11,aresample=48000,"
                f"afade=t=in:st=0:d=0.04[a{index}]"
            )
            concat_inputs.append(f"[v{index}][a{index}]")
        chains.append(f"{''.join(concat_inputs)}concat=n={len(sources)}:v=1:a=1[vc][ac]")
        chains.append("[ac]loudnorm=I=-16:TP=-1.0:LRA=11,aresample=48000[aout]")
        cmd += [
            "-filter_complex",
            ";".join(chains),
            "-map",
            "[vc]",
            "-map",
            "[aout]",
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-crf",
            "20",
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            str(dest),
        ]
        try:
            encode = subprocess.run(cmd, capture_output=True, timeout=1800)
        except subprocess.TimeoutExpired:
            _cleanup_partial()
            raise HTTPException(status_code=502, detail="stitch timed out on the re-encode pass")
        if encode.returncode != 0 or not dest.is_file():
            _cleanup_partial()
            raise HTTPException(
                status_code=502,
                detail=f"stitch failed: {encode.stderr.decode(errors='replace')[-300:]}",
            )
    listing.unlink(missing_ok=True)
    stat = dest.stat()
    return {
        "session": join_id,
        "bytes": stat.st_size,
        "modified": datetime.fromtimestamp(stat.st_mtime, tz=UTC).isoformat(),
        "url": f"/api/director-films/{join_id}",
        "joined_from": body.sessions,
    }


class DeleteFilmsBody(BaseModel):
    sessions: list[str]


@app.post("/api/director-films/delete")
def delete_director_films(body: DeleteFilmsBody) -> dict:
    """Remove selected Director film sessions from the vault.

    Media only: the ledger, receipts, and job records in the control plane are
    never touched — spend history survives every delete.
    """
    import shutil

    if not body.sessions:
        raise HTTPException(status_code=422, detail="select at least one film to remove")
    root = _films_root().resolve()
    removed: list[str] = []
    missing: list[str] = []
    for sid in body.sessions:
        safe = _safe_session(sid)
        target = (root / safe).resolve()
        if safe != sid or not target.is_relative_to(root) or not (target / "film.mp4").is_file():
            missing.append(sid)
            continue
        shutil.rmtree(target, ignore_errors=True)
        removed.append(sid)
    return {"removed": removed, "missing": missing}


@app.get("/api/director-films/{sid}")
def director_film(sid: str) -> FileResponse:
    safe = _safe_session(sid)
    path = _films_root() / safe / "film.mp4"
    if safe != sid or not path.is_file():
        raise HTTPException(status_code=404, detail="no such director film")
    return FileResponse(path, media_type="video/mp4")


def main() -> None:
    import uvicorn

    uvicorn.run(
        "diony_producer.app:app",
        host=bind_host(),
        port=int(os.environ.get("DIONY_PRODUCER_PORT", "8788")),
        reload=False,
        log_level="info",
    )


if __name__ == "__main__":
    main()
