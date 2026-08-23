"""Conductor: DeepSeek tool loop. Live Wan is Producer FARM. Dual-Spark sequential.

Reads the Studio site lock. Dual-Spark, B300, and B200 refuse concurrent brain+Wan.
"""

from __future__ import annotations

import os

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from diony_contracts.cluster import (
    ENGINE_FOR_MODE,
    bind_host,
    refuse_concurrent,
    site_from_lock,
    site_status,
    write_mode_lock,
)
from diony_contracts.live import inference_live
from diony_contracts.enums import Engine, FinishIntent, QCVerdict
from diony_contracts.fixture import demo_project
from diony_contracts.models import QCReport, Take

app = FastAPI(title="Diony Conductor", version="0.2.0")
_project = demo_project()
_project.compute = site_from_lock()
_queue: list[str] = ["sh-030"]  # alley shot still needs a take in the fixture


class TickOut(BaseModel):
    action: str
    detail: str
    project_id: str
    fleet_mode: str


class ModeIn(BaseModel):
    mode: str
    engine: str = ""


@app.get("/health")
def health() -> dict:
    site = site_status()
    return {
        "ok": True,
        "dry_run": not inference_live(),
        "fleet_mode": _project.fleet_mode,
        "site": site,
    }


@app.get("/v1/site")
def site() -> dict:
    return site_status()


@app.get("/v1/project")
def project() -> dict:
    _project.compute = site_from_lock()
    return _project.model_dump(mode="json")


@app.post("/v1/mode")
def set_mode(body: ModeIn) -> dict:
    mode = body.mode.strip().upper()
    if mode not in ENGINE_FOR_MODE:
        raise HTTPException(400, "mode must be BRAIN, SPLIT, FARM, or FINISH")
    engine = (body.engine or ENGINE_FOR_MODE[mode]).strip().lower()
    held = refuse_concurrent(mode, engine)
    if held:
        raise HTTPException(400, held)
    payload = write_mode_lock(mode, engine, reason="conductor")
    _project.fleet_mode = mode
    return payload


@app.post("/v1/tick", response_model=TickOut)
def tick() -> TickOut:
    """One autonomous step: dispatch a missing take as a fake Wan job + QC."""
    _project.compute = site_from_lock()
    if _project.gates[0].open:
        return TickOut(
            action="wait_gate",
            detail="Bible gate is open. Humans own bible lock.",
            project_id=_project.id,
            fleet_mode=_project.fleet_mode,
        )
    if not _queue:
        return TickOut(
            action="idle",
            detail="No queued shots. FARM idle. Production runs 1080 finish and mix.",
            project_id=_project.id,
            fleet_mode=_project.fleet_mode,
        )
    site = site_status()
    if site["sequential"]:
        held = refuse_concurrent("FARM", "wan")
        if held:
            return TickOut(
                action="wait_mode",
                detail=held,
                project_id=_project.id,
                fleet_mode=_project.fleet_mode,
            )
        write_mode_lock("FARM", "wan", reason="dry-run dispatch")
        _project.fleet_mode = "FARM"
    shot_id = _queue.pop(0)
    shot = _project.shot(shot_id)
    attempt = len(_project.takes_for(shot_id)) + 1
    take_id = f"{shot_id}-t{attempt}"
    take = Take(
        id=take_id,
        shot_id=shot_id,
        attempt=attempt,
        engine=shot.engine,
        seed=shot.seed + attempt,
        asset_720p_id=f"neg-{take_id}",
        qc=QCReport(
            id=f"qc-{take_id}",
            take_id=take_id,
            shot_id=shot_id,
            verdict=QCVerdict.WARN,
            identity_ok=True,
            morph_ok=True,
            flicker_ok=True,
            duration_ok=True,
            legal_ok=True,
            notes="Dry-run take. Not a real Wan negative. Finish still required for deliverable.",
        ),
    )
    _project.takes.append(take)
    if shot.finish == FinishIntent.DELIVERABLE and shot.engine == Engine.I2V_A14B:
        detail = f"Dispatched {take_id} on {shot.engine} (stub). QC warn — dry-run."
    else:
        detail = f"Dispatched {take_id} (stub)."
    if site["sequential"]:
        detail += " Sequential site: drain Wan before the next brain load."
    return TickOut(
        action="dispatch_shot",
        detail=detail,
        project_id=_project.id,
        fleet_mode=_project.fleet_mode,
    )


@app.post("/v1/reset")
def reset() -> dict:
    global _project, _queue
    _project = demo_project()
    _project.compute = site_from_lock()
    _queue = ["sh-030"]
    return {"ok": True}


def main() -> None:
    import uvicorn

    uvicorn.run(
        "diony_conductor.app:app",
        host=bind_host(),
        port=int(os.environ.get("DIONY_CONDUCTOR_PORT", "8789")),
        reload=False,
        log_level="info",
    )


if __name__ == "__main__":
    main()
