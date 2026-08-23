"""Non-destructive, frame-precise Gallery clip composition."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from pydantic import BaseModel, Field, model_validator

from diony_contracts.composition import composition_summary
from diony_contracts.enums import AssetKind, GalleryCompositionStatus, GalleryTransition
from diony_contracts.models import (
    Asset,
    ClipComposition,
    ClipJoinPoint,
    ClipJoinSource,
    Project,
)
from diony_contracts.stitch import stitch_events
from diony_producer.vault import abs_path, list_vault_projects, persist_project


TRANSITIONS = [
    {"id": "cut", "label": "Clean cut", "default_frames": 0, "min_frames": 0, "max_frames": 0},
    {"id": "cross_dissolve", "label": "Cross dissolve", "default_frames": 12, "min_frames": 2, "max_frames": 120},
    {"id": "film_dissolve", "label": "Film dissolve", "default_frames": 16, "min_frames": 2, "max_frames": 120},
    {"id": "fade_through_black", "label": "Fade through black", "default_frames": 12, "min_frames": 2, "max_frames": 120},
    {"id": "dip_to_white", "label": "Dip to white", "default_frames": 10, "min_frames": 2, "max_frames": 72},
    {"id": "wipe_left", "label": "Wipe left", "default_frames": 12, "min_frames": 2, "max_frames": 72},
    {"id": "wipe_right", "label": "Wipe right", "default_frames": 12, "min_frames": 2, "max_frames": 72},
]


MEDIA_TOOL_DIRS = (
    Path(sys.executable).resolve().parent,
    Path(sys.executable).resolve().parent.parent / "Resources" / "bin",
    Path("/opt/homebrew/bin"),
    Path("/usr/local/bin"),
)


def resolve_media_tool(name: str) -> str:
    """Find FFmpeg tools in shell, packaged, and Finder-safe Mac locations."""
    env_name = "DIONY_FFMPEG" if name == "ffmpeg" else "DIONY_FFPROBE"
    configured = os.environ.get(env_name, "").strip()
    if configured:
        resolved = shutil.which(configured)
        if resolved:
            return resolved
        if Path(configured).is_file():
            return configured
    resolved = shutil.which(name)
    if resolved:
        return resolved
    for folder in MEDIA_TOOL_DIRS:
        candidate = folder / name
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate)
    return ""


class TrimRequest(BaseModel):
    in_frame: int = Field(default=0, ge=0)
    out_frame: int | None = Field(default=None, ge=1)


class JunctionRequest(BaseModel):
    transition: GalleryTransition = GalleryTransition.CUT
    duration_frames: int = Field(default=0, ge=0, le=120)


class JoinRequest(BaseModel):
    clip_ids: list[str] = Field(min_length=2, max_length=32)
    trims: dict[str, TrimRequest] = Field(default_factory=dict)
    junctions: list[JunctionRequest] = Field(default_factory=list, max_length=31)
    parent_composition_id: str | None = None
    preview_junction: int = Field(default=0, ge=0, le=30)

    @model_validator(mode="after")
    def selection_is_unique_and_complete(self) -> JoinRequest:
        if len(set(self.clip_ids)) != len(self.clip_ids):
            raise ValueError("Select each Gallery clip once, then reorder the sequence")
        if self.junctions and len(self.junctions) != len(self.clip_ids) - 1:
            raise ValueError("Set one transition for every junction")
        unknown = set(self.trims).difference(self.clip_ids)
        if unknown:
            raise ValueError("Trim controls must belong to selected Gallery clips")
        return self


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _project_inventory(current: Project) -> list[Project]:
    rows: list[Project] = []
    seen: set[str] = set()
    for project in (current, *list_vault_projects()):
        if project.id in seen:
            continue
        seen.add(project.id)
        rows.append(project)
    return rows


def _probe_media_with_ffmpeg(
    path: Path,
    *,
    fallback_fps: float,
    fallback_frames: int,
) -> dict[str, object] | None:
    ffmpeg = resolve_media_tool("ffmpeg")
    if not ffmpeg:
        return None
    try:
        proc = subprocess.run(
            [ffmpeg, "-hide_banner", "-i", str(path)],
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    detail = f"{proc.stderr}\n{proc.stdout}"
    video_line = next((line for line in detail.splitlines() if "Video:" in line), "")
    dimensions = re.search(r"(?<!\d)(\d{2,5})x(\d{2,5})(?!\d)", video_line)
    if dimensions is None:
        return None
    fps_match = re.search(r"(?:,|\s)(\d+(?:\.\d+)?)\s+fps(?:,|\s)", video_line)
    fps = float(fps_match.group(1)) if fps_match else fallback_fps
    duration_match = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", detail)
    frames = fallback_frames
    if duration_match:
        hours, minutes, seconds = duration_match.groups()
        duration = int(hours) * 3600 + int(minutes) * 60 + float(seconds)
        frames = round(duration * fps) or fallback_frames
    codec_match = re.search(r"Video:\s*([^\s,(]+)", video_line)
    return {
        "width": int(dimensions.group(1)),
        "height": int(dimensions.group(2)),
        "fps": fps,
        "frames": frames,
        "codec": codec_match.group(1) if codec_match else "unknown",
        "has_audio": any("Audio:" in line for line in detail.splitlines()),
    }


def probe_media(
    path: Path,
    *,
    fallback_fps: float,
    fallback_frames: int,
    fallback_width: int = 1920,
    fallback_height: int = 1080,
) -> dict[str, object]:
    probe = resolve_media_tool("ffprobe")
    fallback = {
        "width": fallback_width,
        "height": fallback_height,
        "fps": fallback_fps,
        "frames": fallback_frames,
        "codec": "unknown",
        "has_audio": False,
    }
    if probe:
        try:
            proc = subprocess.run(
                [
                    probe,
                    "-v",
                    "error",
                    "-show_entries",
                    "stream=index,codec_type,codec_name,width,height,avg_frame_rate,nb_frames,duration",
                    "-of",
                    "json",
                    str(path),
                ],
                capture_output=True,
                text=True,
                check=False,
                timeout=30,
            )
            if not proc.returncode:
                streams = json.loads(proc.stdout).get("streams", [])
                video = next(row for row in streams if row.get("codec_type") == "video")
                rate = str(video.get("avg_frame_rate") or "")
                if "/" in rate:
                    num, den = rate.split("/", 1)
                    fps = float(num) / float(den or 1)
                else:
                    fps = float(rate or fallback_fps)
                frames = int(video.get("nb_frames") or 0)
                if not frames:
                    frames = round(float(video.get("duration") or 0) * fps) or fallback_frames
                return {
                    "width": int(video.get("width") or fallback_width),
                    "height": int(video.get("height") or fallback_height),
                    "fps": fps,
                    "frames": frames,
                    "codec": str(video.get("codec_name") or "unknown"),
                    "has_audio": any(row.get("codec_type") == "audio" for row in streams),
                }
        except (OSError, ValueError, StopIteration, TypeError, json.JSONDecodeError, subprocess.SubprocessError):
            pass
    return _probe_media_with_ffmpeg(
        path,
        fallback_fps=fallback_fps,
        fallback_frames=fallback_frames,
    ) or fallback


def gallery_source_inventory(current: Project) -> dict[str, dict[str, object]]:
    inventory: dict[str, dict[str, object]] = {}
    for project in _project_inventory(current):
        if project.timeline and project.timeline.preview_path:
            path = abs_path(project.timeline.preview_path)
            if path.is_file() and path.stat().st_size > 32:
                _, frames = stitch_events(project)
                clip_id = f"program-{project.id}"
                profile = probe_media(path, fallback_fps=project.bible.fps, fallback_frames=frames)
                inventory[clip_id] = {
                    "clip_id": clip_id,
                    "project_id": project.id,
                    "title": project.bible.title,
                    "path": project.timeline.preview_path,
                    **profile,
                }
        for composition in project.compositions:
            if composition.status != GalleryCompositionStatus.COMPLETE or not composition.output_path:
                continue
            path = abs_path(composition.output_path)
            if not path.is_file() or path.stat().st_size <= 32:
                continue
            inventory[composition.asset_id] = {
                "clip_id": composition.asset_id,
                "project_id": project.id,
                "title": f"{project.bible.title} · joined v{composition.version}",
                "path": composition.output_path,
                "width": composition.width,
                "height": composition.height,
                "fps": composition.fps,
                "frames": composition.total_frames,
                "codec": "prores",
                "has_audio": True,
            }
    return inventory


def create_join_plan(
    project: Project,
    request: JoinRequest,
    *,
    profile_name: str,
    cost_center_ids: list[str],
) -> ClipComposition:
    inventory = gallery_source_inventory(project)
    missing = [clip_id for clip_id in request.clip_ids if clip_id not in inventory]
    if missing:
        raise ValueError(f"{', '.join(missing)} is not in this Gallery or has no finished media")
    target_fps = project.bible.fps
    sources: list[ClipJoinSource] = []
    for clip_id in request.clip_ids:
        row = inventory[clip_id]
        source_fps = float(row["fps"] or target_fps)
        source_frames = int(row["frames"] or 0)
        target_frames = max(2, round(source_frames * target_fps / source_fps))
        trim = request.trims.get(clip_id, TrimRequest())
        trim_out = trim.out_frame if trim.out_frame is not None else target_frames
        width, height = int(row["width"]), int(row["height"])
        codec = str(row["codec"])
        notes: list[str] = []
        if (width, height) != (1920, 1080):
            notes.append(f"{width}×{height} scaled to 1920×1080")
        if abs(source_fps - target_fps) > 0.01:
            notes.append(f"{source_fps:.3f} conformed to {target_fps:.3f} fps")
        if codec not in {"prores", "h264", "hevc"}:
            notes.append(f"{codec} decoded and normalized")
        sources.append(
            ClipJoinSource(
                clip_id=clip_id,
                project_id=str(row["project_id"]),
                title=str(row["title"]),
                path=str(row["path"]),
                duration_frames=target_frames,
                trim_in_frame=trim.in_frame,
                trim_out_frame=trim_out,
                fps=target_fps,
                width=width,
                height=height,
                codec=codec,
                normalized=bool(notes),
                normalization_note="; ".join(notes),
            )
        )
    junction_requests = request.junctions or [JunctionRequest() for _ in range(len(sources) - 1)]
    junctions = [
        ClipJoinPoint(
            left_clip_id=sources[index].clip_id,
            right_clip_id=sources[index + 1].clip_id,
            transition=row.transition,
            duration_frames=row.duration_frames,
        )
        for index, row in enumerate(junction_requests)
    ]
    next_version = max((row.version for row in project.compositions), default=0) + 1
    composition_id = f"cmp-{uuid.uuid4().hex}"
    root = f"projects/{project.id}/compositions/{composition_id}"
    return ClipComposition(
        id=composition_id,
        project_id=project.id,
        version=next_version,
        parent_composition_id=request.parent_composition_id,
        sources=sources,
        junctions=junctions,
        fps=target_fps,
        profile_name=profile_name,
        cost_center_ids=sorted(set(cost_center_ids)),
        asset_id=f"asset-{composition_id}",
        output_path=f"{root}/master_1080.mov",
        proxy_path=f"{root}/preview_1080.mp4",
        preview_path=f"{root}/junction-preview.mp4",
        manifest_path=f"{root}/composition.json",
    )


def preview_window(composition: ClipComposition, junction_index: int) -> ClipComposition:
    """Bound preview work to the actual junction, even when sources are hours long."""
    index = max(0, min(int(junction_index), len(composition.junctions) - 1))
    junction = composition.junctions[index]
    left = composition.sources[index]
    right = composition.sources[index + 1]
    context = max(round(composition.fps * 2), junction.duration_frames + 2)
    left_in = max(left.trim_in_frame, left.trim_out_frame - context)
    right_out = min(right.trim_out_frame, right.trim_in_frame + context)
    left_preview = left.model_copy(
        update={"trim_in_frame": left_in, "duration_frames": left.duration_frames}
    )
    right_preview = right.model_copy(
        update={"trim_out_frame": right_out, "duration_frames": right.duration_frames}
    )
    return ClipComposition(
        id=composition.id,
        project_id=composition.project_id,
        version=composition.version,
        parent_composition_id=composition.parent_composition_id,
        sources=[left_preview, right_preview],
        junctions=[junction],
        fps=composition.fps,
        profile_name=composition.profile_name,
        cost_center_ids=composition.cost_center_ids,
        asset_id=composition.asset_id,
        output_path=composition.output_path,
        proxy_path=composition.proxy_path,
        preview_path=composition.preview_path,
        manifest_path=composition.manifest_path,
    )


_XFADE = {
    GalleryTransition.CROSS_DISSOLVE: "fade",
    GalleryTransition.FILM_DISSOLVE: "dissolve",
    GalleryTransition.FADE_THROUGH_BLACK: "fadeblack",
    GalleryTransition.DIP_TO_WHITE: "fadewhite",
    GalleryTransition.WIPE_LEFT: "wipeleft",
    GalleryTransition.WIPE_RIGHT: "wiperight",
}


def _write_manifest(composition: ClipComposition) -> None:
    path = abs_path(composition.manifest_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(composition.model_dump_json(indent=2) + "\n")


def _run_process(command: list[str], cancel_event: threading.Event | None) -> None:
    process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    while True:
        try:
            _, stderr = process.communicate(timeout=0.2)
            break
        except subprocess.TimeoutExpired:
            if cancel_event and cancel_event.is_set():
                process.terminate()
                try:
                    process.communicate(timeout=2)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.communicate()
                raise InterruptedError("Join cancelled. Source clips were not changed.")
    if process.returncode:
        raise RuntimeError((stderr or "1080 composition failed")[-3000:])


def _fake_video(path: Path, marker: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"\x00\x00\x00\x18ftypmp42" + marker * 800)


def render_composition(
    composition: ClipComposition,
    output: Path,
    proxy: Path,
    *,
    preview_only: bool = False,
    cancel_event: threading.Event | None = None,
) -> None:
    """Render actual trims/transitions. Final is ProRes; proxy/preview is H.264."""
    if os.environ.get("DIONY_DRY_RUN", "").lower() in {"1", "true", "yes"}:
        if cancel_event and cancel_event.is_set():
            raise InterruptedError("Join cancelled. Source clips were not changed.")
        _fake_video(output, b"j")
        if proxy != output:
            _fake_video(proxy, b"p")
        return
    ffmpeg = resolve_media_tool("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("FFmpeg is required to create a joined clip version")
    inputs: list[str] = []
    filters: list[str] = []
    durations: list[float] = []
    has_audio: list[bool] = []
    for index, source in enumerate(composition.sources):
        path = abs_path(source.path)
        if not path.is_file():
            raise FileNotFoundError(f"Source clip is no longer available: {source.title}")
        inputs.extend(["-i", str(path)])
        start = source.trim_in_frame / composition.fps
        end = source.trim_out_frame / composition.fps
        duration = end - start
        durations.append(duration)
        profile = probe_media(path, fallback_fps=composition.fps, fallback_frames=source.duration_frames)
        audio = bool(profile["has_audio"])
        has_audio.append(audio)
        filters.append(
            f"[{index}:v]trim=start={start:.6f}:end={end:.6f},setpts=PTS-STARTPTS,"
            f"scale=1920:1080:force_original_aspect_ratio=decrease,"
            f"pad=1920:1080:(ow-iw)/2:(oh-ih)/2:black,fps={composition.fps:.3f},"
            f"format=yuv422p10le[v{index}]"
        )
        if audio:
            filters.append(
                f"[{index}:a]atrim=start={start:.6f}:end={end:.6f},"
                f"asetpts=PTS-STARTPTS,aresample=48000[a{index}]"
            )
        else:
            filters.append(f"anullsrc=r=48000:cl=stereo,atrim=duration={duration:.6f}[a{index}]")
    video_label, audio_label = "v0", "a0"
    running = durations[0]
    for index, junction in enumerate(composition.junctions, start=1):
        next_video, next_audio = f"v{index}", f"a{index}"
        out_video, out_audio = f"joinv{index}", f"joina{index}"
        transition_seconds = junction.duration_frames / composition.fps
        if junction.transition == GalleryTransition.CUT:
            filters.append(f"[{video_label}][{next_video}]concat=n=2:v=1:a=0[{out_video}]")
            filters.append(f"[{audio_label}][{next_audio}]concat=n=2:v=0:a=1[{out_audio}]")
        else:
            offset = max(0, running - transition_seconds)
            filters.append(
                f"[{video_label}][{next_video}]xfade=transition={_XFADE[junction.transition]}:"
                f"duration={transition_seconds:.6f}:offset={offset:.6f}[{out_video}]"
            )
            filters.append(
                f"[{audio_label}][{next_audio}]acrossfade=d={transition_seconds:.6f}:"
                f"c1=tri:c2=tri[{out_audio}]"
            )
        running += durations[index] - transition_seconds
        video_label, audio_label = out_video, out_audio
    output.parent.mkdir(parents=True, exist_ok=True)
    mapped_video = video_label
    if preview_only:
        filters.append(f"[{video_label}]scale=960:540[previewv]")
        mapped_video = "previewv"
    base = [
        ffmpeg,
        "-y",
        *inputs,
        "-filter_complex",
        ";".join(filters),
        "-map",
        f"[{mapped_video}]",
        "-map",
        f"[{audio_label}]",
    ]
    if preview_only:
        command = base + [
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-crf",
            "18",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-movflags",
            "+faststart",
            str(output),
        ]
        _run_process(command, cancel_event)
        return
    command = base + [
        "-c:v",
        "prores_ks",
        "-profile:v",
        "3",
        "-pix_fmt",
        "yuv422p10le",
        "-c:a",
        "pcm_s24le",
        str(output),
    ]
    _run_process(command, cancel_event)
    _run_process(
        [
            ffmpeg,
            "-y",
            "-i",
            str(output),
            "-c:v",
            "libx264",
            "-preset",
            "fast",
            "-crf",
            "17",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-movflags",
            "+faststart",
            str(proxy),
        ],
        cancel_event,
    )


def render_and_commit(
    project: Project,
    composition: ClipComposition,
    *,
    cancel_event: threading.Event | None = None,
) -> Asset:
    composition.status = GalleryCompositionStatus.PROCESSING
    composition.updated_at = _now()
    _write_manifest(composition)
    output, proxy = abs_path(composition.output_path), abs_path(composition.proxy_path)
    try:
        render_composition(composition, output, proxy, cancel_event=cancel_event)
        digest = hashlib.sha256(output.read_bytes()).hexdigest()
        asset = Asset(
            id=composition.asset_id,
            kind=AssetKind.VIDEO,
            filename=f"{project.id}-joined-v{composition.version}-1080.mov",
            mime="video/quicktime",
            bytes=output.stat().st_size,
            path=composition.output_path,
            proxy_path=composition.proxy_path,
            sha256=digest,
            role="gallery_join",
            source_asset_ids=composition.source_asset_ids,
            metadata={
                "composition_id": composition.id,
                "version": composition.version,
                "width": 1920,
                "height": 1080,
                "fps": composition.fps,
                "color_space": "rec709",
                "profile_name": composition.profile_name,
                "cost_center_ids": ",".join(composition.cost_center_ids),
                "non_destructive": True,
            },
        )
        project.assets = [row for row in project.assets if row.id != asset.id]
        project.assets.append(asset)
        project.compositions = [row for row in project.compositions if row.id != composition.id]
        composition.status = GalleryCompositionStatus.COMPLETE
        composition.updated_at = _now()
        project.compositions.append(composition)
        project.version += 1
        project.updated_at = _now()
        _write_manifest(composition)
        persist_project(project)
        return asset
    except InterruptedError:
        composition.status = GalleryCompositionStatus.CANCELLED
        composition.error = "Cancelled. Source clips were not changed."
        composition.updated_at = _now()
        _write_manifest(composition)
        raise
    except Exception as exc:
        composition.status = GalleryCompositionStatus.FAILED
        composition.error = str(exc)[:500]
        composition.updated_at = _now()
        _write_manifest(composition)
        raise


class JoinManager:
    def __init__(self) -> None:
        self._plans: dict[str, ClipComposition] = {}
        self._events: dict[str, threading.Event] = {}
        self._lock = threading.Lock()

    def remember(self, composition: ClipComposition) -> None:
        with self._lock:
            self._plans[composition.id] = composition

    def start(
        self,
        project: Project,
        composition: ClipComposition,
        *,
        on_complete: Callable[[ClipComposition], None] | None = None,
    ) -> ClipComposition:
        event = threading.Event()
        composition.status = GalleryCompositionStatus.QUEUED
        composition.updated_at = _now()
        _write_manifest(composition)
        with self._lock:
            self._plans[composition.id] = composition
            self._events[composition.id] = event

        def work() -> None:
            try:
                render_and_commit(project, composition, cancel_event=event)
            except Exception:
                pass
            finally:
                if on_complete:
                    on_complete(composition)

        threading.Thread(target=work, name=f"diony-join-{composition.id}", daemon=True).start()
        return composition

    def get(self, composition_id: str) -> ClipComposition:
        with self._lock:
            try:
                return self._plans[composition_id]
            except KeyError as exc:
                raise KeyError("Join job not found") from exc

    def cancel(self, composition_id: str) -> ClipComposition:
        composition = self.get(composition_id)
        if composition.status in {
            GalleryCompositionStatus.COMPLETE,
            GalleryCompositionStatus.FAILED,
            GalleryCompositionStatus.CANCELLED,
        }:
            return composition
        with self._lock:
            self._events[composition_id].set()
        return composition

    def wait(self, composition_id: str, timeout: float = 5) -> ClipComposition:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            composition = self.get(composition_id)
            if composition.status in {
                GalleryCompositionStatus.COMPLETE,
                GalleryCompositionStatus.FAILED,
                GalleryCompositionStatus.CANCELLED,
            }:
                return composition
            time.sleep(0.02)
        return self.get(composition_id)


join_manager = JoinManager()


def join_payload(composition: ClipComposition) -> dict[str, object]:
    payload = composition.model_dump(mode="json")
    payload["summary"] = composition_summary(composition)
    payload["normalization"] = [
        source.normalization_note for source in composition.sources if source.normalization_note
    ]
    payload["transitions"] = TRANSITIONS
    return payload
