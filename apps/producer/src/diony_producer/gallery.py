"""Gallery is the inventory of finished full clips — one card per produced show.

Coverage takes stay inside Production lineage. Gallery never lists SH-010…SH-060.
"""

from __future__ import annotations

import hashlib
import os
import re
import subprocess
import threading
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote

from diony_contracts.enums import (
    ArtifactKind,
    GalleryCompositionStatus,
    ProductionStopDisposition,
)
from diony_contracts.models import Asset, Project
from diony_contracts.stitch import program_master_rel, stitch_events
from diony_contracts.wan_prompt import prompt_receipt_sha256
from diony_finish.pipeline import finished_media_has_picture
from diony_producer.gallery_join import probe_media, resolve_media_tool
from diony_producer.vault import abs_path, list_vault_projects, persist_project


_SAFE_CLIP_ID = re.compile(r"[^A-Za-z0-9._-]+")
_PREVIEW_LOCK = threading.Lock()
_HASH_LOCK = threading.Lock()
_HASH_CACHE: dict[tuple[str, int, int], str] = {}


def _visual_qc_required() -> bool:
    disabled = {"1", "true", "yes", "on"}
    return not (
        os.environ.get("DIONY_DRY_RUN", "").strip().lower() in disabled
        or os.environ.get("DIONY_FINISH_FAKE", "").strip().lower() in disabled
    )


def _project_inventory(current: Project) -> tuple[Project, ...]:
    projects: list[Project] = []
    seen: set[str] = set()
    for project in (current, *list_vault_projects()):
        if project.id in seen or project.deleted_at is not None:
            continue
        seen.add(project.id)
        projects.append(project)
    return tuple(projects)


def _thumbnail_rel(clip_id: str, source: Path) -> str:
    stat = source.stat()
    fingerprint = hashlib.sha256(
        f"{clip_id}|{source}|{stat.st_size}|{stat.st_mtime_ns}".encode()
    ).hexdigest()[:12]
    safe = _SAFE_CLIP_ID.sub("-", clip_id).strip(".-") or "clip"
    return f"gallery/thumbnails/{safe[:80]}-{fingerprint}.jpg"


def _browser_preview_rel(clip_id: str, source: Path) -> str:
    stat = source.stat()
    fingerprint = hashlib.sha256(
        f"{clip_id}|{source}|{stat.st_size}|{stat.st_mtime_ns}".encode()
    ).hexdigest()[:12]
    safe = _SAFE_CLIP_ID.sub("-", clip_id).strip(".-") or "clip"
    return f"gallery/previews/{safe[:80]}-{fingerprint}.mp4"


def ensure_first_frame(source: Path, destination: Path) -> Path:
    """Decode and cache frame zero without loading a whole movie into the browser."""
    if destination.is_file() and destination.stat().st_size > 4:
        return destination
    ffmpeg = resolve_media_tool("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("First-frame previews require the bundled FFmpeg tool")
    destination.parent.mkdir(parents=True, exist_ok=True)
    pending = destination.with_name(f".{destination.stem}.part{destination.suffix}")
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
        "-map",
        "0:v:0",
        "-vf",
        r"select=eq(n\,0),scale=min(960\,iw):-2",
        "-frames:v",
        "1",
        "-q:v",
        "2",
        str(pending),
    ]
    try:
        subprocess.run(command, check=True, capture_output=True, timeout=45)
        if not pending.is_file() or pending.stat().st_size <= 4:
            raise RuntimeError("FFmpeg did not produce a first-frame preview")
        pending.replace(destination)
    except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
        pending.unlink(missing_ok=True)
        raise RuntimeError("The first frame could not be decoded") from exc
    return destination


