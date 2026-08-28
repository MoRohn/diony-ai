from __future__ import annotations

import os
import re
import threading
import asyncio
import time
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from starlette.requests import Request
from starlette.responses import Response

import httpx

from diony_contracts.cluster import (
    ENGINE_FOR_MODE,
    RETAKE_CAP,
    bind_host,
    refuse_concurrent,
    site_from_lock,
    site_status,
    write_mode_lock,
)
from diony_contracts.enums import (
    CustomerKind,
    GateName,
    GalleryCompositionStatus,
    ProductionKind,
    ProductionStopDisposition,
    ProjectStatus,
    TakeOrigin,
    VoiceCastingSource,
)
from diony_contracts.fixture import demo_project
from diony_contracts.launch import MembershipRole
from diony_contracts.models import (
    Artifact,
    Asset,
    Brief,
    Gate,
    Note,
    ProductionInfrastructureRecovery,
    Project,
    Take,
)
from diony_contracts.stitch import assemble_program, program_view
from diony_contracts.studio import (
    CUSTOMER_CATALOG,
    TARGET_ALLOTMENTS,
    blank_studio,
    expand_idea,
    refresh_reference_shot,
)
from diony_producer.advance import (
    apply_mix_lock,
    apply_picture_lock,
    maybe_advance_run,
    reopen_picture_for_retake,
)
from diony_producer.brain import expand_on_site, plan_production
from diony_producer.soundtrack import plan_soundtrack
from diony_producer.sizing import announce_sizing
from diony_producer.camera import live_camera
from diony_producer.control_plane import ControlPlane, WorkspaceAccessDenied
from diony_producer.farm import (
    _clear_fail_for_reroll,
    abort_farm,
    camera_rolling,
    clear_production_pause,
    enter_farm,
    farm_status,
    hero_candidates_enabled,
    new_generation_take,
    reset_farm,
    request_pause_after_current,
    resume_camera,
    start_farm,
    take_has_live_negative,
)
from diony_producer.finish import abort_finish, finish_status, live_finish, reset_finish
from diony_producer.gallery import (
    gallery_item_details,
    gallery_payload,
    gallery_program_preview_path,
    gallery_reference_file,
    gallery_thumbnail_path,
    remove_gallery_item,
)
from diony_producer.gallery_join import (
    JoinRequest,
    TRANSITIONS,
    create_join_plan,
    join_manager,
    join_payload,
    preview_window,
    render_composition,
)
from diony_producer.mix import abort_mix, mix_status, reset_mix, start_mix
from diony_producer.gpu import (
    apply_ssh_env,
    backfill_connect_from_wizard,
    cached_gpu_monitor,
    drive_remote_advance,
    ensure_gpu_producer,
    gpu_block_copy,
    gpu_monitor,
    gpu_producer_ready_base,
    last_gpu_error,
    should_proxy,
    signed_gpu_site,
    wizard_pack_on_gpu,
)
from diony_producer.run import run_payload
from diony_producer.production_stop import finalize_production_stop, record_production_stop
from diony_producer.recalibration import recalibrate_stopped_project
from diony_producer.launch_api import prometheus_metrics, router as launch_router
from diony_producer.observability import bind_correlation, log_event, reset_correlation
from diony_producer.security import verify_token
from diony_producer.studio_entry_api import router as studio_entry_router
from diony_producer.vault import (
    abs_path,
    clear_activity,
    load_current_project,
    list_vault_projects,
    materialize_project,
    persist_project,
    record_activity,
    save_upload,
    vault_root,
    write_stub,
)
from diony_producer.voice import (
    VoiceCastingRegistry,
    VoicePipelineError,
)

STATIC = Path(__file__).resolve().parent / "static"
_gpu_reconcile_lock = threading.Lock()
_gpu_reconcile_thread: threading.Thread | None = None
_legacy_advance_lock = threading.Lock()
_legacy_advance_thread: threading.Thread | None = None
_legacy_advance_stop = threading.Event()


def _start_gpu_reconcile() -> None:
    """Reconcile the signed GPU without delaying the local Producer health gate."""
    global _gpu_reconcile_thread

    def reconcile() -> None:
        try:
            ensure_gpu_producer()
        except Exception as exc:  # noqa: BLE001 — keep the local control plane live
            log_event("gpu.producer.reconcile_failed", level="error", error=str(exc)[:800])

    with _gpu_reconcile_lock:
        if _gpu_reconcile_thread is not None and _gpu_reconcile_thread.is_alive():
            return
        _gpu_reconcile_thread = threading.Thread(
            target=reconcile,
            name="diony-gpu-producer-reconcile",
            daemon=True,
        )
        _gpu_reconcile_thread.start()


def _advance_legacy_once() -> None:
    """Advance outside request handling; GET endpoints remain observational."""
    if should_proxy():
        base = ensure_gpu_producer()
        if base:
            drive_remote_advance(base)
            gpu_monitor()
        return
    stop = _project.production_stop
    if (
        stop is not None
        and stop.disposition == ProductionStopDisposition.KEEP
        and stop.completed_take_ids
        and not stop.result_path
        and not stop.result_error
    ):
        # record_production_stop persists before cancellation. If the Producer
        # is replaced in that narrow window, finish the already-authorized Keep
        # exactly once on the next controller tick.
        abort_farm()
        abort_finish()
        abort_mix()
        finalize_production_stop(_project)
        return
    pause = _project.production_pause
    if pause is not None and pause.reason == "stop_keep":
        # Older Producers recorded STOP Keep as a Take-boundary drain. Honor
        # that already-authorized STOP immediately after an upgrade instead of
        # letting a wedged or long-running camera keep Production alive.
        _project.production_pause = None
        record_production_stop(_project, ProductionStopDisposition.KEEP)
        abort_farm()
        abort_finish()
        abort_mix()
        finalize_production_stop(_project)
        return
    maybe_advance_run(_project)


