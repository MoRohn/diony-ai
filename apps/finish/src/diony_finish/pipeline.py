"""720p negative → 1920×1080 Rec.709 legal master. Not lanczos-as-deliverable."""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from diony_contracts.live import finish_ckpt


class FinishError(RuntimeError):
    pass


TILE = 192
OVERLAP = 64
SR_SCALE = 2
OUT_W = 1920
OUT_H = 1080


def _truthy(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in {"1", "true", "yes", "on"}


def _ffmpeg() -> str:
    exe = os.environ.get("DIONY_FFMPEG", "").strip() or shutil.which("ffmpeg") or "ffmpeg"
    return exe


def _run(cmd: list[str], label: str) -> None:
    proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if proc.returncode != 0:
        err = (proc.stderr or proc.stdout or label)[-3000:]
        raise FinishError(err)


def _fake_master(src: Path, dest: Path, fps: float) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    ff = shutil.which("ffmpeg")
    if ff:
        proc = subprocess.run(
            [
                ff,
                "-y",
                "-i",
                str(src),
                "-vf",
                f"scale={OUT_W}:{OUT_H}:flags=bicubic,fps={fps},setsar=1,format=yuv422p",
                "-an",
                "-c:v",
                "libx264",
                "-crf",
                "18",
                str(dest),
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        if proc.returncode == 0 and dest.is_file() and dest.stat().st_size > 32:
            return dest
    dest.write_bytes(b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 800)
    return dest


def _find_ckpt() -> Path:
    root = finish_ckpt()
    if root.is_file():
        return root
    if root.is_dir():
        pths = sorted(root.glob("*.pth")) + sorted(root.glob("*.pt"))
        if pths:
            return pths[0]
    raise FinishError(
        f"diony_sr_v1 checkpoint missing at {root}. Wizard App must pull RealESRGAN_x2.pth."
    )


def _load_model():
    import torch

    from diony_finish.rrdb import RRDBNet

    ckpt_path = _find_ckpt()
    blob = torch.load(str(ckpt_path), map_location="cpu", weights_only=False)
    if isinstance(blob, dict):
        blob = blob.get("params_ema") or blob.get("params") or blob.get("state_dict") or blob
    model = RRDBNet(scale=SR_SCALE)
    model.load_state_dict(blob, strict=False)
    model.eval()
    device = "cuda" if torch.cuda.is_available() else "cpu"
    return model.to(device), device


def _tile_sr(model, device, frame) -> object:
    import numpy as np
    import torch

    rgb = frame.astype(np.float32) / 255.0
    tensor = torch.from_numpy(rgb.transpose(2, 0, 1)).unsqueeze(0).to(device)
    h, w = rgb.shape[:2]
    pad_h = (TILE - h % TILE) % TILE
    pad_w = (TILE - w % TILE) % TILE
    if pad_h or pad_w:
        tensor = torch.nn.functional.pad(tensor, (0, pad_w, 0, pad_h), mode="reflect")
    _, _, ph, pw = tensor.shape
    out = torch.zeros(1, 3, ph * SR_SCALE, pw * SR_SCALE, device=device)
    weight = torch.zeros_like(out)
    step = TILE - OVERLAP
    for y in range(0, ph, step):
        for x in range(0, pw, step):
            y1 = min(y + TILE, ph)
            x1 = min(x + TILE, pw)
            y0 = y1 - TILE if y1 - y < TILE else y
            x0 = x1 - TILE if x1 - x < TILE else x
            patch = tensor[:, :, y0:y1, x0:x1]
            with torch.no_grad():
                pred = model(patch)
            out[:, :, y0 * SR_SCALE : y1 * SR_SCALE, x0 * SR_SCALE : x1 * SR_SCALE] += pred
            weight[:, :, y0 * SR_SCALE : y1 * SR_SCALE, x0 * SR_SCALE : x1 * SR_SCALE] += 1
    sr = (out / weight.clamp(min=1e-6)).clamp(0, 1)[0]
    arr = (sr.detach().cpu().numpy().transpose(1, 2, 0) * 255.0).clip(0, 255).astype(np.uint8)
    return arr[: h * SR_SCALE, : w * SR_SCALE]


def _warp_blend(prev, current, prev_gray, gray):
    import numpy as np
    try:
        import cv2
    except ImportError:
        return current
    flow = cv2.calcOpticalFlowFarneback(prev_gray, gray, None, 0.5, 3, 15, 3, 5, 1.2, 0)
    flow = cv2.resize(flow, (current.shape[1], current.shape[0])) * SR_SCALE
    h, w = current.shape[:2]
    grid_x, grid_y = np.meshgrid(np.arange(w), np.arange(h))
    map_x = (grid_x + flow[:, :, 0]).astype(np.float32)
    map_y = (grid_y + flow[:, :, 1]).astype(np.float32)
    warped = cv2.remap(prev, map_x, map_y, cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)
    return (0.72 * current.astype(np.float32) + 0.28 * warped.astype(np.float32)).clip(0, 255).astype(
        np.uint8
    )


def _decode_frames(src: Path):
    import numpy as np
    ff = _ffmpeg()
    probe = subprocess.run(
        [
            shutil.which("ffprobe") or "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=width,height,r_frame_rate",
            "-of",
            "csv=p=0",
            str(src),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if probe.returncode != 0:
        raise FinishError(probe.stderr or "ffprobe failed")
    width_s, height_s, rate = probe.stdout.strip().split(",")
    width, height = int(width_s), int(height_s)
    if "/" in rate:
        num, den = rate.split("/", 1)
        fps = float(num) / float(den or 1)
    else:
        fps = float(rate or 24)
    raw = subprocess.run(
        [ff, "-i", str(src), "-f", "rawvideo", "-pix_fmt", "rgb24", "-v", "error", "pipe:1"],
        capture_output=True,
        check=False,
    )
    if raw.returncode != 0:
        raise FinishError("ffmpeg decode failed")
    frame_bytes = width * height * 3
    blob = raw.stdout
    frames = [
        np.frombuffer(blob[i : i + frame_bytes], dtype=np.uint8).reshape(height, width, 3)
        for i in range(0, len(blob) - frame_bytes + 1, frame_bytes)
    ]
    if not frames:
        raise FinishError("no frames in 720 negative")
    return frames, fps


def _encode_sr_stream(frames: list, fps: float, dest: Path) -> None:
    h, w = frames[0].shape[:2]
    ff = _ffmpeg()
    vf = (
        f"zscale={OUT_W}:{OUT_H}:filter=bicubic:matrixin=709:matrix=709:"
        f"rangein=full:range=limited,fps={fps:.3f},setsar=1,limiter=min=16:max=235"
    )
    raw = b"".join(f.tobytes() for f in frames)
    base = [
        ff,
        "-y",
        "-f",
        "rawvideo",
        "-pix_fmt",
        "rgb24",
        "-s",
        f"{w}x{h}",
        "-r",
        f"{fps:.3f}",
        "-i",
        "-",
        "-vf",
        vf,
        "-an",
    ]
    prores = base + [
        "-c:v",
        "prores_ks",
        "-profile:v",
        "3",
        "-pix_fmt",
        "yuv422p10le",
        str(dest),
    ]
    proc = subprocess.run(prores, input=raw, check=False)
    if proc.returncode == 0 and dest.is_file() and dest.stat().st_size > 32:
        return
    h264 = base + [
        "-c:v",
        "libx264",
        "-crf",
        "12",
        "-pix_fmt",
        "yuv422p",
        str(dest),
    ]
    proc = subprocess.run(h264, input=raw, check=False)
    if proc.returncode != 0 or not dest.is_file():
        raise FinishError("ffmpeg legalize/conform failed")


def finish_clip(src: Path, dest: Path, *, fps: float = 23.976, grain: str = "none") -> Path:
    """Write a 1920×1080 Rec.709 legal master. Raises FinishError."""
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    src = Path(src)
    if not src.is_file():
        raise FinishError(f"720 negative missing: {src}")
    if _truthy("DIONY_FINISH_FAKE"):
        return _fake_master(src, dest, fps)
    model, device = _load_model()
    frames, src_fps = _decode_frames(src)
    rate = fps if fps > 1 else src_fps
    out_frames: list = []
    prev_sr = None
    prev_gray = None
    try:
        import cv2
    except ImportError:
        cv2 = None  # type: ignore[assignment]
    for frame in frames:
        sr = _tile_sr(model, device, frame)
        gray = None
        if cv2 is not None:
            gray = cv2.cvtColor(frame, cv2.COLOR_RGB2GRAY)
        if prev_sr is not None and prev_gray is not None and gray is not None:
            sr = _warp_blend(prev_sr, sr, prev_gray, gray)
        if grain in {"fine", "35mm"}:
            import numpy as np

            sigma = 1.4 if grain == "fine" else 3.2
            noise = np.random.normal(0, sigma, sr.shape).astype(np.float32)
            sr = np.clip(sr.astype(np.float32) + noise, 0, 255).astype(np.uint8)
        out_frames.append(sr)
        prev_sr = sr
        prev_gray = gray
    with tempfile.TemporaryDirectory() as tmp:
        tmp_dest = Path(tmp) / dest.name
        _encode_sr_stream(out_frames, rate, tmp_dest)
        dest.write_bytes(tmp_dest.read_bytes())
    if dest.stat().st_size < 32:
        raise FinishError("finish wrote an empty master")
    return dest


def stitch_masters(
    clips: list[tuple[Path, int, int]],
    dest: Path,
    fps: float = 23.976,
) -> Path:
    """Overlap-aware 1080 program master. clips are (path, handle_in, duration_frames)."""
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    valid = [(Path(path), int(handle), int(duration)) for path, handle, duration in clips if Path(path).is_file()]
    if not valid:
        raise FinishError("no 1080 clips to stitch")
    if _truthy("DIONY_FINISH_FAKE"):
        if _concat_masters(valid, dest, fps):
            return dest
        dest.write_bytes(valid[0][0].read_bytes() or (b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 800))
        if dest.stat().st_size < 32:
            dest.write_bytes(b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 800)
        return dest
    if not _concat_masters(valid, dest, fps):
        raise FinishError("1080 program stitch failed")
    return dest


def _concat_masters(clips: list[tuple[Path, int, int]], dest: Path, fps: float) -> bool:
    ff = shutil.which("ffmpeg") or os.environ.get("DIONY_FFMPEG", "").strip()
    if not ff:
        return False
    if len(clips) == 1:
        proc = subprocess.run(
            [ff, "-y", "-i", str(clips[0][0]), "-an", "-c", "copy", str(dest)],
            capture_output=True,
            text=True,
            check=False,
        )
        if proc.returncode == 0 and dest.is_file() and dest.stat().st_size > 32:
            return True
        proc = subprocess.run(
            [
                ff,
                "-y",
                "-i",
                str(clips[0][0]),
                "-an",
                "-c:v",
                "libx264",
                "-crf",
                "12",
                str(dest),
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        return proc.returncode == 0 and dest.is_file() and dest.stat().st_size > 32
    inputs: list[str] = []
    filters: list[str] = []
    labels: list[str] = []
    for index, (path, handle, duration) in enumerate(clips):
        inputs.extend(["-i", str(path)])
        start = max(0, handle)
        end = max(start + 1, duration)
        filters.append(
            f"[{index}:v]trim=start_frame={start}:end_frame={end},setpts=PTS-STARTPTS[v{index}]"
        )
        labels.append(f"[v{index}]")
    filters.append(
        f"{''.join(labels)}concat=n={len(clips)}:v=1:a=0[outv]"
    )
    proc = subprocess.run(
        [
            ff,
            "-y",
            *inputs,
            "-filter_complex",
            ";".join(filters),
            "-map",
            "[outv]",
            "-an",
            "-c:v",
            "libx264",
            "-crf",
            "12",
            "-pix_fmt",
            "yuv422p",
            "-r",
            f"{fps:.3f}",
            str(dest),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    return proc.returncode == 0 and dest.is_file() and dest.stat().st_size > 32