def ensure_browser_preview(source: Path, destination: Path) -> Path:
    """Cache an HTML-video-safe H.264 4:2:0 + AAC version of a broadcast master."""
    with _PREVIEW_LOCK:
        if destination.is_file() and destination.stat().st_size > 32:
            return destination
        ffmpeg = resolve_media_tool("ffmpeg")
        if not ffmpeg:
            raise RuntimeError("Gallery playback requires the bundled FFmpeg tool")
        destination.parent.mkdir(parents=True, exist_ok=True)
        pending = destination.with_name(f".{destination.stem}.part{destination.suffix}")
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
            "-map",
            "0:v:0",
            "-map",
            "0:a:0?",
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-crf",
            "19",
            "-pix_fmt",
            "yuv420p",
            "-color_primaries",
            "bt709",
            "-color_trc",
            "bt709",
            "-colorspace",
            "bt709",
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            "-ar",
            "48000",
            "-ac",
            "2",
            "-movflags",
            "+faststart",
            str(pending),
        ]
        try:
            subprocess.run(command, check=True, capture_output=True, timeout=7200)
            if not pending.is_file() or pending.stat().st_size <= 32:
                raise RuntimeError("FFmpeg did not produce a browser preview")
            pending.replace(destination)
        except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
            pending.unlink(missing_ok=True)
            raise RuntimeError("The browser preview could not be encoded") from exc
        return destination


def _action_urls(
    item_id: str,
    file_url: str,
    *,
    download_url: str | None = None,
    cache_key: str = "",
) -> dict[str, str]:
    encoded = quote(item_id, safe="")
    separator = "&" if "?" in file_url else "?"
    version = f"?v={quote(cache_key, safe='')}" if cache_key else ""
    return {
        "thumbnail_url": f"/api/gallery/items/{encoded}/thumbnail{version}",
        "details_url": f"/api/gallery/items/{encoded}/details",
        "download_url": download_url or f"{file_url}{separator}download=true",
        "remove_url": f"/api/gallery/items/{encoded}",
    }


def _preview_rel(project: Project) -> str:
    if project.timeline and project.timeline.preview_path:
        return project.timeline.preview_path
    return program_master_rel(project) if project.timeline else ""


def _media_ok(rel: str) -> bool:
    if not rel:
        return False
    suffix = Path(rel).suffix.lower()
    if suffix not in {".mp4", ".mov", ".webm", ".mkv"}:
        return False
    try:
        dest = abs_path(rel)
    except ValueError:
        return False
    return dest.is_file() and dest.stat().st_size > 32


def _sha256_file(path: Path) -> str:
    stat = path.stat()
    key = (str(path), stat.st_size, stat.st_mtime_ns)
    with _HASH_LOCK:
        cached = _HASH_CACHE.get(key)
    if cached:
        return cached
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    value = digest.hexdigest()
    with _HASH_LOCK:
        if len(_HASH_CACHE) >= 256:
            _HASH_CACHE.clear()
        _HASH_CACHE[key] = value
    return value


def _reference_sha256(project: Project, asset_ids: list[str]) -> str:
    rows: list[str] = []
    for asset_id in asset_ids:
        try:
            asset = project.asset(asset_id)
        except KeyError:
            rows.append(f"{asset_id}:missing")
            continue
        rows.append(f"{asset.id}:{asset.sha256}")
    return hashlib.sha256("|".join(rows).encode("utf-8")).hexdigest() if rows else ""


def _selected_finish_hashes(project: Project) -> list[str] | None:
    timeline = project.timeline
    if timeline is None or len(timeline.events) != len(project.shots):
        return None
    receipts: list[str] = []
    for event in timeline.events:
        if not event.take_id:
            return None
        try:
            shot = project.shot(event.shot_id)
            take = project.take(event.take_id)
        except KeyError:
            return None
        prompt_sha = prompt_receipt_sha256(shot)
        reference_ids = list(shot.keyframe_asset_ids)
        reference_sha = _reference_sha256(project, reference_ids)
        negative_path = f"projects/{project.id}/takes/{take.id}/negative_720.mp4"
        negative = next(
            (
                art
                for art in take.artifacts
                if art.kind == ArtifactKind.NEGATIVE_720
                and art.path == negative_path
                and art.project_id == project.id
                and art.shot_id == shot.id
                and art.sha256
                and art.prompt_sha256 == prompt_sha
                and art.reference_asset_ids == reference_ids
                and art.reference_sha256 == reference_sha
            ),
            None,
        )
        if negative is None:
            return None
        finish_path = f"projects/{project.id}/takes/{take.id}/finish_1080.mov"
        finish = next(
            (
                art
                for art in take.artifacts
                if art.kind == ArtifactKind.FINISH_1080
                and art.path == finish_path
                and art.project_id == project.id
                and art.shot_id == shot.id
                and art.sha256
                and art.source_sha256 == negative.sha256
                and art.prompt_sha256 == prompt_sha
                and art.reference_asset_ids == reference_ids
                and art.reference_sha256 == reference_sha
                and art.generation_task == "diony_sr_v1"
            ),
            None,
        )
        if finish is None:
            return None
        receipts.append(finish.sha256)
    return receipts


