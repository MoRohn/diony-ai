"""Diony Director service: the chat-first conductor of the pivoted platform.

Guided mode only in M1: every proposal carries a locked estimate; a run is
refused when the engine is legacy, the provider is unconfigured, or the rate
card is stale. Completed media is persisted to the vault before the op may
call itself completed, and the ledger endpoint reads the same store the chat
speaks from.
"""

from __future__ import annotations

import os
import threading
import time
from collections.abc import Callable
from datetime import UTC
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from diony_contracts.cluster import bind_host, cluster_root
from diony_contracts.director import CloudGenerationMode, DirectorOp, DirectorOpType, EngineMode
from diony_director import (
    assembly,
    capabilities,
    engine,
    ledger,
    ratecards,
    session,
)
from diony_director import brain as brain_mod
from diony_director import (
    review as review_mod,
)
from diony_director.providers import base as provider_base
from diony_director.providers import flow, grok
from diony_producer.control_plane import ControlPlane, control_plane_path

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
    from datetime import datetime

    from diony_contracts.launch import JobStatus

    reaped = 0
    try:
        runs = plane.runs(ctx.workspace.id, project_id=ledger.DIRECTOR_PROJECT_ID)
    except Exception:
        return 0
    for run in runs:
        try:
            jobs = plane.jobs(ctx.workspace.id, run.id)
        except Exception:
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
            job.completed_at = datetime.now(UTC)
            try:
                plane.save_job(job)
                reaped += 1
            except Exception:
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


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC / "index.html")


@app.exception_handler(session.EngineNotChosen)
async def _engine_not_chosen(_request, err: session.EngineNotChosen):
    return JSONResponse(status_code=409, content={"detail": str(err)})