def _start_legacy_advance() -> None:
    global _legacy_advance_thread
    if os.environ.get("PYTEST_CURRENT_TEST"):
        return

    def loop() -> None:
        while not _legacy_advance_stop.is_set():
            try:
                _advance_legacy_once()
            except Exception as exc:  # noqa: BLE001 - controller stays available and retries
                log_event("legacy.advance.failed", level="error", error=str(exc)[:800])
            _legacy_advance_stop.wait(1.0)

    with _legacy_advance_lock:
        if _legacy_advance_thread is not None and _legacy_advance_thread.is_alive():
            return
        _legacy_advance_stop.clear()
        _legacy_advance_thread = threading.Thread(
            target=loop,
            name="diony-legacy-production-controller",
            daemon=True,
        )
        _legacy_advance_thread.start()


@asynccontextmanager
async def lifespan(_: FastAPI):
    backfill_connect_from_wizard()
    apply_ssh_env()
    if should_proxy():
        _start_gpu_reconcile()
    _start_legacy_advance()
    try:
        yield
    finally:
        _legacy_advance_stop.set()


app = FastAPI(title="Diony Producer", version="0.9.0", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=STATIC), name="static")
app.include_router(launch_router)
app.include_router(studio_entry_router)
app.add_api_route("/metrics", prometheus_metrics, methods=["GET"], include_in_schema=False)

_HOP = {"host", "content-length", "connection", "transfer-encoding", "content-encoding"}


_HOP = {"host", "content-length", "connection", "transfer-encoding", "content-encoding"}


_DESK_API_FALLBACK = frozenset({"/api/gallery"})
# Reads whose answer only means something from the GPU Producer: the desk's
# own project has no faces to cast, so a local fallback would say "no
# detector" while the GPU is merely restarting.
_GPU_ONLY_READS: frozenset[str] = frozenset()


# A signed GPU Producer restarts after every desktop update and takes two to
# three minutes to answer again (its lifespan resumes the camera before uvicorn
# binds :8788). A mutation that arrives in that window — a dropped image, a
# new Idea — waits here for the line instead of failing the person's action.
GPU_MUTATION_WAIT_SECONDS = float(os.environ.get("DIONY_GPU_MUTATION_WAIT_SECONDS", "360") or "360")
GPU_MUTATION_POLL_SECONDS = 3.0


def _gpu_unavailable_detail(waited: float, *, refused: bool) -> str:
    cause = last_gpu_error().strip()
    what = (
        "GPU Producer did not accept the request"
        if refused
        else "GPU control plane is not ready"
    )
    minutes = int(round(waited / 60)) if waited >= 60 else 0
    waited_copy = f"Diony waited {minutes} minute{'s' if minutes != 1 else ''}" if minutes else f"Diony waited {int(waited)} s"
    detail = (
        f"{what}. {waited_copy}; it is usually restarting after an update and answers within two to "
        "three minutes. Nothing was applied to the local fallback — try the same action again."
    )
    if cause:
        detail += f" Line: {cause[:240]}"
    return detail


async def _forward_to_gpu(request: Request, body: bytes, base: str) -> Response:
    path = request.url.path
    url = f"{base}{path}"
    if request.url.query:
        url = f"{url}?{request.url.query}"
    headers = {key: value for key, value in request.headers.items() if key.lower() not in _HOP}
    timeout = 30.0 if request.method == "GET" else 7200.0
    async with httpx.AsyncClient(timeout=timeout) as client:
        res = await client.request(request.method, url, headers=headers, content=body)
    out = {
        key: value
        for key, value in res.headers.items()
        if key.lower() not in _HOP and key.lower() != "content-length"
    }
    return Response(content=res.content, status_code=res.status_code, headers=out)