def _program_lineage_ok(project: Project) -> bool:
    timeline = project.timeline
    if timeline is None:
        return False
    if timeline.preview_path != program_master_rel(project):
        return False
    receipts = _selected_finish_hashes(project)
    if receipts is None or receipts != timeline.source_artifact_sha256s:
        return False
    if not timeline.picture_sha256 or not timeline.program_sha256:
        return False
    try:
        master = abs_path(timeline.preview_path)
        return (
            master.is_file()
            and master.stat().st_size > 32
            and _sha256_file(master) == timeline.program_sha256
            and (not _visual_qc_required() or finished_media_has_picture(master))
        )
    except (OSError, ValueError):
        return False


def _retained_stop_lineage_ok(project: Project) -> bool:
    """A Keep result is Gallery-safe as clearly labeled incomplete 720p media."""
    receipt = project.production_stop
    timeline = project.timeline
    if (
        receipt is None
        or receipt.disposition != ProductionStopDisposition.KEEP
        or not receipt.completed_take_ids
        or not receipt.result_path
        or receipt.result_error
        or receipt.result_width != 1280
        or receipt.result_height != 720
        or timeline is None
        or timeline.gallery_removed_at is not None
        or timeline.preview_path != receipt.result_path
    ):
        return False
    try:
        result = abs_path(receipt.result_path)
        return (
            result.is_file()
            and result.stat().st_size > 32
            and _sha256_file(result) == receipt.result_sha256
        )
    except (OSError, ValueError):
        return False


def _program_card(project: Project, *, current: bool) -> dict | None:
    """Only a real program file. Stub title cards are not clips."""
    if project.timeline and project.timeline.gallery_removed_at is not None:
        return None
    final_lineage = _program_lineage_ok(project)
    retained_stop = _retained_stop_lineage_ok(project)
    if not final_lineage and not retained_stop:
        return None
    rel = _preview_rel(project)
    playable = _media_ok(rel)
    if not playable:
        return None
    finished = final_lineage and "master_1080" in (rel or "")
    if finished and playable:
        plate = "1080 program"
    elif playable:
        plate = "720 program"
    else:
        plate = "full clip"
    _, fallback_total = stitch_events(project)
    media = probe_media(
        abs_path(rel),
        fallback_fps=project.bible.fps,
        fallback_frames=fallback_total,
    )
    source_fps = float(media["fps"] or project.bible.fps)
    total = max(2, round(int(media["frames"] or fallback_total) * project.bible.fps / source_fps))
    minutes = project.brief.target_minutes if project.brief else 0
    production_kind = project.brief.production_kind.value if project.brief else "program"
    sample_seconds = project.brief.sample_seconds if project.brief else None
    item_id = f"program-{project.id}"
    cache_key = (
        project.timeline.program_sha256[:16]
        if final_lineage
        else project.production_stop.result_sha256[:16]
    )
    master_url = f"/api/projects/{project.id}/program/file"
    file_url = f"/api/projects/{project.id}/program/preview?v={cache_key}"
    return {
        "id": item_id,
        "kind": "program",
        "current": current,
        "project_id": project.id,
        "title": project.bible.title,
        "slug": project.bible.title,
        "prompt": project.brief.idea if project.brief else project.bible.title,
        "duration_frames": total,
        "fps": project.bible.fps,
        "target_minutes": minutes,
        "production_kind": production_kind,
        "sample_seconds": sample_seconds,
        "file_url": file_url,
        "playable": playable,
        "finished": finished,
        "plate": (
            "720 incomplete · not final"
            if retained_stop
            else "1080 sample" if production_kind == "sample" else plate
        ),
        "program": True,
        "width": int(media["width"]),
        "height": int(media["height"]),
        "codec": str(media["codec"]),
        "source_fps": source_fps,
        "source_count": 1,
        **_action_urls(
            item_id,
            file_url,
            download_url=f"{master_url}?download=true&v={cache_key}",
            cache_key=cache_key,
        ),
    }


