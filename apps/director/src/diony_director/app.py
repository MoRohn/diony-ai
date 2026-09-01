"""Diony Director service: the chat-first conductor of the pivoted platform.

Guided mode only in M1: every proposal carries a locked estimate; a run is
refused when the engine is legacy, the provider is unconfigured, or the rate
card is stale. Completed media is persisted to the vault before the op may
call itself completed, and the ledger endpoint reads the same store the chat
speaks from.
"""

from __future__ import annotations

import os
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from diony_contracts.cluster import bind_host, cluster_root
from diony_contracts.director import CloudGenerationMode, DirectorOp, DirectorOpType, EngineMode
from diony_producer.control_plane import ControlPlane, control_plane_path

import threading

from diony_director import assembly, capabilities, engine, ledger, ratecards, review as review_mod, session
from diony_director.providers import base as provider_base
from diony_director.providers import flow, grok

# One gate over the status check-and-set: two concurrent runs of the same op
# must never both cross into a provider submit (double-spend).
_RUN_GUARD = threading.Lock()

STATIC = Path(__file__).resolve().parent / "static"

_ADAPTERS = {"grok": grok, "google-flow": flow}

_STATE: dict[str, dict] = {}


def _reap_orphaned_jobs(plane: ControlPlane, ctx) -> int:
    """Close plane job rows that nothing will ever poll again.

    With persistence, a rehydrated op resumes its own polling — the reaper only
    touches RUNNING rows whose op is gone (deleted session, corrupt state) or
    already terminal. Producer's Gallery stops showing dead batches as active."""
    from datetime import datetime, timezone

    from diony_contracts.launch import JobStatus

    reaped = 0
    try:
        runs = plane.runs(ctx.workspace.id, project_id=ledger.DIRECTOR_PROJECT_ID)
    except Exception:  # noqa: BLE001 - a reaper failure must never block boot
        return 0
    for run in runs:
        try:
            jobs = plane.jobs(ctx.workspace.id, run.id)
        except Exception:  # noqa: BLE001
            continue
        for job in jobs:
            if job.status is not JobStatus.RUNNING:
                continue
            op = session._OPS.get(str(job.metadata.get("op_id") or ""))
            if op is not None and op.status in {"submitted", "submitting"}:
                continue  # rehydrated and resumable — the live path polls it
            if op is not None and op.status == "completed":
                job.status = JobStatus.SUCCEEDED
                job.error_message = ""
            else:
                job.status = JobStatus.FAILED
                job.error_message = (
                    "reaped: orphaned by a restart — no live session state left to poll it"
                )
            job.completed_at = datetime.now(timezone.utc)
            try:
                plane.save_job(job)
                reaped += 1
            except Exception:  # noqa: BLE001
                continue
    return reaped


def _boot() -> tuple[ControlPlane, object]:
    from diony_director import config

    config.load_keys_env()
    key = str(control_plane_path())
    state = _STATE.get(key)
    if state is None:
        plane = ControlPlane()
        ctx = plane.bootstrap_local()
        ledger.seed_rate_cards(plane, ctx.workspace.id)
        rehydrated = session.rehydrate_from_vault()
        reaped = _reap_orphaned_jobs(plane, ctx)
        state = {
            "plane": plane,
            "ctx": ctx,
            "boot_info": {"rehydrated_sessions": rehydrated, "reaped_jobs": reaped},
        }
        _STATE[key] = state
    return state["plane"], state["ctx"]


app = FastAPI(title="Diony Director", version="0.1.0")
if STATIC.is_dir():
    app.mount("/static", StaticFiles(directory=STATIC), name="static")


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC / "index.html")


@app.get("/api/director/status")
def status() -> dict:
    from diony_contracts.cluster import read_engine_choice

    plane, ctx = _boot()
    boot_info = _STATE.get(str(control_plane_path()), {}).get("boot_info", {})
    return {
        "engine": engine.engine_mode().value,
        "engine_choice": read_engine_choice(),
        "in_flight_jobs": session.in_flight_jobs(),
        "boot": boot_info,
        "providers": {"grok": grok.configured(), "google-flow": flow.configured()},
        "flow_plan": ratecards.flow_plan(),
        "brain": {
            "configured": bool(__import__("diony_director.screenwriter", fromlist=["brain_url"]).brain_url()),
            "model": __import__("diony_director.screenwriter", fromlist=["brain_model"]).brain_model(),
        },
        "workspace": ctx.workspace.id,
        "studio_url": "http://studio.diony.local",
        "producer_url": "http://producer.diony.local",
    }


@app.post("/api/director/sessions")
def create_session() -> dict:
    return {"session_id": session.create_session()}


@app.get("/api/director/resumable")
def resumable() -> dict:
    """Rehydrated sessions with unfinished business the room should reattach to."""
    _boot()  # ensure vault rehydration ran before we claim there's nothing
    return {"sessions": session.resumable_sessions()}


class MessageIn(BaseModel):
    text: str
    provider: str = "auto"
    model: str = ""
    duration_s: float = Field(default=6.0, gt=0)
    resolution: str = "720p"
    aspect: str = Field(default="16:9", pattern="^(16:9|9:16)$")


def _apply_aspect(sid: str, aspect: str) -> None:
    """Adopt the requested program aspect — unless footage already exists at
    another shape. A film never changes raster halfway through."""
    if aspect == session.aspect_for(sid):
        return
    if any(op.status == "completed" and op.clip_id for op in session.ops_for(sid)):
        raise HTTPException(
            status_code=409,
            detail=(
                f"the program aspect is locked at {session.aspect_for(sid)} — "
                "footage already exists at that shape; start a new session for "
                f"a {aspect} production"
            ),
        )
    session.set_aspect(sid, aspect)


@app.post("/api/director/sessions/{sid}/message")
def message(sid: str, body: MessageIn) -> dict:
    plane, ctx = _boot()
    spent = _session_spent(plane, ctx, sid)  # the reply talks about THIS session
    _apply_aspect(sid, body.aspect)
    try:
        proposal = session.propose(
            sid,
            body.text,
            provider=body.provider,
            model=body.model,
            duration_s=body.duration_s,
            resolution=body.resolution,
            spent_usd_micros=spent,
        )
    except (KeyError, ValueError) as err:
        raise HTTPException(status_code=422, detail=str(err)) from err
    return {"reply": proposal.reply, "op": proposal.op.model_dump(mode="json")}


class UploadIn(BaseModel):
    name: str = "reference"
    data_base64: str


@app.post("/api/director/sessions/{sid}/uploads")
def add_upload(sid: str, body: UploadIn) -> dict:
    """Take a reference still into the session: the first upload opens the
    initial shot; the rest ride as reference images."""
    import base64

    _boot()  # rehydrate first — persisting over an unloaded session would erase it

    try:
        data = base64.b64decode(body.data_base64, validate=True)
    except Exception as err:  # noqa: BLE001 - malformed payload is a client error
        raise HTTPException(status_code=422, detail="the image payload is not valid base64") from err
    try:
        record = session.add_upload(sid, name=body.name, data=data)
    except ValueError as err:
        raise HTTPException(status_code=422, detail=str(err)) from err
    return {"upload": record, "uploads": session.uploads_for(sid)}


@app.get("/api/director/sessions/{sid}/uploads")
def list_uploads(sid: str) -> dict:
    _boot()
    return {"uploads": session.uploads_for(sid)}