@app.middleware("http")
async def gpu_api_proxy(request: Request, call_next):
    """Signed lease: this desk is the UI. Wan farms on GPU Producer via the Connect SSH line."""
    path = request.url.path
    mutating = request.method.upper() not in {"GET", "HEAD", "OPTIONS"}
    if (
        not path.startswith("/api/")
        or path.startswith("/api/v1/")
        or path.startswith("/api/studio-entry/")
        or path in {"/api/run", "/api/catalog"}
    ):
        return await call_next(request)
    if not should_proxy():
        return await call_next(request)
    body = await request.body()
    started = time.monotonic()
    deadline = started + (GPU_MUTATION_WAIT_SECONDS if mutating else 0.0)
    refused = False
    while True:
        base = gpu_producer_ready_base()
        if base:
            try:
                res = await _forward_to_gpu(request, body, base)
            except httpx.HTTPError:
                refused = True
                if not mutating:
                    if path in _GPU_ONLY_READS:
                        return JSONResponse(status_code=503, content={"detail": _gpu_unavailable_detail(0.0, refused=True)}, headers={"Retry-After": "5"})
                    return await call_next(request)
            else:
                if res.status_code == 404 and path in _DESK_API_FALLBACK:
                    return await call_next(request)
                return res
        else:
            _start_gpu_reconcile()
            if not mutating:
                if path in _GPU_ONLY_READS:
                    return JSONResponse(status_code=503, content={"detail": _gpu_unavailable_detail(0.0, refused=False)}, headers={"Retry-After": "5"})
                return await call_next(request)
        if time.monotonic() >= deadline:
            waited = time.monotonic() - started
            return JSONResponse(
                status_code=502 if refused else 503,
                content={"detail": _gpu_unavailable_detail(waited, refused=refused)},
                headers={"Retry-After": "30"},
            )
        await asyncio.sleep(GPU_MUTATION_POLL_SECONDS)


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
async def legacy_mutation_auth(request: Request, call_next):
    """Shared controllers cannot mutate through an unsigned legacy UI request."""
    path = request.url.path
    guarded = (
        os.environ.get("DIONY_AUTH_MODE", "local").strip().lower() == "token"
        and path.startswith("/api/")
        and not path.startswith("/api/v1/")
        and not path.startswith("/api/studio-entry/")
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
            ControlPlane().authorize(
                actor.user_id, actor.workspace_id, MembershipRole.CREATOR
            )
        except (ValueError, WorkspaceAccessDenied) as exc:
            return JSONResponse({"detail": str(exc)}, status_code=401)
    return await call_next(request)


def _require_gpu() -> None:
    if not should_proxy():
        return
    if ensure_gpu_producer():
        return
    raise HTTPException(503, gpu_block_copy())

_project: Project = demo_project()
_assets: dict[str, Asset] = {}


def _apply_site(project: Project) -> Project:
    project.compute = site_from_lock()
    return project


def _seat_program(project: Project) -> Project:
    assemble_program(project)
    materialize_project(project)
    if project.timeline and project.timeline.preview_path:
        write_stub(project.timeline.preview_path, project.id, "program_stitch")
    return _apply_site(project)


def _boot_project() -> Project:
    loaded = load_current_project()
    if loaded:
        return _apply_site(loaded)
    return _apply_site(blank_studio())


class NoteIn(BaseModel):
    body: str
    shot_id: str | None = None
    take_id: str | None = None


class GateIn(BaseModel):
    locked_by: str = "showrunner"


class StudioOpenIn(BaseModel):
    idea: str = ""
    customer_kind: CustomerKind
    target_minutes: float = 5
    production_kind: ProductionKind = ProductionKind.PROGRAM
    sample_seconds: int | None = None
    title_hint: str = ""
    audience: str = ""
    attachment_ids: list[str] = []


class BiblePatch(BaseModel):
    title: str | None = None
    logline: str | None = None
    world: str | None = None
    visual_grammar: str | None = None


class ModeIn(BaseModel):
    mode: str
    engine: str = ""


class ProductionStopIn(BaseModel):
    disposition: ProductionStopDisposition


class ProductionRestartIn(BaseModel):
    reason: str = "reference_and_performance_recalibration"


class ProductionPauseIn(BaseModel):
    reason: str = "performance_qualification"
    after_take_id: str = ""


class ProductionInfrastructureRecoveryIn(BaseModel):
    reason: str = "gpu_service_restart"


class VoiceCastingIn(BaseModel):
    source: VoiceCastingSource = VoiceCastingSource.LIBRARY
    # Reference casting: one local audio Asset (5–15 s, ≥ 16 kHz) and its exact transcript.
    reference_asset_id: str = ""
    prompt_text: str = ""
    descriptors: dict[str, str] = {}


def _gate(name: GateName) -> Gate:
    for g in _project.gates:
        if g.name == name:
            return g
    raise HTTPException(404, name.value)


def _require_running_project() -> None:
    if _project.production_stop is not None:
        raise HTTPException(
            409,
            "Production already stopped. Start a new Idea to roll more clips.",
        )


def _dump() -> dict:
    _apply_site(_project)
    materialize_project(_project)
    persist_project(_project)
    return _project.model_dump(mode="json")


def _lookup_asset(aid: str) -> Asset:
    asset = _assets.get(aid)
    if asset is None:
        try:
            asset = _project.asset(aid)
        except KeyError as exc:
            raise HTTPException(404, f"unknown asset {aid}") from exc
    return asset


def _hold_assets(ids: list[str]) -> list[Asset]:
    return [_lookup_asset(aid) for aid in ids]


def _attach_assets(ids: list[str]) -> list[Asset]:
    held = _hold_assets(ids)
    for asset in held:
        if all(existing.id != asset.id for existing in _project.assets):
            _project.assets.append(asset)
    return held


def _find_artifact(art_id: str) -> Artifact:
    for take in _project.takes:
        for art in take.artifacts:
            if art.id == art_id:
                return art
    raise HTTPException(404, "artifact not on this project")


def _generation_take(shot, *, attempt: int | None = None) -> Take:
    take = new_generation_take(_project, shot, attempt=attempt)
    if not shot.selected_take_id:
        shot.selected_take_id = take.id
    return take


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC / "index.html")


@app.get("/api/project")
def get_project() -> dict:
    # Polling is observation, never project selection. Persisting here allowed
    # a transient blank process during GPU restart to replace current.json.
    _apply_site(_project)
    return _project.model_dump(mode="json")


@app.get("/api/voice-castings")
def get_voice_castings() -> dict:
    """Which Wan2.2 prompt clip each speaking character clones."""
    return {
        "project_id": _project.id,
        "castings": [casting.model_dump(mode="json") for casting in _project.voice_castings],
    }


@app.post("/api/characters/{character_id}/voice-casting")
def cast_voice(character_id: str, body: VoiceCastingIn) -> dict:
    """Cast a character on the site library (idempotent) or on an attached reference clip."""
    try:
        registry = VoiceCastingRegistry(_project)
        current = registry.current(character_id)
        if body.source == VoiceCastingSource.LIBRARY:
            if body.reference_asset_id:
                raise HTTPException(400, "Library casting does not accept reference audio")
            if current is not None:
                if current.source == VoiceCastingSource.LIBRARY:
                    return current.model_dump(mode="json")
                raise HTTPException(
                    409,
                    "This character already has a locked reference voice. Create a new Project or versioned Bible to change identity.",
                )
            casting = registry.ensure_library(character_id)
        else:
            if not body.reference_asset_id:
                raise HTTPException(400, "Reference casting needs one attached audio asset")
            if current is not None and current.source == VoiceCastingSource.REFERENCE:
                raise HTTPException(
                    409,
                    "This character already has a locked reference voice. Create a new Project or versioned Bible to change identity.",
                )
            casting = registry.cast_from_reference(
                character_id,
                body.reference_asset_id,
                prompt_text=body.prompt_text,
                descriptors=body.descriptors,
                vault=vault_root(),
            )
        persist_project(_project)
        return casting.model_dump(mode="json")
    except HTTPException:
        raise
    except (KeyError, VoicePipelineError, ValueError) as exc:
        raise HTTPException(400, str(exc)) from exc


@app.post("/api/projects/{project_id}/activate")
def activate_vault_project(project_id: str) -> dict:
    """Select an existing durable Project without creating or rerolling Takes."""
    global _project, _assets
    if camera_rolling():
        raise HTTPException(409, "Cannot switch Projects while Camera is rolling.")
    target = next((item for item in list_vault_projects() if item.id == project_id), None)
    if target is None:
        raise HTTPException(404, "Project is not in this vault.")
    abort_farm()
    abort_finish()
    abort_mix()
    reset_farm()
    reset_finish()
    reset_mix()
    _project = resume_camera(_apply_site(target))
    _assets = {asset.id: asset for asset in _project.assets}
    persist_project(_project)
    maybe_advance_run(_project)
    return _project.model_dump(mode="json")