def _composition_cards(project: Project, *, current: bool) -> list[dict]:
    rows: list[dict] = []
    for composition in project.compositions:
        if composition.status != GalleryCompositionStatus.COMPLETE:
            continue
        asset = next((row for row in project.assets if row.id == composition.asset_id), None)
        if asset is not None and asset.deleted_at is not None:
            continue
        rel = composition.proxy_path or composition.output_path
        if not _media_ok(rel):
            continue
        file_url = f"/api/gallery/joins/{composition.id}/file?proxy=true"
        rows.append(
            {
                "id": composition.asset_id,
                "kind": "composition",
                "composition_id": composition.id,
                "current": current,
                "project_id": project.id,
                "title": f"{project.bible.title} · joined v{composition.version}",
                "slug": f"Joined version {composition.version}",
                "prompt": project.brief.idea if project.brief else project.bible.title,
                "duration_frames": composition.total_frames,
                "fps": composition.fps,
                "target_minutes": round(composition.total_frames / composition.fps / 60, 3),
                "file_url": file_url,
                "playable": True,
                "finished": True,
                "plate": "1080 joined version",
                "program": False,
                "width": composition.width,
                "height": composition.height,
                "codec": "ProRes HQ",
                "source_count": len(composition.sources),
                "version": composition.version,
                "profile_name": composition.profile_name,
                "cost_center_ids": composition.cost_center_ids,
                **_action_urls(composition.asset_id, file_url),
            }
        )
    return rows


def gallery_media_source(current: Project, clip_id: str) -> tuple[Project, str]:
    for project in _project_inventory(current):
        if clip_id == f"program-{project.id}":
            if (
                project.timeline
                and project.timeline.gallery_removed_at is None
                and (_program_lineage_ok(project) or _retained_stop_lineage_ok(project))
            ):
                rel = _preview_rel(project)
                if _media_ok(rel):
                    return project, rel
        for composition in project.compositions:
            if composition.asset_id != clip_id or composition.status != GalleryCompositionStatus.COMPLETE:
                continue
            asset = next((row for row in project.assets if row.id == clip_id), None)
            if asset is not None and asset.deleted_at is not None:
                continue
            rel = composition.proxy_path or composition.output_path
            if _media_ok(rel):
                return project, rel
    raise KeyError(clip_id)


def _source_projects(current: Project, clip_id: str) -> tuple[Project, ...]:
    """Resolve a Gallery card to its original Project sources in screen order."""
    projects = _project_inventory(current)
    project_by_id = {project.id: project for project in projects}
    composition_by_asset = {
        composition.asset_id: composition
        for project in projects
        for composition in project.compositions
        if composition.status == GalleryCompositionStatus.COMPLETE
    }
    resolved: list[Project] = []
    seen_projects: set[str] = set()
    visited_clips: set[str] = set()

    def append_project(project_id: str) -> None:
        project = project_by_id.get(project_id)
        if project is None or project.id in seen_projects:
            return
        seen_projects.add(project.id)
        resolved.append(project)

    def walk(source_clip_id: str, fallback_project_id: str = "") -> None:
        if source_clip_id in visited_clips:
            return
        visited_clips.add(source_clip_id)
        if source_clip_id.startswith("program-"):
            append_project(source_clip_id.removeprefix("program-"))
            return
        composition = composition_by_asset.get(source_clip_id)
        if composition is None:
            append_project(fallback_project_id)
            return
        for source in composition.sources:
            walk(source.clip_id, source.project_id)

    walk(clip_id)
    if not resolved:
        project, _ = gallery_media_source(current, clip_id)
        append_project(project.id)
    return tuple(resolved)


def _reference_payload(clip_id: str, asset: Asset) -> dict[str, object]:
    encoded_clip = quote(clip_id, safe="")
    encoded_asset = quote(asset.id, safe="")
    return {
        "id": asset.id,
        "filename": asset.filename,
        "kind": asset.kind.value,
        "mime": asset.mime,
        "bytes": asset.bytes,
        "download_url": (
            f"/api/gallery/items/{encoded_clip}/references/{encoded_asset}/file"
        ),
    }