@app.delete("/api/director/sessions/{sid}/uploads/{upload_id}")
def delete_upload(sid: str, upload_id: str) -> dict:
    _boot()
    if not session.remove_upload(sid, upload_id):
        raise HTTPException(status_code=404, detail=f"unknown upload {upload_id}")
    return {"uploads": session.uploads_for(sid)}


def _op_or_404(op_id: str):
    _boot()  # rehydration must happen before any lookup can 404 honestly
    try:
        return session.get_op(op_id)
    except KeyError as err:
        raise HTTPException(status_code=404, detail=f"unknown op {op_id}") from err


# Every bridge frame and plate is conformed to the program raster before it
# seeds a generation: mixed input shapes were echoed back as mixed output
# shapes (1456×624 / 1168×768 / 1280×720 in one film) — the raster is locked
# at the source, not papered over with letterboxing at assembly. The raster is
# a session setting: 16:9 by default, 9:16 for vertical productions.
def _conform_vf(sid: str) -> str:
    width, height = session.program_raster(sid)
    return (
        f"scale={width}:{height}:force_original_aspect_ratio=increase,"
        f"crop={width}:{height}"
    )


def _cut_point_s(op: DirectorOp, clip_path: Path) -> float | None:
    """Where assembly will actually CUT this clip — the bridge must continue
    from the frame the audience last sees, not from surplus tail that the
    needed_s trim throws away."""
    try:
        probe = assembly._probe(clip_path)
    except Exception:  # noqa: BLE001 - a failed probe falls back to the raw tail
        return None
    if float(probe.get("duration_s") or 0) <= 0:
        return None
    head, effective = assembly.effective_windows([probe], [_assembly_spec(op)])[0]
    return max(0.0, head + effective - 0.1)


def _last_frame(
    clip_path: Path, dest: Path, *, at_s: float | None = None, sid: str = ""
) -> str:
    """Extract the join frame of an accepted clip as the next clip's opening,
    conformed to the program raster."""
    import subprocess

    from diony_producer.gallery_join import resolve_media_tool

    ffmpeg = resolve_media_tool("ffmpeg")
    if not ffmpeg or not clip_path.is_file():
        return ""
    dest.parent.mkdir(parents=True, exist_ok=True)
    seek = ["-ss", f"{at_s:.3f}"] if at_s and at_s > 0 else ["-sseof", "-0.2"]
    result = subprocess.run(
        [ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
         *seek, "-i", str(clip_path), "-frames:v", "1",
         "-vf", _conform_vf(sid), "-q:v", "2", str(dest)],
        capture_output=True, timeout=120,
    )
    return str(dest) if result.returncode == 0 and dest.is_file() else ""


def _conform_still(src: str, dest: Path, *, sid: str = "") -> str:
    """A still (setting plate, uploaded frame) scaled and center-cropped to the
    program raster so an off-shape input never re-shapes the generated clip."""
    import subprocess

    from diony_producer.gallery_join import resolve_media_tool

    ffmpeg = resolve_media_tool("ffmpeg")
    source = Path(src)
    if not ffmpeg or not source.is_file():
        return ""
    dest.parent.mkdir(parents=True, exist_ok=True)
    result = subprocess.run(
        [ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
         "-i", str(source), "-frames:v", "1", "-vf", _conform_vf(sid), "-q:v", "2", str(dest)],
        capture_output=True, timeout=120,
    )
    return str(dest) if result.returncode == 0 and dest.is_file() else ""


def _pin_aspect(op: DirectorOp) -> None:
    """Every video generation carries the session's program aspect, in the
    dialect its provider speaks — an unpinned request echoes whatever shape
    the seed image happened to have."""
    if op.provider is None:
        return
    aspect = session.aspect_for(op.session_id)
    if op.provider.value == "grok":
        op.artifact_refs.setdefault("aspect_ratio", aspect)
    else:
        op.artifact_refs.setdefault(
            "aspect_ratio", "portrait" if aspect == "9:16" else "landscape"
        )


def _late_bind_locks(op: DirectorOp) -> None:
    """Re-resolve the clip's locks at submit time: whatever plates, sheets, and
    bridge frames exist by now get attached. Identity is an input, never a hope.

    Merge, never replace: source media refs set by a revision survive. On the
    Flow lane frames are exclusive with refs/characters/voices, so a voice or
    character lock outranks the bridge frame — a lock is never dropped for a
    frame; a frame gives way for a lock."""
    from diony_director import compiler

    clip = session.clip_for_op(op)
    package = session.package_for(op.session_id)
    if clip is None or package is None:
        return
    refs = session.apply_uploads_to_refs(
        op.session_id, clip, package, compiler.provider_artifact_refs(clip, package)
    )
    position = session.chain_position(op.session_id, op.id)
    # Cadence 2, not 4: at 4 the set had already mutated beyond recall —
    # per-link drift of 0.003 compounded into a different room by link 4
    # (s-2e0a74644b measured 0.055 vs its own anchor at cl-1-4).
    re_anchor = bool(position) and position % 2 == 0
    prev = session.previous_completed_clip_op(op.session_id, op)
    frame = ""
    if prev is not None:
        stored = session.runtime(prev.id).get("clip")
        if stored is not None:
            frame = _last_frame(
                Path(stored.path),
                cluster_root() / "director" / op.session_id / "frames" / f"{prev.id}.jpg",
                at_s=_cut_point_s(prev, Path(stored.path)),
                sid=op.session_id,
            )
    frame_conflicts_with_lock = op.provider is not None and (
        op.provider.value == "google-flow"
        and bool(refs.get("voices") or refs.get("characters"))
    )
    if frame and not frame_conflicts_with_lock:
        if re_anchor:
            # Every 2nd link re-anchors on the bible so frame-to-frame drift
            # can't compound. The MASTER look leads the pack — with the bridge
            # frame first, the re-anchor inherited the drift it existed to
            # correct (s-2e0a74644b mutated purple→cyan straight through its
            # re-anchors). Plate holds the room, bridge holds the pose, sheet
            # holds the identity. Sheets alone are still never enough
            # (s-1b80548c4d invented a new room from them).
            plate = compiler._live_plate_asset(clip, package)
            anchors = [plate] if plate else []
            if frame not in anchors:
                anchors.append(frame)
            for image in refs.get("references") or []:
                if image not in anchors:
                    anchors.append(image)
            refs["references"] = anchors[:3]
            refs.pop("start_image", None)
        else:
            refs["start_image"] = frame
            refs.pop("references", None)
    start = str(refs.get("start_image") or "")
    if start and Path(start).is_file() and Path(start).parent.name != "frames":
        # A plate start still is conformed to the program raster before it
        # seeds the clip — bridge frames from _last_frame already are.
        conformed = _conform_still(
            start,
            cluster_root() / "director" / op.session_id / "frames" / f"plate-{op.id}.jpg",
            sid=op.session_id,
        )
        if conformed:
            refs["start_image"] = conformed
    for key in ("source_video", "end_image"):
        if op.artifact_refs.get(key) and not refs.get(key):
            refs[key] = op.artifact_refs[key]
    op.artifact_refs = refs


