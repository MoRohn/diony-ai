"""Wan2.2 camera from Producer. Live after Studio pack on the signed site."""

from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path

import httpx

from diony_contracts.enums import ArtifactKind, AssetKind, ContinuityMode
from diony_contracts.live import generate_py, inference_live, wan_base, wan_frame_num
from diony_contracts.models import Project, Shot, Take
from diony_contracts.wan_prompt import validate_wan_prompt_dispatch
from diony_producer.vault import vault_root
from diony_producer.gallery_join import resolve_media_tool

try:
    from diony_wan.runner import WanError, run_generate
except ImportError:  # producer installed without wan package
    WanError = RuntimeError  # type: ignore[misc, assignment]

    def run_generate(**kwargs):  # type: ignore[no-untyped-def]
        raise WanError("diony-wan is not installed")


STILLS = {".png", ".jpg", ".jpeg", ".webp"}


def qualified_quality_loras(shot: Shot, task: str) -> list[str]:
    """Select only adapters supported by the active official runtime contract."""
    available = {
        value.strip()
        for value in os.environ.get("DIONY_WAN_QUALIFIED_LORAS", "").split(",")
        if value.strip()
    }
    selected = (
        ["wan2.2-animate-relighting"]
        if task == "animate-14B" and "wan2.2-animate-relighting" in available
        else []
    )
    if shot.prompt_plan is not None:
        shot.prompt_plan.quality_lora_ids = selected
    return selected


def live_camera() -> bool:
    """True when this process farms Wan, or when desk Producer is proxied to the signed GPU."""
    if inference_live() and (generate_py().is_file() or _wan_http_up() or _comfy_http_up()):
        return True
    try:
        from diony_producer.gpu import gpu_producer_up, should_proxy
    except ImportError:
        return False
    return should_proxy() and gpu_producer_up()


def resolve_camera_task(
    engine: str,
    image: Path | None,
    *,
    audio: Path | None = None,
    animate_root: Path | None = None,
) -> str:
    """Resolve the requested engine without silently changing creative semantics."""
    if engine == "i2v-A14B" and image is not None and image.suffix.lower() in STILLS:
        return "i2v-A14B"
    if engine == "i2v-A14B":
        raise RuntimeError("I2V reference lock requires a readable still image")
    if engine == "t2v-A14B":
        return "t2v-A14B"
    if engine == "s2v-14B":
        if image is None or image.suffix.lower() not in STILLS:
            raise RuntimeError("S2V requires a readable reference still image")
        if audio is None or audio.suffix.lower() not in {".wav", ".mp3", ".flac"}:
            raise RuntimeError("S2V requires recorded audio in WAV, MP3, or FLAC format")
        return "s2v-14B"
    if engine == "animate-14B":
        if image is None or image.suffix.lower() not in STILLS:
            raise RuntimeError("Animate requires a readable character still image")
        if animate_root is None or not animate_root.is_dir():
            raise RuntimeError("Animate requires a preprocessed motion reference directory")
        return "animate-14B"
    raise RuntimeError(f"{engine} requires its purpose-built Wan runtime; Diony will not replace it with T2V")


def camera_task_for(project: Project, shot: Shot) -> str:
    """Task generate.py will actually run for this shot.

    Previous-frame I2V shots deliberately have no canonical keyframe on the
    Shot: their conditioning image is the prior selected Take's last frame.
    Preflight must resolve that same image instead of rejecting the shot before
    ``generate_clip`` can stage it.
    """
    image = _keyframe(project, shot)
    if shot.continuity_mode == ContinuityMode.PREVIOUS_FRAME:
        image, _previous_take_id = continuity_frame(project, shot)
        if image is None:
            raise RuntimeError(
                "Previous-frame continuity is required, but the prior selected print is unavailable"
            )
    return resolve_camera_task(
        shot.engine.value,
        image,
        audio=_audio_reference(project, shot),
        animate_root=_animate_reference_root(project, shot),
    )