@app.get("/api/site")
def get_site() -> dict:
    return site_status()


@app.get("/api/cluster/mode")
def get_mode() -> dict:
    from diony_contracts.cluster import read_mode_lock

    return read_mode_lock() or {
        "mode": _project.fleet_mode,
        "engine": "none",
        "sequential": site_status()["sequential"],
    }


@app.post("/api/cluster/mode")
def set_mode(body: ModeIn) -> dict:
    mode = body.mode.strip().upper()
    if mode not in ENGINE_FOR_MODE:
        raise HTTPException(400, "mode must be BRAIN, SPLIT, FARM, or FINISH")
    engine = (body.engine or ENGINE_FOR_MODE[mode]).strip().lower()
    held = refuse_concurrent(mode, engine)
    if held:
        raise HTTPException(400, held)
    payload = write_mode_lock(mode, engine)
    _project.fleet_mode = mode
    return {"mode": payload, "project": _dump()}


@app.get("/api/farm")
def get_farm() -> dict:
    return farm_status()


@app.get("/api/run")
def get_run() -> dict:
    """Production floor: the autonomous run, not a shot list."""
    if should_proxy():
        # Reconciliation and SSH sampling belong to the background controller.
        # A browser status read never starts/restarts remote services.
        base = gpu_producer_ready_base()
        if base:
            try:
                res = httpx.get(base + "/api/run", timeout=8.0)
                if res.status_code == 200:
                    payload = res.json()
                    if isinstance(payload, dict):
                        monitor = cached_gpu_monitor()
                        if monitor.get("observed_at") or monitor.get("heartbeat"):
                            payload["monitor"] = monitor
                            monitoring = payload.get("monitoring")
                            if isinstance(monitoring, dict):
                                gpu = monitor.get("gpu")
                                if isinstance(gpu, dict):
                                    monitoring["gpu"] = {
                                        **gpu,
                                        "heartbeat": monitor.get("heartbeat") or "",
                                    }
                                monitoring["connection"] = "live"
                                signal = monitoring.get("signal")
                                if isinstance(signal, dict) and monitor.get("observed_at"):
                                    signal["updated_at"] = monitor["observed_at"]
                        return payload
            except (httpx.HTTPError, ValueError, TypeError):
                pass
            try:
                proj = Project.model_validate(httpx.get(base + "/api/project", timeout=8.0).json())
                farm = httpx.get(base + "/api/farm", timeout=8.0).json()
                fin = dict(httpx.get(base + "/api/finish", timeout=8.0).json())
                mix = dict(httpx.get(base + "/api/mix", timeout=8.0).json())
                if wizard_pack_on_gpu():
                    fin["live"] = True
                cam = True
                if isinstance(farm.get("live"), bool):
                    cam = bool(farm["live"]) or wizard_pack_on_gpu()
                return run_payload(proj, farm=farm, finish=fin, mix=mix, live=cam)
            except (httpx.HTTPError, ValueError, KeyError):
                pass
            try:
                res = httpx.get(base + "/api/run", timeout=8.0)
                if res.status_code == 200:
                    return res.json()
            except httpx.HTTPError:
                pass
    return run_payload(_project)


@app.post("/api/run/advance")
def advance_run_controller() -> dict:
    """Explicit compatibility control action; never hidden inside GET /api/run."""
    _advance_legacy_once()
    return get_run()


@app.post("/api/production/stop")
def stop_production(body: ProductionStopIn) -> dict:
    """Stop all future dispatch, then keep or delete this Project's completed clips."""
    _require_gpu()
    if _project.production_started_at is None or not _project.takes:
        raise HTTPException(409, "Production has not started.")
    if _project.production_stop is not None:
        if _project.production_stop.disposition != body.disposition:
            raise HTTPException(
                409,
                (
                    "Production already stopped with "
                    f"{_project.production_stop.disposition.value} selected."
                ),
            )
        return run_payload(_project)
    # STOP is immediate for both retention choices. Keep snapshots only the
    # checksum-valid, QC-passed negatives that landed before this request; an
    # in-flight camera process is cancelled and is never promoted into Keep.
    _project.production_pause = None
    try:
        record_production_stop(_project, body.disposition)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    abort_farm()
    abort_finish()
    abort_mix()
    try:
        finalize_production_stop(_project)
    except Exception as exc:  # noqa: BLE001 — STOP is durable even if the partial stitch fails
        raise HTTPException(
            500,
            f"Production stopped, but the incomplete result could not be saved: {exc}",
        ) from exc
    return run_payload(_project)


@app.get("/api/finish")
def get_finish() -> dict:
    return finish_status()


@app.get("/api/mix")
def get_mix() -> dict:
    return mix_status()


@app.post("/api/mix/start")
def begin_mix() -> dict:
    start_mix(_project)
    return mix_status()


@app.post("/api/mix/retry")
def retry_mix() -> dict:
    reset_mix()
    start_mix(_project)
    return mix_status()


@app.get("/api/catalog")
def catalog() -> dict:
    site = site_status()
    live = live_camera()
    if live:
        camera = "Wan2.2 generate.py 720p"
    elif signed_gpu_site():
        camera = "Wan2.2 on the signed GPU. This desk is not the camera."
    else:
        camera = "stub until /models/wan is present"
    return {
        "customers": CUSTOMER_CATALOG,
        "allotments": TARGET_ALLOTMENTS,
        "clock": site["clock"],
        "negative": site["negative"],
        "master": site["master"],
        "floors": ["idea", "production", "gallery"],
        "stitch": "overlap 8–16 frames; I2V inherits the previous print",
        "site": site,
        "retake_cap": RETAKE_CAP,
        "dry_run": (not live) and (not signed_gpu_site()),
        "live": live,
        "camera": camera,
        "finish": (
            "diony_sr_v1 1920×1080 Rec.709"
            if live_finish() or wizard_pack_on_gpu()
            else "1080 finish when SR weights are present"
        ),
    }


@app.post("/api/assets")
async def upload_asset(
    file: UploadFile = File(...),
    role: str = "idea_ref",
) -> dict:
    payload = await file.read()
    try:
        asset = save_upload(file.filename or "untitled", payload, role=role)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    _assets[asset.id] = asset
    return asset.model_dump(mode="json")