def _kick_submitter(sid: str) -> None:
    """Submit ready waves on a worker thread. Bible stills are synchronous
    provider calls that can take minutes in aggregate — no batch request may
    ever block on them (a gateway timeout must never read as a failed batch)."""
    import threading

    with _RUN_GUARD:
        # check-and-set under the lock: two concurrent advance ticks must not
        # start two submitter threads (the first to finish would clear the
        # "submitting" flag while the other still rolls, and the UI would cut
        # a partial film)
        if session.submitter_active(sid):
            return
        session.set_submitter_active(sid, True)

    def _worker() -> None:
        try:
            while True:
                submitted, held = _submit_ready(sid)
                session.log_held(sid, held)
                if not submitted:
                    break
        finally:
            session.set_submitter_active(sid, False)

    threading.Thread(target=_worker, name=f"diony-submit-{sid}", daemon=True).start()


TRANSIENT_HOLD_LIMIT = 3  # waves a single op may be network-held before it needs a manual Run


def _submit_ready(sid: str) -> tuple[list[str], list[dict]]:
    """Submit every op the sequence says is safe now; hold the rest with reasons."""
    submitted: list[str] = []
    held: list[dict] = []
    enforce_budget = bool(session.budget_for(sid).get("max_usd_micros"))
    for op in session.ready_ops(sid):
        if enforce_budget:
            cost = op.estimate.total_usd_micros if op.estimate else 0
            allowed, reason = session.budget_allows(sid, cost, _spent_and_committed(sid))
            if not allowed:
                held.append({"op_id": op.id, "reason": reason})
                session.batch_targets(sid).discard(op.id)  # stays proposed for a manual run
                continue
        try:
            _run_approved(op)
            submitted.append(op.id)
            session.runtime(op.id).pop("transient_holds", None)
        except HTTPException as err:
            if err.status_code == 503:
                # transient network failure: nothing was submitted, the op is
                # still proposed. Leave it targeted so the next wave retries,
                # but give up after a few — a dead link must not loop forever.
                rt = session.runtime(op.id)
                holds = int(rt.get("transient_holds", 0)) + 1
                rt["transient_holds"] = holds
                if holds < TRANSIENT_HOLD_LIMIT:
                    held.append(
                        {
                            "op_id": op.id,
                            "reason": f"{err.detail} — held (network retry {holds}/{TRANSIENT_HOLD_LIMIT}), will retry on the next wave",
                        }
                    )
                    continue
                held.append(
                    {
                        "op_id": op.id,
                        "reason": f"{err.detail} — the network kept failing; Run it manually when the connection is back",
                    }
                )
                session.batch_targets(sid).discard(op.id)  # stays proposed for a manual run
                continue
            held.append({"op_id": op.id, "reason": str(err.detail)})
            op.status = "failed"
        except Exception as err:  # noqa: BLE001 - one bad op never blocks the wave
            held.append({"op_id": op.id, "reason": f"{type(err).__name__}: {err}"})
            op.status = "failed"
    if submitted or held:
        session.persist(sid)
    return submitted, held


def _dispatch_submit(op: DirectorOp) -> provider_base.CloudJobHandle:
    # Edit/extend/upscale ops carry their source in artifact_refs; rebuilding
    # locks would wipe it (the bug that submitted {"url": ""} to every edit).
    if op.clip_id and op.op not in {
        DirectorOpType.EDIT,
        DirectorOpType.EXTEND,
        DirectorOpType.UPSCALE,
    }:
        _late_bind_locks(op)
    if op.mode in {
        CloudGenerationMode.T2V,
        CloudGenerationMode.I2V,
        CloudGenerationMode.R2V,
        CloudGenerationMode.FIRST_LAST,
    } and op.op is DirectorOpType.GENERATE:
        # single shots and scripted clips alike carry the session's aspect;
        # edits, extensions, and upscales inherit their source's shape.
        # After late binding: the locks rebuild artifact_refs from scratch.
        _pin_aspect(op)
    if op.clip_id and op.op not in {
        DirectorOpType.EDIT,
        DirectorOpType.EXTEND,
        DirectorOpType.UPSCALE,
    }:
        problems = capabilities.violations(op)
        if problems:
            # the legality gate holds AFTER late binding too — the refs that
            # ship are the refs that were checked
            raise provider_base.CloudAdapterError(
                "refused before submit — the late-bound locks are not legal on "
                f"{op.model}: " + "; ".join(problems)
            )
    adapter = _ADAPTERS[op.provider.value]
    source = str(op.artifact_refs.get("source_video") or "")
    if op.mode is CloudGenerationMode.IMAGE:
        from diony_director import artifacts

        references = list(op.artifact_refs.get("references") or [])
        package = session.package_for(op.session_id)
        if package is not None and op.artifact_refs.get("bible"):
            references = artifacts.references_for(package, op) + references
        aspect = session.aspect_for(op.session_id)
        if op.provider.value == "grok":
            return grok.submit_image(
                op.prompt,
                model=op.model,
                references=references[:1],
                aspect_ratio=aspect,
                resolution=op.resolution if op.resolution in {"1k", "2k"} else "",
            )
        return flow.submit_image(
            op.prompt,
            model=op.model,
            references=references[:7],
            aspect_ratio="portrait" if aspect == "9:16" else "landscape",
        )
    if op.provider.value == "grok":
        if op.mode is CloudGenerationMode.EXTENSION:
            return grok.submit_extension(
                source, prompt=op.prompt, model=op.model, duration_s=int(op.duration_s)
            )
        if op.op is DirectorOpType.EDIT:
            return grok.submit_edit(source, prompt=op.prompt, model=op.model)
        return grok.submit(op)
    if op.op is DirectorOpType.UPSCALE:
        return flow.submit_upscale(source, resolution=op.resolution)
    if op.mode is CloudGenerationMode.EXTENSION:
        return flow.submit_extend(
            source, prompt=op.prompt, duration_s=int(op.duration_s), model=op.model
        )
    return adapter.submit(op)


def _run_approved(op: DirectorOp) -> dict:
    if engine.engine_mode() is not EngineMode.CLOUD:
        raise HTTPException(
            status_code=409,
            detail="the engine is set to the legacy local lane; cloud runs are disabled "
            "(DIONY_ENGINE=local_legacy)",
        )
    if os.environ.get("DIONY_DIRECTOR_DRY_RUN", "").strip() == "1":
        raise HTTPException(
            status_code=409,
            detail="DIONY_DIRECTOR_DRY_RUN=1 — this host refuses to spend on providers "
            "(the explicit Director dry-run flag; unset it to roll for real)",
        )
    with _RUN_GUARD:
        if op.status not in {"proposed", "approved"}:
            raise HTTPException(status_code=409, detail=f"op is {op.status}, not runnable")
        op.status = "submitting"  # claimed — a concurrent run of the same op now 409s
    try:
        if op.estimate is None:
            raise HTTPException(status_code=409, detail="no locked estimate — refusing to spend")
        if op.estimate.rate_cards_stale:
            raise HTTPException(
                status_code=409,
                detail="rate cards are stale; re-verify provider pricing before spending",
            )
        problems = capabilities.violations(op)
        if problems:
            raise HTTPException(
                status_code=409,
                detail="the routed model cannot honor this op: " + "; ".join(problems),
            )
        adapter = _ADAPTERS.get(op.provider.value if op.provider else "")
        if adapter is None or not adapter.configured():
            raise HTTPException(
                status_code=409, detail=f"provider {op.provider} is not configured"
            )
        plane, ctx = _boot()
        try:
            handle = _dispatch_submit(op)
        except provider_base.CloudAdapterTransientError as err:
            # 503 = the network failed, not the op: it stays proposed and a
            # later wave (or a manual Run) retries without redrafting anything
            raise HTTPException(status_code=503, detail=str(err)) from err
        except provider_base.CloudAdapterError as err:
            raise HTTPException(status_code=502, detail=str(err)) from err
    except BaseException:
        op.status = "proposed"  # nothing was spent; the op stays runnable
        raise
    # Money is now at the provider. FIRST make the op pollable and durable —
    # a ledger write failure after this point must leave a submitted, resumable
    # op, never a resubmittable one (that would spend twice).
    op.status = "submitted"
    rt = session.runtime(op.id)
    rt["handle"] = handle
    session.persist(op.session_id)
    try:
        job = ledger.commit_job(plane, ctx.workspace.id, ctx.user.id, op, handle.external_id)
        op.job_id = job.id
    except Exception as err:  # noqa: BLE001 - the plane write retries on the next tick
        rt["ledger_error"] = f"{type(err).__name__}: {err}"
        session.persist(op.session_id)
        return {"job_id": "", "external_id": handle.external_id, "op": op.model_dump(mode="json")}
    session.persist(op.session_id)
    return {"job_id": job.id, "external_id": handle.external_id, "op": op.model_dump(mode="json")}


