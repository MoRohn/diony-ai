"""Wan2.2 camera worker. Health on :8010. Dual-Spark FARM after brain drain."""

from __future__ import annotations

import os
import threading
from pathlib import Path

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from diony_contracts.cluster import bind_host, site_status
from diony_contracts.live import ckpt_dir, generate_py, inference_live, wan_ready
from diony_wan.comfy import (
    DEFAULT_NEGATIVE_PROMPT,
    ComfyError,
    GenerationConfig,
    build_cinematic_prompt,
    resolve_quality_tier,
)
from diony_wan.jobs import WanJobManager
from diony_wan.runner import WanError, abort_wan, run_generate, wan_backend

app = FastAPI(title="Diony Wan", version="0.1.0")


class GenerateIn(BaseModel):
    task: str = "t2v-A14B"
    prompt: str
    seed: int
    save_file: str
    duration_frames: int = 81
    fps: float = 23.976
    image: str | None = None
    last_image: str | None = None
    audio: str | None = None
    animate_root: str | None = None
    quality_lora_ids: list[str] = Field(default_factory=list)
    negative_prompt: str = DEFAULT_NEGATIVE_PROMPT
    width: int = Field(default=1280, ge=16)
    height: int = Field(default=720, ge=16)
    steps: int = Field(default=24, ge=1, le=100)
    cfg: float = Field(default=3.5, gt=0, le=10)
    sampler: str = "euler"
    scheduler: str = "beta"


class CinematicPromptIn(BaseModel):
    mode: str = "t2v-A14B"
    subject: str = ""
    motion: str
    atmosphere: str
    camera: str = "static shot"


class JobIn(BaseModel):
    """High-level, durable Wan request. Overrides are recorded with the job."""

    quality_tier: str = "production"
    mode: str | None = None
    task: str | None = None
    prompt: str
    seed: int
    save_file: str
    image: str | None = None
    last_image: str | None = None
    audio: str | None = None
    animate_root: str | None = None
    quality_lora_ids: list[str] = Field(default_factory=list)
    negative_prompt: str = DEFAULT_NEGATIVE_PROMPT
    width: int | None = Field(default=None, ge=16)
    height: int | None = Field(default=None, ge=16)
    frames: int | None = Field(default=None, ge=17)
    steps: int | None = Field(default=None, ge=1, le=100)
    cfg: float | None = Field(default=None, gt=0, le=10)
    sampler: str | None = None
    scheduler: str | None = None


class RerunIn(BaseModel):
    save_file: str


_jobs: WanJobManager | None = None
_jobs_lock = threading.Lock()


def _job_root() -> Path:
    configured = os.environ.get("DIONY_WAN_JOBS", "").strip()
    if configured:
        return Path(configured)
    vault = Path(os.environ.get("DIONY_VAULT") or "/diony/vault")
    return vault / ".wan-jobs"


def _execute_job(job: dict) -> Path:
    config = GenerationConfig(**dict(job["config"]))
    destination = Path(str(job["destination"]))
    image = Path(str(job["image"])) if job.get("image") else None
    last_image = Path(str(job["last_image"])) if job.get("last_image") else None
    audio = Path(str(job["audio"])) if job.get("audio") else None
    animate_root = Path(str(job["animate_root"])) if job.get("animate_root") else None
    if wan_backend() == "comfyui":
        if config.mode in {"s2v-14B", "animate-14B"}:
            raise ComfyError(f"{config.mode} requires Diony's official native Wan runtime")
        from diony_wan.comfy import configured_runner

        return configured_runner().run(
            config,
            destination,
            image=image,
            last_image=last_image,
            status_callback=lambda event: get_job_manager().update_progress(
                str(job["id"]), event
            ),
        )
    return run_generate(
        task=config.mode,
        prompt=config.prompt,
        negative_prompt=config.negative_prompt,
        seed=config.seed,
        save_file=destination,
        duration_frames=config.frames,
        fps=24.0,
        image=image,
        last_image=last_image,
        audio=audio,
        animate_root=animate_root,
        quality_lora_ids=config.quality_lora_ids,
        width=config.width,
        height=config.height,
        steps=config.steps,
        cfg=config.cfg,
        sampler=config.sampler,
        scheduler=config.scheduler,
    )


def _resume_job(job: dict) -> Path | None:
    if wan_backend() != "comfyui":
        return None
    from diony_wan.comfy import configured_runner

    return configured_runner().resume(
        Path(str(job["destination"])),
        status_callback=lambda event: get_job_manager().update_progress(
            str(job["id"]), event
        ),
    )


def get_job_manager() -> WanJobManager:
    global _jobs
    with _jobs_lock:
        root = _job_root()
        if _jobs is None or _jobs.root != root:
            _jobs = WanJobManager(
                root=root,
                execute=_execute_job,
                resume=_resume_job,
                # One executor owns one GPU/Comfy endpoint. HTTP requests may be
                # concurrent, but generation is serialized so /free and
                # /interrupt can never affect a different in-flight shot.
                max_workers=1,
            )
            _jobs.recover()
        return _jobs