def staged_camera_task(project: Project, shot: Shot) -> str:
    """Family to stage into the hot tier before this shot's voice is locked.

    ``camera_task_for`` answers for the shot as it is now. A spoken shot is
    conditioned to S2V by the voice pipeline moments before camera, so staging
    its current engine would copy the wrong 100+ GB family and then load S2V
    cold. Predict the conditioned family instead; preflight still resolves the
    real inputs when the take rolls.
    """
    if shot.audio_line or shot.dialogue_turns:
        return "s2v-14B"
    return camera_task_for(project, shot)


def _wan_http_up() -> bool:
    try:
        res = httpx.get(wan_base() + "/health", timeout=2.0)
        return res.status_code == 200
    except httpx.HTTPError:
        return False


def _comfy_http_up() -> bool:
    if os.environ.get("DIONY_WAN_BACKEND", "native").strip().lower() != "comfyui":
        return False
    base = os.environ.get("DIONY_COMFY_URL", "http://127.0.0.1:8188").rstrip("/")
    try:
        res = httpx.get(base + "/system_stats", timeout=2.0)
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


def _audio_reference(project: Project, shot: Shot) -> Path | None:
    for asset_id in shot.audio_asset_ids:
        try:
            asset = project.asset(asset_id)
        except KeyError:
            continue
        path = vault_root() / asset.path
        if asset.kind == AssetKind.AUDIO and path.is_file() and path.suffix.lower() in {
            ".wav",
            ".mp3",
            ".flac",
        }:
            return path
    return None


def _animate_reference_root(project: Project, shot: Shot) -> Path | None:
    """Resolve operator-preprocessed official Wan-Animate material only."""
    for asset_id in shot.motion_reference_asset_ids:
        try:
            asset = project.asset(asset_id)
        except KeyError:
            continue
        configured = str(asset.metadata.get("wan_animate_src_root") or "").strip()
        if not configured:
            continue
        path = Path(configured)
        if not path.is_absolute():
            path = vault_root() / configured
        if path.is_dir():
            return path
    return None