@app.post("/api/director/ops/{op_id}/run")
def run_op(op_id: str) -> dict:
    return _run_approved(_op_or_404(op_id))


@app.post("/api/director/ops/{op_id}/retry")
def retry_op(op_id: str) -> dict:
    """Un-strand a failed op: back to proposed with a clean runtime, then roll.

    The locked estimate the user already approved still stands, and the budget
    gate in the submit wave still applies — a retry re-spends only this op."""
    op = _op_or_404(op_id)
    if op.status != "failed":
        raise HTTPException(status_code=409, detail=f"op is {op.status}; only failed ops retry")
    op.status = "proposed"
    op.job_id = ""
    rt = session.runtime(op.id)
    for stale in ("handle", "ledger_error", "transient_holds"):
        rt.pop(stale, None)
    session.add_batch_target(op.session_id, op.id)
    session.persist(op.session_id)
    _kick_submitter(op.session_id)
    return {"op": op.model_dump(mode="json")}


def _clip_dest(op) -> Path:
    suffix = ".jpg" if op.mode is CloudGenerationMode.IMAGE else ".mp4"
    return cluster_root() / "director" / op.session_id / f"{op.job_id}{suffix}"


def _advance_op(op: DirectorOp, *, allow_spend: bool = False) -> dict:
    """Poll a submitted op once, fetching/reconciling/reviewing on completion.

    Review runs BEFORE the op may call itself completed — a take is not a take
    until its evidence is on record. `allow_spend` gates autopilot follow-ups:
    only the POST advance/run paths may spend; a GET can never trigger a paid
    submit (a browser refresh is not an approval)."""
    plane, ctx = _boot()
    rt = session.runtime(op.id)
    if op.status == "submitted" and not op.job_id and rt.get("handle") is not None:
        # the submit landed but the plane write failed — retry the ledger
        # commit so the job row exists before the next reconcile
        try:
            job = ledger.commit_job(
                plane, ctx.workspace.id, ctx.user.id, op, rt["handle"].external_id
            )
            op.job_id = job.id
            rt.pop("ledger_error", None)
            session.persist(op.session_id)
        except Exception as err:  # noqa: BLE001
            rt["ledger_error"] = f"{type(err).__name__}: {err}"
    job = plane.job(ctx.workspace.id, op.job_id) if op.job_id else None

    if op.status == "submitted" and job is not None:
        handle = rt.get("handle")
        adapter = _ADAPTERS[op.provider.value]
        poll = adapter.poll(handle)
        if poll.state == "done":
            clip = adapter.fetch(handle, poll, _clip_dest(op))
            job = ledger.reconcile_completion(
                plane,
                ctx.workspace.id,
                job,
                op,
                actual_duration_s=int(poll.metadata.get("duration_s") or 0) or None,
                credits_used=clip.metadata.get("credits_used"),
            )
            rt["clip"] = clip
            if poll.metadata.get("media_generation_id"):
                rt["media_generation_id"] = poll.metadata["media_generation_id"]
            rt["result_url"] = clip.media_url
            try:
                _review_and_maybe_revise(op, rt, allow_spend=allow_spend)
            except Exception as err:  # noqa: BLE001 - a crashed reviewer never wedges a paid take
                rt["advance_error"] = f"review: {type(err).__name__}: {err}"
                if op.clip_id and session.review_for(op.id) is None:
                    # the take never enters the film evidence-free: the crash
                    # itself goes on record as a warn needing a human eye
                    from diony_contracts.production import ClipReview

                    session.register_review(
                        op.id,
                        ClipReview(
                            id=f"rev-crash-{op.id}",
                            clip_id=op.clip_id,
                            op_id=op.id,
                            verdict="warn",
                            families={"review": "unmeasured"},
                            issues=[f"the reviewer crashed: {type(err).__name__}: {err}"],
                            recommended_op="accept",
                        ),
                    )
            op.status = "completed"
            session.persist(op.session_id)
        elif poll.terminal:
            credits_burned = 0
            if op.provider and op.provider.value == "google-flow":
                credits_burned = flow.credits_used(handle, poll) if poll.state != "moderated" else 0
            job = ledger.record_failure(
                plane,
                ctx.workspace.id,
                job,
                op,
                poll.detail or poll.state,
                credits_burned=credits_burned if poll.state == "failed" else 0,
            )
            op.status = "failed"
            _revise_after_job_failure(op, poll.detail or poll.state, allow_spend=allow_spend)
            session.persist(op.session_id)

    clip = rt.get("clip")
    review = session.review_for(op.id)
    can_upscale = bool(
        op.status == "completed"
        and rt.get("media_generation_id")
        and op.provider is not None
        and op.provider.value == "google-flow"
        and op.op is not DirectorOpType.UPSCALE
    )
    return {
        "op": op.model_dump(mode="json"),
        "can_upscale": can_upscale,
        "job": job.model_dump(mode="json") if job is not None else None,
        "clip": {
            "path": str(clip.path),
            "sha256": clip.sha256,
            "bytes": clip.bytes,
        }
        if clip
        else None,
        "review": review.model_dump(mode="json") if review else None,
        "next_op": rt.get("next_op"),
        "narration": rt.get("narration", ""),
    }


@app.get("/api/director/ops/{op_id}")
def op_state(op_id: str) -> dict:
    return _advance_op(_op_or_404(op_id), allow_spend=False)


@app.post("/api/director/ops/{op_id}/accept")
def accept_op(op_id: str) -> dict:
    """A human stands behind a warned or failed take — it may enter the film."""
    op = _op_or_404(op_id)
    if op.status != "completed":
        raise HTTPException(
            status_code=409, detail=f"op is {op.status}; only a completed take can be accepted"
        )
    session.mark_accepted(op.id)
    review = session.review_for(op.id)
    verdict = review.verdict if review else "unreviewed"
    return {
        "reply": f"Accepted over {verdict} — {op.clip_id or op.id} cuts into the film on your call.",
        "accepted": True,
    }


class RunBatchIn(BaseModel):
    op_ids: list[str] | None = None