def gallery_item_details(current: Project, clip_id: str) -> dict[str, object]:
    """Return the original Idea and downloadable Brief references for one card."""
    gallery_media_source(current, clip_id)
    sources: list[dict[str, object]] = []
    for project in _source_projects(current, clip_id):
        attachment_ids = project.brief.attachment_ids if project.brief else []
        references: list[dict[str, object]] = []
        for asset_id in attachment_ids:
            try:
                asset = project.asset(asset_id)
            except KeyError:
                continue
            if asset.deleted_at is None:
                references.append(_reference_payload(clip_id, asset))
        sources.append(
            {
                "project_id": project.id,
                "title": project.bible.title,
                "idea_prompt": project.brief.idea if project.brief else "",
                "references": references,
            }
        )
    return {"clip_id": clip_id, "sources": sources}


def gallery_reference_file(
    current: Project,
    clip_id: str,
    asset_id: str,
) -> tuple[Path, Asset]:
    """Authorize a reference against the card's source lineage before serving it."""
    gallery_media_source(current, clip_id)
    for project in _source_projects(current, clip_id):
        if project.brief is None or asset_id not in project.brief.attachment_ids:
            continue
        try:
            asset = project.asset(asset_id)
        except KeyError:
            continue
        if asset.deleted_at is not None:
            continue
        path = abs_path(asset.path)
        if not path.is_file():
            raise FileNotFoundError(asset.path)
        return path, asset
    raise KeyError(asset_id)


def gallery_thumbnail_path(current: Project, clip_id: str) -> Path:
    _, rel = gallery_media_source(current, clip_id)
    source = abs_path(rel)
    destination = abs_path(_thumbnail_rel(clip_id, source))
    return ensure_first_frame(source, destination)


def gallery_program_preview_path(current: Project, project_id: str) -> Path:
    for project in _project_inventory(current):
        if project.id != project_id:
            continue
        if project.timeline and project.timeline.gallery_removed_at is not None:
            break
        if not (_program_lineage_ok(project) or _retained_stop_lineage_ok(project)):
            break
        rel = _preview_rel(project)
        if not _media_ok(rel):
            break
        source = abs_path(rel)
        destination = abs_path(_browser_preview_rel(f"program-{project.id}", source))
        return ensure_browser_preview(source, destination)
    raise KeyError(project_id)


def remove_gallery_item(current: Project, clip_id: str) -> dict[str, str]:
    """Remove a card recoverably while preserving its media and lineage."""
    removed_at = datetime.now(timezone.utc)
    for project in _project_inventory(current):
        if clip_id == f"program-{project.id}" and project.timeline and _media_ok(_preview_rel(project)):
            project.timeline.gallery_removed_at = removed_at
            project.updated_at = removed_at
            persist_project(project, set_current=False)
            return {
                "status": "removed",
                "detail": "Removed from Gallery. The program master remains in the vault.",
            }
        for composition in project.compositions:
            if composition.asset_id != clip_id or composition.status != GalleryCompositionStatus.COMPLETE:
                continue
            composition.status = GalleryCompositionStatus.SUPERSEDED
            composition.updated_at = removed_at
            for asset in project.assets:
                if asset.id == clip_id:
                    asset.deleted_at = removed_at
            project.updated_at = removed_at
            persist_project(project, set_current=False)
            return {
                "status": "removed",
                "detail": "Removed from Gallery. Source clips and version lineage remain in the vault.",
            }
    raise KeyError(clip_id)


def gallery_payload(project: Project) -> dict:
    """One inventory row per produced show: the full program, never shot dailies."""
    items: list[dict] = []
    for prod in _project_inventory(project):
        card = _program_card(prod, current=prod.id == project.id)
        if card:
            items.append(card)
        items.extend(_composition_cards(prod, current=prod.id == project.id))
    return {
        "project_id": project.id,
        "title": project.bible.title,
        "items": items,
        "count": len(items),
        "negative": "720p Wan",
        "master": "1080 Diony finish",
        "join_minimum": 2,
    }
