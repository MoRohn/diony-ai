"""Long-lived native Wan2.2 worker for validated B200/B300 quality runs.

Stdout is reserved for one JSON response per stdin JSON request. Upstream logs
and tqdm output are redirected to the per-Take `.wan.log` supplied in the job.
T2V-A14B, I2V-A14B, and receipt-qualified S2V-A14B use this worker; Animate and
TI2V retain the pinned official CLI path.
"""

from __future__ import annotations

import contextlib
import gc
import json
import logging
import os
import sys
import time
import traceback
from pathlib import Path
from typing import Any, Callable

# Executed by the CUDA-capable system Python as a script. Add the package's
# `src` parent explicitly so the worker uses the same checked-in Diony code as
# Producer even when that interpreter is not the Producer virtualenv.
_SRC = str(Path(__file__).resolve().parents[1])
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)


class PipelineCache:
    """Retain exactly one compatible pipeline; task/checkpoint changes drain it."""

    def __init__(self, *, factory: Callable[[str, str, str], Any]) -> None:
        self.factory = factory
        self.key: tuple[str, str, str] | None = None
        self.pipeline: Any = None

    def clear(self) -> None:
        self.pipeline = None
        self.key = None
        gc.collect()
        try:
            import torch

            if torch.cuda.is_available():
                torch.cuda.empty_cache()
        except ImportError:
            pass

    def get(self, task: str, checkpoint: str, kernel_mode: str = "eager") -> tuple[Any, bool]:
        key = (task, checkpoint, kernel_mode)
        if self.pipeline is not None and self.key == key:
            return self.pipeline, True
        self.clear()
        self.pipeline = self.factory(task, checkpoint, kernel_mode)
        self.key = key
        return self.pipeline, False


def _apply_kernel_mode(pipeline: Any, kernel_mode: str) -> None:
    """Compile one repeated Wan block region; keep eager as the exact rollback."""
    if kernel_mode == "eager":
        return
    if kernel_mode != "regional_compile":
        raise RuntimeError(f"Resident Wan does not support kernel mode {kernel_mode!r}.")
    models = [
        model
        for name in ("low_noise_model", "high_noise_model", "noise_model")
        if (model := getattr(pipeline, name, None)) is not None
    ]
    if not models or any(not getattr(model, "blocks", None) for model in models):
        raise RuntimeError("Regional compile requires the active Wan A14B transformer blocks.")
    for model in models:
        for block in model.blocks:
            block.compile(fullgraph=True, dynamic=False)


def _pipeline_factory(task: str, checkpoint: str, kernel_mode: str):
    import torch
    import wan
    from wan.configs import WAN_CONFIGS

    if not torch.cuda.is_available():
        raise RuntimeError("Resident Wan requires a CUDA device.")
    if task not in {"t2v-A14B", "i2v-A14B", "s2v-14B"}:
        raise RuntimeError(f"Resident Wan does not support {task}.")
    cls = {
        "t2v-A14B": wan.WanT2V,
        "i2v-A14B": wan.WanI2V,
        "s2v-14B": wan.WanS2V,
    }[task]
    pipeline = cls(
        config=WAN_CONFIGS[task],
        checkpoint_dir=checkpoint,
        device_id=0,
        rank=0,
        t5_fsdp=False,
        dit_fsdp=False,
        use_sp=False,
        t5_cpu=False,
        init_on_cpu=False,
        convert_model_dtype=False,
    )
    _apply_kernel_mode(pipeline, kernel_mode)
    return pipeline


def _s2v_generation_kwargs(job: dict[str, Any], cfg: Any, *, max_area: int) -> dict[str, Any]:
    negative = str(job.get("negative_prompt") or "").strip()
    default_negative = str(getattr(cfg, "sample_neg_prompt", "") or "").strip()
    n_prompt = f"{default_negative}, {negative}" if default_negative and negative else negative
    return {
        "input_prompt": str(job["prompt"]),
        "ref_image_path": str(job.get("image") or ""),
        "audio_path": str(job.get("audio") or ""),
        "enable_tts": False,
        "tts_prompt_audio": None,
        "tts_prompt_text": None,
        "tts_text": None,
        "num_repeat": None,
        "pose_video": None,
        "max_area": max_area,
        "infer_frames": int(job.get("inference_frames_per_pass") or 0),
        "shift": cfg.sample_shift,
        "sample_solver": "unipc",
        "sampling_steps": 24,
        "guide_scale": cfg.sample_guide_scale,
        "n_prompt": n_prompt,
        "seed": int(job["seed"]),
        "offload_model": False,
        "init_first_frame": True,
    }