@app.post("/api/director/sessions/{sid}/run_batch")
def run_batch(sid: str, body: RunBatchIn) -> dict:
    """Run every proposed op (or the selected subset). A single bad op holds
    with its reason instead of killing the batch."""
    _boot()
    ops = session.ops_for(sid)
    if body.op_ids is not None:
        wanted = set(body.op_ids)
        ops = [op for op in ops if op.id in wanted]
    targets = {op.id for op in ops if op.status == "proposed"}
    all_targets = session.set_batch_targets(sid, targets)
    _kick_submitter(sid)
    return {
        "submitted": [],
        "targeted": sorted(all_targets),
        "held": session.drain_held(sid),
        "queued": session.queued_count(sid),
        "submitting": session.submitter_active(sid),
        "skipped": len(
            [op for op in ops if op.status not in {"proposed", "submitting", "submitted"}]
        ),
    }


@app.post("/api/director/sessions/{sid}/advance")
def advance_session(sid: str) -> dict:
    """One tick: poll every submitted op in the session, advancing completions."""
    _boot()
    counts = {"pending": 0, "completed": 0, "failed": 0, "proposed": 0, "other": 0}
    stalled: list[dict] = []
    for op in session.ops_for(sid):
        if op.status == "submitted":
            try:
                _advance_op(op, allow_spend=True)
                rt = session.runtime(op.id)
                rt.pop("advance_error", None)
                rt.pop("advance_failures", None)
            except Exception as err:  # noqa: BLE001 - retried next tick, but NAMED
                detail = f"{type(err).__name__}: {err}"
                rt = session.runtime(op.id)
                rt["advance_error"] = detail
                rt["advance_failures"] = int(rt.get("advance_failures") or 0) + 1
                stalled.append({"op_id": op.id, "reason": detail[:200]})
                if rt["advance_failures"] >= 5:
                    # An op that cannot be polled or fetched five ticks running
                    # (expired URL, lost handle) will never finish on its own:
                    # fail it, close its plane row, and let the ladder re-shoot
                    # the clip instead of wedging the whole batch forever.
                    op.status = "failed"
                    try:
                        plane, ctx = _boot()
                        if op.job_id:
                            job = plane.job(ctx.workspace.id, op.job_id)
                            ledger.record_failure(
                                plane, ctx.workspace.id, job, op,
                                f"gave up after 5 poll failures: {detail[:160]}",
                            )
                    except Exception:  # noqa: BLE001 - closing the row is best-effort
                        pass
                    _revise_after_job_failure(
                        op, f"gave up after 5 poll failures: {detail[:160]}", allow_spend=True
                    )
                    session.persist(sid)
    _kick_submitter(sid)
    held = session.drain_held(sid)
    for op in session.ops_for(sid):
        if op.status in {"submitted", "submitting"}:
            counts["pending"] += 1
        elif op.status == "completed":
            counts["completed"] += 1
        elif op.status == "failed":
            counts["failed"] += 1
        elif op.status == "proposed":
            counts["proposed"] += 1
        else:
            counts["other"] += 1
    counts["queued"] = session.queued_count(sid)
    counts["submitting"] = session.submitter_active(sid)
    counts["held"] = held
    counts["stalled"] = stalled
    return counts


def _session_spent(plane, ctx, sid: str) -> int:
    return sum(
        u.cost_breakdown.total_usd_micros
        for u in plane.usage(ctx.workspace.id)
        if u.run_id == f"director-{sid}"
    )


def _spent_and_committed(sid: str) -> int:
    """THIS session's spend plus its in-flight commitments. A session budget is
    a per-session promise — workspace-lifetime spend never counts against it."""
    plane, ctx = _boot()
    return _session_spent(plane, ctx, sid) + session.committed_usd_micros(sid)


def _maybe_autopilot_run(sid: str, op: DirectorOp, rt: dict, *, allow_spend: bool) -> None:
    budget = session.budget_for(sid)
    if not budget.get("autopilot"):
        return
    if not allow_spend:
        rt["narration"] = rt.get("narration", "") + " Autopilot: queued for the next advance tick."
        return
    cost = op.estimate.total_usd_micros if op.estimate else 0
    allowed, reason = session.budget_allows(sid, cost, _spent_and_committed(sid))
    if allowed:
        _run_approved(op)
        rt["narration"] = rt.get("narration", "") + " Autopilot: revision submitted."
    else:
        rt["narration"] = rt.get("narration", "") + f" Autopilot held: {reason}."


def _review_and_maybe_revise(op: DirectorOp, rt: dict, *, allow_spend: bool = False) -> None:
    stored = rt.get("clip")
    if stored is None:
        return
    package = session.package_for(op.session_id)
    if op.mode is CloudGenerationMode.IMAGE and op.artifact_refs.get("bible"):
        from diony_director import artifacts

        bible = op.artifact_refs["bible"]
        if package is not None and bible.get("setting_id"):
            artifacts.register_plate(package, op, path=Path(stored.path))
            setting = package.setting(str(bible["setting_id"]))
            rt["narration"] = f"Setting plate locked: {setting.name}."
            return
        if package is not None:
            char = artifacts.register_still(
                package,
                op,
                path=Path(stored.path),
                media_generation_id=rt.get("media_generation_id", ""),
            )
            slot = op.artifact_refs["bible"]["slot"]
            rt["narration"] = (
                f"Sheet still landed: {char.name} · {slot}. "
                f"{len(char.hero_stills)}/{len(artifacts.SHEET_SLOTS)} angles done."
            )
        return
    clip_unit = session.clip_for_op(op)
    if clip_unit is None:
        return
    prev = session.previous_completed_clip_op(op.session_id, op)
    prev_clip = session.runtime(prev.id).get("clip") if prev is not None else None
    anchor: Path | None = None
    if package is not None:
        # The scene's master look: its setting plate. Every clip measures
        # against it, so drift that compounds invisibly link-by-link is caught
        # by the third or fourth clip instead of by the audience.
        try:
            plate = package.setting(
                str(clip_unit.continuity.setting_plate.get("setting_id") or "")
            ).plate_asset_id
            if plate and Path(plate).is_file():
                anchor = Path(plate)
        except KeyError:
            pass
    result = review_mod.review_clip(
        Path(stored.path),
        clip_unit,
        op_id=op.id,
        package=package,
        prev_clip_path=Path(prev_clip.path) if prev_clip is not None else None,
        anchor_path=anchor,
    )
    session.register_review(op.id, result)
    if result.verdict == "fail":
        next_op, narration = session.next_revision(op.session_id, op, result)
        rt["narration"] = narration
        if next_op is not None:
            rt["next_op"] = next_op.model_dump(mode="json")
            _maybe_autopilot_run(op.session_id, next_op, rt, allow_spend=allow_spend)


def _revise_after_job_failure(op: DirectorOp, detail: str, *, allow_spend: bool = False) -> None:
    from diony_contracts.production import ClipReview

    rt = session.runtime(op.id)
    clip_unit = session.clip_for_op(op)
    if clip_unit is None:
        rt["narration"] = f"Job failed: {detail}. No package behind this op — nothing to restage."
        return
    synthetic = ClipReview(
        id=f"rev-job-{op.id}",
        clip_id=clip_unit.clip_id,
        op_id=op.id,
        verdict="fail",
        families={"delivery": "fail"},
        issues=[detail],
    )
    session.register_review(op.id, synthetic)
    next_op, narration = session.next_revision(op.session_id, op, synthetic)
    rt["narration"] = narration
    if next_op is not None:
        rt["next_op"] = next_op.model_dump(mode="json")
        _maybe_autopilot_run(op.session_id, next_op, rt, allow_spend=allow_spend)


