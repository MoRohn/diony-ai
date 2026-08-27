"""When the signed site has Wan weights, inference is live. Laptop pytest is not."""

from __future__ import annotations

import math
import os
from pathlib import Path

WAN_CKPT = {
    "t2v-A14B": "/models/wan/Wan2.2-T2V-A14B",
    "i2v-A14B": "/models/wan/Wan2.2-I2V-A14B",
    "ti2v-5B": "/models/wan/Wan2.2-TI2V-5B",
    "s2v-14B": "/models/wan/Wan2.2-S2V-14B",
    "animate-14B": "/models/wan/Wan2.2-Animate-14B",
    "vace-fun-A14B": "/models/wan/Wan2.2-VACE-Fun-A14B",
}

BRAIN_CKPT = "/models/deepseek/DeepSeek-V4-Flash-0731"
FINISH_CKPT = "/models/diony/diony_sr_v1"
WAN_REPO_DEFAULT = "/opt/wan22"
BRAIN_URL_DEFAULT = "http://127.0.0.1:8000"
WAN_URL_DEFAULT = "http://127.0.0.1:8010"
FINISH_URL_DEFAULT = "http://127.0.0.1:8020"


def _truthy(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in {"1", "true", "yes", "on"}


def _falsy_live() -> bool:
    return _truthy("DIONY_DRY_RUN")


def ckpt_dir(engine: str) -> Path:
    override = os.environ.get("WAN_CKPT_" + engine.replace("-", "_").upper(), "").strip()
    if override:
        return Path(override)
    mapped = {
        "t2v-A14B": os.environ.get("WAN_CKPT_T2V", WAN_CKPT["t2v-A14B"]),
        "i2v-A14B": os.environ.get("WAN_CKPT_I2V", WAN_CKPT["i2v-A14B"]),
        "ti2v-5B": os.environ.get("WAN_CKPT_TI2V", WAN_CKPT["ti2v-5B"]),
        "s2v-14B": os.environ.get("WAN_CKPT_S2V", WAN_CKPT["s2v-14B"]),
        "animate-14B": os.environ.get("WAN_CKPT_ANIMATE", WAN_CKPT["animate-14B"]),
        "vace-fun-A14B": os.environ.get("WAN_CKPT_VACE", WAN_CKPT["vace-fun-A14B"]),
    }
    return Path(mapped.get(engine, WAN_CKPT["t2v-A14B"]))


def _dir_has_weights(path: Path) -> bool:
    if not path.is_dir():
        return False
    try:
        return any(path.iterdir())
    except OSError:
        return False


def wan_repo() -> Path:
    return Path(os.environ.get("DIONY_WAN_REPO", WAN_REPO_DEFAULT))


def generate_py() -> Path:
    override = os.environ.get("DIONY_WAN_GENERATE", "").strip()
    if override:
        return Path(override)
    return wan_repo() / "generate.py"


def brain_base() -> str:
    return os.environ.get("DIONY_BRAIN_URL", BRAIN_URL_DEFAULT).rstrip("/")


def wan_base() -> str:
    return os.environ.get("DIONY_WAN_URL", WAN_URL_DEFAULT).rstrip("/")


def finish_base() -> str:
    return os.environ.get("DIONY_FINISH_URL", FINISH_URL_DEFAULT).rstrip("/")


def finish_ckpt() -> Path:
    override = os.environ.get("DIONY_SR_CKPT", "").strip()
    return Path(override) if override else Path(FINISH_CKPT)


def wan_ready(engine: str = "t2v-A14B") -> bool:
    return _dir_has_weights(ckpt_dir(engine))


def generate_ready() -> bool:
    return generate_py().is_file()


def brain_ckpt_ready() -> bool:
    dest = Path(os.environ.get("DIONY_BRAIN_CKPT", BRAIN_CKPT))
    return _dir_has_weights(dest)


def inference_live() -> bool:
    """True on a prepped cluster (weights on NVMe) unless DIONY_DRY_RUN=1."""
    if _falsy_live():
        return False
    if _truthy("DIONY_LIVE"):
        return True
    return wan_ready("t2v-A14B")


def wizard_live() -> bool:
    """Dual-Spark / B300 / B200 App SSHes, pulls the pack, and starts Producer. Pytest stays dry."""
    if _falsy_live():
        return False
    if _truthy("DIONY_WIZARD_LIVE") or _truthy("DIONY_LIVE"):
        return True
    return Path("/models").is_dir()


def finish_ready() -> bool:
    """diony_sr_v1 checkpoint on NVMe, or an explicit test fake."""
    if _falsy_live():
        return False
    if _truthy("DIONY_FINISH_FAKE"):
        return True
    return _dir_has_weights(finish_ckpt())


S2V_NATIVE_FPS = 16


# Wan2.2 S2V (with --start_from_ref) sometimes lands its mouth motion late:
# measured takes of the same shot came in at +7 frames and at 0. The lag is
# per take, so Producer renders headroom and aligns after measuring rather
# than guessing a lead: the measured late frames are trimmed from the head
# and the locked audio is muxed back, inside the same lane window.
S2V_HEADROOM_FRAMES = 8
S2V_MAX_LEAD_FRAMES = 16


def s2v_frame_num(duration_frames: int, fps: float = 23.976) -> int:
    """Frames Wan S2V must render, at its native 16 fps, to cover a booking.

    S2V conditions on audio at 16 video frames per second. Asking it for the
    81-frame lane cap against a 3.4 s booking makes it render 5.1 s — 1.7 s
    of it against padded silence — and the finish stage throws that third
    away. Book the seconds instead: ceil(seconds × 16) plus one frame of
    conform margin, rounded up to Wan's 4n+1, capped at the 81-frame lane.
    """
    rate = fps if fps > 1 else 23.976
    seconds = max(1, int(duration_frames)) / rate
    want = int(math.ceil(seconds * S2V_NATIVE_FPS)) + 1
    n = max(4, int(math.ceil((want - 1) / 4)))
    return min(81, 4 * n + 1)


def s2v_render_frames(duration_frames: int, fps: float = 23.976) -> int:
    """Booked S2V frames plus alignment headroom, still a 4n+1 lane count ≤ 81."""
    booked = s2v_frame_num(duration_frames, fps)
    want = booked + S2V_HEADROOM_FRAMES
    n = max(4, (want + 2) // 4)
    return min(81, 4 * n + 1)


def wan_frame_num(duration_frames: int, fps: float = 23.976) -> int:
    """Wan A14B wants 4n+1 frames. Cap 81. Generate at 24 fps intent.

    Rounds up within the cap: a booked timeline duration may be conformed by
    trimming delivered frames, never by stretching them, so the camera must
    always deliver at least what the timeline books (up to the 81-frame lane).
    """
    rate = fps if fps > 1 else 23.976
    want = max(17, int(round(duration_frames * 24.0 / rate)))
    n = max(4, (want + 2) // 4)
    return min(81, 4 * n + 1)