@app.get("/api/assets/{asset_id}/file")
def asset_file(asset_id: str) -> FileResponse:
    asset = _assets.get(asset_id)
    if asset is None:
        try:
            asset = _project.asset(asset_id)
        except KeyError as exc:
            raise HTTPException(404, "unknown asset") from exc
    path = abs_path(asset.path)
    if not path.is_file():
        raise HTTPException(404, "asset bytes missing")
    return FileResponse(path, filename=asset.filename, media_type=asset.mime)


@app.post("/api/studio/open")
def studio_open(body: StudioOpenIn) -> dict:
    global _project
    _require_gpu()
    held = _hold_assets(body.attachment_ids)
    try:
        brief = Brief(
            idea=body.idea,
            customer_kind=body.customer_kind,
            target_minutes=body.target_minutes,
            production_kind=body.production_kind,
            sample_seconds=body.sample_seconds,
            title_hint=body.title_hint,
            audience=body.audience,
            attachment_ids=body.attachment_ids,
        )
    except Exception as exc:
        raise HTTPException(400, str(exc)) from exc
    if camera_rolling():
        raise HTTPException(
            409,
            "Wan2.2 is already rolling a 720p negative. Refresh starts a new Idea. Do not Produce a second show on this GPU.",
        )
    try:
        candidate = expand_idea(brief, held, compute=site_from_lock())
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    _project = candidate
    abort_farm()
    reset_farm()
    persist_project(_project)
    return _dump()


@app.post("/api/studio/new")
def studio_new() -> dict:
    """Blank Idea. Active production must use the explicit STOP workflow first."""
    global _project, _assets
    active = (
        camera_rolling()
        or bool(farm_status().get("busy"))
        or bool(finish_status().get("busy"))
        or bool(mix_status().get("busy"))
        or (
            _project.production_pause is not None
            and _project.production_pause.reason == "stop_keep"
        )
    )
    if active:
        raise HTTPException(
            409,
            "Start over cannot interrupt an active production. Use STOP, choose Keep or Delete, then start over.",
        )
    abort_farm()
    abort_finish()
    abort_mix()
    clear_activity()
    _assets = {}
    _project = _apply_site(blank_studio(site_from_lock()))
    persist_project(_project)
    reset_farm()
    reset_finish()
    reset_mix()
    return _dump()


@app.patch("/api/bible")
def patch_bible(body: BiblePatch) -> dict:
    if not _gate(GateName.BIBLE).open:
        raise HTTPException(400, "bible is locked")
    if body.title is not None:
        _project.bible.title = body.title.strip() or _project.bible.title
    if body.logline is not None:
        _project.bible.logline = body.logline.strip() or _project.bible.logline
    if body.world is not None:
        _project.bible.world = body.world.strip() or _project.bible.world
    if body.visual_grammar is not None:
        _project.bible.visual_grammar = (
            body.visual_grammar.strip() or _project.bible.visual_grammar
        )
    return _dump()


@app.post("/api/shots/{shot_id}/expand")
def expand_one(shot_id: str) -> dict:
    shot = _project.shot(shot_id)
    shot.prompt = expand_on_site(shot, _project.bible)
    return _dump()


@app.post("/api/studio/roll")
def roll_dailies() -> dict:
    """Farm Wan2.2 720p negatives when the pack is on this box. Laptop stays stub."""
    _require_gpu()
    _require_running_project()
    _project.mark_production_start()
    # Prompt text is part of the immutable camera receipt. A recovery roll
    # must not silently rewrite the prompt behind an already-created Take, so
    # planning only touches shots that have no Take yet — batched, with every
    # outcome on the activity feed.
    plan_production(
        _project,
        [shot for shot in _project.shots if not _project.takes_for(shot.id)],
    )
    # Soundtrack cues are edit-listed while the brain is still resident; the
    # digest-protected plan is what Mix builds the licensed stems from later.
    plan_soundtrack(_project)
    queued: list = []
    for shot in _project.shots:
        existing = _project.takes_for(shot.id)
        if existing:
            take = existing[-1]
            if not shot.selected_take_id:
                shot.selected_take_id = take.id
            if live_camera() and not take_has_live_negative(take, _project):
                _clear_fail_for_reroll(take)
                queued.append(take)
            continue
        take = _generation_take(shot)
        _project.takes.append(take)
        queued.append(take)
        if shot.hero and live_camera() and hero_candidates_enabled():
            # Hero-class coverage rolls a second seed candidate; AutoQC scores
            # pick the print once both land.
            sibling = _generation_take(shot, attempt=2)
            _project.takes.append(sibling)
            queued.append(sibling)
    if live_camera() and queued:
        announce_sizing(_project, planned_takes=len(queued))
    warning = enter_farm() if live_camera() else None
    if warning and _project.shots:
        _project.notes.append(
            Note(
                id=f"n-farm-{len(_project.notes) + 1}",
                author="conductor",
                body=warning,
                shot_id=_project.shots[0].id,
            )
        )
    if live_camera() and queued:
        start_farm(_project, queued)
    assemble_program(_project)
    if _project.timeline and _project.timeline.preview_path:
        write_stub(_project.timeline.preview_path, _project.id, "program_stitch")
    _project.status = ProjectStatus.SHOOTING
    _project.fleet_mode = "FARM"
    maybe_advance_run(_project)
    return _dump()


@app.post("/api/production/restart")
def restart_production(body: ProductionRestartIn) -> dict:
    """Restart this stopped Project using today's prompt/reference contract."""
    _require_gpu()
    if _project.production_stop is None:
        raise HTTPException(
            409,
            "Production must be durably stopped before it can be restarted.",
        )
    abort_farm()
    abort_finish()
    abort_mix()
    reset_farm()
    reset_finish()
    reset_mix()
    clear_activity()
    try:
        recalibrate_stopped_project(_project, reason=body.reason)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    persist_project(_project)
    return roll_dailies()