class ProduceIn(BaseModel):
    idea: str
    runtime_s: int = Field(default=60, gt=0)  # no length guard: cost is the governor, and it is visible
    autopilot: bool = False
    max_usd: float = Field(default=0, ge=0)
    max_revisions_per_clip: int = Field(default=3, ge=0, le=10)
    aspect: str = Field(default="16:9", pattern="^(16:9|9:16)$")


@app.post("/api/director/sessions/{sid}/produce")
def produce(sid: str, body: ProduceIn) -> dict:
    _boot()
    if body.autopilot and not body.max_usd:
        # validate BEFORE storing: writing autopilot=True with no ceiling and
        # then 409ing would leave a later lock free to auto-submit uncapped
        raise HTTPException(
            status_code=409,
            detail="autopilot cannot arm without a max project budget (max_usd)",
        )
    session.set_budget(
        sid,
        max_usd_micros=int(body.max_usd * 1_000_000),
        max_revisions_per_clip=body.max_revisions_per_clip,
        autopilot=body.autopilot,
    )
    _apply_aspect(sid, body.aspect)
    state = session.begin_draft(sid, body.idea, body.runtime_s)
    return state


@app.get("/api/director/sessions/{sid}/draft")
def draft_status(sid: str) -> dict:
    _boot()
    return session.draft_state(sid)


class DraftLineIn(BaseModel):
    line_id: str
    text: str


@app.post("/api/director/sessions/{sid}/draft/line")
def edit_draft_line(sid: str, body: DraftLineIn) -> dict:
    _boot()
    try:
        return session.edit_draft_line(sid, body.line_id, body.text)
    except KeyError as err:
        raise HTTPException(status_code=404, detail=f"unknown line {body.line_id}") from err
    except (session.DraftNotReady, session.DraftLocked) as err:
        raise HTTPException(status_code=409, detail=str(err)) from err
    except ValueError as err:
        raise HTTPException(status_code=422, detail=str(err)) from err


def _wardrobe_line(char) -> str:
    return char.wardrobe.get("default") or next(iter(char.wardrobe.values()), "")


@app.get("/api/director/sessions/{sid}/board")
def board(sid: str) -> dict:
    """One payload for the production board: script state, bibles, the shot
    list with verdicts, and the cut. The user directs from this, not from
    memory of chat scrollback."""
    _boot()
    draft = session.draft_state(sid)
    package = session.package_for(sid)
    latest: dict[str, DirectorOp] = {}
    for op in session.ops_for(sid):
        if op.clip_id:
            latest[op.clip_id] = op
    clips = []
    if package is not None:
        for clip in package.clips:
            op = latest.get(clip.clip_id)
            review = session.review_for(op.id) if op is not None else None
            rt = session.runtime(op.id) if op is not None else {}
            clips.append(
                {
                    "clip_id": clip.clip_id,
                    "scene_id": clip.scene_id,
                    "clip_class": clip.clip_class,
                    "camera": clip.camera_idea,
                    "duration_s": clip.duration_s,
                    "join_next": clip.join_next,
                    "provider": clip.provider.value if clip.provider else "",
                    "model": clip.model,
                    "est_usd_micros": clip.estimate_usd_micros,
                    "op_id": op.id if op is not None else "",
                    "status": op.status if op is not None else "unplanned",
                    "verdict": review.verdict if review is not None else "",
                    "issues": review.issues[:3] if review is not None else [],
                    "accepted": session.is_accepted(op.id) if op is not None else False,
                    "can_upscale": bool(
                        op is not None
                        and op.status == "completed"
                        and rt.get("media_generation_id")
                        and op.provider is not None
                        and op.provider.value == "google-flow"
                        and op.op is not DirectorOpType.UPSCALE
                    ),
                }
            )
    characters = []
    settings = []
    if package is not None:
        source_chars = package.characters
        source_settings = package.settings
    else:
        # Pre-lock, the bibles come from the draft — wardrobe and grade are
        # reviewable BEFORE any money rolls, which is the point of the gate.
        from diony_contracts.production import CharacterProfile, SettingProfile

        source_chars = [CharacterProfile.model_validate(c) for c in draft.get("characters") or []]
        source_settings = [SettingProfile.model_validate(s) for s in draft.get("settings") or []]
    for char in source_chars:
        characters.append(
            {
                "char_id": char.char_id,
                "name": char.name,
                "role": char.role,
                "wardrobe": _wardrobe_line(char),
                "face_approved": char.face_approved,
                "stills": sorted(char.hero_stills.keys()),
                "flow_registered": bool(char.flow_character_ref),
            }
        )
    for setting in source_settings:
        settings.append(
            {
                "setting_id": setting.setting_id,
                "name": setting.name,
                "time_weather": setting.time_weather,
                "grade_language": setting.grade_language,
                "camera_kit": setting.camera_kit,
                "plate_ready": bool(setting.plate_asset_id),
            }
        )
    film_path = cluster_root() / "director" / sid / "film.mp4"
    from diony_director.deliverables import DELIVERABLE_NAMES

    delivered = [
        name for name in DELIVERABLE_NAMES if (cluster_root() / "director" / sid / name).is_file()
    ]
    return {
        "session_id": sid,
        "draft_status": draft.get("status"),
        "script": draft.get("script"),
        "beats": (draft.get("script") or {}).get("beats", []),
        "locked": bool((draft.get("script") or {}).get("locked")),
        "characters": characters,
        "settings": settings,
        "clips": clips,
        "film_ready": film_path.is_file(),
        "deliverables": delivered,
    }


def _vault_media(path_text: str) -> FileResponse:
    path = Path(path_text)
    root = cluster_root().resolve()
    try:
        resolved = path.resolve()
    except OSError as err:
        raise HTTPException(status_code=404, detail="asset unreadable") from err
    if not resolved.is_file() or not resolved.is_relative_to(root):
        raise HTTPException(status_code=404, detail="no such asset in the vault")
    suffix = resolved.suffix.lower()
    media = {"png": "image/png", "webp": "image/webp"}.get(suffix.lstrip("."), "image/jpeg")
    return FileResponse(resolved, media_type=media)


@app.get("/api/director/sessions/{sid}/bible/media/{char_id}/{slot}")
def bible_media(sid: str, char_id: str, slot: str) -> FileResponse:
    _boot()
    package = session.package_for(sid)
    if package is None:
        raise HTTPException(status_code=404, detail=f"session {sid} has no locked package")
    try:
        char = package.character(char_id)
    except KeyError as err:
        raise HTTPException(status_code=404, detail=f"unknown character {char_id}") from err
    still = char.hero_stills.get(slot, "")
    if not still:
        raise HTTPException(status_code=404, detail=f"{char.name} has no {slot} still yet")
    return _vault_media(still)


