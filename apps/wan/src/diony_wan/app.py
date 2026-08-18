"""Wan2.2 camera worker. Health on :8010. Dual-Spark FARM after brain drain."""

from __future__ import annotations

import os
from pathlib import Path

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from diony_contracts.cluster import bind_host, site_status
from diony_contracts.live import ckpt_dir, generate_py, inference_live, wan_ready
from diony_wan.runner import WanError, run_generate

app = FastAPI(title="Diony Wan", version="0.1.0")


class GenerateIn(BaseModel):
    task: str = "t2v-A14B"
    prompt: str
    seed: int
    save_file: str
    duration_frames: int = 81
    fps: float = 23.976
    image: str | None = None
    audio: str | None = None


@app.get("/health")
def health() -> dict:
    site = site_status()
    t2v = wan_ready("t2v-A14B")
    return {
        "ok": True,
        "model": "t2v-A14B",
        "busy": False,
        "live": inference_live(),
        "ckpt": t2v,
        "generate_py": generate_py().is_file(),
        "ckpt_dir": str(ckpt_dir("t2v-A14B")),
        "site": site["path"],
        "dashscope": False,
        "size": "1280*720",
        "finish": "720p negative — Diony 1080 is FINISH",
    }


@app.post("/v1/generate")
def generate(body: GenerateIn) -> dict:
    if os.environ.get("DASH_API_KEY"):
        raise HTTPException(400, "Unset DASH_API_KEY. Prompt extend is DeepSeek on this site.")
    dest = Path(body.save_file)
    try:
        run_generate(
            task=body.task,
            prompt=body.prompt,
            seed=body.seed,
            save_file=dest,
            duration_frames=body.duration_frames,
            fps=body.fps,
            image=Path(body.image) if body.image else None,
            audio=Path(body.audio) if body.audio else None,
        )
    except WanError as exc:
        raise HTTPException(500, str(exc)) from exc
    return {
        "ok": True,
        "path": str(dest),
        "bytes": dest.stat().st_size,
        "kind": "negative_720",
        "master": False,
    }


def main() -> None:
    import uvicorn

    uvicorn.run(
        "diony_wan.app:app",
        host=bind_host(),
        port=int(os.environ.get("DIONY_WAN_PORT", "8010")),
        reload=False,
        log_level="info",
    )


if __name__ == "__main__":
    main()