@app.post("/api/production/pause-after-current")
def pause_after_current(body: ProductionPauseIn) -> dict:
    """Drain at the active immutable Take boundary without stopping the Project."""
    _require_gpu()
    if _project.production_stop is not None:
        raise HTTPException(409, "Stopped Production cannot be paused.")
    status = farm_status()
    active_take_id = body.after_take_id.strip()
    if not active_take_id:
        camera = status.get("camera") if isinstance(status.get("camera"), dict) else {}
        active_take_id = str(camera.get("take_id") or "")
    try:
        request_pause_after_current(
            _project,
            reason=body.reason,
            after_take_id=active_take_id,
        )
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    return _dump()


@app.post("/api/production/recover-infrastructure")
def recover_infrastructure(body: ProductionInfrastructureRecoveryIn) -> dict:
    """Resume the same immutable Take set after a non-quality service interruption."""
    if _project.production_stop is None:
        raise HTTPException(409, "Production has no interrupted STOP receipt to recover.")
    if camera_rolling():
        raise HTTPException(409, "Camera must be idle before infrastructure recovery.")
    reason = body.reason.strip()
    if not reason:
        raise HTTPException(400, "Infrastructure recovery requires a reason.")
    selected = [shot.selected_take_id for shot in _project.shots if shot.selected_take_id]
    _project.production_recoveries.append(
        ProductionInfrastructureRecovery(
            reason=reason,
            interrupted_stop=_project.production_stop,
            resumed_take_ids=selected,
        )
    )
    _project.production_stop = None
    _project.production_ended_at = None
    _project.status = ProjectStatus.SHOOTING
    reset_farm()
    reset_finish()
    reset_mix()
    persist_project(_project)
    record_activity(
        "run",
        f"Infrastructure recovery · reusing {len(selected)} immutable Takes",
        level="info",
    )
    return _project.model_dump(mode="json")


@app.post("/api/production/resume")
def resume_production() -> dict:
    """Resume the same selected Take set after a durable drain checkpoint."""
    _require_gpu()
    if _project.production_stop is not None:
        raise HTTPException(409, "Stopped Production requires a recalibrated restart.")
    if _project.production_pause is None:
        return _dump()
    clear_production_pause(_project)
    return roll_dailies()


@app.get("/api/program")
def get_program() -> dict:
    view = program_view(_project)
    view["project_id"] = _project.id
    return view


def _program_file_response(project: Project, *, download: bool = False) -> FileResponse:
    if not project.timeline or not project.timeline.preview_path:
        raise HTTPException(404, "no program file yet")
    path = abs_path(project.timeline.preview_path)
    if not path.is_file():
        raise HTTPException(404, "program bytes missing")
    suffix = path.suffix.lower()
    media = {
        ".mp4": "video/mp4",
        ".mov": "video/quicktime",
        ".webm": "video/webm",
        ".mkv": "video/x-matroska",
    }.get(suffix)
    return FileResponse(path, filename=path.name if download else None, media_type=media)


@app.get("/api/program/file")
def program_file(download: bool = False) -> FileResponse:
    return _program_file_response(_project, download=download)


_DELIVERABLE_MEDIA = {
    ".mov": "video/quicktime",
    ".mp4": "video/mp4",
    ".wav": "audio/wav",
    ".srt": "application/x-subrip",
    ".vtt": "text/vtt",
    ".json": "application/json",
    ".edl": "text/plain",
}


def _deliverable_or_404(project: Project, deliverable_id: str) -> Path:
    """Only a deliverable listed on this Project, inside its own vault namespace."""
    row = next((item for item in project.deliverables if item.id == deliverable_id), None)
    if row is None:
        raise HTTPException(404, "deliverable not on this project")
    root = (vault_root() / "projects" / project.id).resolve()
    path = abs_path(row.path).resolve()
    if root != path and root not in path.parents:
        raise HTTPException(404, "deliverable is outside this project's vault namespace")
    if not path.is_file():
        raise HTTPException(404, "deliverable bytes missing")
    return path


@app.get("/api/program/deliverables")
def program_deliverables() -> dict:
    """Listed deliverables of the current program (Blueprint 24): master, stems, subtitles, QC packet."""
    return {
        "project_id": _project.id,
        "final_review": (
            {"status": _project.final_review.status.value, "issues": list(_project.final_review.issues_found)}
            if _project.final_review is not None
            else None
        ),
        "deliverables": [
            {
                "id": item.id,
                "kind": item.kind.value,
                "path": item.path,
                "sha256": item.sha256,
                "url": f"/api/deliverables/{item.id}/file",
            }
            for item in _project.deliverables
        ],
    }


@app.get("/api/deliverables/{deliverable_id}/file")
def deliverable_file(deliverable_id: str, download: bool = True) -> FileResponse:
    path = _deliverable_or_404(_project, deliverable_id)
    return FileResponse(
        path,
        filename=path.name if download else None,
        media_type=_DELIVERABLE_MEDIA.get(path.suffix.lower(), "application/octet-stream"),
    )


@app.get("/api/projects/{project_id}/program/file")
def project_program_file(project_id: str, download: bool = False) -> FileResponse:
    if project_id == _project.id:
        return _program_file_response(_project, download=download)
    path = vault_root() / "projects" / project_id / "project.json"
    if not path.is_file():
        raise HTTPException(404, "project not in this vault")
    try:
        other = Project.model_validate_json(path.read_text())
    except ValueError as exc:
        raise HTTPException(404, "project could not be read") from exc
    return _program_file_response(other, download=download)


@app.get("/api/projects/{project_id}/program/preview")
def project_program_preview(project_id: str) -> FileResponse:
    try:
        path = gallery_program_preview_path(_project, project_id)
    except KeyError as exc:
        raise HTTPException(404, "verified Gallery program is not available") from exc
    except RuntimeError as exc:
        raise HTTPException(503, str(exc)) from exc
    return FileResponse(path, media_type="video/mp4")


@app.get("/api/gallery")
def get_gallery() -> dict:
    materialize_project(_project)
    return gallery_payload(_project)


@app.get("/api/gallery/items/{clip_id}/thumbnail")
def gallery_item_thumbnail(clip_id: str) -> FileResponse:
    try:
        path = gallery_thumbnail_path(_project, clip_id)
    except KeyError as exc:
        raise HTTPException(404, "Gallery clip is not available") from exc
    except RuntimeError as exc:
        raise HTTPException(503, str(exc)) from exc
    return FileResponse(
        path,
        media_type="image/jpeg",
        headers={"Cache-Control": "public, max-age=31536000, immutable"},
    )