@app.get("/api/director/sessions/{sid}/plate/{setting_id}")
def plate_media(sid: str, setting_id: str) -> FileResponse:
    _boot()
    package = session.package_for(sid)
    if package is None:
        raise HTTPException(status_code=404, detail=f"session {sid} has no locked package")
    try:
        setting = package.setting(setting_id)
    except KeyError as err:
        raise HTTPException(status_code=404, detail=f"unknown setting {setting_id}") from err
    if not setting.plate_asset_id:
        raise HTTPException(status_code=404, detail=f"{setting.name} has no plate yet")
    return _vault_media(setting.plate_asset_id)


@app.post("/api/director/sessions/{sid}/lock_script")
def lock_script(sid: str) -> dict:
    plane, ctx = _boot()
    lifetime = ledger.snapshot(plane, ctx.workspace.id)["totals"]["cost_usd_micros"]
    session_spent = _session_spent(plane, ctx, sid)
    try:
        package, ops, bible_ops, reply = session.lock_script(
            sid, session_spent, lifetime_usd_micros=lifetime
        )
    except (session.DraftNotReady, session.ScriptAlreadyLocked) as err:
        raise HTTPException(status_code=409, detail=str(err)) from err
    except session.DraftInvalid as err:
        raise HTTPException(status_code=422, detail=str(err)) from err
    except KeyError as err:
        raise HTTPException(status_code=404, detail=str(err)) from err

    targeted: list[str] = []
    if session.budget_for(sid).get("autopilot"):
        targets = session.set_batch_targets(sid, {op.id for op in ops})
        _kick_submitter(sid)
        targeted = sorted(targets)  # a plan, honestly named — nothing has reached a provider yet

    return {
        "reply": reply,
        "package": package.model_dump(mode="json"),
        "ops": [op.model_dump(mode="json") for op in ops],
        "bible_ops": [b.model_dump(mode="json") for b in bible_ops],
        "targeted": targeted,
        "submitted": [],
        "held": [],
    }


@app.get("/api/director/sessions/{sid}/package")
def get_package(sid: str) -> dict:
    _boot()
    package = session.package_for(sid)
    if package is None:
        raise HTTPException(status_code=404, detail=f"session {sid} has no locked package")
    return package.model_dump(mode="json")


class AssembleIn(BaseModel):
    loudness: str = "streaming"
    look: bool = True
    look_profile: str = "diony-default"
    # Ship the cut even when scripted shots have no usable take. Off by
    # default: a silently missing story beat (s-1b80548c4d shipped without
    # cl-2-5) is a defect, not a preference.
    allow_gaps: bool = False


def _cut_gaps(sid: str) -> tuple[list[str], list[DirectorOp], list[str]]:
    """Scripted clips with no usable take on disk; the drafted (proposed) ops
    that would fill them — originals never run, or retakes awaiting a Run —
    and the clips whose fill is already rolling (submitting/submitted)."""
    package = session.package_for(sid)
    if package is None:
        return [], [], []
    have: set[str] = set()
    for op in session.completed_clip_ops(sid):
        stored = session.runtime(op.id).get("clip")
        if stored is not None and Path(stored.path).is_file():
            have.add(op.clip_id)
    missing = [c.clip_id for c in package.clips if c.clip_id not in have]
    gaps = set(missing)
    drafts = [
        o for o in session.ops_for(sid) if o.clip_id in gaps and o.status == "proposed"
    ]
    rolling = sorted(
        {
            o.clip_id
            for o in session.ops_for(sid)
            if o.clip_id in gaps and o.status in {"submitting", "submitted"}
        }
    )
    return missing, drafts, rolling


@app.post("/api/director/sessions/{sid}/run_retakes")
def run_retakes(sid: str) -> dict:
    """Roll the drafted ops that fill the cut's missing shots — the functional
    'Retry' beside 'Assemble anyway'. Targets ride the directed sequence, so a
    retake still bridges from its predecessor's frame."""
    _boot()
    missing, drafts, rolling = _cut_gaps(sid)
    drafted_clips = {o.clip_id for o in drafts}
    undrafted = [c for c in missing if c not in drafted_clips and c not in rolling]
    targets = {o.id for o in drafts}
    all_targets: set[str] = set()
    if targets:
        all_targets = session.set_batch_targets(sid, targets)
        _kick_submitter(sid)
    return {
        "missing": missing,
        "targeted": sorted(all_targets),
        "rolling": rolling,
        "undrafted": undrafted,
        "queued": session.queued_count(sid),
        "submitting": session.submitter_active(sid),
        "held": session.drain_held(sid),
    }


def _assembly_spec(op: DirectorOp) -> dict:
    """Per-clip assembly instructions from the clip unit: spoken clips trim the
    provider round-up beyond what the words need (plus handles and a safety
    margin — a clipped word is a retake, dead air is not), joins carry the
    splitter's authored type."""
    from diony_director.splitter import PRE_HANDLE_S, TAIL_HANDLE_S, WORDS_PER_SECOND

    clip = session.clip_for_op(op)
    if clip is None:
        return {}
    spec: dict = {"join": clip.join_next}
    if clip.clip_class in {"sync-dialogue", "vo-over"} and clip.audio_packet.script_excerpt:
        words = len(str(clip.audio_packet.script_excerpt[0].get("line", "")).split())
        spec["needed_s"] = round(
            max(1, words) / WORDS_PER_SECOND + PRE_HANDLE_S + TAIL_HANDLE_S + 0.25, 3
        )
    return spec


@app.post("/api/director/sessions/{sid}/assemble")
def assemble_session(sid: str, body: AssembleIn) -> dict:
    from diony_director import deliverables as deliverables_mod

    _boot()
    ops = session.completed_clip_ops(sid)
    paths = []
    specs = []
    used_ops = []
    for op in ops:
        stored = session.runtime(op.id).get("clip")
        if stored is not None and Path(stored.path).is_file():
            paths.append(Path(stored.path))
            specs.append(_assembly_spec(op))
            used_ops.append(op)
    if not paths:
        raise HTTPException(status_code=409, detail="no completed clips to assemble yet")
    if not body.allow_gaps:
        missing, drafts, rolling = _cut_gaps(sid)
        if missing:
            pending = sorted({o.clip_id for o in drafts})
            if pending:
                why = f" — retakes are drafted for {', '.join(pending)}; run them first"
            elif rolling:
                why = f" — {', '.join(rolling)} are rolling now; assemble again when they print"
            else:
                why = " — no usable take exists; rerun those clips"
            raise HTTPException(
                status_code=409,
                detail=(
                    "refusing to cut the film with missing shots: "
                    + ", ".join(missing)
                    + why
                    + ", or assemble with allow_gaps to ship without them"
                ),
            )
    dest = cluster_root() / "director" / sid / "film.mp4"
    raster_w, raster_h = session.program_raster(sid)
    try:
        result = assembly.assemble(
            paths,
            dest,
            look=body.look,
            loudness_target=body.loudness,
            specs=specs,
            look_profile=body.look_profile,
            width=raster_w,
            height=raster_h,
        )
    except assembly.AssemblyUnavailable as err:
        raise HTTPException(status_code=409, detail=str(err)) from err
    except assembly.AssemblyFailed as err:
        raise HTTPException(status_code=502, detail=str(err)) from err

    delivered: dict = {}
    try:
        entries = [
            {
                "op": op,
                "clip": session.clip_for_op(op),
                "window": result.windows[index],
                "review": session.review_for(op.id),
                "accepted": session.is_accepted(op.id),
            }
            for index, op in enumerate(used_ops)
        ]
        delivered = deliverables_mod.build_deliverables(
            dest.parent,
            session_id=sid,
            entries=entries,
            film=dest,
            film_duration_s=result.duration_s,
            look=result.look,
            loudness_target=result.loudness_target,
        )
    except Exception as err:  # noqa: BLE001 - sidecars never block the film
        delivered = {"names": [], "error": f"{type(err).__name__}: {err}"}
    return {
        "film": str(result.path),
        "duration_s": result.duration_s,
        "clip_count": result.clip_count,
        "look": result.look,
        "loudness_target": result.loudness_target,
        "deliverables": delivered.get("names", []),
        "final_check": delivered.get("final_check"),
        "cue_count": delivered.get("cue_count", 0),
    }


