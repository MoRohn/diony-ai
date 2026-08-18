from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from diony_contracts.cluster import (
    ENGINE_FOR_MODE,
    RETAKE_CAP,
    bind_host,
    refuse_concurrent,
    site_from_lock,
    site_status,
    write_mode_lock,
)
from diony_contracts.edits import apply_clip_edit, version_ladder
from diony_contracts.enums import (
    ArtifactKind,
    CustomerKind,
    GateName,
    MixLockState,
    PictureLockState,
    ProjectStatus,
    QCVerdict,
    TakeOrigin,
)
from diony_contracts.fixture import demo_project
from diony_contracts.live import inference_live
from diony_contracts.models import (
    Artifact,
    Asset,
    Brief,
    Gate,
    Note,
    Project,
    QCReport,
    Take,
)
from diony_contracts.stitch import assemble_program, program_view
from diony_contracts.studio import CUSTOMER_CATALOG, TARGET_ALLOTMENTS, blank_studio, expand_idea
from diony_producer.brain import expand_on_site
from diony_producer.camera import live_camera
from diony_producer.farm import enter_farm, farm_status, start_farm
from diony_producer.finish import (
    enter_finish,
    finish_status,
    live_finish,
    selected_masters_ready,
    start_finish,
)
from diony_producer.vault import (
    abs_path,
    load_current_project,
    materialize_project,
    persist_project,
    save_upload,
    write_stub,
)

STATIC = Path(__file__).resolve().parent / "static"

app = FastAPI(title="Diony Producer", version="0.8.0")
app.mount("/static", StaticFiles(directory=STATIC), name="static")

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
    title_hint: str = ""
    audience: str = ""
    attachment_ids: list[str] = []


class BiblePatch(BaseModel):
    title: str | None = None
    logline: str | None = None
    world: str | None = None
    visual_grammar: str | None = None


class ClipEditIn(BaseModel):
    instruction: str
    t_in_frames: int | None = None
    t_out_frames: int | None = None
    attachment_ids: list[str] = []


class ModeIn(BaseModel):
    mode: str
    engine: str = ""


def _gate(name: GateName) -> Gate:
    for g in _project.gates:
        if g.name == name:
            return g
    raise HTTPException(404, name.value)


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
    n = attempt or (len(_project.takes_for(shot.id)) + 1)
    tid = f"{shot.id}-t{n}"
    live = live_camera()
    rel = f"takes/{tid}/negative_720.mp4" if live else f"takes/{tid}/negative_720.txt"
    take = Take(
        id=tid,
        shot_id=shot.id,
        attempt=n,
        engine=shot.engine,
        seed=shot.seed + (n - 1) * 19,
        asset_720p_id=f"neg-{tid}",
        version=n,
        origin=TakeOrigin.GENERATION,
        label="print" if n == 1 else f"retake {n}",
        artifacts=[
            Artifact(
                id=f"art-{tid}-720",
                take_id=tid,
                kind=ArtifactKind.NEGATIVE_720,
                path=rel,
                duration_frames=shot.duration_frames,
                width=1280,
                height=720,
            )
        ],
        qc=QCReport(
            id=f"qc-{tid}",
            take_id=tid,
            shot_id=shot.id,
            verdict=QCVerdict.WARN,
            identity_ok=True,
            morph_ok=True,
            flicker_ok=True,
            duration_ok=True,
            legal_ok=True,
            notes=(
                "Queued for Wan2.2 on this pair. 720p negative, not a 1080 master."
                if live
                else "Laptop stub. No /models/wan on this machine. Dual-Spark FARM writes mp4."
            ),
        ),
    )
    if not shot.selected_take_id:
        shot.selected_take_id = tid
    return take


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC / "index.html")


@app.get("/api/project")
def get_project() -> dict:
    return _dump()


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


@app.get("/api/finish")
def get_finish() -> dict:
    return finish_status()