@app.get("/api/gallery/items/{clip_id}/details")
def get_gallery_item_details(clip_id: str) -> dict:
    try:
        return gallery_item_details(_project, clip_id)
    except KeyError as exc:
        raise HTTPException(404, "Gallery clip is not available") from exc


@app.get("/api/gallery/items/{clip_id}/references/{asset_id}/file")
def gallery_reference_download(clip_id: str, asset_id: str) -> FileResponse:
    try:
        path, asset = gallery_reference_file(_project, clip_id, asset_id)
    except (FileNotFoundError, KeyError, ValueError) as exc:
        raise HTTPException(404, "Gallery reference is not available") from exc
    return FileResponse(path, filename=asset.filename, media_type=asset.mime)


@app.delete("/api/gallery/items/{clip_id}")
def delete_gallery_item(clip_id: str) -> dict:
    try:
        return remove_gallery_item(_project, clip_id)
    except KeyError as exc:
        raise HTTPException(404, "Gallery clip is not available") from exc


def _join_attribution(request: Request) -> tuple[str, list[str], str, str, ControlPlane]:
    plane = ControlPlane()
    mode = os.environ.get("DIONY_AUTH_MODE", "local").strip().lower()
    if mode == "local":
        context = plane.bootstrap_local()
        membership = context.membership
        actor_user_id = context.user.id
        workspace_id = context.workspace.id
    else:
        authorization = request.headers.get("Authorization", "")
        if not authorization.lower().startswith("bearer "):
            raise HTTPException(401, "A signed Diony workspace token is required.")
        try:
            actor = verify_token(authorization.split(" ", 1)[1].strip())
            membership = plane.authorize(actor.user_id, actor.workspace_id, MembershipRole.CREATOR)
        except (ValueError, WorkspaceAccessDenied) as exc:
            raise HTTPException(401, str(exc)) from exc
        actor_user_id = actor.user_id
        workspace_id = actor.workspace_id
    centers = [membership.personal_cost_center_id, *membership.assigned_cost_center_ids]
    return (
        membership.profile_name or actor_user_id,
        sorted({center for center in centers if center}),
        actor_user_id,
        workspace_id,
        plane,
    )


