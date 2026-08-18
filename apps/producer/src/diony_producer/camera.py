"""Wan2.2 camera from Producer. Live after Wizard pack on the signed site."""

from __future__ import annotations

import os
from pathlib import Path

import httpx

from diony_contracts.live import generate_py, inference_live, wan_base
from diony_contracts.models import Project, Shot
from diony_producer.vault import vault_root

try:
    from diony_wan.runner import WanError, run_generate
except ImportError:  # producer installed without wan package
    WanError = RuntimeError  # type: ignore[misc, assignment]

    def run_generate(**kwargs):  # type: ignore[no-untyped-def]
        raise WanError("diony-wan is not installed")


STILLS = {".png", ".jpg", ".jpeg", ".webp"}


def live_camera() -> bool:
    if not inference_live():
        return False
    return generate_py().is_file() or _wan_http_up()


def resolve_camera_task(engine: str, image: Path | None) -> str:
    """Official generate.py. I2V needs a still. S2V/Animate/VACE are not this FARM."""
    if engine == "i2v-A14B" and image is not None and image.suffix.lower() in STILLS:
        return "i2v-A14B"
    return "t2v-A14B"


def _wan_http_up() -> bool:
    try:
        res = httpx.get(wan_base() + "/health", timeout=2.0)
        return res.status_code == 200
    except httpx.HTTPError:
        return False


def _keyframe(project: Project, shot: Shot) -> Path | None:
    for aid in shot.keyframe_asset_ids:
        try:
            asset = project.asset(aid)
        except KeyError:
            continue
        path = vault_root() / asset.path
        if path.is_file() and path.suffix.lower() in STILLS:
            return path
    return None


def generate_clip(project: Project, shot: Shot, dest: Path, seed: int) -> Path:
    """Write a 720p Wan negative to dest. Raises on failure."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    image = _keyframe(project, shot)
    task = resolve_camera_task(shot.engine.value, image)
    prompt = shot.prompt
    timeout = float(os.environ.get("DIONY_WAN_TIMEOUT", "7200"))
    want_http = bool(os.environ.get("DIONY_WAN_URL", "").strip())
    if (want_http or not generate_py().is_file()) and _wan_http_up():
        payload = {
            "task": task,
            "prompt": prompt,
            "seed": seed,
            "save_file": str(dest),
            "duration_frames": shot.duration_frames,
            "fps": shot.fps,
            "image": str(image) if image else None,
        }
        res = httpx.post(wan_base() + "/v1/generate", json=payload, timeout=timeout)
        if res.status_code >= 400:
            detail = res.json().get("detail") if res.headers.get("content-type", "").startswith("application/json") else res.text
            raise RuntimeError(str(detail) or res.text)
        if dest.is_file() and dest.stat().st_size > 32:
            return dest
        raise RuntimeError(f"diony-wan returned ok but wrote no clip at {dest}")
    run_generate(
        task=task,
        prompt=prompt,
        seed=seed,
        save_file=dest,
        duration_frames=shot.duration_frames,
        fps=shot.fps,
        image=image,
    )
    return dest