@app.get("/health")
def health() -> dict:
    site = site_status()
    t2v = wan_ready("t2v-A14B")
    backend = wan_backend()
    queue = _jobs.metrics() if _jobs is not None else {"queued": 0, "running": 0, "cancelling": 0}
    return {
        "ok": True,
        "backend": backend,
        "model": "t2v-A14B",
        "busy": bool(queue.get("queued") or queue.get("running") or queue.get("cancelling")),
        "queue": queue,
        "live": inference_live(),
        "ckpt": t2v,
        "generate_py": generate_py().is_file() if backend == "native" else None,
        "ckpt_dir": str(ckpt_dir("t2v-A14B")),
        "site": site["path"],
        "dashscope": False,
        "size": "1280*720",
        "finish": "720p negative — Diony 1080 is FINISH",
    }


@app.get("/ready")
def ready() -> dict:
    if wan_backend() == "comfyui":
        from diony_wan.comfy import readiness_report

        report = readiness_report()
        if not report.get("ok"):
            raise HTTPException(503, report)
        return report
    payload = health()
    payload["ok"] = bool(payload["generate_py"] and payload["ckpt"])
    if not payload["ok"]:
        raise HTTPException(503, payload)
    return payload


@app.post("/v1/prompts/cinematic")
def cinematic_prompt(body: CinematicPromptIn) -> dict:
    try:
        prompt = build_cinematic_prompt(
            subject=body.subject,
            motion=body.motion,
            atmosphere=body.atmosphere,
            camera=body.camera,
            mode=body.mode,
        )
    except ComfyError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"ok": True, "mode": body.mode, "prompt": prompt, "word_count": len(prompt.split())}


@app.post("/v1/jobs", status_code=202)
def submit_job(body: JobIn) -> dict:
    try:
        if body.mode and body.task and body.mode != body.task:
            raise ComfyError("mode and task disagree; provide one Wan mode")
        config = resolve_quality_tier(
            quality_tier=body.quality_tier,
            prompt=body.prompt,
            seed=body.seed,
            mode=body.mode or body.task,
            negative_prompt=body.negative_prompt,
            width=body.width,
            height=body.height,
            frames=body.frames,
            steps=body.steps,
            cfg=body.cfg,
            sampler=body.sampler,
            scheduler=body.scheduler,
            quality_lora_ids=body.quality_lora_ids,
        )
        if config.mode == "flf2v-A14B" and wan_backend() != "comfyui":
            raise ComfyError("FLF2V requires the ComfyUI backend and its official FLF2V API workflow")
        if config.mode == "s2v-14B":
            if not body.image or not body.audio:
                raise ComfyError("S2V requires a reference image and recorded audio")
        if config.mode == "animate-14B" and not body.animate_root:
            raise ComfyError("Animate requires an official preprocessed source root")
        job = get_job_manager().submit(
            config=config,
            destination=Path(body.save_file),
            quality_tier=body.quality_tier,
            image=Path(body.image) if body.image else None,
            last_image=Path(body.last_image) if body.last_image else None,
            audio=Path(body.audio) if body.audio else None,
            animate_root=Path(body.animate_root) if body.animate_root else None,
        )
    except (ComfyError, OSError, ValueError) as exc:
        raise HTTPException(400, str(exc)) from exc
    return job


@app.post("/v1/jobs/{job_id}/rerun", status_code=202)
def rerun_job(job_id: str, body: RerunIn) -> dict:
    """Create a new job with the exact recorded generation config and seed."""
    try:
        manager = get_job_manager()
        original = manager.status(job_id)
        config = GenerationConfig(**dict(original["config"]))
        return manager.submit(
            config=config,
            destination=Path(body.save_file),
            quality_tier=str(original.get("quality_tier") or "production"),
            image=Path(str(original["image"])) if original.get("image") else None,
            last_image=(
                Path(str(original["last_image"])) if original.get("last_image") else None
            ),
            audio=Path(str(original["audio"])) if original.get("audio") else None,
            animate_root=(
                Path(str(original["animate_root"])) if original.get("animate_root") else None
            ),
        )
    except (ComfyError, KeyError, OSError, TypeError, ValueError) as exc:
        raise HTTPException(400, str(exc)) from exc


@app.get("/v1/jobs/{job_id}")
def job_status(job_id: str) -> dict:
    try:
        return get_job_manager().status(job_id)
    except ComfyError as exc:
        raise HTTPException(404, str(exc)) from exc


@app.post("/v1/jobs/{job_id}/cancel")
def cancel_job(job_id: str) -> dict:
    try:
        return get_job_manager().cancel(job_id, cancel_running=abort_wan)
    except ComfyError as exc:
        raise HTTPException(404, str(exc)) from exc


@app.get("/v1/metrics")
def metrics() -> dict:
    return get_job_manager().metrics()


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
            last_image=Path(body.last_image) if body.last_image else None,
            audio=Path(body.audio) if body.audio else None,
            animate_root=Path(body.animate_root) if body.animate_root else None,
            quality_lora_ids=body.quality_lora_ids,
            negative_prompt=body.negative_prompt,
            width=body.width,
            height=body.height,
            steps=body.steps,
            cfg=body.cfg,
            sampler=body.sampler,
            scheduler=body.scheduler,
        )
    except WanError as exc:
        raise HTTPException(500, str(exc)) from exc
    return {
        "ok": True,
        "path": str(dest),
        "bytes": dest.stat().st_size,
        "kind": "negative_720",
        "master": False,
        "backend": wan_backend(),
        "seed": body.seed,
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