@app.get("/api/catalog")
def catalog() -> dict:
    site = site_status()
    return {
        "customers": CUSTOMER_CATALOG,
        "allotments": TARGET_ALLOTMENTS,
        "clock": site["clock"],
        "negative": site["negative"],
        "master": site["master"],
        "edit_engine": "vace-fun-A14B",
        "floors": ["idea", "dailies", "program", "lock"],
        "stitch": "overlap 8–16 frames; I2V inherits the previous print",
        "site": site,
        "retake_cap": RETAKE_CAP,
        "dry_run": site["dry_run"],
        "live": site["live"],
        "camera": "Wan2.2 generate.py 720p" if site["live"] else "stub until /models/wan is present",
        "finish": "diony_sr_v1 1920×1080 Rec.709" if live_finish() else "1080 after picture lock when SR weights are present",
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
    held = _hold_assets(body.attachment_ids)
    try:
        brief = Brief(
            idea=body.idea,
            customer_kind=body.customer_kind,
            target_minutes=body.target_minutes,
            title_hint=body.title_hint,
            audience=body.audience,
            attachment_ids=body.attachment_ids,
        )
    except Exception as exc:
        raise HTTPException(400, str(exc)) from exc
    _project = expand_idea(brief, held, compute=site_from_lock())
    return _dump()


@app.post("/api/studio/new")
def studio_new() -> dict:
    global _project, _assets
    _assets = {}
    _project = _apply_site(blank_studio(site_from_lock()))
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
    for shot in _project.shots:
        shot.prompt = expand_on_site(shot, _project.bible)
    queued: list = []
    for shot in _project.shots:
        if _project.takes_for(shot.id):
            if not shot.selected_take_id:
                latest = _project.takes_for(shot.id)[-1]
                shot.selected_take_id = latest.id
            continue
        take = _generation_take(shot)
        _project.takes.append(take)
        queued.append(take)
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
    return _dump()


@app.get("/api/program")
def get_program() -> dict:
    view = program_view(_project)
    view["project_id"] = _project.id
    return view


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


@app.post("/api/takes/{take_id}/edit")
def edit_take(take_id: str, body: ClipEditIn) -> dict:
    global _project
    _attach_assets(body.attachment_ids)
    try:
        _project.take(take_id)
        _project = apply_clip_edit(
            _project,
            source_take_id=take_id,
            instruction=body.instruction,
            t_in_frames=body.t_in_frames,
            t_out_frames=body.t_out_frames,
            attachment_ids=body.attachment_ids,
        )
    except KeyError as exc:
        raise HTTPException(404, "take not on this project") from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    assemble_program(_project)
    if _project.timeline and _project.timeline.preview_path:
        write_stub(_project.timeline.preview_path, _project.id, "program_stitch")
    return _dump()


@app.get("/api/shots/{shot_id}/versions")
def shot_versions(shot_id: str) -> dict:
    try:
        shot = _project.shot(shot_id)
    except KeyError as exc:
        raise HTTPException(404, "shot not on this project") from exc
    takes = version_ladder(_project, shot_id)
    return {
        "shot_id": shot_id,
        "selected_take_id": shot.selected_take_id,
        "takes": [t.model_dump(mode="json") for t in takes],
        "edits": [e.model_dump(mode="json") for e in _project.edits if e.shot_id == shot_id],
    }


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
    shot.selected_take_id = take_id
    return _dump()


@app.post("/api/shots/{shot_id}/retake")
def retake_shot(shot_id: str) -> dict:
    """Reject-and-retake. Live Wan2.2 when the pack is on this box. Cap 4 generation takes."""
    if not _gate(GateName.PICTURE).open:
        raise HTTPException(400, "picture is locked")
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
    take = _generation_take(shot)
    _project.takes.append(take)
    shot.selected_take_id = take.id
    if live_camera():
        enter_farm()
        start_farm(_project, [take])
    assemble_program(_project)
    if _project.timeline and _project.timeline.preview_path:
        write_stub(_project.timeline.preview_path, _project.id, "program_stitch")
    return _dump()


@app.post("/api/gates/{name}/lock")
def lock_gate(name: GateName, body: GateIn) -> dict:
    gate = _gate(name)
    if name == GateName.PICTURE and any(
        s.selected_take_id is None for s in _project.shots
    ):
        raise HTTPException(400, "picture lock needs a selected take on every shot")
    if name == GateName.PICTURE and inference_live() and not live_finish():
        raise HTTPException(
            400,
            "picture lock needs diony_sr_v1. Wizard App pulls RealESRGAN_x2.pth.",
        )
    if name == GateName.MIX:
        pic = _gate(GateName.PICTURE)
        if pic.open:
            raise HTTPException(400, "mix lock requires picture lock")
        if live_finish() and not selected_masters_ready(_project):
            raise HTTPException(400, "mix lock needs 1080 masters on every selected take")
        if _project.mix:
            _project.mix.state = MixLockState.LOCKED
    if name == GateName.DISTRIBUTION:
        mix = _gate(GateName.MIX)
        if mix.open:
            raise HTTPException(400, "distribution requires mix lock")
    gate.open = False
    gate.locked_by = body.locked_by
    gate.locked_at = datetime.now(timezone.utc)
    if _project.timeline and name == GateName.PICTURE:
        _project.timeline.picture_lock = PictureLockState.LOCKED
        _project.status = ProjectStatus.PICTURE_LOCKED
        _project.fleet_mode = "FINISH"
        if live_finish():
            enter_finish()
            selected = []
            for shot in _project.shots:
                if shot.selected_take_id:
                    try:
                        selected.append(_project.take(shot.selected_take_id))
                    except KeyError:
                        continue
            if selected:
                start_finish(_project, selected)
    if name == GateName.MIX:
        _project.status = ProjectStatus.MIX_LOCKED
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
    return _dump()


def main() -> None:
    import uvicorn

    global _project
    _project = _boot_project()
    uvicorn.run(
        "diony_producer.app:app",
        host=bind_host(),
        port=int(os.environ.get("DIONY_PRODUCER_PORT", "8788")),
        reload=False,
        log_level="info",
    )


if __name__ == "__main__":
    main()
