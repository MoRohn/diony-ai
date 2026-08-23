"""720p negative → 1920×1080 Rec.709 legal master. Not lanczos-as-deliverable."""

from __future__ import annotations

import os
import itertools
import re
import shutil
import subprocess
import tempfile
import threading
from collections.abc import Callable, Mapping
from pathlib import Path

from diony_contracts.live import finish_ckpt


class FinishError(RuntimeError):
    pass


TILE = 192
OVERLAP = 64
SR_SCALE = 2
OUT_W = 1920
OUT_H = 1080
_VISUAL_CACHE_LOCK = threading.Lock()
_VISUAL_CACHE: dict[tuple[str, int, int], bool] = {}
_SIGNALSTAT_RE = re.compile(
    r"lavfi\.signalstats\.(YMIN|YMAX|UMIN|UMAX|VMIN|VMAX)=([0-9]+(?:\.[0-9]+)?)"
)


class FinishModelCache:
    """Process-local SR cache invalidated by checkpoint identity.

    Finish is sequential on the supported profiles, but the lock also prevents
    duplicate loads if two controller threads reach the boundary together.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._key: tuple[str, int, int] | None = None
        self._value: object | None = None

    @staticmethod
    def _checkpoint_key(path: Path) -> tuple[str, int, int]:
        stat = path.stat()
        return (str(path.resolve()), stat.st_size, stat.st_mtime_ns)

    def get(self, path: Path, loader: Callable[[Path], object]) -> object:
        key = self._checkpoint_key(path)
        with self._lock:
            if self._value is not None and self._key == key:
                return self._value
            value = loader(path)
            self._key = key
            self._value = value
            return value

    def clear(self) -> None:
        with self._lock:
            self._key = None
            self._value = None


_FINISH_MODEL_CACHE = FinishModelCache()


def _select_tile_size(total_memory: int = 0) -> int:
    """Choose an RRDB tile for the GPU, with an operator override."""
    override = os.environ.get("DIONY_SR_TILE", "").strip()
    if override:
        try:
            tile = int(override)
        except ValueError as exc:
            raise FinishError("DIONY_SR_TILE must be an even integer from 128 to 1024") from exc
        if tile < 128 or tile > 1024 or tile % 2:
            raise FinishError("DIONY_SR_TILE must be an even integer from 128 to 1024")
        return tile
    gib = 1024**3
    if total_memory >= 80 * gib:
        return 720
    if total_memory >= 40 * gib:
        return 512
    return TILE


def _device_tile_size(device: str) -> int:
    if device != "cuda":
        return _select_tile_size()
    try:
        import torch

        total_memory = int(torch.cuda.get_device_properties(torch.cuda.current_device()).total_memory)
    except Exception:  # noqa: BLE001 — fall back to the conservative Spark tile
        total_memory = 0
    return _select_tile_size(total_memory)


def _truthy(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in {"1", "true", "yes", "on"}


def _ffmpeg() -> str:
    exe = os.environ.get("DIONY_FFMPEG", "").strip() or shutil.which("ffmpeg") or "ffmpeg"
    return exe


def _ffprobe() -> str:
    return os.environ.get("DIONY_FFPROBE", "").strip() or shutil.which("ffprobe") or "ffprobe"


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
        f"diony_sr_v1 checkpoint missing at {root}. Studio App must pull RealESRGAN_x2.pth."
    )


def _validate_rrdb_checkpoint(state: Mapping[str, object], path: Path) -> None:
    """Refuse a checkpoint that cannot be the pinned x2 RRDB architecture."""
    first = state.get("conv_first.weight")
    shape = tuple(getattr(first, "shape", ()))
    if len(shape) != 4:
        raise FinishError(
            f"diony_sr_v1 checkpoint architecture mismatch at {path}: "
            "conv_first.weight is missing. Studio App must reinstall the pinned x2 pack."
        )
    actual = int(shape[1])
    expected = 3 * SR_SCALE * SR_SCALE
    if actual != expected:
        raise FinishError(
            f"diony_sr_v1 checkpoint architecture mismatch at {path}: "
            f"conv_first.weight has {actual} input channels; expected {expected} for x2 "
            "pixel-unshuffle. Studio App must reinstall the pinned RealESRGAN x2 pack."
        )
    if "conv_up2.weight" not in state:
        raise FinishError(
            f"diony_sr_v1 checkpoint architecture mismatch at {path}: "
            "conv_up2.weight is missing from the x2 RRDB trunk. Studio App must reinstall "
            "the pinned RealESRGAN x2 pack."
        )


def _checkpoint_state(blob: object, path: Path) -> dict[str, object]:
    if isinstance(blob, Mapping):
        for key in ("params_ema", "params", "state_dict"):
            candidate = blob.get(key)
            if isinstance(candidate, Mapping):
                blob = candidate
                break
    if not isinstance(blob, Mapping):
        raise FinishError(f"diony_sr_v1 checkpoint at {path} does not contain a state dictionary")
    state = {str(key): value for key, value in blob.items()}
    if state and all(key.startswith("module.") for key in state):
        state = {key.removeprefix("module."): value for key, value in state.items()}
    return state


def _load_model_uncached(ckpt_path: Path):
    import torch

    from diony_finish.rrdb import RRDBNet

    blob = torch.load(str(ckpt_path), map_location="cpu", weights_only=False)
    state = _checkpoint_state(blob, ckpt_path)
    _validate_rrdb_checkpoint(state, ckpt_path)
    model = RRDBNet(scale=SR_SCALE)
    try:
        model.load_state_dict(state, strict=True)
    except RuntimeError as exc:
        summary = " ".join(str(exc).split())[:600]
        raise FinishError(
            f"diony_sr_v1 checkpoint architecture mismatch at {ckpt_path}: {summary}. "
            "Studio App must reinstall the pinned RealESRGAN x2 pack."
        ) from exc
    model.eval()
    device = "cuda" if torch.cuda.is_available() else "cpu"
    return model.to(device), device


def _load_model():
    """Load once per unchanged checkpoint for the lifetime of Finish."""
    return _FINISH_MODEL_CACHE.get(_find_ckpt(), _load_model_uncached)


def _tile_sr(model, device, frame, *, tile: int | None = None) -> object:
    import numpy as np
    import torch

    rgb = frame.astype(np.float32) / 255.0
    tensor = torch.from_numpy(rgb.transpose(2, 0, 1)).unsqueeze(0).to(device)
    h, w = rgb.shape[:2]
    tile = tile or _device_tile_size(device)
    overlap = min(OVERLAP, tile // 4)
    pad_h = (tile - h % tile) % tile
    pad_w = (tile - w % tile) % tile
    if pad_h or pad_w:
        tensor = torch.nn.functional.pad(tensor, (0, pad_w, 0, pad_h), mode="reflect")
    _, _, ph, pw = tensor.shape
    out = torch.zeros(1, 3, ph * SR_SCALE, pw * SR_SCALE, device=device)
    weight = torch.zeros_like(out)
    step = tile - overlap
    for y in range(0, ph, step):
        for x in range(0, pw, step):
            y1 = min(y + tile, ph)
            x1 = min(x + tile, pw)
            y0 = y1 - tile if y1 - y < tile else y
            x0 = x1 - tile if x1 - x < tile else x
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


def _decode_frame_stream(src: Path):
    import numpy as np

    ff = _ffmpeg()
    probe = subprocess.run(
        [
            _ffprobe(),
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=width,height,r_frame_rate,nb_frames",
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
    values = probe.stdout.strip().split(",")
    if len(values) < 3:
        raise FinishError("ffprobe returned incomplete video stream metadata")
    width_s, height_s, rate = values[:3]
    width, height = int(width_s), int(height_s)
    if "/" in rate:
        num, den = rate.split("/", 1)
        fps = float(num) / float(den or 1)
    else:
        fps = float(rate or 24)
    try:
        total_frames = int(values[3]) if len(values) > 3 and values[3] not in {"", "N/A"} else 0
    except ValueError:
        total_frames = 0
    process = subprocess.Popen(
        [ff, "-i", str(src), "-f", "rawvideo", "-pix_fmt", "rgb24", "-v", "error", "pipe:1"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    frame_bytes = width * height * 3

    def frames():
        if process.stdout is None:
            process.kill()
            raise FinishError("ffmpeg decode stream is unavailable")
        decoded = 0
        try:
            while True:
                chunks: list[bytes] = []
                remaining = frame_bytes
                while remaining:
                    chunk = process.stdout.read(remaining)
                    if not chunk:
                        break
                    chunks.append(chunk)
                    remaining -= len(chunk)
                if remaining:
                    break
                decoded += 1
                yield np.frombuffer(b"".join(chunks), dtype=np.uint8).reshape(height, width, 3)
        finally:
            process.stdout.close()
            stderr = process.stderr.read() if process.stderr is not None else b""
            if process.stderr is not None:
                process.stderr.close()
            returncode = process.wait()
            if returncode != 0:
                message = stderr.decode("utf-8", errors="replace")[-3000:]
                raise FinishError(message or "ffmpeg decode failed")
            if decoded == 0:
                raise FinishError("no frames in 720 negative")

    return frames(), fps, total_frames


def _decode_frames(src: Path):
    """Compatibility helper for QC/tests; Finish itself uses the bounded stream."""
    frames, fps, _total = _decode_frame_stream(src)
    return list(frames), fps


def finished_media_has_picture(path: Path) -> bool:
    """Decode representative frames and reject empty, flat, or frozen masters."""
    import numpy as np

    path = Path(path)
    try:
        stat = path.stat()
    except OSError:
        return False
    if stat.st_size <= 32:
        return False
    key = (str(path), stat.st_size, stat.st_mtime_ns)
    with _VISUAL_CACHE_LOCK:
        cached = _VISUAL_CACHE.get(key)
    if cached is not None:
        return cached
    probe = subprocess.run(
        [
            _ffprobe(),
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=nw=1:nk=1",
            str(path),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    try:
        duration = max(0.0, float(probe.stdout.strip())) if probe.returncode == 0 else 0.0
    except ValueError:
        duration = 0.0
    positions = sorted({0.0, duration / 2, max(0.0, duration - 0.1)})
    structured_frames: list[object] = []
    for position in positions:
        sample = subprocess.run(
            [
                _ffmpeg(),
                "-v",
                "error",
                "-ss",
                f"{position:.6f}",
                "-i",
                str(path),
                "-map",
                "0:v:0",
                "-frames:v",
                "1",
                "-vf",
                "scale=160:90:flags=area",
                "-f",
                "rawvideo",
                "-pix_fmt",
                "rgb24",
                "pipe:1",
            ],
            capture_output=True,
            check=False,
        )
        expected = 160 * 90 * 3
        if sample.returncode != 0 or len(sample.stdout) < expected:
            continue
        frame = np.frombuffer(sample.stdout[:expected], dtype=np.uint8).reshape(90, 160, 3)
        channel_std = np.std(frame.astype(np.float32), axis=(0, 1))
        channel_range = np.percentile(frame, 99, axis=(0, 1)) - np.percentile(
            frame, 1, axis=(0, 1)
        )
        structured_channels = (channel_std >= 2.0) & (channel_range >= 8.0)
        if int(np.count_nonzero(structured_channels)) >= 2:
            structured_frames.append(frame)
    valid = False
    for index, frame in enumerate(structured_frames):
        for earlier in structured_frames[:index]:
            temporal_delta = np.mean(
                np.abs(frame.astype(np.float32) - earlier.astype(np.float32))
            )
            if float(temporal_delta) >= 0.5:
                valid = True
                break
        if valid:
            break
    with _VISUAL_CACHE_LOCK:
        if len(_VISUAL_CACHE) >= 256:
            _VISUAL_CACHE.clear()
        _VISUAL_CACHE[key] = valid
    return valid


def verify_finished_media(path: Path, *, label: str = "1080 finish") -> Path:
    path = Path(path)
    if not finished_media_has_picture(path):
        raise FinishError(
            f"{label} failed visual QC: decoded frames are empty, flat color, or frozen"
        )
    probe = subprocess.run(
        [
            _ffprobe(),
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=codec_name,width,height,pix_fmt,color_range,color_space,color_primaries,color_transfer,avg_frame_rate",
            "-of",
            "json",
            str(path),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    try:
        import json

        streams = json.loads(probe.stdout).get("streams", []) if probe.returncode == 0 else []
        stream = streams[0]
    except (IndexError, TypeError, ValueError):
        raise FinishError(f"{label} has no measurable video stream")
    width = int(stream.get("width") or 0)
    height = int(stream.get("height") or 0)
    if (width, height) != (OUT_W, OUT_H):
        raise FinishError(f"{label} is {width}x{height}; Diony requires 1920x1080")
    if stream.get("codec_name") != "prores" or stream.get("pix_fmt") != "yuv422p10le":
        raise FinishError(f"{label} must be ProRes 422 HQ 10-bit")
    if stream.get("color_space") != "bt709":
        raise FinishError(f"{label} must carry a Rec.709 picture tag")
    if stream.get("color_range") != "tv":
        bounds = _decoded_signal_bounds(path)
        if not _signal_bounds_are_limited(bounds):
            raise FinishError(
                f"{label} must carry or decode inside Rec.709 limited-range picture levels"
            )
    if stream.get("color_primaries") != "bt709" or stream.get("color_transfer") != "bt709":
        raise FinishError(f"{label} must carry Rec.709 primaries and transfer tags")
    rate = str(stream.get("avg_frame_rate") or "")
    try:
        if "/" in rate:
            numerator, denominator = rate.split("/", 1)
            fps = float(numerator) / float(denominator or 1)
        else:
            fps = float(rate)
    except ValueError:
        fps = 0.0
    if not (abs(fps - 23.976) < 0.01 or abs(fps - 24.0) < 0.01):
        raise FinishError(f"{label} frame rate is {fps:g}; Diony requires 23.976 or 24.000")
    return path


def _decoded_signal_bounds(path: Path) -> dict[str, float]:
    """Measure decoded 10-bit Y/Cb/Cr extrema when a ProRes range tag is unavailable."""
    sample = subprocess.run(
        [
            _ffmpeg(),
            "-hide_banner",
            "-loglevel",
            "info",
            "-i",
            str(path),
            "-map",
            "0:v:0",
            "-vf",
            "signalstats,metadata=mode=print",
            "-an",
            "-f",
            "null",
            "-",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if sample.returncode != 0:
        return {}
    bounds: dict[str, float] = {}
    for key, raw in _SIGNALSTAT_RE.findall(f"{sample.stdout}\n{sample.stderr}"):
        value = float(raw)
        if key.endswith("MIN"):
            bounds[key] = min(bounds.get(key, value), value)
        else:
            bounds[key] = max(bounds.get(key, value), value)
    return bounds


def _signal_bounds_are_limited(bounds: Mapping[str, float]) -> bool:
    required = {"YMIN", "YMAX", "UMIN", "UMAX", "VMIN", "VMAX"}
    if not required.issubset(bounds):
        return False
    return (
        float(bounds["YMIN"]) >= 64
        and float(bounds["YMAX"]) <= 940
        and float(bounds["UMIN"]) >= 64
        and float(bounds["UMAX"]) <= 960
        and float(bounds["VMIN"]) >= 64
        and float(bounds["VMAX"]) <= 960
    )


def _encode_sr_stream(
    frames,
    fps: float,
    dest: Path,
    *,
    source_fps: float | None = None,
) -> None:
    iterator = iter(frames)
    try:
        first = next(iterator)
    except StopIteration as exc:
        raise FinishError("no frames to encode") from exc
    h, w = first.shape[:2]
    ff = _ffmpeg()
    vf = (
        f"scale=w={OUT_W}:h={OUT_H}:flags=bicubic:in_range=full:out_range=limited,"
        f"fps={fps:.3f},setsar=1,format=yuv422p10le,"
        "limiter=min=72:max=932:planes=1,"
        "limiter=min=72:max=952:planes=6"
    )
    input_fps = source_fps if source_fps and source_fps > 0 else fps
    base = [
        ff,
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-f",
        "rawvideo",
        "-pix_fmt",
        "rgb24",
        "-s",
        f"{w}x{h}",
        "-r",
        f"{input_fps:.6f}",
        "-i",
        "-",
        "-vf",
        vf,
        "-an",
        "-color_range",
        "tv",
        "-colorspace",
        "bt709",
        "-color_primaries",
        "bt709",
        "-color_trc",
        "bt709",
    ]
    prores = base + [
        "-c:v",
        "prores_ks",
        "-profile:v",
        "3",
        "-bits_per_mb",
        "8192",
        "-quant_mat",
        "hq",
        "-pix_fmt",
        "yuv422p10le",
        str(dest),
    ]
    proc = subprocess.Popen(prores, stdin=subprocess.PIPE, stderr=subprocess.PIPE)
    error: Exception | None = None
    try:
        if proc.stdin is None:
            raise FinishError("ffmpeg encode stream is unavailable")
        for frame in itertools.chain((first,), iterator):
            if frame.shape[:2] != (h, w):
                raise FinishError("finish frame dimensions changed during encode")
            proc.stdin.write(frame.tobytes())
    except Exception as exc:  # noqa: BLE001 - terminate encoder before preserving the error
        error = exc
    finally:
        if proc.stdin is not None:
            try:
                proc.stdin.close()
            except OSError:
                pass
    stderr = proc.stderr.read() if proc.stderr is not None else b""
    if proc.stderr is not None:
        proc.stderr.close()
    returncode = proc.wait()
    if error is not None:
        dest.unlink(missing_ok=True)
        raise error
    if returncode != 0 or not dest.is_file() or dest.stat().st_size <= 32:
        dest.unlink(missing_ok=True)
        message = stderr.decode("utf-8", errors="replace")[-3000:]
        raise FinishError(message or "ffmpeg ProRes legalize/conform failed")


def finish_clip(
    src: Path,
    dest: Path,
    *,
    fps: float = 23.976,
    grain: str = "none",
    progress: Callable[[int, int], None] | None = None,
) -> Path:
    """Write a 1920×1080 Rec.709 legal master. Raises FinishError."""
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    src = Path(src)
    if not src.is_file():
        raise FinishError(f"720 negative missing: {src}")
    if _truthy("DIONY_FINISH_FAKE"):
        return _fake_master(src, dest, fps)
    model, device = _load_model()
    tile = _device_tile_size(device)
    frames, src_fps, measured_total = _decode_frame_stream(src)
    rate = fps if fps > 1 else src_fps
    prev_sr = None
    prev_gray = None
    try:
        import cv2
    except ImportError:
        cv2 = None  # type: ignore[assignment]
    total_frames = max(1, measured_total)

    def finished_frames():
        nonlocal prev_sr, prev_gray
        for frame_number, frame in enumerate(frames, start=1):
            sr = _tile_sr(model, device, frame, tile=tile)
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
            prev_sr = sr
            prev_gray = gray
            if progress is not None:
                progress(frame_number, total_frames)
            yield sr

    with tempfile.TemporaryDirectory(dir=dest.parent) as tmp:
        tmp_dest = Path(tmp) / dest.name
        _encode_sr_stream(finished_frames(), rate, tmp_dest, source_fps=src_fps)
        verify_finished_media(tmp_dest)
        os.replace(tmp_dest, dest)
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


def _stitch_filter(
    clips: list[tuple[Path, int, int]],
    fps: float,
) -> tuple[str, str, int]:
    """Build an overlap graph; positive handles are temporal blends, never discarded frames."""
    if not clips:
        raise FinishError("no 1080 clips to stitch")
    filters: list[str] = []
    for index, (path, _handle, duration) in enumerate(clips):
        source_frames = _media_frame_count(path)
        stretch = max(1, duration) / max(1, source_frames or duration)
        filters.append(
            f"[{index}:v]trim=start_frame=0,setpts={stretch:.9f}*PTS,"
            f"fps={fps:.6f},tpad=stop_mode=clone:stop=-1,"
            f"trim=start_frame=0:end_frame={max(1, duration)},"
            f"settb=AVTB,setpts=PTS-STARTPTS[v{index}]"
        )
    current = "[v0]"
    total_frames = max(1, clips[0][2])
    for index, (_path, handle, duration) in enumerate(clips[1:], start=1):
        overlap = min(max(0, handle), max(0, total_frames - 1), max(0, duration - 1))
        output = f"[x{index}]"
        if overlap:
            filters.append(
                f"{current}[v{index}]xfade=transition=fade:"
                f"duration={overlap / fps:.6f}:offset={(total_frames - overlap) / fps:.6f}"
                f"{output}"
            )
        else:
            filters.append(f"{current}[v{index}]concat=n=2:v=1:a=0{output}")
        total_frames += max(1, duration) - overlap
        current = output
    # xfade works through an intermediate pixel representation and can expand
    # otherwise legal 10-bit clip levels at the overlap. Re-assert the same
    # guard band used by the per-clip Finish encoder before ProRes encoding.
    program = "[program]"
    filters.append(
        f"{current}format=yuv422p10le,"
        "limiter=min=72:max=932:planes=1,"
        f"limiter=min=72:max=952:planes=6{program}"
    )
    return ";".join(filters), program, total_frames


def _media_frame_count(path: Path) -> int:
    """Count decoded source frames so timeline conformance preserves motion, not a frozen tail."""
    path = Path(path)
    if not path.is_file():
        return 0
    probe = subprocess.run(
        [
            _ffprobe(),
            "-v",
            "error",
            "-count_frames",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=nb_read_frames,nb_frames",
            "-of",
            "csv=p=0",
            str(path),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if probe.returncode != 0:
        return 0
    for value in probe.stdout.strip().split(","):
        try:
            frames = int(value)
        except ValueError:
            continue
        if frames > 0:
            return frames
    return 0


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
                "prores_ks",
                "-profile:v",
                "3",
                "-bits_per_mb",
                "8192",
                "-quant_mat",
                "hq",
                "-pix_fmt",
                "yuv422p10le",
                str(dest),
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        return proc.returncode == 0 and dest.is_file() and dest.stat().st_size > 32
    inputs: list[str] = []
    for path, _handle, _duration in clips:
        inputs.extend(["-i", str(path)])
    graph, output, _total_frames = _stitch_filter(clips, fps)
    proc = subprocess.run(
        [
            ff,
            "-y",
            *inputs,
            "-filter_complex",
            graph,
            "-map",
            output,
            "-an",
            "-c:v",
            "prores_ks",
            "-profile:v",
            "3",
            "-bits_per_mb",
            "8192",
            "-quant_mat",
            "hq",
            "-pix_fmt",
            "yuv422p10le",
            "-color_range",
            "tv",
            "-colorspace",
            "bt709",
            "-color_primaries",
            "bt709",
            "-color_trc",
            "bt709",
            "-r",
            f"{fps:.3f}",
            str(dest),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    return proc.returncode == 0 and dest.is_file() and dest.stat().st_size > 32
