"""Finish worker. Health on :8020. Dual-Spark FINISH during Production after the Wan farm."""

from __future__ import annotations

import os
from pathlib import Path

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from diony_contracts.cluster import bind_host, site_status
from diony_contracts.live import finish_ckpt, finish_ready, inference_live
from diony_finish.pipeline import FinishError, finish_clip

app = FastAPI(title="Diony Finish", version="0.1.0")


class FinishIn(BaseModel):
    src: str
    dest: str
    fps: float = 23.976
    grain: str = "none"


@app.get("/health")
def health() -> dict:
    site = site_status()
    return {
        "ok": True,
        "live": inference_live(),
        "sr": finish_ready(),
        "ckpt": str(finish_ckpt()),
        "size": "1920x1080",
        "legal": "Rec.709 studio 16-235",
        "lanczos_deliverable": False,
        "site": site["path"],
    }


@app.post("/v1/finish")
def finish(body: FinishIn) -> dict:
    try:
        dest = finish_clip(Path(body.src), Path(body.dest), fps=body.fps, grain=body.grain)
    except FinishError as exc:
        raise HTTPException(500, str(exc)) from exc
    return {
        "ok": True,
        "path": str(dest),
        "bytes": dest.stat().st_size,
        "kind": "finish_1080",
        "width": 1920,
        "height": 1080,
        "master": True,
    }


def main() -> None:
    import uvicorn

    uvicorn.run(
        "diony_finish.app:app",
        host=bind_host(),
        port=int(os.environ.get("DIONY_FINISH_PORT", "8020")),
        reload=False,
        log_level="info",
    )


if __name__ == "__main__":
    main()