def _generate(cache: PipelineCache, job: dict[str, Any]) -> dict[str, Any]:
    import torch
    from PIL import Image
    from wan.configs import MAX_AREA_CONFIGS, SIZE_CONFIGS, WAN_CONFIGS
    from wan.utils.utils import merge_video_audio, save_video

    from diony_wan.resident import QUALITY_CONTRACT_SHA256

    if job.get("quality_contract_sha256") != QUALITY_CONTRACT_SHA256:
        raise RuntimeError("Resident job does not match the validated quality contract.")
    task = str(job.get("task") or "")
    checkpoint = str(job.get("checkpoint") or "")
    if task not in {"t2v-A14B", "i2v-A14B", "s2v-14B"}:
        raise RuntimeError(f"Resident Wan does not support {task}.")
    if job.get("size") != "1280*720" or int(job.get("frame_num") or 0) != 81:
        raise RuntimeError("Resident Wan accepts only the validated 1280x720, 81-frame lane.")
    if int(job.get("sample_steps") or 0) != 24 or job.get("sample_solver") != "unipc":
        raise RuntimeError("Resident Wan accepts only the validated 24-step UniPC lane.")
    if job.get("offload_model") is not False or job.get("init_on_cpu") is not False:
        raise RuntimeError("Resident Wan requires both BF16 experts to remain on the GPU.")

    started = time.monotonic()
    kernel_mode = str(job.get("kernel_mode") or "eager")
    if kernel_mode not in {"eager", "regional_compile"}:
        raise RuntimeError(f"Resident Wan does not support kernel mode {kernel_mode!r}.")
    pipeline, reused = cache.get(task, checkpoint, kernel_mode)
    loaded = time.monotonic()
    cfg = WAN_CONFIGS[task]
    image = None
    image_path = str(job.get("image") or "")
    if image_path and task != "s2v-14B":
        image = Image.open(image_path).convert("RGB")
    if task == "i2v-A14B" and image is None:
        raise RuntimeError("Resident I2V requires the locked input image.")
    audio_path = str(job.get("audio") or "")
    if task == "s2v-14B" and (
        not image_path
        or not audio_path
        or int(job.get("inference_frames_per_pass") or 0) != 84
    ):
        raise RuntimeError("Resident S2V requires locked image/audio and one 84-frame window.")
    negative = str(job.get("negative_prompt") or "").strip()
    n_prompt = f"{cfg.sample_neg_prompt}, {negative}" if negative else ""
    torch.cuda.reset_peak_memory_stats()
    if task == "t2v-A14B":
        video = pipeline.generate(
            str(job["prompt"]),
            size=SIZE_CONFIGS["1280*720"],
            frame_num=81,
            shift=cfg.sample_shift,
            sample_solver="unipc",
            sampling_steps=24,
            guide_scale=cfg.sample_guide_scale,
            n_prompt=n_prompt,
            seed=int(job["seed"]),
            offload_model=False,
        )
    elif task == "i2v-A14B":
        video = pipeline.generate(
            str(job["prompt"]),
            image,
            max_area=MAX_AREA_CONFIGS["1280*720"],
            frame_num=81,
            shift=cfg.sample_shift,
            sample_solver="unipc",
            sampling_steps=24,
            guide_scale=cfg.sample_guide_scale,
            n_prompt=n_prompt,
            seed=int(job["seed"]),
            offload_model=False,
        )
    else:
        video = pipeline.generate(
            **_s2v_generation_kwargs(
                job,
                cfg,
                max_area=MAX_AREA_CONFIGS["1280*720"],
            )
        )
    destination = Path(str(job["save_file"]))
    destination.parent.mkdir(parents=True, exist_ok=True)
    save_video(
        tensor=video[None],
        save_file=str(destination),
        fps=cfg.sample_fps,
        nrow=1,
        normalize=True,
        value_range=(-1, 1),
    )
    if task == "s2v-14B":
        merge_video_audio(
            video_path=str(destination),
            audio_path=audio_path,
            video_frames=81,
            fps=cfg.sample_fps,
        )
    del video
    torch.cuda.synchronize()
    finished = time.monotonic()
    return {
        "ok": True,
        "path": str(destination),
        "pipeline_reused": reused,
        "pipeline_load_seconds": round(loaded - started, 6),
        "generation_seconds": round(finished - loaded, 6),
        "total_seconds": round(finished - started, 6),
        "peak_vram_mib": round(torch.cuda.max_memory_allocated() / (1024 * 1024), 2),
        "quality_contract_sha256": QUALITY_CONTRACT_SHA256,
        "kernel_mode": kernel_mode,
    }


def main() -> None:
    cache = PipelineCache(factory=_pipeline_factory)
    for raw in sys.stdin:
        try:
            job = json.loads(raw)
            if not isinstance(job, dict) or job.get("protocol") != 1:
                raise RuntimeError("Unsupported resident Wan request protocol.")
            log_path = Path(str(job.get("log_path") or "resident.wan.log"))
            log_path.parent.mkdir(parents=True, exist_ok=True)
            with log_path.open("a", encoding="utf-8", buffering=1) as log:
                logging.basicConfig(
                    level=logging.INFO,
                    format="[%(asctime)s] %(levelname)s: %(message)s",
                    handlers=[logging.StreamHandler(log)],
                    force=True,
                )
                with contextlib.redirect_stdout(log), contextlib.redirect_stderr(log):
                    response = _generate(cache, job)
                log.write("DIONY_RESIDENT_METRICS " + json.dumps(response, sort_keys=True) + "\n")
        except Exception as exc:  # noqa: BLE001 - preserve the worker for a safe next request
            response = {
                "ok": False,
                "error": str(exc).strip()[:2000] or type(exc).__name__,
                "fatal": "CUDA out of memory" in str(exc) or "out of memory" in str(exc).lower(),
            }
            try:
                log.write(traceback.format_exc() + "\n")
            except Exception:
                pass
            if response["fatal"]:
                cache.clear()
        sys.stdout.write(json.dumps(response, separators=(",", ":")) + "\n")
        sys.stdout.flush()


if __name__ == "__main__":
    main()