@app.get("/api/director/sessions/{sid}/deliverables/{name}")
def deliverable(sid: str, name: str) -> FileResponse:
    from diony_director.deliverables import DELIVERABLE_NAMES

    if name not in DELIVERABLE_NAMES:
        raise HTTPException(status_code=404, detail=f"unknown deliverable {name}")
    path = cluster_root() / "director" / sid / name
    if not path.is_file():
        raise HTTPException(status_code=404, detail=f"{name} has not been delivered yet")
    media = "application/json" if name.endswith(".json") else "text/plain; charset=utf-8"
    return FileResponse(path, media_type=media, filename=name)


@app.get("/api/director/films/{sid}")
def film(sid: str) -> FileResponse:
    path = cluster_root() / "director" / sid / "film.mp4"
    if not path.is_file():
        raise HTTPException(status_code=404, detail="no assembled film for this session")
    return FileResponse(path, media_type="video/mp4")


@app.post("/api/director/sessions/{sid}/bible/stills")
def propose_sheet_stills(sid: str) -> dict:
    from diony_director import artifacts

    _boot()
    package = session.package_for(sid)
    if package is None:
        raise HTTPException(status_code=404, detail=f"session {sid} has no locked package")
    ops = artifacts.plan_sheet_ops(sid, package)
    for op in ops:
        session.register_op(sid, op)
    total = sum(op.estimate.total_usd_micros for op in ops if op.estimate)
    return {
        "ops": [op.model_dump(mode="json") for op in ops],
        "reply": (
            f"Angle boards for {len({o.artifact_refs['bible']['char_id'] for o in ops})} "
            f"character(s): {len(ops)} still(s), about ${total / 1e6:.2f} total. "
            "Stills are cheap — they anchor every expensive video frame."
        )
        if ops
        else "Every character sheet is already complete or approved.",
    }


@app.post("/api/director/sessions/{sid}/bible/approve/{char_id}")
def approve_face(sid: str, char_id: str) -> dict:
    _boot()
    package = session.package_for(sid)
    if package is None:
        raise HTTPException(status_code=404, detail=f"session {sid} has no locked package")
    try:
        char = package.character(char_id)
    except KeyError as err:
        raise HTTPException(status_code=404, detail=f"unknown character {char_id}") from err
    if not char.hero_stills:
        raise HTTPException(status_code=409, detail=f"{char.name} has no stills to approve yet")
    char.face_approved = True
    session.persist(sid)  # an approval is a decision; decisions survive restarts
    return {"reply": f"{char.name}'s face is approved and frozen at sheet v{char.sheet_version}."}


@app.post("/api/director/sessions/{sid}/bible/register_character/{char_id}")
def register_flow_character(sid: str, char_id: str) -> dict:
    _boot()
    package = session.package_for(sid)
    if package is None:
        raise HTTPException(status_code=404, detail=f"session {sid} has no locked package")
    try:
        char = package.character(char_id)
    except KeyError as err:
        raise HTTPException(status_code=404, detail=f"unknown character {char_id}") from err
    media_ids = list(char.flow_media_ids.values())
    if not media_ids:
        raise HTTPException(
            status_code=409,
            detail=f"{char.name} has no Flow-hosted stills yet — generate the sheet first",
        )
    try:
        ref = flow.register_character(char.name, media_ids[:7])
    except provider_base.CloudAdapterError as err:
        raise HTTPException(status_code=502, detail=str(err)) from err
    char.flow_character_ref = ref
    session.persist(sid)  # a PAID registration whose only record is this field
    return {
        "reply": f"{char.name} is registered as a reusable Flow character ({ref}).",
        "flow_character_ref": ref,
    }


@app.post("/api/director/ops/{op_id}/upscale")
def propose_upscale(op_id: str) -> dict:
    import uuid as _uuid

    from diony_contracts.director import CloudProvider
    from diony_director import estimate as estimate_mod

    op = _op_or_404(op_id)
    if op.status != "completed":
        raise HTTPException(status_code=409, detail=f"op is {op.status}; upscale needs a completed clip")
    rt = session.runtime(op.id)
    source = rt.get("media_generation_id")
    if not source:
        raise HTTPException(
            status_code=409,
            detail="no Flow-hosted media id for this clip — server-side upscale needs "
            "Flow-origin media (Grok clips finish through the local lane instead)",
        )
    est = estimate_mod.estimate_op(
        provider=CloudProvider.GOOGLE_FLOW,
        model="video-upscale",
        mode=CloudGenerationMode.UPSCALE,
        duration_s=1,
        resolution="1080p",
    )
    upscale_op = DirectorOp(
        id=f"op-{_uuid.uuid4().hex[:10]}",
        session_id=op.session_id,
        op=DirectorOpType.UPSCALE,
        clip_id=op.clip_id,
        provider=CloudProvider.GOOGLE_FLOW,
        model="video-upscale",
        mode=CloudGenerationMode.UPSCALE,
        duration_s=1,
        resolution="1080p",
        artifact_refs={"source_video": source},
        estimate=est,
        rationale=f"finish pass on {op.clip_id or op.id}",
    )
    session.register_op(op.session_id, upscale_op)
    usd = est.total_usd_micros / 1_000_000
    return {
        "op": upscale_op.model_dump(mode="json"),
        "reply": f"Upscale to 1080p adds about ${usd:.2f}. Run it when ready.",
    }


@app.get("/api/director/sessions/{sid}/receipt")
def receipt(sid: str) -> dict:
    plane, ctx = _boot()
    ops = session.ops_for(sid)
    reviews = {
        op.id: (session.review_for(op.id).verdict if session.review_for(op.id) else "")
        for op in ops
    }
    return ledger.build_receipt(plane, ctx.workspace.id, sid, ops, reviews)


@app.get("/api/director/clips/{op_id}")
def clip(op_id: str) -> FileResponse:
    op = _op_or_404(op_id)
    rt = session.runtime(op.id)
    stored = rt.get("clip")
    if not stored or not Path(stored.path).is_file():
        raise HTTPException(status_code=404, detail="no persisted clip for this op")
    return FileResponse(stored.path, media_type="video/mp4")


@app.get("/api/director/ledger")
def ledger_view() -> dict:
    plane, ctx = _boot()
    return ledger.snapshot(
        plane, ctx.workspace.id, committed_usd_micros=session.committed_usd_micros()
    )


def main() -> None:
    import uvicorn

    uvicorn.run(
        "diony_director.app:app",
        host=bind_host(),
        port=int(os.environ.get("DIONY_DIRECTOR_PORT", "8790")),
        reload=False,
        log_level="info",
    )


if __name__ == "__main__":
    main()