@app.get("/api/director/status")
def status() -> dict:
    from diony_contracts.cluster import cluster_root, read_engine_choice

    plane, ctx = _boot()
    boot_info = _STATE.get(str(control_plane_path()), {}).get("boot_info", {})
    return {
        "engine": engine.engine_mode().value,
        "engine_choice": read_engine_choice(),
        # The vault this Director reads its Studio choice from. The desk
        # compares it with its own before adopting a Director already on
        # :8790 — a checkout Director on the packaged desk's port would
        # otherwise never see the engine Studio wrote.
        "vault": str(cluster_root().resolve()),
        "in_flight_jobs": session.in_flight_jobs(),
        "boot": boot_info,
        "providers": {"grok": grok.configured(), "google-flow": flow.configured()},
        "flow_plan": ratecards.flow_plan(),
        "brain": brain_mod.status(),
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


@app.post("/api/director/sessions/{sid}/dismiss")
def dismiss(sid: str) -> dict:
    """Retire the room from the resume slot. Failed clips never expire, so a
    session with dead takes would otherwise reclaim the room on every launch —
    ＋ NEW SESSION dismisses the room it is leaving. The ledger, media, and
    any still-rolling polls are untouched."""
    _boot()
    if not session.session_exists(sid):
        raise HTTPException(status_code=404, detail=f"unknown session {sid}")
    session.dismiss_session(sid)
    return {"session_id": sid, "dismissed": True}


class MessageIn(BaseModel):
    text: str
    provider: str = ""  # "" = the camera Studio chose
    model: str = ""
    duration_s: float = Field(default=6.0, gt=0)
    resolution: str = "720p"
    # None = keep the session's current aspect — a client that omits the
    # field must never flip a 9:16 session back to the default, or 409 an
    # innocent message once footage locks the shape.
    aspect: str | None = Field(default=None, pattern="^(16:9|9:16)$")
    # Lexicon templates — flow_* shapes the Google/Veo route only, grok_*
    # the Grok route only; each is invisible to the other. None = keep the
    # session's current template (a client that doesn't speak these fields
    # must never clear a chosen template or 409 a locked session); an
    # explicit value sets it ("" = none/authored only).
    flow_world: str | None = None
    flow_camera: str | None = None
    grok_camera: str | None = None
    grok_atmosphere: str | None = None
    # Narrator voice for the film: None = keep, "" / "auto" = the Director
    # casts, a roster name pins one voice (Blueprint 28, post-recorded).
    voice: str | None = None
    # Production style (Blueprint 31): None = keep; "narrated" (default),
    # "dialogue", or "mix". Fixed once the script locks.
    style: str | None = None
    # Cast lock (Blueprint 32): None = keep; "standard" (default) or
    # "locked". Fixed once the script locks — the split packs, sheets, and
    # keyframes from it.
    cast: str | None = None


# Each program-setting change is PLANNED (validated, never mutating) before
# any is COMMITTED — a message that carries several settings where a later
# one is refused must not leave an earlier one silently persisted. A planner
# returns a zero-arg committer, or None when nothing changes; it raises the
# HTTPException here, before any sibling has touched state.


def _plan_aspect(sid: str, aspect: str | None) -> Callable[[], None] | None:
    """Validate a program-aspect change — footage already at another shape
    locks it. None keeps the current aspect; only an explicit value asks."""
    if aspect is None or aspect == session.aspect_for(sid):
        return None
    if any(op.status == "completed" and op.clip_id for op in session.ops_for(sid)):
        raise HTTPException(
            status_code=409,
            detail=(
                f"the program aspect is locked at {session.aspect_for(sid)} — "
                "footage already exists at that shape; start a new session for "
                f"a {aspect} production"
            ),
        )
    return lambda: session.set_aspect(sid, aspect)


def _plan_flow_template(
    sid: str, world: str | None, camera: str | None
) -> Callable[[], None] | None:
    """Validate a Flow lexicon change — refused only while THIS session's
    current draft is locked (prompts compiled), never merely because a stale
    package from a finished film lingers. None keeps, explicit sets."""
    from diony_director import flow_lexicon

    current = session.flow_template_for(sid)
    requested_world = (
        current["world"]
        if world is None
        else (world.strip().lower() or flow_lexicon.BASELINE_WORLD)
    )
    requested_camera = current["camera"] if camera is None else camera.strip().lower()
    if requested_world == current["world"] and requested_camera == current["camera"]:
        return None
    if session.script_locked(sid):
        raise HTTPException(
            status_code=409,
            detail=(
                "the script is locked and every prompt is compiled — the Flow "
                "template cannot change mid-production; start a new session to restyle"
            ),
        )
    if requested_world not in flow_lexicon.worlds():
        raise HTTPException(status_code=422, detail=f"unknown Flow world {requested_world!r}")
    if requested_camera and requested_camera not in flow_lexicon.cameras():
        raise HTTPException(status_code=422, detail=f"unknown Flow camera {requested_camera!r}")
    return lambda: session.set_flow_template(sid, world=requested_world, camera=requested_camera)


def _plan_grok_template(
    sid: str, camera: str | None, atmosphere: str | None
) -> Callable[[], None] | None:
    """Validate a Grok lexicon change — same lock contract as the Flow one."""
    from diony_director import grok_lexicon

    current = session.grok_template_for(sid)
    requested_camera = current["camera"] if camera is None else camera.strip().lower()
    requested_atmosphere = (
        current["atmosphere"] if atmosphere is None else atmosphere.strip().lower()
    )
    if requested_camera == current["camera"] and requested_atmosphere == current["atmosphere"]:
        return None
    if session.script_locked(sid):
        raise HTTPException(
            status_code=409,
            detail=(
                "the script is locked and every prompt is compiled — the Grok "
                "template cannot change mid-production; start a new session to restyle"
            ),
        )
    if requested_camera and requested_camera not in grok_lexicon.cameras():
        raise HTTPException(status_code=422, detail=f"unknown Grok camera {requested_camera!r}")
    if requested_atmosphere and requested_atmosphere not in grok_lexicon.atmospheres():
        raise HTTPException(
            status_code=422, detail=f"unknown Grok atmosphere {requested_atmosphere!r}"
        )
    return lambda: session.set_grok_template(
        sid, camera=requested_camera, atmosphere=requested_atmosphere
    )


def _plan_voice(sid: str, voice: str | None) -> Callable[[], None] | None:
    """Validate a narrator-voice change. None keeps; "" or "auto" returns the
    film to Director casting; a roster name pins one voice for the whole
    film. Narration is recorded in post (Blueprint 28), so this may change
    at any time before the cut — a lock never freezes it."""
    from diony_director import narration as narration_mod

    if voice is None:
        return None
    try:
        resolved = narration_mod.normalize_voice(voice)
    except ValueError as err:
        raise HTTPException(status_code=422, detail=str(err)) from err
    if resolved == session.voice_for(sid):
        return None
    return lambda: session.set_voice(sid, resolved)


def _plan_style(sid: str, style: str | None) -> Callable[[], None] | None:
    """Validate a production-style change (Blueprint 31). None keeps; a key
    sets it. Refused once THIS session's script is locked: every clip's
    voicing was resolved from the style at the split and its prompts are
    compiled — the cut reads the clips, never the style, so a late change
    would describe a film that was not shot. One-shots drafted later take
    the new style; ops already drafted keep the voicing they were priced with."""
    from diony_director import styles

    if style is None:
        return None
    try:
        resolved = styles.normalize_style(style).value
    except ValueError as err:
        raise HTTPException(status_code=422, detail=str(err)) from err
    if resolved == session.style_for(sid):
        return None
    if session.script_locked(sid):
        raise HTTPException(
            status_code=409,
            detail=(
                "the script is locked and every clip's voicing is compiled — the "
                "production style cannot change mid-production; start a new session "
                f"for a {styles.describe(resolved)} film"
            ),
        )
    return lambda: session.set_style(sid, resolved)


def _plan_cast(sid: str, cast: str | None) -> Callable[[], None] | None:
    """Validate a cast-lock change (Blueprint 32). None keeps; a key sets it.
    Refused once THIS session's script is locked: the package was split,
    sheeted, and keyframed under the mode it carries."""
    from diony_director import cast as cast_mod

    if cast is None:
        return None
    try:
        resolved = cast_mod.normalize_mode(cast)
    except ValueError as err:
        raise HTTPException(status_code=422, detail=str(err)) from err
    if resolved == session.cast_mode_for(sid):
        return None
    if session.script_locked(sid):
        raise HTTPException(
            status_code=409,
            detail=(
                "the cast lock is fixed once the script locks. Start a new session "
                f"to shoot with a {cast_mod.describe(resolved)} cast."
            ),
        )
    return lambda: session.set_cast_mode(sid, resolved)


def _apply_program_settings(sid: str, body: MessageIn | ProduceIn) -> None:
    """Validate every program-setting change, then commit them together — so
    a request the caller sees rejected never half-mutates the session."""
    committers = [
        _plan_aspect(sid, body.aspect),
        _plan_flow_template(sid, body.flow_world, body.flow_camera),
        _plan_grok_template(sid, body.grok_camera, body.grok_atmosphere),
        _plan_voice(sid, body.voice),
        _plan_cast(sid, getattr(body, "cast", None)),
        _plan_style(sid, body.style),
    ]
    for commit in committers:  # every planner validated first; none raise here
        if commit is not None:
            commit()


def _apply_aspect(sid: str, aspect: str | None) -> None:
    """Standalone aspect apply for the aspect endpoint and tests."""
    commit = _plan_aspect(sid, aspect)
    if commit is not None:
        commit()


@app.get("/api/director/flow/lexicon")
def flow_lexicon_catalog() -> dict:
    """The Flow templating catalog: worlds, cameras, pairings, and the custom
    lexicon path. Google route only — the Grok dialect never uses it."""
    from diony_director import flow_lexicon

    return flow_lexicon.catalog()


@app.get("/api/director/grok/lexicon")
def grok_lexicon_catalog() -> dict:
    """The Grok templating catalog: cameras, atmospheres, and the custom
    lexicon path. Grok route only — the Flow dialects never use it."""
    from diony_director import grok_lexicon

    return grok_lexicon.catalog()


@app.get("/api/director/sessions/{sid}/settings")
def session_settings(sid: str) -> dict:
    """The session's stored program settings — the aspect and both lexicon
    templates. A resumed room MUST sync its controls from this before it
    speaks: the console sends explicit values, and defaults sent over a
    stored template would silently clear it (or 409 an innocent message
    after the lock)."""
    _boot()
    if not session.session_exists(sid):
        # a typo'd or deleted sid must not masquerade as a fresh session —
        # the resumed room would sync its controls from nothing
        raise HTTPException(status_code=404, detail=f"unknown session {sid}")
    return {
        "aspect": session.aspect_for(sid),
        "voice": session.voice_for(sid),
        "style": session.style_for(sid),
        "cast_lock": session.cast_mode_for(sid),
        "flow_template": session.flow_template_for(sid),
        "grok_template": session.grok_template_for(sid),
    }


class BrainIn(BaseModel):
    brain: str = "auto"  # auto = the camera's own house; grok | gemini


@app.get("/api/director/brain")
def brain_state() -> dict:
    """Which brain writes, what it is resolved from, and what else is available."""
    return brain_mod.status()


@app.post("/api/director/brain")
def set_brain(body: BrainIn) -> dict:
    """Choose the desk's brain — a named brain needs its key; Auto follows the camera."""
    try:
        brain_mod.write_brain_choice(body.brain)
    except ValueError as err:
        raise HTTPException(status_code=422, detail=str(err)) from err
    chosen = brain_mod.read_brain_choice()
    if chosen != "auto" and not brain_mod.available().get(chosen):
        key = "GEMINI_API_KEY" if chosen == "gemini" else "XAI_API_KEY"
        raise HTTPException(
            status_code=409, detail=f"{chosen} is chosen but {key} is not set on this desk"
        )
    return brain_mod.status()


@app.get("/api/director/voices")
def voice_catalog() -> dict:
    """The narrator voices the console offers: Auto (the Director casts from
    the film's tone, spread per film) plus the Grok roster by name."""
    import os as _os

    from diony_director import narration as narration_mod

    which = narration_mod.backend() if narration_mod.narrator_configured() else ""
    pin_env = narration_mod.GEMINI_VOICE_ENV if which == "gemini" else narration_mod.GROK_VOICE_ENV
    return {
        "default": "",
        "voices": narration_mod.roster_catalog(),
        "pinned": _os.environ.get(pin_env, "").strip(),
        "backend": which,
        "backends": narration_mod.available_backends(),
    }


class VoiceIn(BaseModel):
    voice: str = ""  # "" or "auto" = the Director casts


@app.post("/api/director/sessions/{sid}/voice")
def set_session_voice(sid: str, body: VoiceIn) -> dict:
    """Choose (or release to Auto) the film's narrator voice. Post-recorded,
    so it applies to the next cut whether the script is locked or not."""
    _boot()
    if not session.session_exists(sid):
        raise HTTPException(status_code=404, detail=f"unknown session {sid}")
    commit = _plan_voice(sid, body.voice)
    if commit is not None:
        commit()
    return {"session_id": sid, "voice": session.voice_for(sid)}


@app.get("/api/director/styles")
def style_catalog() -> dict:
    """The production styles the console offers (Blueprint 31): Narrated
    (the default — one post narrator), Dialogue (characters voice their
    lines in the clips), Mix (on-camera dialogue in the clip, voice-over in
    post; balanced, rescued, and muted at the cut)."""
    from diony_director import styles

    return {"default": styles.DEFAULT_STYLE.value, "styles": styles.catalog()}


@app.get("/api/director/cast/modes")
def cast_catalog() -> dict:
    """The cast locks the console offers (Blueprint 32): Standard (the
    pre-32 platform) and Locked (identity cards, a measured board sheet,
    cast keyframes, provider identity markers, Flow characters). Reports
    whether the desk can MEASURE faces — without the models the sheet and
    the identity family stay honestly unmeasured."""
    from diony_director import cast as cast_mod

    return {
        "default": cast_mod.DEFAULT_MODE,
        "modes": cast_mod.catalog(),
        "face_models_staged": cast_mod.face_models_staged(),
    }


class CastIn(BaseModel):
    cast: str = ""  # "" = the Standard default


@app.post("/api/director/sessions/{sid}/cast")
def set_session_cast(sid: str, body: CastIn) -> dict:
    """Choose the film's cast lock. Refused (409) once the script is locked."""
    _boot()
    if not session.session_exists(sid):
        raise HTTPException(status_code=404, detail=f"unknown session {sid}")
    commit = _plan_cast(sid, body.cast)
    if commit is not None:
        commit()
    return {"session_id": sid, "cast_lock": session.cast_mode_for(sid)}


class StyleIn(BaseModel):
    style: str = ""  # "" = the Narrated default


@app.post("/api/director/sessions/{sid}/style")
def set_session_style(sid: str, body: StyleIn) -> dict:
    """Choose the film's production style. Refused (409) once the script is
    locked — the voicing is on the clips by then."""
    _boot()
    if not session.session_exists(sid):
        raise HTTPException(status_code=404, detail=f"unknown session {sid}")
    commit = _plan_style(sid, body.style)
    if commit is not None:
        commit()
    return {"session_id": sid, "style": session.style_for(sid)}


@app.post("/api/director/sessions/{sid}/message")
def message(sid: str, body: MessageIn) -> dict:
    plane, ctx = _boot()
    spent = _session_spent(plane, ctx, sid)  # the reply talks about THIS session
    _apply_program_settings(sid, body)
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
    return {
        "reply": proposal.reply,
        # structured facts behind the reply — the room renders the plan card
        # from these; the prose reply is the fallback when absent
        "plan": proposal.plan,
        "op": proposal.op.model_dump(mode="json"),
        # chained one-shot: every link in play order (empty for a single clip)
        "ops": [op.model_dump(mode="json") for op in proposal.ops],
    }


class UploadIn(BaseModel):
    name: str = "reference"
    data_base64: str
    # 'reference' (look + identity, the default) or 'opener' (the first frame)
    role: str = "reference"


@app.post("/api/director/sessions/{sid}/uploads")
def add_upload(sid: str, body: UploadIn) -> dict:
    """Take a still into the session as a look-and-identity reference — or,
    when marked, as the opener (the first frame). Nothing opens the frame
    unless the user says so."""
    import base64

    _boot()  # rehydrate first — persisting over an unloaded session would erase it

    try:
        data = base64.b64decode(body.data_base64, validate=True)
    except Exception as err:
        raise HTTPException(
            status_code=422, detail="the image payload is not valid base64"
        ) from err
    try:
        record = session.add_upload(sid, name=body.name, data=data, role=body.role)
    except ValueError as err:
        raise HTTPException(status_code=422, detail=str(err)) from err
    return {"upload": record, "uploads": session.uploads_for(sid)}


@app.get("/api/director/sessions/{sid}/uploads")
def list_uploads(sid: str) -> dict:
    _boot()
    return {"uploads": session.uploads_for(sid)}


class UploadRoleIn(BaseModel):
    role: str


@app.post("/api/director/sessions/{sid}/uploads/{upload_id}/role")
def set_upload_role(sid: str, upload_id: str, body: UploadRoleIn) -> dict:
    """Mark a still as the opener (the first frame) or back to a reference."""
    _boot()
    try:
        record = session.set_upload_role(sid, upload_id, body.role)
    except ValueError as err:
        raise HTTPException(status_code=422, detail=str(err)) from err
    if record is None:
        raise HTTPException(status_code=404, detail=f"unknown upload {upload_id}")
    return {"upload": record, "uploads": session.uploads_for(sid)}


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
    return f"scale={width}:{height}:force_original_aspect_ratio=increase,crop={width}:{height}"


def _scene_master_frame(op: DirectorOp) -> str:
    """The scene's master frame: the FIRST frame of the scene's first usable
    take — the room and the cast as the scene established them. The re-anchor
    target for models with no reference lane."""
    seq = session.sequence_for(op.session_id)
    if seq is None:
        return ""
    for chain in (seq.get("scenes") or {}).values():
        if op.id not in chain:
            continue
        for op_id in chain:
            if op_id == op.id:
                return ""  # nothing usable landed before this op
            try:
                candidate = session.get_op(op_id)
            except KeyError:
                continue
            if not session.usable_take(candidate):
                continue
            stored = session.runtime(candidate.id).get("clip")
            if stored is None:
                continue
            return _last_frame(
                Path(stored.path),
                cluster_root()
                / "director"
                / op.session_id
                / "frames"
                / f"master-{candidate.id}.jpg",
                at_s=0.05,
                sid=op.session_id,
            )
    return ""


def _cut_point_s(op: DirectorOp, clip_path: Path) -> float | None:
    """Where assembly will actually CUT this clip — the bridge must continue
    from the frame the audience last sees, not from surplus tail that the
    needed_s trim throws away."""
    try:
        probe = assembly._probe(clip_path)
    except Exception:
        return None
    if float(probe.get("duration_s") or 0) <= 0:
        return None
    head, effective = assembly.effective_windows([probe], [_assembly_spec(op)])[0]
    return max(0.0, head + effective - 0.1)


def _last_frame(clip_path: Path, dest: Path, *, at_s: float | None = None, sid: str = "") -> str:
    """Extract the join frame of an accepted clip as the next clip's opening,
    conformed to the program raster."""
    import subprocess

    from diony_producer.media_tools import resolve_media_tool

    ffmpeg = resolve_media_tool("ffmpeg")
    if not ffmpeg or not clip_path.is_file():
        return ""
    dest.parent.mkdir(parents=True, exist_ok=True)
    seek = ["-ss", f"{at_s:.3f}"] if at_s and at_s > 0 else ["-sseof", "-0.2"]
    result = subprocess.run(
        [
            ffmpeg,
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            *seek,
            "-i",
            str(clip_path),
            "-frames:v",
            "1",
            "-vf",
            _conform_vf(sid),
            "-q:v",
            "2",
            str(dest),
        ],
        capture_output=True,
        timeout=120,
    )
    return str(dest) if result.returncode == 0 and dest.is_file() else ""


def _conform_still(src: str, dest: Path, *, sid: str = "") -> str:
    """A still (setting plate, uploaded frame) scaled and center-cropped to the
    program raster so an off-shape input never re-shapes the generated clip."""
    import subprocess

    from diony_producer.media_tools import resolve_media_tool

    ffmpeg = resolve_media_tool("ffmpeg")
    source = Path(src)
    if not ffmpeg or not source.is_file():
        return ""
    dest.parent.mkdir(parents=True, exist_ok=True)
    result = subprocess.run(
        [
            ffmpeg,
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-i",
            str(source),
            "-frames:v",
            "1",
            "-vf",
            _conform_vf(sid),
            "-q:v",
            "2",
            str(dest),
        ],
        capture_output=True,
        timeout=120,
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
        op.artifact_refs.setdefault("aspect_ratio", "portrait" if aspect == "9:16" else "landscape")


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
        op.provider.value == "google-flow" and bool(refs.get("voices") or refs.get("characters"))
    )
    if frame and not frame_conflicts_with_lock:
        caps = (
            capabilities.caps_for(op.provider.value, op.model) if op.provider is not None else None
        )
        if re_anchor and caps is not None and caps.max_reference_images == 0:
            # No reference lane on this model (veo-3.1-quality): re-anchor by
            # opening FROM the scene's master frame — the scene opener's own
            # first frame, which holds the room AND the cast. The empty plate
            # would make the people pop out of existence.
            master = _scene_master_frame(op)
            refs["start_image"] = master or frame
            refs.pop("references", None)
        elif re_anchor:
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
    keyframe = str(session.runtime(op.id).get("style_frame") or "")
    if keyframe and Path(keyframe).is_file() and not frame_conflicts_with_lock:
        # Blueprint 32: the cast keyframe IS the opening frame — composed
        # from the sheet, the plate, and the bridge frame, so opening from it
        # loses no identity and no continuity. The references it was drawn
        # from need not ride again (frames and refs are exclusive anyway).
        refs["start_image"] = keyframe
        refs.pop("references", None)
    refs = compiler.reference_roles(refs, clip, package, frames=[frame] if frame else None)
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


def _late_bind_chain_bridge(op: DirectorOp) -> None:
    """A chained one-shot clip opens from the previous link's join frame —
    resolved at submit time exactly like a scripted bridge, minus the bible.
    No usable predecessor frame leaves the refs untouched (the run guard has
    already refused to roll out of order)."""
    # A link that cuts to a new place opens from its STYLE FRAME — the still
    # composed in the film's look (Blueprint 29) — never from the previous
    # clip's last frame, which is a different subject to morph away from.
    style_frame = str(session.runtime(op.id).get("style_frame") or "")
    if style_frame and Path(style_frame).is_file():
        op.artifact_refs["start_image"] = style_frame
        op.artifact_refs.pop("references", None)
        return
    prev = session.previous_completed_clip_op(op.session_id, op)
    if prev is None:
        return
    stored = session.runtime(prev.id).get("clip")
    if stored is None:
        return
    frame = _last_frame(
        Path(stored.path),
        cluster_root() / "director" / op.session_id / "frames" / f"{prev.id}.jpg",
        sid=op.session_id,
    )
    if frame:
        op.artifact_refs["start_image"] = frame
        op.artifact_refs.pop("references", None)


def _guard_chain_order(op: DirectorOp) -> None:
    """A chained one-shot link cannot roll before its predecessor's join frame
    exists — running clip 3 first would open from nothing and generate an
    unrelated shot. Scripted clips keep their existing batch gating."""
    if op.clip_id:
        return
    position = session.chain_position(op.session_id, op.id)
    if not position:
        return
    if session.previous_completed_clip_op(op.session_id, op) is None:
        seq = session.sequence_for(op.session_id) or {}
        chain = next((c for c in (seq.get("scenes") or {}).values() if op.id in c), [])
        pred = session._OPS.get(chain[position - 1]) if len(chain) > position - 1 >= 0 else None
        if pred is not None and pred.status == "completed" and not session.usable_take(pred):
            review = session.review_for(pred.id)
            why = "; ".join((review.issues if review else [])[:2]) or "see its review"
            raise HTTPException(
                status_code=409,
                detail=(
                    f"clip {position} printed but failed its look review ({why}) — "
                    f"Retake it or Accept it before clip {position + 1} can open from it"
                ),
            )
        raise HTTPException(
            status_code=409,
            detail=(
                f"this is clip {position + 1} of a chained one-shot — run the previous "
                "clip first; its final frame opens this one"
            ),
        )


def _is_chain_link(op: DirectorOp) -> bool:
    return (
        not op.clip_id
        and op.op is DirectorOpType.GENERATE
        and op.mode is not CloudGenerationMode.IMAGE
        and session.chain_position(op.session_id, op.id) is not None
    )


def _roll_style_frame(op: DirectorOp) -> None:
    """Before a chain link that cuts to a new place rolls, its style frame
    prints — synchronously on the image lane — and becomes the link's
    opening frame. A frame that fails holds the link loudly; a frame still
    rendering holds it transiently (the next wave retries)."""
    frame_op = session.style_frame_op_for(op)
    if frame_op is None:
        return
    rt = session.runtime(op.id)
    if frame_op.status == "completed" and rt.get("style_frame"):
        return
    info = frame_op.artifact_refs.get("style_frame") or {}
    keyframe = bool(info.get("keyframe"))
    if keyframe and _keyframe_yields_to_lock(op):
        # Blueprint 32: on Flow a registered character (or a voice) rides the
        # clip and frames are exclusive with it — the keyframe is never
        # spent on a clip that could not open from it
        rt["style_frame_skipped"] = "the Flow character object carries the identity"
        return
    if keyframe:
        label = f"clip {info.get('clip_id') or op.clip_id}"
        what = "cast keyframe"
    else:
        position = session.chain_position(op.session_id, op.id) or 0
        label = f"clip {position + 1}"
        what = "style frame"
    if frame_op.status in {"proposed", "approved"}:
        _run_approved(frame_op)  # raises HTTPException with the reason
    if frame_op.status == "submitted":
        _advance_op(frame_op, allow_spend=False)
    if frame_op.status == "submitted":
        raise HTTPException(
            status_code=503,
            detail=f"the {what} for {label} is still rendering",
        )
    if frame_op.status != "completed" or not rt.get("style_frame"):
        why = session.runtime(frame_op.id).get("submit_error") or frame_op.status
        raise HTTPException(
            status_code=409,
            detail=(
                f"the {what} for {label} did not print ({why}) — "
                "retry the frame before the clip can open from it"
            ),
        )


def _keyframe_yields_to_lock(op: DirectorOp) -> bool:
    """Whether the clip's late-bound refs carry a Flow character or voice
    lock — frames are exclusive with those, and a lock outranks a frame."""
    from diony_director import compiler

    clip = session.clip_for_op(op)
    package = session.package_for(op.session_id)
    if clip is None or package is None or op.provider is None or op.provider.value != "google-flow":
        return False
    refs = compiler.provider_artifact_refs(clip, package)
    return bool(refs.get("characters") or refs.get("voices"))


def _run_link_or_op(op: DirectorOp) -> dict:
    """Run an op; a chain link that cuts to a new place — or, under Cast
    Lock, a scripted clip that shows the cast — prints its opening frame
    first (style frame / cast keyframe), then rolls."""
    if _is_chain_link(op) or session.style_frame_op_for(op) is not None:
        _roll_style_frame(op)
    return _run_approved(op)


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

# A provider quota hold (429) is time-gated, not wave-counted: the run pauses
# until the provider's own retryAfter passes, then resumes on the next tick.
QUOTA_HOLD_DEFAULT_S = 5 * 60  # the provider named no reset time
QUOTA_HOLD_MIN_S = 60
QUOTA_HOLD_MAX_S = 6 * 3600  # a reset time absurdly far out never wedges a run for days


def _quota_wait_s(retry_at: float | None) -> int:
    if not retry_at:
        return QUOTA_HOLD_DEFAULT_S
    return int(min(max(retry_at - time.time(), QUOTA_HOLD_MIN_S), QUOTA_HOLD_MAX_S))


def resume_clock(resume_at: float) -> str:
    """A provider reset time the way a person reads it on this desk: 12-hour
    local time with the zone (1:14 PM EDT), never 24-hour or UTC."""
    return time.strftime("%I:%M %p %Z", time.localtime(resume_at)).lstrip("0")


def _hold_for_quota(op: DirectorOp, detail: str, wait_s: int, held: list[dict]) -> None:
    """Pause one op for the provider's quota window: still targeted, still
    proposed, retried automatically once the window passes — never failed.
    The held entry is structured (kind, resume_at, provider) so the room can
    draw a proper pause card with a live countdown instead of a raw line."""
    resume_at = time.time() + wait_s
    session.runtime(op.id)["quota_hold_until"] = resume_at
    provider = op.provider.value if op.provider else ""
    label = {"google-flow": "Google Flow", "grok": "Grok Imagine"}.get(
        provider, provider or "the provider"
    )
    # the card shows the reason alone; the adapter's "<lane> held by quota
    # (429):" prefix and the bracketed token stay in the logged line
    import re as _re

    plain = _re.sub(r"^.*?\(429\):\s*", "", detail).strip()
    plain = _re.sub(r"\s*\[[A-Za-z_]+\]$", "", plain).strip() or detail
    held.append(
        {
            "op_id": op.id,
            "kind": "quota",
            "provider": provider,
            "clip_id": op.clip_id or "",
            "detail": plain,
            "resume_at": round(resume_at, 3),
            "reason": (
                f"{label} quota reached — {detail}. The run pauses and resumes "
                f"automatically at {resume_clock(resume_at)}"
            ),
        }
    )


def _submit_ready(sid: str) -> tuple[list[str], list[dict]]:
    """Submit every op the sequence says is safe now; hold the rest with reasons."""
    submitted: list[str] = []
    held: list[dict] = []
    enforce_budget = bool(session.budget_for(sid).get("max_usd_micros"))
    for op in session.ready_ops(sid):
        hold_until = float(session.runtime(op.id).get("quota_hold_until") or 0)
        if hold_until > time.time():
            # quota pause already on record (logged when it was set): skip
            # quietly until the provider's window passes, then retry
            continue
        if enforce_budget:
            cost = op.estimate.total_usd_micros if op.estimate else 0
            allowed, reason = session.budget_allows(sid, cost, _spent_and_committed(sid))
            if not allowed:
                held.append({"op_id": op.id, "reason": reason})
                session.batch_targets(sid).discard(op.id)  # stays proposed for a manual run
                continue
        try:
            _guard_chain_order(op)
        except HTTPException as err:
            # a chained one-shot link whose predecessor has no usable frame
            # (failed, discarded, or never rolled) is HELD, still proposed —
            # a batch must never roll it from nothing. Retry the earlier link
            # and it becomes runnable again.
            held.append({"op_id": op.id, "reason": str(err.detail)})
            session.batch_targets(sid).discard(op.id)
            continue
        try:
            _run_link_or_op(op)
            submitted.append(op.id)
            session.runtime(op.id).pop("transient_holds", None)
            session.runtime(op.id).pop("quota_hold_until", None)
        except HTTPException as err:
            retry_after = str((err.headers or {}).get("Retry-After") or "")
            if err.status_code == 503 and retry_after.isdigit():
                # provider quota (429 upstream): time-gated, never counted
                # against the network-hold limit and never failed
                _hold_for_quota(op, str(err.detail), int(retry_after), held)
                continue
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
            # the reason must survive the drained held list: the card's
            # "failed" state reads it back long after the chat scrolled
            session.runtime(op.id)["submit_error"] = str(err.detail)
            session.record_failure(op, "submit", str(err.detail))
        except provider_base.CloudAdapterQuotaError as err:
            # a 429 that surfaced raw (e.g. while polling a style frame
            # mid-roll): the same time-gated pause, never a failed op
            _hold_for_quota(op, str(err), _quota_wait_s(err.retry_at), held)
        except Exception as err:
            held.append({"op_id": op.id, "reason": f"{type(err).__name__}: {err}"})
            op.status = "failed"
            session.runtime(op.id)["submit_error"] = f"{type(err).__name__}: {err}"
            session.record_failure(op, "submit", f"{type(err).__name__}: {err}")
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
    elif op.op is DirectorOpType.GENERATE and session.chain_position(op.session_id, op.id):
        # a chained one-shot link past position 0: no clip, no bibles — just
        # the previous link's join frame, resolved at submit time
        _late_bind_chain_bridge(op)
    if (
        op.mode
        in {
            CloudGenerationMode.T2V,
            CloudGenerationMode.I2V,
            CloudGenerationMode.R2V,
            CloudGenerationMode.FIRST_LAST,
        }
        and op.op is DirectorOpType.GENERATE
    ):
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
    if session.pictures_set_aside(op) and op.artifact_refs.pop("references", None):
        # a Retry set the uploaded pictures aside for this op: after late
        # binding (which re-applies uploads to a clip), so it holds
        op.artifact_refs.pop("reference_roles", None)
    if op.mode is CloudGenerationMode.IMAGE:
        from diony_director import artifacts

        references = list(op.artifact_refs.get("references") or [])
        package = session.package_for(op.session_id)
        if package is not None and op.artifact_refs.get("bible"):
            references = artifacts.references_for(package, op) + references
        if op.artifact_refs.get("style_frame"):
            references = _style_frame_references(op) + [
                r for r in references if r not in _style_frame_references(op)
            ]
        # a still drawn FROM pictures names no one either (the same filter
        # reads the image lane — the poster rode every style frame)
        session.guard_pictured_prompt(op, pictured=bool(references))
        aspect = session.aspect_for(op.session_id)
        if op.provider.value == "grok":
            # Blueprint 32: a board is drawn FROM its anchor and a keyframe FROM
            # its references — the edit lane keeps the identity in the source
            # pixels (up to five), where generation takes one loose reference
            bible_slot = str((op.artifact_refs.get("bible") or {}).get("slot") or "")
            keyframe = bool((op.artifact_refs.get("style_frame") or {}).get("keyframe"))
            if references and (keyframe or bible_slot == "board"):
                return grok.submit_image_edit(
                    op.prompt,
                    references[: grok.MAX_EDIT_IMAGES],
                    model=op.model,
                    aspect_ratio="1:1" if bible_slot == "board" else aspect,
                    resolution=op.resolution if op.resolution in {"1k", "2k"} else "",
                )
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
    session.guard_pictured_prompt(
        op,
        pictured=any(
            op.artifact_refs.get(key)
            for key in ("start_image", "end_image", "references", "characters")
        )
        or op.mode is CloudGenerationMode.EXTENSION
        or op.op is DirectorOpType.EDIT,
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


def _chain_look_frame(link: DirectorOp) -> str:
    """The frame the chain established its look with: the first frame of the
    chain's first usable link. Every style frame references it first."""
    seq = session.sequence_for(link.session_id)
    if seq is None:
        return ""
    for chain in (seq.get("scenes") or {}).values():
        if link.id not in chain:
            continue
        for op_id in chain:
            if op_id == link.id:
                return ""
            candidate = session._OPS.get(op_id)
            if candidate is None or not session.usable_take(candidate):
                continue
            stored = session.runtime(candidate.id).get("clip")
            if stored is None:
                continue
            return _last_frame(
                Path(stored.path),
                cluster_root()
                / "director"
                / link.session_id
                / "frames"
                / f"look-{candidate.id}.jpg",
                at_s=0.05,
                sid=link.session_id,
            )
    return ""


def _style_frame_references(op: DirectorOp) -> list[str]:
    """What a style frame is composed FROM, in reference order: the frame
    the film established its look with, the user's stills, then the previous
    link's last frame for compositional continuity. On a one-reference image
    lane (Grok) the look frame alone rides."""
    info = op.artifact_refs.get("style_frame") or {}
    link = session._OPS.get(str(info.get("for_op") or ""))
    if link is None:
        return []
    refs: list[str] = []

    def _add(path: str) -> None:
        if path and Path(path).is_file() and path not in refs:
            refs.append(path)

    if info.get("keyframe"):
        # Blueprint 32: a cast keyframe composes from the character boards
        # (identity), the setting plate (the room), and — when the clip
        # continues its scene — the previous clip's last frame (the pose and
        # light to continue from). Identity first: it is what the frame is for.
        from diony_director import cast as cast_mod
        from diony_director import compiler

        clip = session.clip_for_op(link)
        package = session.package_for(link.session_id)
        if clip is None or package is None:
            return []
        for char in compiler._characters_in(clip, package):
            for still in cast_mod.identity_refs(char)[:1]:
                _add(still)
        _add(compiler._live_plate_asset(clip, package))
        for path in session.all_upload_paths(link.session_id):
            _add(path)  # the director's own stills shape the frame too
        prev = session.previous_completed_clip_op(link.session_id, link)
        prev_clip = session.clip_for_op(prev) if prev is not None else None
        if prev is not None and prev_clip is not None and prev_clip.scene_id == clip.scene_id:
            stored = session.runtime(prev.id).get("clip")
            if stored is not None:
                _add(
                    _last_frame(
                        Path(stored.path),
                        cluster_root() / "director" / link.session_id / "frames" / f"{prev.id}.jpg",
                        at_s=_cut_point_s(prev, Path(stored.path)),
                        sid=link.session_id,
                    )
                )
        return refs

    # A REDRAW after a refusal composes from less each time: the first safe
    # redraw drops the previous link's last frame (the likeliest carrier of
    # the face the filter matched); a second drops every frame pulled from the
    # film and keeps only the user's own stills. The prompt's steer does the
    # rest. A redraw after a plain failure keeps the full reference set.
    redraw = int(info.get("redraw") or 0)
    safe = bool(info.get("safe"))
    frames_from_film = not (safe and redraw >= 2)
    previous_last_frame = not safe
    if frames_from_film:
        _add(_chain_look_frame(link))
    master = session.master_for_op(link.id)
    if frames_from_film:
        for asset in master.look.anchor_assets if master is not None and master.look else []:
            _add(asset)
    for path in session.all_upload_paths(link.session_id):
        _add(path)
    prev = (
        session.previous_completed_clip_op(link.session_id, link) if previous_last_frame else None
    )
    if prev is not None:
        stored = session.runtime(prev.id).get("clip")
        if stored is not None:
            _add(
                _last_frame(
                    Path(stored.path),
                    cluster_root() / "director" / link.session_id / "frames" / f"{prev.id}.jpg",
                    sid=link.session_id,
                )
            )
    return refs


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
    block = session.derivation_block(op)
    if block:
        # s-16d2e6e5e0: twelve clips rolled on the raw idea after the
        # derivation failed — a provisional prompt never spends
        raise HTTPException(status_code=409, detail=block)
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
            raise HTTPException(status_code=409, detail=f"provider {op.provider} is not configured")
        plane, ctx = _boot()
        try:
            handle = _dispatch_submit(op)
        except provider_base.CloudAdapterQuotaError as err:
            # 429 = the provider's quota, not the op or the network: nothing
            # was spent, the op stays proposed, and the wave resumes on its
            # own once the window resets. The Retry-After header carries the
            # wait so _submit_ready can time-gate the hold.
            raise HTTPException(
                status_code=503,
                detail=str(err),
                headers={"Retry-After": str(_quota_wait_s(err.retry_at))},
            ) from err
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
    # Start the watchdog clock at submit, not at first poll: an op whose
    # ledger commit fails every tick never reaches the poll block, and
    # without this its progress timestamp would stay unset and the stall
    # would never surface. Persisted with the handle so a restart doesn't
    # reset the grace window on a job already stuck for hours.
    rt["last_poll_state"] = "submitted"
    rt["last_progress_ts"] = time.time()
    session.persist(op.session_id)
    try:
        job = ledger.commit_job(plane, ctx.workspace.id, ctx.user.id, op, handle.external_id)
        op.job_id = job.id
    except Exception as err:
        rt["ledger_error"] = f"{type(err).__name__}: {err}"
        session.persist(op.session_id)
        return {"job_id": "", "external_id": handle.external_id, "op": op.model_dump(mode="json")}
    session.persist(op.session_id)
    return {"job_id": job.id, "external_id": handle.external_id, "op": op.model_dump(mode="json")}


@app.post("/api/director/ops/{op_id}/run")
def run_op(op_id: str) -> dict:
    op = _op_or_404(op_id)
    _guard_chain_order(op)
    return _run_link_or_op(op)


@app.post("/api/director/ops/{op_id}/derive")
def derive_op(op_id: str) -> dict:
    """Retry the master derivation this op (or its whole chain) is held on,
    from the inputs recorded at proposal — the same priced ops, no re-paste."""
    op = _op_or_404(op_id)
    try:
        ops = session.retry_derivation(op.id)
    except LookupError as err:
        raise HTTPException(status_code=404, detail=str(err)) from err
    except RuntimeError as err:
        raise HTTPException(status_code=409, detail=str(err)) from err
    return {"op_ids": [o.id for o in ops], "status": "deriving"}


class RetryIn(BaseModel):
    # force a fresh opening frame (or forbid one) regardless of the ladder
    fresh_frame: bool | None = None


def _plan_picture_setaside(op: DirectorOp) -> tuple[bool, str]:
    """The opener's rung of the refusal ladder (2026-09-07, clip 1/15 of
    s-9c2f04d993, refused three times with the poster riding as a reference).
    A Google Flow refusal of an op that opens the film — no frame to redraw —
    while its uploaded pictures ride as references: when the pictured-prompt
    guard already ran on the refused attempt, the words were clean and the
    picture is what the filter matches, so the Retry sets the pictures aside
    and the clip rolls from the prompt alone (the prompt alone rendered on the
    live probe). An op the guard never saw (proposed before it existed) just
    retries — the guard now scrubs it on the way out."""
    if op.provider is None or op.provider.value != "google-flow":
        return False, ""
    if _opens_from_a_frame(op) or not (op.artifact_refs or {}).get("references"):
        return False, ""
    rt = session.runtime(op.id)
    failure = rt.get("failure") or {}
    if not failure.get("detail"):
        detail = _failure_detail(op)
        if detail:
            failure = {**failure, "detail": detail}
    if not session.failure_is_refusal(failure):
        return False, ""
    if not rt.get("pictured_guard") and session.pictured_people(op):
        # the refused attempt went out before the guard existed and the room
        # knows names to describe: the guard scrubs this retry on the way out
        return False, ""
    return True, (
        "the provider refused the clip although its prompt named no one — "
        "the reference picture itself is what the filter matches"
    )


def _failure_detail(op: DirectorOp) -> str:
    """Why the op failed, in the words the room showed: the recorded failure,
    else the plane's job row (ops that failed before failures were recorded),
    else the submit error."""
    failure = session.runtime(op.id).get("failure") or {}
    if failure.get("detail"):
        return str(failure["detail"])
    if op.job_id:
        try:
            plane, ctx = _boot()
            job = plane.job(ctx.workspace.id, op.job_id)
            if job is not None and job.error_message:
                return str(job.error_message)
        except Exception:
            pass
    return str(session.runtime(op.id).get("submit_error") or "")


def _opens_from_a_frame(op: DirectorOp) -> bool:
    """A chain link past the opener opens from a picture — its style frame or
    the previous link's last frame — and that picture can be the thing the
    provider refuses."""
    return _is_chain_link(op) and bool(session.chain_position(op.session_id, op.id))


def _plan_frame_redraw(op: DirectorOp, body: RetryIn | None) -> tuple[bool, bool, str]:
    """Decide whether this Retry redraws the link's opening frame.

    Returns (redraw, safe, why). The ladder: a provider REFUSAL (moderated /
    filter) redraws at once, steered clear of recognizable people — the same
    still would be refused the same way (clip 6/30, 2026-09-06, four times).
    Any other failure retries once on the same frame, then redraws — by the
    second failure the frame is the common factor. `fresh_frame` overrides
    either way. A frame redrawn FRAME_REDRAW_LIMIT times is refused: by then
    the prompt is what keeps tripping the filter."""
    if not _opens_from_a_frame(op):
        return False, False, ""
    rt = session.runtime(op.id)
    failure = rt.get("failure") or {}
    if not failure.get("detail"):
        detail = _failure_detail(op)
        if detail:
            failure = {**failure, "detail": detail}
    refusal = session.failure_is_refusal(failure)
    retries = int(rt.get("retries") or 0)
    if body is not None and body.fresh_frame is False:
        return False, False, ""
    if body is not None and body.fresh_frame is True:
        return True, refusal, "a fresh opening frame was asked for"
    if refusal:
        return True, True, "the provider refused the picture it opened from"
    if retries >= 1:
        return True, False, f"the same opening frame failed {retries + 1} times"
    return False, False, ""


@app.post("/api/director/ops/{op_id}/retry")
def retry_op(op_id: str, body: RetryIn | None = None) -> dict:
    """Un-strand a failed op: back to proposed with a clean runtime, then roll.

    The locked estimate the user already approved still stands, and the budget
    gate in the submit wave still applies — a retry re-spends only this op
    (plus a redrawn opening frame, a priced still, when the ladder calls for
    one). A Retry also re-arms the RUN the op belonged to: the later links and
    the plan's waiting ops are targeted again, and the plan (assemble, finish)
    comes back so the room resumes it without another press."""
    op = _op_or_404(op_id)
    if op.status == "completed" and _is_chain_link(op) and not session.usable_take(op):
        # printed, failed its look review, unaccepted: a RETAKE — the
        # rejected take stays on the ledger, the link rolls again
        session.retake_chain_link(op)
        session.add_batch_target(op.session_id, op.id)
        retargeted = session.retarget_after_retry(op.session_id, op)
        _kick_submitter(op.session_id)
        return {
            "op": op.model_dump(mode="json"),
            "redraw": None,
            "retargeted": retargeted,
            "run_plan": session.run_plan(op.session_id),
        }
    if op.status != "failed":
        raise HTTPException(status_code=409, detail=f"op is {op.status}; only failed ops retry")
    redraw, safe, why = _plan_frame_redraw(op, body)
    rt = session.runtime(op.id)
    if redraw and int(rt.get("frame_redraws") or 0) >= session.FRAME_REDRAW_LIMIT:
        position = (session.chain_position(op.session_id, op.id) or 0) + 1
        raise HTTPException(
            status_code=409,
            detail=(
                f"clip {position}'s opening frame was redrawn {session.FRAME_REDRAW_LIMIT} "
                "times and the provider still refused the clip — the prompt itself is what "
                "the filter matches. Rewrite the clip (describe the role, wardrobe, and "
                "setting; name no real person) and Retry again."
            ),
        )
    op.status = "proposed"
    op.job_id = ""
    for stale in (
        "handle",
        "ledger_error",
        "transient_holds",
        "quota_hold_until",
        "submit_error",
        "poll_not_before",
        "last_poll_state",
        "last_progress_ts",
        "advance_error",
        "advance_failures",
        "next_op",
    ):
        rt.pop(stale, None)
    rt["retries"] = int(rt.get("retries") or 0) + 1
    redraw_note = None
    set_aside, aside_why = _plan_picture_setaside(op)
    if set_aside:
        pictures = list(op.artifact_refs.get("references") or [])
        rt["pictures_set_aside"] = {"paths": pictures, "at": time.time(), "why": aside_why}
        op.artifact_refs.pop("references", None)
        op.artifact_refs.pop("reference_roles", None)
        op.rationale += (
            f" · retry {rt['retries']}: {len(pictures)} reference "
            f"picture{'s' if len(pictures) != 1 else ''} set aside — {aside_why}; "
            "the clip rolls from the prompt alone"
        )
    if redraw:
        reason = _failure_detail(op) or why
        frame_op = session.redraw_opening_frame(op, reason=reason, safe=safe)
        cost = frame_op.estimate.total_usd_micros if frame_op.estimate else 0
        position = (session.chain_position(op.session_id, op.id) or 0) + 1
        redraws = int(rt.get("frame_redraws") or 0)
        redraw_note = {
            "frame_op_id": frame_op.id,
            "attempt": redraws,
            "safe": safe,
            "model": frame_op.model,
            "est_usd_micros": cost,
            "note": (
                f"Retry {rt['retries']} of clip {position}: {why} — its opening frame is "
                f"redrawn on {frame_op.model} (attempt {redraws}, {session._usd(cost)})"
                + (", steered clear of any recognizable person" if safe else "")
                + ", and the clip rolls again from the new frame."
            ),
        }
        op.rationale += (
            f" · retry {rt['retries']}: opening frame redrawn (attempt {redraws}"
            + (", likeness-safe" if safe else "")
            + f") — {why}"
        )
    # A retry must be able to succeed: riders the routed model refuses beside
    # a start/end frame are dropped here, loudly — the opening frame wins.
    # (Un-strands ops proposed before the frames-exclusive rule reached the
    # proposal path; new proposals never carry the combination.)
    refs = op.artifact_refs or {}
    if refs.get("start_image") or refs.get("end_image"):
        caps = capabilities.caps_for(op.provider.value if op.provider else "", op.model)
        if caps is not None and caps.frames_exclusive_with_refs:
            dropped = [
                key
                for key in ("references", "characters", "voices", "grok_voices")
                if refs.pop(key, None)
            ]
            if dropped:
                op.rationale += (
                    f" · retry dropped {', '.join(dropped)}: {op.model} takes an "
                    "opening frame or identity refs, never both"
                )
    session.add_batch_target(op.session_id, op.id)
    retargeted = session.retarget_after_retry(op.session_id, op)
    session.persist(op.session_id)
    _kick_submitter(op.session_id)
    return {
        "op": op.model_dump(mode="json"),
        "redraw": redraw_note,
        "set_aside": (
            {"pictures": rt["pictures_set_aside"]["paths"], "note": aside_why}
            if set_aside
            else None
        ),
        "retargeted": retargeted,
        "run_plan": session.run_plan(op.session_id),
    }


def _failed_for_retry(sid: str) -> list[DirectorOp]:
    """The room's failed clips in shot order — the ones a Retry on the card
    would take, minus the links of a script the room moved past."""
    retired = session.retired_op_ids(sid)
    return [op for op in session.ops_for(sid) if op.status == "failed" and op.id not in retired]


@app.get("/api/director/sessions/{sid}/failed")
def failed_ops(sid: str) -> dict:
    """What a Retry all would re-queue, priced: the strip's button shows the
    sum before anything is pressed."""
    ops = _failed_for_retry(sid)
    rows = [
        {
            "op_id": op.id,
            "clip_id": op.clip_id or "",
            "est_usd_micros": op.estimate.total_usd_micros if op.estimate else 0,
        }
        for op in ops
    ]
    return {
        "ops": rows,
        "count": len(rows),
        "total_usd_micros": sum(int(r["est_usd_micros"]) for r in rows),
    }


@app.post("/api/director/sessions/{sid}/retry-all")
def retry_all_failed(sid: str, body: RetryIn | None = None) -> dict:
    """Every failed clip of the room's film re-queued in shot order with one
    press from the run strip — no scrolling the board for each card's Retry.
    Each clip takes the same ladder its own Retry would (a redrawn opening
    frame, pictures set aside); a clip the ladder refuses stays failed with
    the reason, and the rest roll. The run re-arms once, behind them all."""
    retried: list[dict] = []
    held: list[dict] = []
    for op in _failed_for_retry(sid):
        try:
            res = retry_op(op.id, body)
        except HTTPException as err:
            held.append({"op_id": op.id, "clip_id": op.clip_id or "", "detail": str(err.detail)})
            continue
        retried.append(
            {
                "op_id": op.id,
                "clip_id": op.clip_id or "",
                "est_usd_micros": op.estimate.total_usd_micros if op.estimate else 0,
                "redraw": (res.get("redraw") or {}).get("note"),
                "set_aside": (res.get("set_aside") or {}).get("note"),
            }
        )
    total = sum(int(r["est_usd_micros"]) for r in retried)
    if not retried and not held:
        reply = "Nothing is failed — every clip is printed, rolling, or queued."
    else:
        labels = [r["clip_id"] or r["op_id"] for r in retried]
        shown = ", ".join(labels[:6]) + (f" +{len(labels) - 6} more" if len(labels) > 6 else "")
        reply = (
            f"{len(retried)} failed clip{'s' if len(retried) != 1 else ''} re-queued at "
            f"{'their' if len(retried) != 1 else 'its'} locked price ({session._usd(total)})"
            + (f": {shown}" if shown else "")
            + "; they roll in shot order behind the clips already rolling."
            if retried
            else "No clip could be re-queued."
        )
        redraws = [r for r in retried if r.get("redraw")]
        if redraws:
            reply += f" {len(redraws)} roll{'s' if len(redraws) == 1 else ''} again from a redrawn opening frame."
        aside = [r for r in retried if r.get("set_aside")]
        if aside:
            reply += f" {len(aside)} roll{'s' if len(aside) == 1 else ''} from the prompt alone, pictures set aside."
        if held:
            reply += " Still held: " + "; ".join(
                f"{h['clip_id'] or h['op_id']} — {h['detail']}" for h in held
            )
    if retried:
        session.persist(sid)
    return {
        "retried": retried,
        "held": held,
        "total_usd_micros": total,
        "run_plan": session.run_plan(sid),
        "reply": reply,
    }


@app.post("/api/director/ops/{op_id}/skip")
def skip_op(op_id: str) -> dict:
    """Skip a shot the retries could not land. The op is DISCARDED — it leaves
    the batch, the cut leaves it out (no allow_gaps needed), and the RUN
    continues past it: the later links of its chain and the plan's waiting
    ops are targeted again, and the plan comes back so the room resumes it
    and cuts the film from every other shot. A printed link that failed its
    look review may be skipped the same way instead of accepted or retaken."""
    op = _op_or_404(op_id)
    review_failed = op.status == "completed" and _is_chain_link(op) and not session.usable_take(op)
    if op.status != "failed" and not review_failed:
        raise HTTPException(
            status_code=409,
            detail=f"op is {op.status}; only a failed shot (or a printed link that failed its look review) can be skipped",
        )
    if review_failed:
        review = session.review_for(op.id)
        reason = "look review failed: " + (
            "; ".join((review.issues if review else [])[:2]) or "see its review"
        )
    else:
        reason = _failure_detail(op)
    session.skip_op(op, reason)
    retargeted = session.retarget_after_retry(op.session_id, op)
    session.persist(op.session_id)
    _kick_submitter(op.session_id)
    position = session.chain_position(op.session_id, op.id)
    label = op.clip_id or (f"clip {position + 1}" if position is not None else "the shot")
    if retargeted:
        follow = (
            f" {len(retargeted)} later clip{'s' if len(retargeted) != 1 else ''} rejoin the run "
            "behind it — the next one opens from the last shot that printed."
        )
    else:
        follow = " The cut assembles from the shots that printed."
    return {
        "op": op.model_dump(mode="json"),
        "label": label,
        "retargeted": retargeted,
        "run_plan": session.run_plan(op.session_id),
        "reply": f"{label} skipped — the cut leaves it out.{follow}",
    }


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
        except Exception as err:
            rt["ledger_error"] = f"{type(err).__name__}: {err}"
    job = plane.job(ctx.workspace.id, op.job_id) if op.job_id else None

    # A job the provider asked us to leave alone for a while (a completed
    # Flow job whose download link is still being re-issued — GET /jobs
    # re-resolves it at most once a minute, and a tight loop lengthens the
    # wait) is not polled before its window passes.
    poll_due = float(rt.get("poll_not_before") or 0) <= time.time()
    if op.status == "submitted" and job is not None and poll_due:
        handle = rt.get("handle")
        adapter = _ADAPTERS[op.provider.value]
        poll = adapter.poll(handle)
        hold_s = (poll.metadata or {}).get("poll_after_s")
        if isinstance(hold_s, (int, float)) and hold_s > 0 and poll.state not in {"done"}:
            rt["poll_not_before"] = time.time() + float(hold_s)
        else:
            rt.pop("poll_not_before", None)
        # The watchdog's clock: progress is a CHANGE of observed state. A job
        # answering "running" forever looks healthy to every poll — only the
        # timestamp of its last transition exposes that it's going nowhere.
        if poll.state != rt.get("last_poll_state"):
            rt["last_poll_state"] = poll.state
            rt["last_progress_ts"] = time.time()
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
            except Exception as err:
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
            session.record_failure(op, poll.state, poll.detail or poll.state)
            _revise_after_job_failure(op, poll.detail or poll.state, allow_spend=allow_spend)
            session.persist(op.session_id)

    clip = rt.get("clip")
    review = session.review_for(op.id)
    return {
        "op": op.model_dump(mode="json"),
        "can_upscale": session.can_upscale(op),
        # Blueprint 30: the take's 1080p finish, when one exists
        "finish": session.finish_state_for(op),
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
        # why a submit-side failure failed — there is no provider job to ask
        "submit_error": rt.get("submit_error", ""),
        # Blueprint 29: the still a chain link opens from, when it cuts
        "style_frame": _style_frame_state(op),
        "scene_change": session.chain_scene_change(op),
        # Blueprint 27: a one-shot's pre-split finalized artifact, once derived
        "master": (
            session.master_for_op(op.id).model_dump(mode="json")
            if session.master_for_op(op.id) is not None
            else None
        ),
    }


def _style_frame_state(op: DirectorOp) -> dict | None:
    frame_op = session.style_frame_op_for(op)
    if frame_op is None:
        return None
    return {
        "op_id": frame_op.id,
        "status": frame_op.status,
        "model": frame_op.model,
        "est_usd_micros": frame_op.estimate.total_usd_micros if frame_op.estimate else 0,
        "ready": bool(session.runtime(op.id).get("style_frame")),
        # the Retry ladder: how many times this link's opening frame was
        # redrawn, and whether the current frame continues the previous shot
        # (a redraw on a continuing link) rather than opening a scene change
        "redraws": int(session.runtime(op.id).get("frame_redraws") or 0),
        "continues": bool((frame_op.artifact_refs.get("style_frame") or {}).get("continues")),
        "safe": bool((frame_op.artifact_refs.get("style_frame") or {}).get("safe")),
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
    # what the press asked for AFTER the last print: cut the film, and at
    # which finish ("auto" = each take's native print, "1080p" = Upscale All
    # first). Recorded as the session's run plan so a Retry resumes it.
    assemble: bool = False
    finish: str = "auto"
    upscale: bool = False


@app.post("/api/director/sessions/{sid}/run_batch")
def run_batch(sid: str, body: RunBatchIn) -> dict:
    """Run every proposed op (or the selected subset). A single bad op holds
    with its reason instead of killing the batch."""
    _boot()
    ops = session.ops_for(sid)
    if body.op_ids is not None:
        wanted = set(body.op_ids)
        ops = [op for op in ops if op.id in wanted]
    # an earlier script's links (retired when a newer script landed in this
    # room) are named in `held`, never rolled — and never block the batch
    retired = session.retired_op_ids(sid)
    retired_asked = {op.id for op in ops if op.id in retired}
    ops = [op for op in ops if op.id not in retired]
    blocked = [op for op in ops if op.status == "proposed" and session.derivation_block(op)]
    if blocked:
        raise HTTPException(
            status_code=409,
            detail=(
                f"{len(blocked)} clip(s) still carry the provisional prompt: "
                + session.derivation_block(blocked[0])
            ),
        )
    targets = {op.id for op in ops if op.status == "proposed"}
    all_targets = session.set_batch_targets(sid, targets | retired_asked)
    if body.assemble:
        # the whole set the press covered — printed ones included, so a later
        # Retry knows which clips the cut is waiting on
        session.set_run_plan(
            sid,
            {
                "op_ids": [op.id for op in ops if not op.artifact_refs.get("style_frame")],
                "assemble": True,
                "finish": body.finish or "auto",
                "upscale": bool(body.upscale),
                "started_at": time.time(),
            },
        )
    _kick_submitter(sid)
    return {
        "submitted": [],
        "run_plan": session.run_plan(sid),
        "targeted": sorted(all_targets),
        "held": session.drain_held(sid),
        "queued": session.queued_count(sid),
        "submitting": session.submitter_active(sid),
        "skipped": len(
            [op for op in ops if op.status not in {"proposed", "submitting", "submitted"}]
        ),
    }


# A submitted op whose observed provider state hasn't changed in this long is
# surfaced as stalled — it still polls "running", but a job going nowhere for
# half an hour is a job someone should wonder about before the film is due.
STALL_WATCHDOG_S = 30 * 60


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
            except Exception as err:
                detail = f"{type(err).__name__}: {err}"
                rt = session.runtime(op.id)
                rt["advance_error"] = detail
                if not isinstance(err, provider_base.CloudAdapterQuotaError):
                    # a 429 while polling is the provider's quota, not a dead
                    # job — it never counts toward giving up on the op
                    rt["advance_failures"] = int(rt.get("advance_failures") or 0) + 1
                stalled.append({"op_id": op.id, "reason": detail[:200]})
                if int(rt.get("advance_failures") or 0) >= 5:
                    # An op that cannot be polled or fetched five ticks running
                    # (expired URL, lost handle) will never finish on its own:
                    # fail it, close its plane row, and let the ladder re-shoot
                    # the clip instead of wedging the whole batch forever.
                    op.status = "failed"
                    session.record_failure(
                        op, "lost", f"gave up after 5 poll failures: {detail[:160]}"
                    )
                    try:
                        plane, ctx = _boot()
                        if op.job_id:
                            job = plane.job(ctx.workspace.id, op.job_id)
                            ledger.record_failure(
                                plane,
                                ctx.workspace.id,
                                job,
                                op,
                                f"gave up after 5 poll failures: {detail[:160]}",
                            )
                    except Exception:
                        pass
                    _revise_after_job_failure(
                        op, f"gave up after 5 poll failures: {detail[:160]}", allow_spend=True
                    )
                    session.persist(sid)
    _kick_submitter(sid)
    held = session.drain_held(sid)
    now = time.time()
    for op in session.ops_for(sid):
        if op.status == "submitted" and not any(s["op_id"] == op.id for s in stalled):
            rt = session.runtime(op.id)
            ts = rt.get("last_progress_ts")
            if ts is None:
                # an op submitted before the watchdog landed (rehydrated old
                # state) has no clock — start it now rather than count it
                # healthy forever; it will surface a stall a window later
                rt["last_progress_ts"] = now
                rt.setdefault("last_poll_state", "submitted")
                ts = now
            if now - ts > STALL_WATCHDOG_S:
                state = rt.get("last_poll_state") or "submitted"
                stalled.append(
                    {
                        "op_id": op.id,
                        "reason": (
                            f"{op.clip_id or op.id} has sat at '{state}' for "
                            f"{int((now - ts) // 60)}m — the provider job may be lost"
                        ),
                    }
                )
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
    counts["run_plan"] = session.run_plan(sid)
    # per-op status so the room can show LIVE progress for a targeted set
    # (the retake watch) instead of a silent wait on aggregate counts
    counts["ops"] = [
        {"op_id": op.id, "clip_id": op.clip_id, "status": op.status} for op in session.ops_for(sid)
    ]
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


FINISH_LINES = {"720p": 720, "1080p": 1080, "4K": 2160}
FINISH_DURATION_TOLERANCE_S = 0.5


def _source_op_for_finish(op: DirectorOp) -> DirectorOp | None:
    source_id = str(op.artifact_refs.get("source_op") or "")
    if not source_id:
        return None
    try:
        return session.get_op(source_id)
    except KeyError:
        return None


def _review_finish(op: DirectorOp, rt: dict) -> None:
    """A finished print is measured before it may replace its take at the cut
    (Blueprint 30): it must carry the lines it was booked for and run the
    same length as the take it finished. A print that measures short fails
    its finish review — the take's own print stays in the cut and nothing
    is re-shot; an unmeasurable host records an honest `unmeasured`."""
    from diony_contracts.production import ClipReview

    stored = rt["clip"]
    source = _source_op_for_finish(op)
    label = op.clip_id or (source.clip_id if source is not None else "") or op.id
    probe = review_mod.probe(Path(stored.path))
    issues: list[str] = []
    measured: dict[str, float] = {}
    if probe is None:
        review = ClipReview(
            id=f"rev-finish-{op.id}",
            clip_id=label,
            op_id=op.id,
            verdict="warn",
            families={"finish": "unmeasured"},
            issues=["ffprobe is not available on this host — the finish raster was not measured"],
            recommended_op="accept",
        )
        session.register_review(op.id, review)
        rt["narration"] = f"{label} finished at {op.resolution} (raster unmeasured on this host)."
        return
    width, height = int(probe.get("width") or 0), int(probe.get("height") or 0)
    lines = min(width, height) if width and height else 0
    target = FINISH_LINES.get(op.resolution, 1080)
    measured.update({"width": width, "height": height, "duration_s": float(probe["duration_s"])})
    if lines < target:
        issues.append(
            f"the {op.resolution} finish came back at {width}×{height} — below the {target}-line target"
        )
    source_clip = session.runtime(source.id).get("clip") if source is not None else None
    if source_clip is not None and Path(source_clip.path).is_file():
        source_probe = review_mod.probe(Path(source_clip.path))
        if source_probe is not None:
            drift = abs(float(source_probe["duration_s"]) - float(probe["duration_s"]))
            measured["duration_drift_s"] = round(drift, 3)
            if drift > FINISH_DURATION_TOLERANCE_S:
                issues.append(
                    f"the finish runs {probe['duration_s']:.2f}s against the take's "
                    f"{source_probe['duration_s']:.2f}s — the cut would drift"
                )
    verdict = "fail" if issues else "pass"
    review = ClipReview(
        id=f"rev-finish-{op.id}",
        clip_id=label,
        op_id=op.id,
        verdict=verdict,
        families={"finish": verdict},
        measured=measured,
        issues=issues,
        recommended_op="accept" if verdict == "pass" else "retry",
    )
    session.register_review(op.id, review)
    if verdict == "pass":
        rt["narration"] = f"{label} finished at {width}×{height} — it replaces the take at the cut."
    else:
        rt["narration"] = (
            f"{label}: the {op.resolution} finish failed its check ({'; '.join(issues)}). "
            "The take's own print stays in the cut — Retry re-runs the finish; nothing is re-shot."
        )


def _after_sheet_still(op: DirectorOp, package, char, slot: str) -> str:
    """Blueprint 32, after a sheet still lands: measure the sheet against its
    anchor, redraw a slot that reads as another person (at most
    MAX_SLOT_REDRAWS times), and — once a Locked sheet is complete and
    coheres (or cannot be measured) — register the Flow character object
    from the anchor and the board so the clips can carry it. Every outcome
    is a sentence on the op's narration; nothing here can fail the still."""
    from diony_director import artifacts
    from diony_director import cast as cast_mod

    notes: list[str] = []
    if slot == "front":
        return ""
    receipt = cast_mod.measure_sheet(char)
    char.sheet_receipt = receipt
    notes.append(" " + cast_mod.sheet_summary(char).capitalize() + ".")
    if receipt.get("measured") and slot in (receipt.get("failed") or []):
        tries = int(char.sheet_retries.get(slot, 0))
        if tries < cast_mod.MAX_SLOT_REDRAWS:
            still = char.hero_stills.pop(slot, "")
            if still in char.grok_ref_pack:
                char.grok_ref_pack.remove(still)
            char.flow_media_ids.pop(slot, None)
            char.sheet_retries[slot] = tries + 1
            redraws = [
                o
                for o in artifacts.plan_sheet_ops(op.session_id, package)
                if (o.artifact_refs.get("bible") or {}).get("char_id") == char.char_id
                and (o.artifact_refs.get("bible") or {}).get("slot") == slot
            ]
            for redraw in redraws:
                session.add_bible_op(op.session_id, redraw, chain_key=char.char_id)
            notes.append(
                f" Redrawing {char.name}'s {slot} from the anchor "
                f"(attempt {tries + 2} of {cast_mod.MAX_SLOT_REDRAWS + 1})."
            )
        else:
            notes.append(
                f" {char.name}'s {slot} was redrawn {tries} time(s) and still reads as "
                "another person — approve by eye or rewrite the identity card."
            )
        return "".join(notes)
    if getattr(package, "cast_lock", "standard") != "locked":
        return "".join(notes)
    complete = all(s in char.hero_stills for s in artifacts.sheet_slots(package))
    if complete and not char.flow_character_ref and receipt.get("verified") is not False:
        media = cast_mod.flow_character_media(char)
        if media and flow.configured():
            try:
                voice = ""
                if char.voice is not None and char.voice.engine == "omni_preset":
                    voice = char.voice.engine_ref
                card = cast_mod.ensure_identity(char)
                char.flow_character_ref = flow.register_character(
                    char.name, media, voice=voice, notes=card.anchor
                )
                notes.append(
                    f" {char.name} registered as a Flow character ({char.flow_character_ref}) "
                    "from the anchor and the board — the clips carry it as @character."
                )
            except provider_base.CloudAdapterError as err:
                notes.append(
                    f" Flow character registration failed ({err}) — the stills ride as references instead."
                )
    return "".join(notes)


def _review_and_maybe_revise(op: DirectorOp, rt: dict, *, allow_spend: bool = False) -> None:
    stored = rt.get("clip")
    if stored is None:
        return
    if op.op is DirectorOpType.UPSCALE:
        # a finish is measured, never re-shot: it rides no fix ladder
        _review_finish(op, rt)
        return
    package = session.package_for(op.session_id)
    if op.mode is CloudGenerationMode.IMAGE and op.artifact_refs.get("style_frame"):
        # the still a chain link opens from — conformed to the program raster
        # so it never re-shapes the clip it seeds
        info = op.artifact_refs["style_frame"]
        link_id = str(info.get("for_op") or "")
        conformed = _conform_still(
            str(stored.path),
            cluster_root() / "director" / op.session_id / "frames" / f"style-{op.id}.jpg",
            sid=op.session_id,
        ) or str(stored.path)
        if link_id:
            session.runtime(link_id)["style_frame"] = conformed
        rt["narration"] = (
            f"Style frame composed for clip {int(info.get('clip_index', 0)) + 1} — "
            "the clip opens from it, in the film's look."
        )
        return
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
            slots = artifacts.sheet_slots(package)
            rt["narration"] = (
                f"Sheet still landed: {char.name} · {slot}. "
                f"{len([s for s in slots if s in char.hero_stills])}/{len(slots)} angles done."
            )
            rt["narration"] += _after_sheet_still(op, package, char, slot)
            session.persist(op.session_id)
        return
    clip_unit = session.clip_for_op(op)
    if clip_unit is None:
        if _is_chain_link(op):
            _review_chain_link(op, rt)
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


def _review_chain_link(op: DirectorOp, rt: dict) -> None:
    """A chained one-shot link gets reviewed like any take (Blueprint 29):
    did it open from the frame it was given, did it hold the previous link's
    look when it continued it, did the look wander. A fail holds the chain
    at that link — Retake or Accept — instead of seeding the next link from
    a frame the film rejects. The chain opener's first frame becomes a look
    anchor for every later style frame."""
    stored = rt.get("clip")
    if stored is None:
        return
    position = session.chain_position(op.session_id, op.id) or 0
    prev = session.previous_completed_clip_op(op.session_id, op)
    prev_clip = session.runtime(prev.id).get("clip") if prev is not None else None
    start = str(op.artifact_refs.get("start_image") or "")
    result = review_mod.review_chain_link(
        Path(stored.path),
        op_id=op.id,
        duration_s=int(op.duration_s),
        start_image=Path(start) if start else None,
        prev_clip_path=Path(prev_clip.path) if prev_clip is not None else None,
        scene_change=session.chain_scene_change(op),
        # Blueprint 31: a link that voices its own narration is measured for it
        spoken_line=(
            str(op.narration or "").strip() if getattr(op, "voicing", "post") == "generated" else ""
        ),
        # a link scripted to open or close on black is not a dead picture
        scripted_black=review_mod.scripts_black(op.prompt),
    )
    session.register_review(op.id, result)
    if position == 0:
        session.register_look_anchor(
            op,
            _last_frame(
                Path(stored.path),
                cluster_root() / "director" / op.session_id / "frames" / f"look-{op.id}.jpg",
                at_s=0.05,
                sid=op.session_id,
            ),
        )
    if result.verdict == "fail":
        rt["narration"] = (
            f"Clip {position + 1} printed but failed its look review: "
            + "; ".join(result.issues[:3])
            + ". The chain holds here — Retake it, or Accept it to continue."
        )


def _revise_after_job_failure(op: DirectorOp, detail: str, *, allow_spend: bool = False) -> None:
    from diony_contracts.production import ClipReview

    rt = session.runtime(op.id)
    if op.op is DirectorOpType.UPSCALE:
        # a failed finish never drafts a retake of the take it was finishing:
        # the take printed and stays in the cut; the finish alone retries
        label = op.clip_id or op.id
        rt["narration"] = (
            f"The {op.resolution} finish of {label} failed: {detail}. Its own print stays "
            "in the cut — Retry re-runs the finish; nothing is re-shot."
        )
        return
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
    runtime_s: int = Field(
        default=60, gt=0
    )  # no length guard: cost is the governor, and it is visible
    autopilot: bool = False
    max_usd: float = Field(default=0, ge=0)
    max_revisions_per_clip: int = Field(default=3, ge=0, le=10)
    # None = keep — same contract as MessageIn.aspect
    aspect: str | None = Field(default=None, pattern="^(16:9|9:16)$")
    # Lexicon templates — flow_* Google/Veo route only, grok_* Grok route
    # only; None = keep current.
    flow_world: str | None = None
    flow_camera: str | None = None
    grok_camera: str | None = None
    grok_atmosphere: str | None = None
    voice: str | None = None
    # Production style (Blueprint 31) — None = keep
    style: str | None = None
    cast: str | None = None


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
    _apply_program_settings(sid, body)
    state = session.begin_draft(sid, body.idea, body.runtime_s)
    return state


@app.get("/api/director/sessions/{sid}/draft")
def draft_status(sid: str) -> dict:
    _boot()
    return session.draft_state(sid)


@app.get("/api/director/master/template")
def master_template() -> dict:
    """What the brain is told (Blueprint 27): the master template, the
    duration amendment, and the output contract — transparency, not config."""
    from diony_director import master_prompt as master_mod

    return {
        "template": master_mod.MASTER_TEMPLATE,
        "duration_section": master_mod.DURATION_SECTION,
        "output_contract": master_mod.OUTPUT_CONTRACT,
    }


@app.get("/api/director/sessions/{sid}/master")
def session_master(sid: str) -> dict:
    """The session's pre-split finalized artifact — the locked package's, or
    the ready draft's. A one-shot's artifact rides its op (GET /ops/{id})."""
    from diony_director import master_prompt as master_mod

    _boot()
    if not session.session_exists(sid):
        raise HTTPException(status_code=404, detail=f"unknown session {sid}")
    master = session.master_for_session(sid)
    if master is None:
        raise HTTPException(status_code=404, detail="no master prompt derived yet — draft a script")
    return {"session_id": sid, "master": master_mod.to_doc(master)}


class DraftLineIn(BaseModel):
    line_id: str
    text: str | None = None
    shot: str | None = None
    # Blueprint 32: heard from outside the frame (dialogue lines only)
    off_screen: bool | None = None


@app.post("/api/director/sessions/{sid}/draft/line")
def edit_draft_line(sid: str, body: DraftLineIn) -> dict:
    _boot()
    try:
        state = session.edit_draft_line(
            sid, body.line_id, body.text, shot=body.shot, off_screen=body.off_screen
        )
        if body.shot:
            from diony_contracts.cluster import read_engine_choice
            from diony_director import flow_lexicon, grok_lexicon

            # A token NEITHER lexicon knows is almost surely a typo about to
            # ride a paid prompt as literal text — warned regardless of route.
            grok_unknown = set(grok_lexicon.unknown_tokens(body.shot))
            flow_unknown = set(flow_lexicon.unknown_tokens(body.shot))
            unknown = sorted(grok_unknown & flow_unknown)
            # When the session rolls on ONE provider, a token that is shorthand
            # only on the OTHER route (known there, unknown on the chosen route)
            # will definitely ride literal — a high-confidence, no-false-
            # positive warning. Under auto the route is genuinely undecided,
            # so a route-specific token stays quiet.
            choice = read_engine_choice()
            if choice == "grok":
                unknown += sorted(grok_unknown - flow_unknown)  # Flow-only tokens
            elif choice == "google-flow":
                unknown += sorted(flow_unknown - grok_unknown)  # Grok-only tokens
            if unknown:
                state = {**state, "shot_warnings": unknown}
        return state
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
        # a finish rides beside its take on the board, never as the take
        if op.clip_id and op.op is not DirectorOpType.UPSCALE:
            latest[op.clip_id] = op
    clips = []
    if package is not None:
        for clip in package.clips:
            op = latest.get(clip.clip_id)
            review = session.review_for(op.id) if op is not None else None
            clips.append(
                {
                    "clip_id": clip.clip_id,
                    "scene_id": clip.scene_id,
                    "clip_class": clip.clip_class,
                    "camera": clip.camera_idea,
                    "duration_s": clip.duration_s,
                    # Blueprint 33: how many script beats the take packs
                    "beats": len(clip.script_line_ids),
                    "join_next": clip.join_next,
                    "provider": clip.provider.value if clip.provider else "",
                    "model": clip.model,
                    "est_usd_micros": clip.estimate_usd_micros,
                    "op_id": op.id if op is not None else "",
                    "status": op.status if op is not None else "unplanned",
                    "verdict": review.verdict if review is not None else "",
                    "issues": review.issues[:3] if review is not None else [],
                    "accepted": session.is_accepted(op.id) if op is not None else False,
                    "can_upscale": session.can_upscale(op) if op is not None else False,
                    # Blueprint 30: the take's 1080p finish — usable, rolling, or failed
                    "finish": session.finish_state_for(op) if op is not None else None,
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
    from diony_director import cast as cast_mod

    # Blueprint 33 §3: what the cast dock says about a sheet that has not
    # landed — waiting for Run, drawing at the provider, or failed — read
    # from the sheet ops themselves, never guessed from the empty stills
    sheet_ops: dict[str, list[DirectorOp]] = {}
    for op in session.ops_for(sid):
        bible = op.artifact_refs.get("bible") or {}
        if bible.get("char_id"):
            sheet_ops.setdefault(str(bible["char_id"]), []).append(op)

    def _sheet_state(char) -> str:
        ops = sheet_ops.get(char.char_id) or []
        statuses = {o.status for o in ops}
        if statuses & {"submitting", "submitted", "deriving"}:
            return "drawing"
        if char.hero_stills and not (statuses & {"proposed"}):
            return "landed"
        if statuses & {"proposed"}:
            return "drawing" if char.hero_stills else "waiting"
        if "failed" in statuses and not char.hero_stills:
            return "failed"
        return "landed" if char.hero_stills else "waiting"

    for char in source_chars:
        characters.append(
            {
                "char_id": char.char_id,
                "name": char.name,
                "role": char.role,
                "wardrobe": _wardrobe_line(char),
                "face_approved": char.face_approved,
                "sheet_state": _sheet_state(char),
                "stills": sorted(char.hero_stills.keys()),
                "flow_registered": bool(char.flow_character_ref),
                # Blueprint 32: the identity card as it will ride the prompts,
                # and the sheet's measured coherence (honest when unmeasured)
                "identity": char.identity.model_dump(mode="json")
                if char.identity is not None
                else None,
                "identity_block": cast_mod.identity_block(char)
                if char.identity is not None
                else "",
                "sheet": cast_mod.sheet_summary(char) if char.hero_stills else "",
                "sheet_receipt": dict(char.sheet_receipt or {}),
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
        "draft_stage": draft.get("stage"),
        "master": draft.get("master"),
        "script": draft.get("script"),
        "beats": (draft.get("script") or {}).get("beats", []),
        "locked": bool((draft.get("script") or {}).get("locked")),
        "cast_lock": package.cast_lock if package is not None else session.cast_mode_for(sid),
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


@app.get("/api/director/ops/{op_id}/style_frame")
def style_frame_media(op_id: str) -> FileResponse:
    """The still a chain link opens from."""
    op = _op_or_404(op_id)
    path = str(session.runtime(op.id).get("style_frame") or "")
    if not path:
        raise HTTPException(status_code=404, detail="this clip has no style frame yet")
    return _vault_media(path)


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
        # Blueprint 32: priced with the stills on the batch bar, never targeted
        "keyframe_ops": [k.model_dump(mode="json") for k in session.keyframe_ops_for(sid)],
        "targeted": targeted,
        "submitted": [],
        "held": [],
    }


@app.get("/api/director/sessions/{sid}/lock")
def get_lock(sid: str) -> dict:
    """The locked set as lock_script handed it over — the clip ops (latest
    take per clip), the plate & sheet stills, the cast keyframes — so a
    resumed room can rebuild its priced cards and batch bar (Blueprint 33 §5)."""
    _boot()
    package = session.package_for(sid)
    if package is None:
        raise HTTPException(status_code=404, detail=f"session {sid} has no locked package")
    latest: dict[str, DirectorOp] = {}
    bible_ops: list[DirectorOp] = []
    keyframe_ids = {k.id for k in session.keyframe_ops_for(sid)}
    for op in session.ops_for(sid):
        if op.artifact_refs.get("bible"):
            bible_ops.append(op)
        elif op.clip_id and op.op is not DirectorOpType.UPSCALE and op.id not in keyframe_ids:
            latest[op.clip_id] = op
    order = {clip.clip_id: clip.order for clip in package.clips}
    ops = sorted(latest.values(), key=lambda o: order.get(o.clip_id or "", 0))
    planned_s = sum(clip.duration_s for clip in package.clips)
    return {
        "session_id": sid,
        "locked": bool(package.script.locked),
        "clips": len(package.clips),
        "scenes": len(package.script.scenes),
        "planned_s": planned_s,
        "estimate_usd_micros": package.estimate.total_usd_micros if package.estimate else 0,
        "ops": [op.model_dump(mode="json") for op in ops],
        "bible_ops": [b.model_dump(mode="json") for b in bible_ops],
        "keyframe_ops": [k.model_dump(mode="json") for k in session.keyframe_ops_for(sid)],
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
    # Blueprint 28: scripted/narrated lines are recorded in post, against the
    # assembled cut. Off cuts a mute picture (no voice track) deliberately;
    # a cut that carries lines but has no narrator backend is a loud stop,
    # never a film that silently shipped without its words.
    narrate: bool = True
    # The last line of defense against a provider-invented talking voice
    # reaching the mix: location tracks that measure dirty — or line-carrying
    # clips this host cannot measure — are cut with a silent bed. Off ships
    # the location tracks exactly as delivered.
    speech_guard: bool = True
    # s-16d2e6e5e0: a cast narrator and an authored script, and the cut
    # rendered without a single line because no clip carried one. A cut that
    # expects lines but has none is refused before any render; this ships a
    # deliberately mute picture instead.
    allow_silent: bool = False
    # Blueprint 30 — which print of each take the cut uses. "auto" cuts the
    # 1080p finish wherever a usable one exists (the film's raster rises to
    # 1080 lines the moment any clip carries one); "1080p" refuses, naming
    # them, while any Flow take in the cut still lacks its usable finish;
    # "720p" cuts the takes' own prints and ignores every finish.
    finish: str = Field(default="auto", pattern="^(auto|720p|1080p)$")
    # Narrator voice for THIS cut (and the film from here on): None keeps the
    # session's choice, "" / "auto" releases it to Director casting, a
    # roster name pins one voice.
    voice: str | None = None


def _cut_gaps(sid: str) -> tuple[list[str], list[DirectorOp], list[str]]:
    """Scripted clips with no usable take on disk; the drafted (proposed) ops
    that would fill them — originals never run, or retakes awaiting a Run —
    and the clips whose fill is already rolling (submitting/submitted)."""
    package = session.package_for(sid)
    if package is None:
        return _oneshot_gaps(sid)
    have: set[str] = set()
    for op in session.completed_clip_ops(sid):
        stored = session.runtime(op.id).get("clip")
        if stored is not None and Path(stored.path).is_file():
            have.add(op.clip_id)
    skipped = session.skipped_clip_ids(sid)  # a Skip is a decision, never a gap
    missing = [
        c.clip_id for c in package.clips if c.clip_id not in have and c.clip_id not in skipped
    ]
    gaps = set(missing)
    drafts = [o for o in session.ops_for(sid) if o.clip_id in gaps and o.status == "proposed"]
    rolling = sorted(
        {
            o.clip_id
            for o in session.ops_for(sid)
            if o.clip_id in gaps and o.status in {"submitting", "submitted"}
        }
    )
    return missing, drafts, rolling


def _oneshot_gaps(sid: str) -> tuple[list[str], list[DirectorOp], list[str]]:
    """A chained one-shot's holes: links with no usable take on disk. Without
    this, a chain missing link 7 would silently cut an 11-clip film."""
    missing: list[str] = []
    drafts: list[DirectorOp] = []
    rolling: list[str] = []
    for chain in session.oneshot_chain_ops(sid):
        for index, op in enumerate(chain):
            if op.status == "discarded":
                continue  # skipped by the director — a decision, never a gap
            if session.usable_take(op):
                stored = session.runtime(op.id).get("clip")
                if stored is not None and Path(stored.path).is_file():
                    continue
            label = f"clip {index + 1}/{len(chain)}"
            missing.append(label)
            if op.status == "proposed":
                drafts.append(op)
            elif op.status in {"submitting", "submitted"}:
                rolling.append(label)
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
    # Blueprint 31: a clip that voices its own line is never tail-trimmed to
    # the word-count arithmetic — the provider paced the words itself, and a
    # trim could cut the last of them; the take plays at its full length.
    if (
        clip.clip_class in {"sync-dialogue", "vo-over"}
        and clip.audio_packet.script_excerpt
        and clip.audio_packet.voicing != "generated"
    ):
        words = len(str(clip.audio_packet.script_excerpt[0].get("line", "")).split())
        speech_s = max(1, words) / WORDS_PER_SECOND + PRE_HANDLE_S + TAIL_HANDLE_S
        # Blueprint 33: a take that packs picture beats around its line needs
        # the beats too — the trim follows the take's natural length, never
        # the words alone (which would cut the beats the words sit inside)
        natural_s = float(getattr(clip, "natural_s", 0.0) or 0.0)
        spec["needed_s"] = round(max(speech_s, natural_s) + 0.25, 3)
    return spec


def _speech_guard(
    paths: list[Path],
    labels: list[str],
    carries_line: list[bool],
    specs: list[dict],
    voicing: list[str] | None = None,
    *,
    has_post_lines: bool = False,
) -> dict:
    """Blueprint 28's guarantee at the cut: no talking voice on any location
    track that reaches the mix. A track that MEASURES a voice is muted
    (whatever the clip class); a line-carrying clip whose track this host
    cannot measure is presumed dirty and muted too — the narrator is about to
    speak over it, and an unverified provider voice under the narration is the
    one defect this lane may never ship. Measured-clean tracks keep their
    ambience and score untouched. Mutes are receipts in the response, never a
    silent adjustment; staging DIONY_WHISPER_MODEL turns the presumption into
    a measurement.

    Blueprint 31 (Dialogue / Mix): a clip whose `voicing` is "generated"
    voices its own line — its track IS the voice and is never muted. One that
    measures NO talking voice (the provider stayed mute) is RESCUED: the
    narrator records its line in post (`rescued`). When the cut has a
    narrator rail at all (post lines, or a rescue), every kept spoken track
    is BALANCED to the narrator's voice level (`balanced`) so on-camera
    dialogue and narration sit at one level."""
    from diony_director.narration import NARRATION_LOUDNESS
    from diony_director.review import SPEECH_SECONDS_FAIL, SPEECH_WORDS_FAIL

    voicing = list(voicing or []) + ["post"] * (len(paths) - len(voicing or []))
    muted: list[dict] = []
    kept: list[dict] = []
    rescued: list[dict] = []
    checked = 0
    for index, path in enumerate(paths):
        detection = review_mod.location_speech(path)
        if voicing[index] == "generated" and carries_line[index]:
            if detection is None:
                kept.append(
                    {
                        "clip": labels[index],
                        "why": "voices its own line — the track is the voice; kept as "
                        "delivered (unverified on this host)",
                    }
                )
                continue
            checked += 1
            talking = (
                detection.word_count >= SPEECH_WORDS_FAIL
                or detection.speech_s >= SPEECH_SECONDS_FAIL
            )
            if not talking:
                rescued.append(
                    {
                        "clip": labels[index],
                        "why": "booked to voice its line but no talking voice is heard "
                        f"(heard: “{detection.transcript[:80]}”) — the narrator records "
                        "the line in post over the track",
                    }
                )
                continue
            kept.append(
                {
                    "clip": labels[index],
                    "why": f"voices its own line (heard: “{detection.transcript[:80]}”)",
                }
            )
            continue
        if detection is None:
            if carries_line[index]:
                specs[index]["mute"] = True
                muted.append(
                    {
                        "clip": labels[index],
                        "why": "carries a narrated line and its location track "
                        "cannot be verified speechless on this host — muted; "
                        "stage DIONY_WHISPER_MODEL to measure instead",
                    }
                )
            continue
        checked += 1
        if detection.word_count >= SPEECH_WORDS_FAIL or detection.speech_s >= SPEECH_SECONDS_FAIL:
            specs[index]["mute"] = True
            muted.append(
                {
                    "clip": labels[index],
                    "why": f"a talking voice measured on the location track "
                    f"(heard: “{detection.transcript[:80]}”) — muted",
                }
            )
    # the balance: with a narrator rail in the cut, every kept spoken track
    # is level-matched to the narrator instead of the ambience bed
    balanced: list[str] = []
    if kept and (has_post_lines or rescued):
        kept_labels = {entry["clip"] for entry in kept}
        for index, label in enumerate(labels):
            if label in kept_labels:
                specs[index]["loudness"] = NARRATION_LOUDNESS
                balanced.append(label)
    return {
        "muted": muted,
        "measured": checked,
        "clips": len(paths),
        "kept": kept,
        "rescued": rescued,
        "balanced": balanced,
    }


def _film_voice(report, chosen: str) -> dict | None:
    """Who narrated the film and why: the narrator cue (the first cue that
    is not an authored per-clip voice lock), or the lock when every cue is."""
    if not report.cues:
        return None
    narrator = next(
        (c for c in report.cues if c.get("cast") != "authored voice lock"), report.cues[0]
    )
    return {"voice_id": narrator["voice"], "cast": narrator["cast"], "chosen": chosen}


@app.post("/api/director/sessions/{sid}/assemble")
def assemble_session(sid: str, body: AssembleIn) -> dict:
    from diony_director import deliverables as deliverables_mod
    from diony_director import narration as narration_mod

    _boot()
    ops = session.completed_clip_ops(sid)

    # Blueprint 28: lines are recorded in post. Which clips carry one is
    # known before a single probe runs. Blueprint 31: a clip may instead
    # voice its line itself (`voicing == "generated"`) — it carries a line
    # but books no narrator cue.
    def _carries_line(op) -> bool:
        clip = session.clip_for_op(op)
        if (
            clip is not None
            and clip.clip_class in {"sync-dialogue", "vo-over"}
            and clip.audio_packet.script_excerpt
            and str(clip.audio_packet.script_excerpt[0].get("line", "")).strip()
        ):
            return True
        return bool(str(getattr(op, "narration", "") or "").strip())

    def _voicing(op) -> str:
        clip = session.clip_for_op(op)
        if clip is not None:
            return clip.audio_packet.voicing
        return str(getattr(op, "voicing", "post") or "post")

    def _post_line(op) -> bool:
        return _carries_line(op) and _voicing(op) != "generated"

    printed = [
        op
        for op in ops
        if (stored := session.runtime(op.id).get("clip")) is not None
        and Path(stored.path).is_file()
    ]
    if printed and body.narrate and not body.allow_silent and not any(map(_carries_line, printed)):
        expected: list[str] = []
        voice = session.voice_for(sid)
        if voice:
            expected.append(f"a narrator ({voice}) is cast")
        authored = session.authored_lines_expected(sid)
        if authored:
            expected.append(f"the idea authored {authored} narration line(s)")
        if expected:
            raise HTTPException(
                status_code=409,
                detail=(
                    "no clip carries a line but "
                    + " and ".join(expected)
                    + " — the script never reached the cut; retry the master derivation, "
                    "or assemble with allow_silent to ship a deliberately mute picture"
                ),
            )
    paths = []
    specs = []
    used_ops = []
    # Blueprint 30: the take is the unit of record; its finished print, when
    # one is usable, supplies the pixels
    finished: list[str] = []
    native: list[str] = []
    unfinished: list[str] = []
    for op in ops:
        stored = session.runtime(op.id).get("clip")
        if stored is None or not Path(stored.path).is_file():
            continue
        label = op.clip_id or op.id
        print_path = Path(stored.path)
        finish_op = None if body.finish == "720p" else session.finish_op_for(op)
        if finish_op is not None:
            print_path = Path(session.runtime(finish_op.id).get("clip").path)
            finished.append(label)
        else:
            native.append(label)
            if op.provider is not None and op.provider.value == "google-flow":
                unfinished.append(label)
        paths.append(print_path)
        specs.append(_assembly_spec(op))
        used_ops.append(op)
    if not paths:
        raise HTTPException(status_code=409, detail="no completed clips to assemble yet")
    if body.finish == "1080p" and unfinished:
        reasons = []
        for op in used_ops:
            if (op.clip_id or op.id) not in unfinished:
                continue
            state = session.finish_state_for(op)
            if state is None:
                reasons.append(f"{op.clip_id or op.id} (no finish yet)")
            elif state["status"] in {"proposed", "submitting", "submitted"}:
                reasons.append(f"{op.clip_id or op.id} (finish still rolling)")
            else:
                why = state.get("reason") or "; ".join(state.get("issues") or []) or state["status"]
                reasons.append(f"{op.clip_id or op.id} (finish {why})")
        raise HTTPException(
            status_code=409,
            detail=(
                "refusing to cut a 1080p film while these Flow takes lack their 1080p finish: "
                + ", ".join(reasons)
                + " — run Upscale All (or Retry the failed finish) first, or assemble with "
                "finish=auto to cut the prints that exist"
            ),
        )
    if not body.allow_gaps:
        missing, drafts, rolling = _cut_gaps(sid)
        if missing:
            pending = sorted({o.clip_id for o in drafts if o.clip_id})
            if not pending and drafts:
                # one-shot chain links carry no clip_id — name the count
                pending = [f"{len(drafts)} chain links"]
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
    post_lines = any(_post_line(op) for op in used_ops)
    spoken = [
        op.clip_id or op.id for op in used_ops if _carries_line(op) and _voicing(op) == "generated"
    ]
    speech_report: dict = {
        "muted": [],
        "measured": 0,
        "clips": len(paths),
        "kept": [],
        "rescued": [],
        "balanced": [],
    }
    if body.speech_guard:
        speech_report = _speech_guard(
            paths,
            [op.clip_id or op.id for op in used_ops],
            [_carries_line(op) for op in used_ops],
            specs,
            [_voicing(op) for op in used_ops],
            has_post_lines=post_lines,
        )
    rescued_labels = {entry["clip"] for entry in speech_report.get("rescued", [])}
    # A cut that carries post-recorded lines (or rescues a mute spoken clip)
    # but has no narrator backend fails BEFORE ffmpeg spends a render on it.
    # A Dialogue-style cut whose every line is voiced in its clip needs no
    # narrator at all.
    wants_narration = body.narrate and (post_lines or bool(rescued_labels))
    if wants_narration and not narration_mod.narrator_configured():
        raise HTTPException(
            status_code=409,
            detail=f"this cut carries scripted lines and {narration_mod.UNCONFIGURED}",
        )

    dest = cluster_root() / "director" / sid / "film.mp4"
    # one finished print lifts the whole cut to the 1080 raster: the takes
    # without a finish are conformed up locally and named in the receipt
    raster_w, raster_h = session.finish_raster(sid) if finished else session.program_raster(sid)
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

    entries = [
        {
            "op": op,
            "clip": session.clip_for_op(op),
            "window": result.windows[index],
            "review": session.review_for(op.id),
            "accepted": session.is_accepted(op.id),
            # Blueprint 31: a spoken clip the provider left mute gets its
            # line from the narrator after all
            "narrate_anyway": (op.clip_id or op.id) in rescued_labels,
        }
        for index, op in enumerate(used_ops)
    ]

    narration_report = None
    if wants_narration:
        cues = narration_mod.cue_sheet(entries)
        # the narrator is CAST, not defaulted: the master prompt's tone and
        # the speaking character pick the voice from the Grok roster
        master = session.master_for_session(sid)
        tone_bits = []
        if master is not None:
            tone_bits = [
                master.mood,
                master.visual_style,
                master.performance,
                str(master.lighting.get("quality", "")),
            ]
            if master.look is not None:
                # the Look Bible's medium reads into the cast too — an
                # animated explainer and photoreal noir want different voices
                tone_bits.append(master.look.medium)
        package = session.package_for(sid)
        notes = (
            {c.name: c.age_body_notes for c in package.characters} if package is not None else {}
        )
        commit_voice = _plan_voice(sid, body.voice)
        if commit_voice is not None:
            commit_voice()
        narration_mod.cast_cues(
            cues,
            tone_text=" ".join(b for b in tone_bits if b),
            character_notes=notes,
            voice=session.voice_for(sid),
            seed=sid,
        )
        picture_lock = dest.with_name("picture-lock.mp4")
        narrated = dest.with_name("film-narrated.tmp.mp4")
        try:
            narration_report = narration_mod.narrate(
                dest, cues, narrated, loudness_target=body.loudness
            )
        except narration_mod.NarrationUnavailable as err:
            raise HTTPException(status_code=409, detail=str(err)) from err
        except narration_mod.NarrationFailed as err:
            raise HTTPException(
                status_code=502,
                detail=f"the cut is assembled but its narration failed — {err}",
            ) from err
        # the mute cut survives beside the master: re-narration reads it
        # instead of re-rendering the whole assembly
        dest.replace(picture_lock)
        narrated.replace(dest)
        narration_report.path = dest

    delivered: dict = {}
    try:
        delivered = deliverables_mod.build_deliverables(
            dest.parent,
            session_id=sid,
            entries=entries,
            film=dest,
            # the master may have grown a tail hold so the last line could
            # finish — sidecars time against the delivered length
            film_duration_s=(
                narration_report.duration_s
                if narration_report is not None and narration_report.duration_s > 0
                else result.duration_s
            ),
            look=result.look,
            loudness_target=result.loudness_target,
            narration=narration_report,
        )
    except Exception as err:
        delivered = {"names": [], "error": f"{type(err).__name__}: {err}"}
    return {
        "film": str(result.path),
        "duration_s": (
            narration_report.duration_s
            if narration_report is not None and narration_report.duration_s > 0
            else result.duration_s
        ),
        "clip_count": result.clip_count,
        "look": result.look,
        "loudness_target": result.loudness_target,
        "narration": (
            {
                "cue_count": len(narration_report.cues),
                "overruns": narration_report.overruns,
                "shifts": narration_report.shifts,
                "tail_hold_s": narration_report.tail_hold_s,
                # who spoke, and why: the console's choice or the cast
                "voice": _film_voice(narration_report, session.voice_for(sid)),
            }
            if narration_report is not None
            else {
                "cue_count": 0,
                "skipped": (
                    "narrate off"
                    if not body.narrate
                    else ("lines voiced in the clips" if spoken else "no lines")
                ),
            }
        ),
        "speech_guard": speech_report,
        # Blueprint 31 receipt: the film's production style and how its
        # lines were voiced — in the clips, by the narrator, or rescued
        "style": {
            "key": session.style_for(sid),
            "spoken_in_clip": spoken,
            "narrated": [op.clip_id or op.id for op in used_ops if _post_line(op)],
            "rescued": sorted(rescued_labels),
        },
        "raster": f"{raster_w}x{raster_h}",
        # Blueprint 30 receipt: which takes cut from their 1080p finish, which
        # cut from their own print (and were conformed up when the raster rose)
        "finish": {
            "mode": body.finish,
            "resolution": session.FINISH_RESOLUTION if finished else "720p",
            "finished": finished,
            "native": native,
            "conformed_up": native if finished else [],
        },
        "deliverables": delivered.get("names", []),
        "final_check": delivered.get("final_check"),
        "cue_count": delivered.get("cue_count", 0),
        # the shots the director skipped — the cut leaves them out by decision
        "skipped": _skipped_labels(sid),
    }


def _skipped_labels(sid: str) -> list[str]:
    labels = sorted(session.skipped_clip_ids(sid))
    for chain in session.oneshot_chain_ops(sid):
        for index, op in enumerate(chain):
            if op.status == "discarded":
                labels.append(f"clip {index + 1}/{len(chain)}")
    return labels


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


@app.post("/api/director/sessions/{sid}/bible/approve_all")
def approve_all_faces(sid: str) -> dict:
    """Blueprint 33 §3: every character whose sheet has landed is approved in
    one press from the cast dock — a character with no still yet is left
    for the next press, named in the reply, never silently approved."""
    _boot()
    package = session.package_for(sid)
    if package is None:
        raise HTTPException(status_code=404, detail=f"session {sid} has no locked package")
    approved: list[str] = []
    waiting: list[str] = []
    for char in package.characters:
        if char.face_approved:
            continue
        if not char.hero_stills:
            waiting.append(char.name)
            continue
        char.face_approved = True
        approved.append(char.name)
    if not approved:
        if waiting:
            raise HTTPException(
                status_code=409,
                detail=f"no sheet has landed yet for {', '.join(waiting)} — nothing to approve",
            )
        return {"approved": [], "waiting": [], "reply": "Every face is already approved."}
    session.persist(sid)  # approvals are decisions; decisions survive restarts
    reply = f"Approved {', '.join(approved)} — {len(approved)} face(s) frozen at their sheets."
    if waiting:
        reply += f" Still drawing: {', '.join(waiting)}."
    return {"approved": approved, "waiting": waiting, "reply": reply}


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
    from diony_director import cast as cast_mod

    media_ids = cast_mod.flow_character_media(char)
    if not media_ids:
        raise HTTPException(
            status_code=409,
            detail=f"{char.name} has no Flow-hosted stills yet — generate the sheet first",
        )
    voice = ""
    if char.voice is not None and char.voice.engine == "omni_preset":
        voice = char.voice.engine_ref
    notes = char.identity.anchor if char.identity is not None else ""
    try:
        ref = flow.register_character(char.name, media_ids, voice=voice, notes=notes)
    except provider_base.CloudAdapterError as err:
        raise HTTPException(status_code=502, detail=str(err)) from err
    char.flow_character_ref = ref
    session.persist(sid)  # a PAID registration whose only record is this field
    return {
        "reply": f"{char.name} is registered as a reusable Flow character ({ref}).",
        "flow_character_ref": ref,
    }


@app.post("/api/director/sessions/{sid}/bible/redraw/{char_id}/{slot}")
def redraw_sheet_slot(sid: str, char_id: str, slot: str) -> dict:
    """Blueprint 32: throw one sheet still away and draw it again from the
    anchor — the human's version of the measured redraw. The new still
    joins the bible gate and the running batch like the original did."""
    from diony_director import artifacts

    _boot()
    package = session.package_for(sid)
    if package is None:
        raise HTTPException(status_code=404, detail=f"session {sid} has no locked package")
    try:
        char = package.character(char_id)
    except KeyError as err:
        raise HTTPException(status_code=404, detail=f"unknown character {char_id}") from err
    if slot not in artifacts.sheet_slots(package):
        raise HTTPException(status_code=422, detail=f"{slot} is not a sheet slot of this package")
    if slot == "front":
        others_landed = [s for s in char.hero_stills if s != "front"]
        others_pending = [
            o
            for o in session.ops_for(sid)
            if (o.artifact_refs.get("bible") or {}).get("char_id") == char_id
            and (o.artifact_refs.get("bible") or {}).get("slot") not in {"", "front"}
            and o.status not in {"completed", "failed", "discarded"}
        ]
        if others_landed or others_pending:
            raise HTTPException(
                status_code=409,
                detail=(
                    f"{char.name}'s anchor seeds every other still — redraw the others first, "
                    "or start the sheet over with a new anchor once nothing else is drawing from it"
                ),
            )
    still = char.hero_stills.pop(slot, "")
    if still in char.grok_ref_pack:
        char.grok_ref_pack.remove(still)
    char.flow_media_ids.pop(slot, None)
    char.face_approved = False
    char.sheet_retries[slot] = int(char.sheet_retries.get(slot, 0)) + 1
    planned = [
        o
        for o in artifacts.plan_sheet_ops(sid, package)
        if (o.artifact_refs.get("bible") or {}).get("char_id") == char_id
        and (o.artifact_refs.get("bible") or {}).get("slot") == slot
    ]
    for op in planned:
        session.add_bible_op(sid, op, chain_key=char_id)
    session.persist(sid)
    return {
        "ops": [op.model_dump(mode="json") for op in planned],
        "reply": f"Redrawing {char.name}'s {slot} from the anchor."
        if planned
        else "Nothing to redraw.",
    }


class UpscaleIn(BaseModel):
    # 720p promotes a 360p Omni clip; 1080p is the finish; 4K needs Google AI Ultra
    resolution: str = Field(default=session.FINISH_RESOLUTION, pattern="^(720p|1080p|4K)$")


def _upscale_source(op: DirectorOp) -> str:
    """The Flow-hosted media id a server-side upscale needs, or the reason
    this take cannot have one."""
    if op.op is DirectorOpType.UPSCALE:
        raise HTTPException(
            status_code=409, detail="this op is already a finish — upscale its take"
        )
    if op.status != "completed":
        raise HTTPException(
            status_code=409, detail=f"op is {op.status}; upscale needs a completed clip"
        )
    if op.provider is None or op.provider.value != "google-flow":
        raise HTTPException(
            status_code=409,
            detail=f"{op.provider.value if op.provider else 'this'} clips have no server-side "
            "upscale lane — Grok clips are conformed locally at the cut instead",
        )
    source = str(session.runtime(op.id).get("media_generation_id") or "")
    if not source:
        raise HTTPException(
            status_code=409,
            detail="no Flow-hosted media id for this clip — server-side upscale needs "
            "Flow-origin media (Grok clips are conformed locally at the cut instead)",
        )
    return source


def _draft_upscale(op: DirectorOp, resolution: str) -> DirectorOp:
    """One priced UPSCALE op for a completed Flow take: the take is its
    source_op (the cut substitutes the print), its duration is the take's
    (the ledger row and the finish check both read it)."""
    import uuid as _uuid

    from diony_contracts.director import CloudProvider
    from diony_director import estimate as estimate_mod

    source = _upscale_source(op)
    est = estimate_mod.estimate_op(
        provider=CloudProvider.GOOGLE_FLOW,
        model=flow.UPSCALE_MODEL,
        mode=CloudGenerationMode.UPSCALE,
        duration_s=max(1, int(op.duration_s or 1)),
        resolution=resolution,
    )
    upscale_op = DirectorOp(
        id=f"op-{_uuid.uuid4().hex[:10]}",
        session_id=op.session_id,
        op=DirectorOpType.UPSCALE,
        clip_id=op.clip_id,
        provider=CloudProvider.GOOGLE_FLOW,
        model=flow.UPSCALE_MODEL,
        mode=CloudGenerationMode.UPSCALE,
        duration_s=max(1, int(op.duration_s or 1)),
        resolution=resolution,
        artifact_refs={"source_video": source, "source_op": op.id},
        estimate=est,
        rationale=f"{resolution} finish of {op.clip_id or op.id}",
    )
    return session.register_op(op.session_id, upscale_op)


@app.post("/api/director/ops/{op_id}/upscale")
def propose_upscale(op_id: str, body: UpscaleIn | None = None) -> dict:
    resolution = body.resolution if body is not None else session.FINISH_RESOLUTION
    op = _op_or_404(op_id)
    _upscale_source(op)
    existing = session.finish_op_for(op, resolution)
    if existing is not None:
        raise HTTPException(
            status_code=409,
            detail=f"{op.clip_id or op.id} already has its {resolution} finish ({existing.id})",
        )
    rolling = session.finish_in_flight_for(op)
    if rolling is not None:
        raise HTTPException(
            status_code=409,
            detail=f"a finish of {op.clip_id or op.id} is already {rolling.status} ({rolling.id})",
        )
    upscale_op = _draft_upscale(op, resolution)
    usd = upscale_op.estimate.total_usd_micros / 1_000_000
    return {
        "op": upscale_op.model_dump(mode="json"),
        "reply": f"Upscale to {resolution} adds about ${usd:.2f}. Run it when ready.",
    }


@app.get("/api/director/upscale/rate")
def upscale_rate(resolution: str = session.FINISH_RESOLUTION) -> dict:
    """What one server-side finish costs on the user's plan — the batch bar
    prices 'Run, Assemble & Upscale All' from it before anything rolls."""
    from diony_contracts.director import CloudProvider
    from diony_director import estimate as estimate_mod

    if resolution not in flow.UPSCALE_RESOLUTIONS:
        raise HTTPException(
            status_code=422, detail=f"resolution must be one of {flow.UPSCALE_RESOLUTIONS}"
        )
    try:
        est = estimate_mod.estimate_op(
            provider=CloudProvider.GOOGLE_FLOW,
            model=flow.UPSCALE_MODEL,
            mode=CloudGenerationMode.UPSCALE,
            duration_s=1,
            resolution=resolution,
        )
    except estimate_mod.EstimateError as err:
        raise HTTPException(status_code=409, detail=str(err)) from err
    return {
        "resolution": resolution,
        "usd_micros": est.total_usd_micros,
        "credits": sum(line.internal_credits for line in est.lines),
        "rate_cards_stale": est.rate_cards_stale,
        "pricing_basis": "modeled_from_plan",
        "ultra_only": resolution == "4K",
        "lines": [line.model_dump(mode="json") for line in est.lines],
    }


@app.post("/api/director/sessions/{sid}/upscale_all")
def upscale_all(sid: str, body: UpscaleIn | None = None) -> dict:
    """Blueprint 30 — finish every printed Flow take of the cut at 1080p in
    one directed batch: a priced UPSCALE op per take (idempotent: a usable
    finish is kept, a rolling one is left alone, a drafted one is re-targeted,
    only a missing or failed finish drafts anew), targeted onto the submitter
    a few at a time. Grok takes have no server-side lane and are named as
    skipped — the cut conforms them locally. The cut itself is a separate
    call: assemble with finish=1080p once this settles."""
    _boot()
    resolution = body.resolution if body is not None else session.FINISH_RESOLUTION
    targeted: list[str] = []
    already: list[str] = []
    rolling: list[str] = []
    skipped: list[dict] = []
    est_total = 0
    for op in session.completed_clip_ops(sid):
        label = op.clip_id or op.id
        stored = session.runtime(op.id).get("clip")
        if stored is None or not Path(stored.path).is_file():
            continue
        if session.finish_op_for(op, resolution) is not None:
            already.append(label)
            continue
        in_flight = session.finish_in_flight_for(op)
        if in_flight is not None and in_flight.status != "proposed":
            rolling.append(label)
            continue
        try:
            _upscale_source(op)
        except HTTPException as err:
            skipped.append({"clip_id": label, "reason": str(err.detail)})
            continue
        upscale_op = in_flight if in_flight is not None else _draft_upscale(op, resolution)
        session.add_batch_target(sid, upscale_op.id)
        targeted.append(upscale_op.id)
        est_total += upscale_op.estimate.total_usd_micros if upscale_op.estimate else 0
    if targeted:
        session.persist(sid)
        _kick_submitter(sid)
    return {
        "resolution": resolution,
        "targeted": targeted,
        "already": already,
        "rolling": rolling,
        "skipped": skipped,
        "est_usd_micros": est_total,
        "queued": session.queued_count(sid),
        "submitting": session.submitter_active(sid),
        "held": session.drain_held(sid),
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