def _plan_join(body: JoinRequest, request: Request):
    profile, centers, _, _, _ = _join_attribution(request)
    try:
        return create_join_plan(
            _project,
            body,
            profile_name=profile,
            cost_center_ids=centers,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@app.get("/api/gallery/joins/options")
def join_options() -> dict:
    return {
        "precision": "frames",
        "display": ["timecode", "milliseconds"],
        "transitions": TRANSITIONS,
        "output": "1920×1080 · Rec.709 · source audio preserved",
        "limits": {"clips": 32, "transition_frames": 120},
    }


@app.post("/api/gallery/joins/plan")
def plan_gallery_join(body: JoinRequest, request: Request) -> dict:
    return join_payload(_plan_join(body, request))


@app.post("/api/gallery/joins/preview")
def preview_gallery_join(body: JoinRequest, request: Request) -> dict:
    composition = _plan_join(body, request)
    preview = preview_window(composition, body.preview_junction)
    try:
        render_composition(
            preview,
            abs_path(composition.preview_path),
            abs_path(composition.preview_path),
            preview_only=True,
        )
    except (OSError, RuntimeError, InterruptedError) as exc:
        raise HTTPException(500, f"Preview could not be rendered. {str(exc)[:300]}") from exc
    join_manager.remember(composition)
    payload = join_payload(composition)
    payload["preview_url"] = f"/api/gallery/joins/{composition.id}/preview"
    payload["preview_junction"] = min(body.preview_junction, len(composition.junctions) - 1)
    return payload


@app.post("/api/gallery/joins", status_code=202)
def create_gallery_join(body: JoinRequest, request: Request) -> dict:
    composition = _plan_join(body, request)
    profile, centers, actor_user_id, workspace_id, plane = _join_attribution(request)
    correlation_id = getattr(request.state, "correlation_id", f"corr-{uuid.uuid4().hex}")
    composition.profile_name = profile
    composition.cost_center_ids = centers

    def record(done) -> None:
        plane.audit(
            workspace_id=workspace_id,
            actor_user_id=actor_user_id,
            action=f"gallery.join.{done.status.value}",
            object_type="clip_composition",
            object_id=done.id,
            correlation_id=correlation_id,
            after=done,
            metadata={
                "project_id": done.project_id,
                "source_asset_ids": done.source_asset_ids,
                "cost_center_ids": done.cost_center_ids,
                "profile_name": done.profile_name,
            },
        )

    join_manager.start(_project, composition, on_complete=record)
    return join_payload(composition)


def _find_composition_owner(composition_id: str):
    try:
        composition = join_manager.get(composition_id)
        for project in (_project, *list_vault_projects()):
            if project.id == composition.project_id:
                return project, composition
        return _project, composition
    except KeyError:
        for project in (_project, *list_vault_projects()):
            for composition in project.compositions:
                if composition.id == composition_id:
                    return project, composition
    raise HTTPException(404, "Join version not found")


def _find_composition(composition_id: str):
    return _find_composition_owner(composition_id)[1]


@app.get("/api/gallery/joins/{composition_id}")
def gallery_join_status(composition_id: str) -> dict:
    return join_payload(_find_composition(composition_id))


@app.delete("/api/gallery/joins/{composition_id}")
def cancel_gallery_join(composition_id: str) -> dict:
    try:
        composition = join_manager.cancel(composition_id)
    except KeyError as exc:
        raise HTTPException(404, "Join job not found") from exc
    return join_payload(composition)


@app.post("/api/gallery/joins/{composition_id}/undo")
def undo_gallery_join(composition_id: str) -> dict:
    owner, composition = _find_composition_owner(composition_id)
    if composition.status != GalleryCompositionStatus.COMPLETE:
        raise HTTPException(409, "Only a completed joined version can be removed from the Gallery")
    composition.status = GalleryCompositionStatus.SUPERSEDED
    composition.updated_at = datetime.now(timezone.utc)
    persist_project(owner, set_current=False)
    return {
        "status": "superseded",
        "detail": "Removed from the Gallery. Source clips and the version manifest remain intact.",
    }


@app.get("/api/gallery/joins/{composition_id}/preview")
def gallery_join_preview(composition_id: str) -> FileResponse:
    composition = _find_composition(composition_id)
    path = abs_path(composition.preview_path)
    if not path.is_file():
        raise HTTPException(404, "Preview is no longer available")
    return FileResponse(path, media_type="video/mp4")


@app.get("/api/gallery/joins/{composition_id}/file")
def gallery_join_file(
    composition_id: str,
    proxy: bool = False,
    download: bool = False,
) -> FileResponse:
    composition = _find_composition(composition_id)
    rel = composition.proxy_path if proxy else composition.output_path
    path = abs_path(rel)
    if not path.is_file():
        raise HTTPException(404, "Joined version bytes are missing")
    return FileResponse(
        path,
        filename=path.name if download else None,
        media_type="video/mp4" if proxy else "video/quicktime",
    )


@app.post("/api/program/assemble")
def assemble() -> dict:
    global _project
    missing = [s.id for s in _project.shots if not _project.takes_for(s.id)]
    if missing:
        raise HTTPException(400, f"Roll dailies before stitch. Missing takes: {', '.join(missing)}")
    _project = assemble_program(_project)
    if _project.timeline and _project.timeline.preview_path:
        write_stub(_project.timeline.preview_path, _project.id, "program_stitch")
    return {"program": program_view(_project), "project": _dump()}


@app.get("/api/artifacts/{art_id}/file")
def artifact_file(art_id: str) -> FileResponse:
    art = _find_artifact(art_id)
    path = abs_path(art.path)
    if not path.is_file():
        materialize_project(_project)
        path = abs_path(art.path)
    if not path.is_file():
        raise HTTPException(404, "artifact bytes missing")
    suffix = path.suffix.lower()
    media = "video/mp4" if suffix == ".mp4" else None
    return FileResponse(path, filename=Path(art.path).name, media_type=media)


@app.post("/api/notes")
def add_note(body: NoteIn) -> dict:
    if not body.shot_id and not body.take_id:
        raise HTTPException(400, "notes attach to shot_id or take_id")
    nid = f"n-{len(_project.notes) + 1}"
    _project.notes.append(
        Note(
            id=nid,
            author="human",
            body=body.body,
            shot_id=body.shot_id,
            take_id=body.take_id,
        )
    )
    return _dump()


@app.post("/api/shots/{shot_id}/select/{take_id}")
def select_take(shot_id: str, take_id: str) -> dict:
    shot = _project.shot(shot_id)
    takes = _project.takes_for(shot_id)
    if take_id not in {t.id for t in takes}:
        raise HTTPException(404, "take not on this shot")
    if shot.selected_take_id == take_id:
        return _dump()
    reopen_picture_for_retake(_project)
    shot.selected_take_id = take_id
    assemble_program(_project)
    if _project.timeline and _project.timeline.preview_path:
        write_stub(_project.timeline.preview_path, _project.id, "program_stitch")
    maybe_advance_run(_project)
    return _dump()


@app.post("/api/shots/{shot_id}/retake")
def retake_shot(shot_id: str) -> dict:
    """Reject-and-retake. Live Wan2.2 when the pack is on this box. Cap 4 generation takes."""
    _require_running_project()
    reopen_picture_for_retake(_project)
    try:
        shot = _project.shot(shot_id)
    except KeyError as exc:
        raise HTTPException(404, "shot not on this project") from exc
    gens = [
        t
        for t in _project.takes_for(shot_id)
        if t.origin == TakeOrigin.GENERATION
    ]
    if len(gens) >= RETAKE_CAP:
        raise HTTPException(
            400,
            f"Retake cap ({RETAKE_CAP}). Pin a note and stop for a human.",
        )
    refresh_reference_shot(_project, shot)
    take = _generation_take(shot)
    _project.takes.append(take)
    shot.selected_take_id = take.id
    if live_camera():
        enter_farm()
        start_farm(_project, [take])
    assemble_program(_project)
    if _project.timeline and _project.timeline.preview_path:
        write_stub(_project.timeline.preview_path, _project.id, "program_stitch")
    maybe_advance_run(_project)
    return _dump()


@app.post("/api/gates/{name}/lock")
def lock_gate(name: GateName, body: GateIn) -> dict:
    if name == GateName.PICTURE:
        try:
            apply_picture_lock(_project, body.locked_by or "producer")
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        return _dump()
    if name == GateName.MIX:
        try:
            apply_mix_lock(_project, body.locked_by or "producer")
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        return _dump()
    gate = _gate(name)
    if name == GateName.DISTRIBUTION:
        mix = _gate(GateName.MIX)
        if mix.open:
            raise HTTPException(400, "distribution requires mix lock")
    gate.open = False
    gate.locked_by = body.locked_by
    gate.locked_at = datetime.now(timezone.utc)
    if name == GateName.BIBLE:
        _project.bible.locked = True
        _project.status = ProjectStatus.BIBLE_LOCKED
        _project.fleet_mode = "SPLIT"
    if name == GateName.DISTRIBUTION:
        _project.status = ProjectStatus.DISTRIBUTED
    return _dump()


@app.post("/api/reset")
def reset() -> dict:
    global _project, _assets
    _assets = {}
    _project = _seat_program(demo_project())
    reset_farm()
    reset_mix()
    return _dump()


def main() -> None:
    import uvicorn

    global _project
    backfill_connect_from_wizard()
    apply_ssh_env()
    if should_proxy():
        _start_gpu_reconcile()
    _project = _boot_project()
    _project = resume_camera(_project)
    _project = _apply_site(_project)
    # A service restart must resume an already-locked Production run. This
    # restarts missing 1080 masters but still obeys Finish error/Mix gates.
    maybe_advance_run(_project)
    _start_legacy_advance()
    uvicorn.run(
        "diony_producer.app:app",
        host=bind_host(),
        port=int(os.environ.get("DIONY_PRODUCER_PORT", "8788")),
        reload=False,
        log_level="info",
    )


if __name__ == "__main__":
    main()