def reference_frame(project: Project, shot: Shot) -> Path | None:
    """Stage a subject-defining still on the native 1280×720 Wan canvas."""
    source = _keyframe(project, shot)
    if source is None:
        return None
    asset = None
    for aid in shot.keyframe_asset_ids:
        try:
            candidate = project.asset(aid)
        except KeyError:
            continue
        if vault_root() / candidate.path == source:
            asset = candidate
            break
    fingerprint = (asset.sha256 if asset is not None else "") or str(source.stat().st_mtime_ns)
    asset_id = asset.id if asset is not None else source.stem
    destination = (
        vault_root()
        / "projects"
        / project.id
        / "references"
        / f"{asset_id}-{fingerprint[:12]}-1280x720.png"
    )
    if destination.is_file() and destination.stat().st_size > 32:
        return destination
    ffmpeg = resolve_media_tool("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("Image-conditioned generation requires the bundled FFmpeg tool")
    destination.parent.mkdir(parents=True, exist_ok=True)
    pending = destination.with_name(f".{destination.stem}.part.png")
    pending.unlink(missing_ok=True)
    command = [
        ffmpeg,
        "-nostdin",
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-i",
        str(source),
        "-vf",
        "scale=1280:720:force_original_aspect_ratio=decrease,pad=1280:720:(ow-iw)/2:(oh-ih)/2:color=0xF4F0E6",
        "-frames:v",
        "1",
        str(pending),
    ]
    try:
        subprocess.run(command, capture_output=True, check=True, timeout=90)
        if not pending.is_file() or pending.stat().st_size <= 32:
            raise RuntimeError("FFmpeg produced no staged reference")
        pending.replace(destination)
    except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
        pending.unlink(missing_ok=True)
        raise RuntimeError("The subject image could not be staged at 1280×720") from exc
    return destination


def _negative_for_take(take: Take) -> tuple[Path, str] | None:
    for artifact in take.artifacts:
        if artifact.kind != ArtifactKind.NEGATIVE_720:
            continue
        path = vault_root() / artifact.path
        if path.is_file() and path.stat().st_size > 32:
            return path, artifact.sha256
    return None


def _previous_take(project: Project, shot: Shot) -> Take | None:
    try:
        index = next(i for i, item in enumerate(project.shots) if item.id == shot.id)
    except StopIteration:
        return None
    if index < 1:
        return None
    previous_shot = project.shots[index - 1]
    if not set(previous_shot.character_ref_ids) & set(shot.character_ref_ids):
        return None
    if previous_shot.selected_take_id:
        try:
            selected = project.take(previous_shot.selected_take_id)
        except KeyError:
            selected = None
        if selected is not None and _negative_for_take(selected):
            return selected
    for take in reversed(project.takes_for(previous_shot.id)):
        if _negative_for_take(take):
            return take
    return None


def continuity_frame(project: Project, shot: Shot) -> tuple[Path | None, str | None]:
    """Extract the selected prior print's last usable frame for frame-to-clip generation."""
    if shot.continuity_mode != ContinuityMode.PREVIOUS_FRAME:
        return None, None
    previous = _previous_take(project, shot)
    if previous is None:
        return None, None
    source_row = _negative_for_take(previous)
    if source_row is None:
        return None, None
    source, source_sha256 = source_row
    fingerprint = source_sha256 or str(source.stat().st_mtime_ns)
    destination = (
        vault_root()
        / "projects"
        / project.id
        / "references"
        / f"{previous.id}-{fingerprint[:12]}-last-1280x720.png"
    )
    if destination.is_file() and destination.stat().st_size > 32:
        return destination, previous.id
    ffmpeg = resolve_media_tool("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("Previous-frame continuity requires the bundled FFmpeg tool")
    destination.parent.mkdir(parents=True, exist_ok=True)
    pending = destination.with_name(f".{destination.stem}.part.png")
    pending.unlink(missing_ok=True)
    command = [
        ffmpeg,
        "-nostdin",
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-sseof",
        "-0.1",
        "-i",
        str(source),
        "-vf",
        "scale=1280:720:force_original_aspect_ratio=decrease,pad=1280:720:(ow-iw)/2:(oh-ih)/2:color=0xF4F0E6",
        "-frames:v",
        "1",
        str(pending),
    ]
    try:
        subprocess.run(command, capture_output=True, check=True, timeout=90)
        if not pending.is_file() or pending.stat().st_size <= 32:
            raise RuntimeError("FFmpeg produced no continuity frame")
        pending.replace(destination)
    except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
        pending.unlink(missing_ok=True)
        raise RuntimeError(f"Could not stage the boundary frame from {previous.id}") from exc
    return destination, previous.id


def _response_detail(response: httpx.Response) -> str:
    try:
        payload = response.json()
    except ValueError:
        return response.text
    if isinstance(payload, dict):
        detail = payload.get("detail") or payload.get("error")
        if detail:
            return str(detail)
    return response.text


def _generate_http_job(payload: dict, destination: Path, *, timeout: float) -> Path:
    """Submit to Diony's durable Wan queue and preserve synchronous FARM hand-off."""
    frame_num = wan_frame_num(
        int(payload.get("duration_frames") or 81), float(payload.get("fps") or 23.976)
    )
    job_payload = {
        key: value
        for key, value in payload.items()
        if key not in {"duration_frames", "fps"}
    }
    job_payload.update(
        {
            "quality_tier": os.environ.get("DIONY_WAN_QUALITY_TIER", "production"),
            "frames": frame_num,
        }
    )
    response = httpx.post(
        wan_base() + "/v1/jobs", json=job_payload, timeout=min(timeout, 60.0)
    )
    if response.status_code >= 400:
        raise RuntimeError(_response_detail(response) or "diony-wan rejected the job")
    try:
        job_id = str(response.json()["id"])
    except (KeyError, TypeError, ValueError) as exc:
        raise RuntimeError("diony-wan job submission returned no durable job id") from exc
    deadline = time.monotonic() + timeout
    poll_interval = max(0.0, float(os.environ.get("DIONY_WAN_JOB_POLL", "1.0")))
    while time.monotonic() < deadline:
        response = httpx.get(
            wan_base() + f"/v1/jobs/{job_id}",
            timeout=min(30.0, max(1.0, deadline - time.monotonic())),
        )
        if response.status_code >= 400:
            raise RuntimeError(_response_detail(response) or f"Wan job {job_id} status failed")
        try:
            job = response.json()
        except ValueError as exc:
            raise RuntimeError(f"Wan job {job_id} returned unreadable status") from exc
        state = str(job.get("state") or "")
        if state == "completed":
            if destination.is_file() and destination.stat().st_size > 32:
                return destination
            output = job.get("output") if isinstance(job.get("output"), dict) else {}
            raise RuntimeError(
                f"Wan job {job_id} completed but wrote no clip at {destination}; output={output}"
            )
        if state in {"failed", "cancelled"}:
            progress = job.get("progress") if isinstance(job.get("progress"), dict) else {}
            error = job.get("error") or progress.get("error") or "no backend detail"
            raise RuntimeError(f"Wan job {job_id} {state}: {error}")
        time.sleep(poll_interval)
    raise RuntimeError(f"Wan job {job_id} timed out after {int(timeout)} seconds")


def generate_clip(
    project: Project,
    shot: Shot,
    dest: Path,
    seed: int,
    *,
    attention_backend: str | None = None,
) -> Path:
    """Write a 720p Wan negative to dest. Raises on failure."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    canonical = reference_frame(project, shot)
    boundary, _previous_take_id = continuity_frame(project, shot)
    if shot.continuity_mode == ContinuityMode.PREVIOUS_FRAME and boundary is None:
        raise RuntimeError("Previous-frame continuity is required, but the prior selected print is unavailable")
    image = boundary or canonical
    audio = _audio_reference(project, shot)
    animate_root = _animate_reference_root(project, shot)
    task = resolve_camera_task(
        shot.engine.value,
        image,
        audio=audio,
        animate_root=animate_root,
    )
    quality_lora_ids = qualified_quality_loras(shot, task)
    prompt_diagnostics = validate_wan_prompt_dispatch(shot)
    if prompt_diagnostics:
        raise RuntimeError("Wan prompt contract invalid: " + "; ".join(prompt_diagnostics))
    prompt = shot.prompt
    timeout = float(os.environ.get("DIONY_WAN_TIMEOUT", "7200"))
    want_http = bool(os.environ.get("DIONY_WAN_URL", "").strip())
    if (want_http or not generate_py().is_file()) and _wan_http_up():
        payload = {
            "task": task,
            "prompt": prompt,
            "negative_prompt": shot.negative_prompt,
            "seed": seed,
            "save_file": str(dest),
            "duration_frames": shot.duration_frames,
            "fps": shot.fps,
            "image": str(image) if image else None,
            "audio": str(audio) if audio else None,
            "animate_root": str(animate_root) if animate_root else None,
            "quality_lora_ids": quality_lora_ids,
        }
        return _generate_http_job(payload, dest, timeout=timeout)
    run_generate(
        task=task,
        prompt=prompt,
        negative_prompt=shot.negative_prompt,
        seed=seed,
        save_file=dest,
        duration_frames=shot.duration_frames,
        fps=shot.fps,
        image=image,
        audio=audio,
        animate_root=animate_root,
        quality_lora_ids=quality_lora_ids,
        attention_backend=attention_backend,
    )
    return dest
