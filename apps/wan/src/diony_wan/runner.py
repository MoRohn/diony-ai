"""Official Wan2.2 generate.py. Never Dashscope. Native 720p negative only."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

from diony_contracts.live import ckpt_dir, generate_py, wan_frame_num, wan_repo

SIZE_720 = "1280*720"
TASKS = {
    "t2v-A14B": "t2v-A14B",
    "i2v-A14B": "i2v-A14B",
    "ti2v-5B": "ti2v-5B",
    "s2v-14B": "s2v-14B",
    "animate-14B": "animate-14B",
}


class WanError(RuntimeError):
    pass


def spark_argv(
    *,
    task: str,
    prompt: str,
    seed: int,
    save_file: Path,
    frame_num: int,
    image: Path | None = None,
    audio: Path | None = None,
) -> list[str]:
    if task not in TASKS:
        task = "t2v-A14B"
    if "dashscope" in prompt.lower() or "qwen-plus" in prompt.lower():
        raise WanError("Prompt must not request hosted extend.")
    ckpt = ckpt_dir(task)
    py = generate_py()
    if not py.is_file():
        raise WanError(
            f"Wan2.2 generate.py not at {py}. Clone https://github.com/Wan-Video/Wan2.2 to /opt/wan22."
        )
    if not ckpt.is_dir():
        raise WanError(f"Wan checkpoint missing: {ckpt}. Pull the Dual-Spark pack first.")
    cmd = [
        os.environ.get("DIONY_PYTHON", "python3"),
        str(py),
        "--task",
        task,
        "--size",
        SIZE_720,
        "--ckpt_dir",
        str(ckpt),
        "--offload_model",
        "True",
        "--convert_model_dtype",
        "--t5_cpu",
        "--prompt",
        prompt,
        "--base_seed",
        str(int(seed)),
        "--frame_num",
        str(int(frame_num)),
        "--save_file",
        str(save_file),
    ]
    if image is not None and image.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp"}:
        cmd.extend(["--image", str(image)])
    if audio is not None and task == "s2v-14B" and audio.suffix.lower() in {".wav", ".mp3", ".flac"}:
        cmd.extend(["--audio", str(audio)])
    return cmd


def run_generate(
    *,
    task: str,
    prompt: str,
    seed: int,
    save_file: Path,
    duration_frames: int,
    fps: float = 23.976,
    image: Path | None = None,
    audio: Path | None = None,
    timeout: int | None = None,
) -> Path:
    """Run official generate.py. Unsets Dashscope. Writes 720p mp4."""
    frames = wan_frame_num(duration_frames, fps)
    save_file.parent.mkdir(parents=True, exist_ok=True)
    cmd = spark_argv(
        task=task,
        prompt=prompt,
        seed=seed,
        save_file=save_file,
        frame_num=frames,
        image=image,
        audio=audio,
    )
    env = os.environ.copy()
    env["DASH_API_KEY"] = ""
    env.pop("DASHSCOPE_API_KEY", None)
    repo = wan_repo()
    py_root = str(repo if repo.is_dir() else generate_py().parent)
    env["PYTHONPATH"] = py_root + os.pathsep + env.get("PYTHONPATH", "")
    limit = timeout or int(os.environ.get("DIONY_WAN_TIMEOUT", "7200"))
    proc = subprocess.run(
        cmd,
        cwd=py_root,
        env=env,
        capture_output=True,
        text=True,
        timeout=limit,
        check=False,
    )
    if proc.returncode != 0:
        err = (proc.stderr or proc.stdout or "generate.py failed")[-4000:]
        raise WanError(err)
    if not save_file.is_file() or save_file.stat().st_size < 32:
        raise WanError(f"generate.py exited 0 but wrote no clip at {save_file}")
    return save_file
