"""Diony Studio: the product's front door.

Studio does one job on the cloud product (Blueprint 25): it lets the user
choose the production engine (Director's choice, Grok Imagine, Google Flow),
shows whether each provider key is loaded, and hands the user on to the
Director. The `:80` product-name router that lays `studio`, `director`, and
`producer` `.diony.local` lives beside it in `gateway.py`.
"""

from __future__ import annotations

import os
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from diony_contracts.cluster import (
    bind_host,
    cluster_root,
    read_engine_choice,
    write_engine_choice,
)

STATIC = Path(__file__).resolve().parent / "static"
DIRECTOR_URL = "http://director.diony.local"
PRODUCER_URL = "http://producer.diony.local"
NO_STORE = {"Cache-Control": "no-store, max-age=0", "Pragma": "no-cache"}

app = FastAPI(title="Diony Studio", version="0.20.0")
app.mount("/static", StaticFiles(directory=STATIC), name="static")


@app.middleware("http")
async def no_store_pages(request, call_next):
    response = await call_next(request)
    path = request.url.path
    if path == "/" or path.startswith("/static/") or path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-store, max-age=0"
        response.headers["Pragma"] = "no-cache"
    return response


def _keys_loaded() -> None:
    """Load ~/.diony/keys.env into the environment when the Director is installed."""
    try:
        from diony_director.config import load_keys_env

        load_keys_env()
    except ImportError:
        pass


class EngineChoiceBody(BaseModel):
    engine: str


@app.get("/")
def index() -> FileResponse:
    """The opening: engine selection is the front door."""
    return FileResponse(STATIC / "index.html", headers=NO_STORE)


@app.get("/api/opening")
def opening_state() -> dict:
    _keys_loaded()
    return {
        "engine": read_engine_choice(),
        "providers": {
            "grok": bool(os.environ.get("XAI_API_KEY", "").strip()),
            "google-flow": bool(os.environ.get("USEAPI_TOKEN", "").strip()),
        },
        "flow_plan": os.environ.get("DIONY_FLOW_PLAN", "pro").strip() or "pro",
        "director_url": DIRECTOR_URL,
        "producer_url": PRODUCER_URL,
    }


@app.post("/api/opening/engine")
def choose_engine(body: EngineChoiceBody) -> dict:
    try:
        write_engine_choice(body.engine)
    except ValueError as err:
        raise HTTPException(status_code=422, detail=str(err)) from err
    return {"engine": body.engine, "next": DIRECTOR_URL}


@app.get("/api/health")
def health() -> dict:
    """Liveness for the desk and the router: the vault path and the choice on disk."""
    return {"ok": True, "vault": str(cluster_root()), "engine": read_engine_choice()}


def main() -> None:
    import sys

    import uvicorn

    from diony_wizard.gateway import ensure_product_urls, gateway_healthy, start_product_gateway

    start_product_gateway()
    skip = os.environ.get("DIONY_NAMES_SKIP", "").strip().lower() in {"1", "true", "yes"}
    if not skip and sys.stdin.isatty() and not gateway_healthy():
        ensure_product_urls()
    reload_flag = os.environ.get("DIONY_WIZARD_RELOAD", "").strip().lower()
    reload = reload_flag in {"1", "true", "yes"}
    extra: dict = {"reload": reload, "log_level": "info"}
    if reload:
        extra["reload_dirs"] = [str(Path(__file__).resolve().parent)]
    uvicorn.run(
        "diony_wizard.app:app",
        host=bind_host(),
        port=int(os.environ.get("DIONY_WIZARD_PORT", "8787")),
        **extra,
    )


if __name__ == "__main__":
    main()
