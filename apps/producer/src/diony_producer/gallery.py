"""The Finished Clips inventory: every clip and film the Director printed.

Items come from the Director's own records — each session's ``state.json``
beside its media under ``{vault}/director/{session}/``. A clip is a completed
generation op whose media landed (``{job_id}.mp4``); a film is the session's
assembled ``film.mp4``. Thumbnails are first frames cached beside the media.
Nothing here renders anything but a frame; the cut and the stitch are ffmpeg
concat jobs the Producer runs on request.
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
import uuid
import zipfile
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote

from diony_contracts.cluster import cluster_root, ffmpeg_install_hint
from diony_producer.media_tools import resolve_media_tool

_SAFE = re.compile(r"[^A-Za-z0-9._-]+")
_VIDEO_OPS = {"generate", "extend", "edit", "upscale"}


class GalleryError(LookupError):
    pass


def director_root() -> Path:
    return cluster_root() / "director"


def _safe(value: str) -> str:
    return _SAFE.sub("-", str(value or "")).strip(".-")[:80]


def _load_state(session_dir: Path) -> dict[str, Any] | None:
    try:
        payload = json.loads((session_dir / "state.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return payload if isinstance(payload, dict) else None


def _iso(stat) -> str:
    return datetime.fromtimestamp(stat.st_mtime, tz=UTC).isoformat()


def _item_id(session: str, name: str) -> str:
    return f"{session}:{name}"


def _urls(item_id: str, cache_key: str) -> dict[str, str]:
    encoded = quote(item_id, safe="")
    return {
        "file_url": f"/api/gallery/items/{encoded}/file?v={cache_key}",
        "download_url": f"/api/gallery/items/{encoded}/file?download=true&v={cache_key}",
        "thumbnail_url": f"/api/gallery/items/{encoded}/thumbnail?v={cache_key}",
        "details_url": f"/api/gallery/items/{encoded}/details",
        "remove_url": f"/api/gallery/items/{encoded}",
    }


def _cache_key(path: Path) -> str:
    stat = path.stat()
    return hashlib.sha256(f"{path}|{stat.st_size}|{stat.st_mtime_ns}".encode()).hexdigest()[:12]


def _film_item(session: str, session_dir: Path, state: dict[str, Any] | None) -> dict | None:
    film = session_dir / "film.mp4"
    if not film.is_file():
        return None
    stat = film.stat()
    record = _film_record(session_dir, state or {})
    item_id = _item_id(session, "film")
    return {
        "id": item_id,
        "kind": "film",
        "session": session,
        "title": f"Film · {session}",
        "plate": "assembled film",
        "prompt": record["idea"],
        "provider": record["provider"],
        "model": record["model"],
        "duration_s": record["duration_s"],
        "resolution": record["finish"],
        "clips_in_cut": record["clips_in_cut"],
        "bytes": stat.st_size,
        "created_at": _iso(stat),
        "playable": True,
        "deliverables": sorted(
            name
            for name in ("film.srt", "film.vtt", "edl.json", "assembly-map.json", "qc-packet.json")
            if (session_dir / name).is_file()
        ),
        **_urls(item_id, _cache_key(film)),
    }


def _clip_items(session: str, session_dir: Path, state: dict[str, Any] | None) -> list[dict]:
    rows: list[dict] = []
    ops = (state or {}).get("ops") or []
    seen: set[str] = set()
    for index, op in enumerate(ops, start=1):
        if not isinstance(op, dict):
            continue
        job_id = str(op.get("job_id") or "")
        if not job_id or op.get("status") != "completed" or op.get("op") not in _VIDEO_OPS:
            continue
        media = session_dir / f"{job_id}.mp4"
        if not media.is_file():
            continue
        seen.add(media.name)
        stat = media.stat()
        item_id = _item_id(session, media.stem)
        rows.append(
            {
                "id": item_id,
                "kind": "clip",
                "session": session,
                "op_id": str(op.get("id") or ""),
                "title": f"Clip {index} · {session}",
                "plate": f"{op.get('provider', '')} · {op.get('model', '')}".strip(" ·"),
                "prompt": str(op.get("prompt") or ""),
                "rationale": str(op.get("rationale") or ""),
                "provider": str(op.get("provider") or ""),
                "model": str(op.get("model") or ""),
                "mode": str(op.get("mode") or ""),
                "duration_s": _float(op.get("duration_s")),
                "resolution": str(op.get("resolution") or ""),
                "estimate_usd_micros": _estimate_micros(op),
                "bytes": stat.st_size,
                "created_at": str(op.get("created_at") or _iso(stat)),
                "playable": True,
                **_urls(item_id, _cache_key(media)),
            }
        )
    # media the record does not name (an older state format) still lists,
    # honestly labelled as unrecorded
    for media in sorted(session_dir.glob("job-*.mp4")):
        if media.name in seen:
            continue
        stat = media.stat()
        item_id = _item_id(session, media.stem)
        rows.append(
            {
                "id": item_id,
                "kind": "clip",
                "session": session,
                "op_id": "",
                "title": f"Clip · {media.stem}",
                "plate": "unrecorded clip",
                "prompt": "",
                "rationale": "",
                "provider": "",
                "model": "",
                "mode": "",
                "duration_s": None,
                "resolution": "",
                "estimate_usd_micros": 0,
                "bytes": stat.st_size,
                "created_at": _iso(stat),
                "playable": True,
                **_urls(item_id, _cache_key(media)),
            }
        )
    return rows


def _float(value) -> float | None:
    try:
        return float(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None


def _estimate_micros(op: dict[str, Any]) -> int:
    estimate = op.get("estimate")
    if isinstance(estimate, dict):
        try:
            return int(estimate.get("total_usd_micros") or 0)
        except (TypeError, ValueError):
            return 0
    return 0


def _session_idea(state: dict[str, Any]) -> str:
    """What the production was asked for: the packaged idea when the session
    carried one, else the authored brief of its first chain derivation (a
    one-shot never writes a package, so that is where its idea lives)."""
    package = state.get("package") or {}
    if isinstance(package, dict):
        idea = str(package.get("idea") or package.get("logline") or "").strip()
        if idea:
            return idea
    derivations = state.get("derivations") or {}
    if isinstance(derivations, dict):
        for derivation in derivations.values():
            if isinstance(derivation, dict) and str(derivation.get("idea") or "").strip():
                return str(derivation["idea"]).strip()
    return ""


def _load_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _film_record(session_dir: Path, state: dict[str, Any]) -> dict[str, Any]:
    """The cut film's facts from the records beside it — no decoding: the
    assembly map names the clips in the cut and their booked lengths, the
    QC packet the measured master runtime, the run plan the finish."""
    ops = [op for op in (state.get("ops") or []) if isinstance(op, dict)]
    by_op = {str(op.get("id") or ""): op for op in ops}
    events = [
        event
        for event in (_load_json(session_dir / "assembly-map.json").get("events") or [])
        if isinstance(event, dict)
    ]
    cut_ops = [by_op.get(str(event.get("op_id") or "")) for event in events]
    cut_ops = [op for op in cut_ops if op] or [op for op in ops if op.get("status") == "completed"]
    booked = 0.0
    for event in events:
        try:
            booked += float(event.get("out_s") or 0) - float(event.get("in_s") or 0)
        except (TypeError, ValueError):
            continue
    providers = _distinct(str(op.get("provider") or "") for op in cut_ops)
    models = _distinct(str(op.get("model") or "") for op in cut_ops)
    modes = _distinct(str(op.get("mode") or "") for op in cut_ops)
    booked_resolutions = _distinct(str(op.get("resolution") or "") for op in cut_ops)
    aspects = _distinct(
        str((op.get("artifact_refs") or {}).get("aspect_ratio") or "")
        for op in cut_ops
        if isinstance(op.get("artifact_refs"), dict)
    )
    run_plan = state.get("run_plan") if isinstance(state.get("run_plan"), dict) else {}
    master = _load_json(session_dir / "qc-packet.json").get("master") or {}
    return {
        "idea": _session_idea(state),
        "provider": " + ".join(providers),
        "model": " + ".join(models),
        "mode": " + ".join(modes),
        "clips_in_cut": len(cut_ops),
        "booked_s": round(booked, 3) if booked else None,
        "booked_resolution": " + ".join(booked_resolutions),
        "duration_s": _float(master.get("duration_s")) if isinstance(master, dict) else None,
        "finish": str(run_plan.get("finish") or ""),
        "upscaled": bool(run_plan.get("upscale")),
        "aspect": str(state.get("aspect") or " + ".join(aspects)),
        "style": str(state.get("style") or ""),
        "qc_verdict": str(master.get("verdict") or "") if isinstance(master, dict) else "",
    }


def _distinct(values) -> list[str]:
    seen: list[str] = []
    for value in values:
        if value and value not in seen:
            seen.append(value)
    return seen


def quality_label(width: int, height: int) -> str:
    """The delivery quality a filmmaker says out loud: 4K, 1080p, 720p."""
    lines = min(width, height) if width and height else max(width, height)
    if not lines:
        return ""
    if lines >= 2000:
        return "4K"
    for standard in (1080, 720, 540, 480, 360):
        if abs(lines - standard) <= 24:
            return f"{standard}p"
    return f"{lines}p"


def probe_media(path: Path) -> dict[str, Any] | None:
    """What the file itself says — raster, runtime, frame rate, codec, audio —
    cached beside the media so a card opens without decoding twice. None
    when ffprobe is missing or the file is not a movie."""
    cache = path.parent / "thumbs" / f"{path.stem}-{_cache_key(path)}.probe.json"
    cached = _load_json(cache)
    if cached.get("width"):
        return cached
    ffprobe = resolve_media_tool("ffprobe") or shutil.which("ffprobe")
    if not ffprobe:
        return None
    try:
        raw = subprocess.run(
            [
                ffprobe,
                "-v",
                "quiet",
                "-print_format",
                "json",
                "-show_format",
                "-show_streams",
                str(path),
            ],
            check=True,
            capture_output=True,
            timeout=60,
        ).stdout
        body = json.loads(raw)
    except (OSError, ValueError, subprocess.SubprocessError):
        return None
    streams = [s for s in body.get("streams", []) if isinstance(s, dict)]
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    if not video:
        return None
    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)
    width = int(video.get("width") or 0)
    height = int(video.get("height") or 0)
    fps = 0.0
    rate = str(video.get("avg_frame_rate") or video.get("r_frame_rate") or "")
    if "/" in rate:
        num, _, den = rate.partition("/")
        try:
            fps = float(num) / float(den) if float(den) else 0.0
        except ValueError:
            fps = 0.0
    fmt = body.get("format") or {}
    measured = {
        "width": width,
        "height": height,
        "quality": quality_label(width, height),
        "duration_s": round(float(fmt.get("duration") or video.get("duration") or 0), 3),
        "fps": round(fps, 3),
        "codec": str(video.get("codec_name") or ""),
        "bit_rate": int(float(fmt.get("bit_rate") or 0)),
        "has_audio": audio is not None,
        "audio_channels": int(audio.get("channels") or 0) if audio else 0,
    }
    try:
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps(measured), encoding="utf-8")
    except OSError:
        pass
    return measured


def gallery_items() -> list[dict]:
    """Every finished film and clip on this desk, newest session first."""
    root = director_root()
    if not root.is_dir():
        return []
    sessions = []
    for session_dir in root.iterdir():
        if not session_dir.is_dir() or session_dir.name.startswith("."):
            continue
        try:
            sessions.append((session_dir.stat().st_mtime, session_dir))
        except OSError:
            continue
    items: list[dict] = []
    for _, session_dir in sorted(sessions, key=lambda row: row[0], reverse=True):
        state = _load_state(session_dir)
        film = _film_item(session_dir.name, session_dir, state)
        if film:
            items.append(film)
        items.extend(_clip_items(session_dir.name, session_dir, state))
    return items


def gallery_productions(items: list[dict]) -> list[dict]:
    """One production per session, in the items' order: the cut film (when
    the session cut one) and every printed clip, by id. The Gallery draws
    one card per production and opens its pieces from that card; the flat
    `items` stay the unit of selection, stitching, ZIP, and deletion."""
    by_session: dict[str, dict] = {}
    for item in items:
        session = str(item["session"])
        row = by_session.get(session)
        if row is None:
            row = by_session[session] = {
                "session": session,
                "title": "",
                "idea": "",
                "film_id": "",
                "clip_ids": [],
                "hero_id": "",
                "count": 0,
            }
        if item["kind"] == "film":
            row["film_id"] = item["id"]
            row["idea"] = str(item.get("prompt") or "")
        else:
            row["clip_ids"].append(item["id"])
        row["count"] += 1
    for row in by_session.values():
        row["hero_id"] = row["film_id"] or (row["clip_ids"][0] if row["clip_ids"] else "")
        row["title"] = (
            f"Film · {row['session']}" if row["film_id"] else f"Production · {row['session']}"
        )
    return list(by_session.values())


def gallery_payload() -> dict:
    items = gallery_items()
    productions = gallery_productions(items)
    return {
        "items": items,
        "productions": productions,
        "count": len(items),
        "films": sum(1 for item in items if item["kind"] == "film"),
        "clips": sum(1 for item in items if item["kind"] == "clip"),
        "join_minimum": 2,
    }


def _resolve(item_id: str) -> tuple[Path, Path]:
    """(session dir, media file) for an item id, refusing anything outside the vault."""
    session, _, name = str(item_id).partition(":")
    if not session or not name or _safe(session) != session or _safe(name) != name:
        raise GalleryError("Gallery clip is not available")
    root = director_root().resolve()
    session_dir = (root / session).resolve()
    if not session_dir.is_relative_to(root) or not session_dir.is_dir():
        raise GalleryError("Gallery clip is not available")
    media = session_dir / ("film.mp4" if name == "film" else f"{name}.mp4")
    if not media.is_file():
        raise GalleryError("Gallery clip is not available")
    return session_dir, media


def gallery_media_path(item_id: str) -> Path:
    return _resolve(item_id)[1]


def gallery_archive_sources(item_ids: list[str]) -> list[dict[str, Any]]:
    """Resolve a stable, de-duplicated selection before an archive worker starts."""
    if not item_ids:
        raise ValueError("select at least one clip to download")
    sources: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw_item_id in item_ids:
        item_id = str(raw_item_id)
        if item_id in seen:
            continue
        session_dir, media = _resolve(item_id)
        seen.add(item_id)
        sources.append(
            {
                "id": item_id,
                "session": session_dir.name,
                "kind": "film" if media.name == "film.mp4" else "clip",
                "path": media,
                "bytes": media.stat().st_size,
            }
        )
    return sources


def build_gallery_archive(
    sources: list[dict[str, Any]],
    destination: Path,
    *,
    progress: Callable[[int, int], None] | None = None,
) -> dict[str, int]:
    """Store selected MP4s without recompressing them and add a small manifest."""
    if not sources:
        raise ValueError("select at least one clip to download")
    destination.parent.mkdir(parents=True, exist_ok=True)
    pending = destination.with_name(f".{destination.name}.part")
    pending.unlink(missing_ok=True)
    destination.unlink(missing_ok=True)
    manifest_items: list[dict[str, Any]] = []
    total = len(sources)
    width = max(3, len(str(total)))
    try:
        with zipfile.ZipFile(
            pending,
            mode="w",
            compression=zipfile.ZIP_STORED,
            allowZip64=True,
            strict_timestamps=False,
        ) as archive:
            for index, source in enumerate(sources, start=1):
                path = Path(source["path"])
                archive_name = (
                    f"{index:0{width}d}-{_safe(source['session'])}-{_safe(path.stem)}.mp4"
                )
                archive.write(path, archive_name)
                manifest_items.append(
                    {
                        "id": source["id"],
                        "session": source["session"],
                        "kind": source["kind"],
                        "file": archive_name,
                        "bytes": int(source["bytes"]),
                    }
                )
                if progress:
                    progress(index, total)
            manifest = {
                "schema_version": 1,
                "created_at": datetime.now(UTC).isoformat(),
                "count": total,
                "source_bytes": sum(int(source["bytes"]) for source in sources),
                "items": manifest_items,
            }
            archive.writestr("manifest.json", json.dumps(manifest, indent=2) + "\n")
        pending.replace(destination)
    except Exception:
        pending.unlink(missing_ok=True)
        destination.unlink(missing_ok=True)
        raise
    return {
        "count": total,
        "source_bytes": sum(int(source["bytes"]) for source in sources),
        "archive_bytes": destination.stat().st_size,
    }


def gallery_thumbnail_path(item_id: str) -> Path:
    session_dir, media = _resolve(item_id)
    destination = session_dir / "thumbs" / f"{media.stem}-{_cache_key(media)}.jpg"
    return ensure_first_frame(media, destination)


def ensure_first_frame(source: Path, destination: Path) -> Path:
    """Decode and cache frame zero without loading a whole movie into the browser."""
    if destination.is_file() and destination.stat().st_size > 4:
        return destination
    ffmpeg = resolve_media_tool("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("First-frame previews require ffmpeg")
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
            raise RuntimeError("ffmpeg did not produce a first-frame preview")
        pending.replace(destination)
    except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
        pending.unlink(missing_ok=True)
        raise RuntimeError("The first frame could not be decoded") from exc
    return destination


def gallery_item_details(item_id: str) -> dict[str, Any]:
    """The record behind one card: what the file measures (quality, runtime,
    frame rate), what was booked, the prompt that rolled or the idea the film
    answers, the reference stills the session carried, the deliverables."""
    session_dir, media = _resolve(item_id)
    state = _load_state(session_dir) or {}
    session = session_dir.name
    ops = [o for o in (state.get("ops") or []) if isinstance(o, dict)]
    op = next((o for o in ops if str(o.get("job_id") or "") == media.stem), None)
    uploads = []
    for upload in state.get("uploads") or []:
        if not isinstance(upload, dict):
            continue
        path = Path(str(upload.get("path") or ""))
        uploads.append(
            {
                "name": str(upload.get("name") or path.name),
                "role": str(upload.get("role") or "reference"),
                "bytes": path.stat().st_size if path.is_file() else 0,
            }
        )
    stat = media.stat()
    kind = "film" if media.name == "film.mp4" else "clip"
    details: dict[str, Any] = {
        "clip_id": item_id,
        "session": session,
        "kind": kind,
        "idea": _session_idea(state),
        "prompt": str(op.get("prompt") or "") if op else "",
        "prompt_version": str(op.get("prompt_version") or "") if op else "",
        "rationale": str(op.get("rationale") or "") if op else "",
        "provider": str(op.get("provider") or "") if op else "",
        "model": str(op.get("model") or "") if op else "",
        "mode": str(op.get("mode") or "") if op else "",
        "duration_s": _float(op.get("duration_s")) if op else None,
        "resolution": str(op.get("resolution") or "") if op else "",
        "estimate_usd_micros": _estimate_micros(op) if op else 0,
        "narration": str(op.get("narration") or "") if op else "",
        "voicing": str(op.get("voicing") or "") if op else "",
        "aspect": "",
        "created_at": str(op.get("created_at") or "") if op else _iso(stat),
        "references": uploads,
        "deliverables": sorted(
            p.name
            for p in session_dir.iterdir()
            if p.is_file() and p.suffix in {".srt", ".vtt", ".json"} and p.name != "state.json"
        ),
        "bytes": stat.st_size,
        "measured": probe_media(media),
        "review": None,
    }
    if op:
        refs = op.get("artifact_refs") if isinstance(op.get("artifact_refs"), dict) else {}
        details["aspect"] = str(refs.get("aspect_ratio") or "")
        reviews = state.get("reviews") if isinstance(state.get("reviews"), dict) else {}
        review = reviews.get(str(op.get("id") or ""))
        if isinstance(review, dict):
            details["review"] = {
                "verdict": str(review.get("verdict") or ""),
                "issues": [str(issue) for issue in (review.get("issues") or [])][:6],
            }
        # where the clip sits in the cut, when the session cut a film
        events = _load_json(session_dir / "assembly-map.json").get("events") or []
        for position, event in enumerate(events, start=1):
            if isinstance(event, dict) and str(event.get("op_id") or "") == str(op.get("id") or ""):
                details["position_in_cut"] = position
                details["clips_in_cut"] = len(events)
                break
    if kind == "film":
        record = _film_record(session_dir, state)
        details.update(
            {
                "provider": record["provider"],
                "model": record["model"],
                "mode": record["mode"],
                "duration_s": record["booked_s"] or record["duration_s"],
                "resolution": record["booked_resolution"],
                "finish": record["finish"],
                "upscaled": record["upscaled"],
                "clips_in_cut": record["clips_in_cut"],
                "aspect": record["aspect"],
                "style": record["style"],
                "created_at": _iso(stat),
                "review": {"verdict": record["qc_verdict"], "issues": []}
                if record["qc_verdict"]
                else None,
            }
        )
    return details


def remove_gallery_item(item_id: str) -> dict[str, str]:
    """Media only: the ledger and the session record keep every receipt."""
    _, media = _resolve(item_id)
    media.unlink()
    thumbs = media.parent / "thumbs"
    if thumbs.is_dir():
        for stale in thumbs.glob(f"{media.stem}-*.jpg"):
            stale.unlink(missing_ok=True)
    return {
        "removed": item_id,
        "detail": f"{media.name} was removed; its receipt stays on the ledger.",
    }


def remove_gallery_items(item_ids: list[str]) -> dict[str, Any]:
    """Remove a de-duplicated selection while reporting stale cards honestly."""
    if not item_ids:
        raise ValueError("select at least one clip to delete")
    removed: list[str] = []
    missing: list[str] = []
    seen: set[str] = set()
    for raw_item_id in item_ids:
        item_id = str(raw_item_id)
        if item_id in seen:
            continue
        seen.add(item_id)
        try:
            remove_gallery_item(item_id)
        except GalleryError:
            missing.append(item_id)
        else:
            removed.append(item_id)
    return {
        "removed": removed,
        "missing": missing,
        "detail": (
            f"{len(removed)} item(s) were removed; their records and receipts stay on the ledger."
        ),
    }


def stitch_items(item_ids: list[str], *, title: str = "") -> dict:
    """Join selected clips and films, in the given order, into one new film
    session (``join-*``) that lists and serves like any other."""
    if len(item_ids) < 2:
        raise ValueError("select at least two clips to stitch")
    ffmpeg = resolve_media_tool("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("ffmpeg was not found — " + ffmpeg_install_hint())
    sources = [_resolve(item_id)[1] for item_id in item_ids]
    join_id = f"join-{uuid.uuid4().hex[:8]}"
    dest_dir = director_root() / join_id
    dest = dest_dir / "film.mp4"
    dest_dir.mkdir(parents=True, exist_ok=True)
    listing = dest_dir / "inputs.txt"
    listing.write_text(
        "".join("file '" + str(src).replace("'", "'\\''") + "'\n" for src in sources),
        encoding="utf-8",
    )
    (dest_dir / "state.json").write_text(
        json.dumps(
            {
                "version": 1,
                "session_id": join_id,
                "joined_from": item_ids,
                "title": title,
                "package": {"idea": title or f"Stitched from {len(sources)} clips"},
                "ops": [],
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    def _cleanup_partial() -> None:
        dest.unlink(missing_ok=True)
        listing.unlink(missing_ok=True)

    try:
        copy = subprocess.run(
            [
                ffmpeg,
                "-hide_banner",
                "-loglevel",
                "error",
                "-y",
                "-f",
                "concat",
                "-safe",
                "0",
                "-i",
                str(listing),
                "-c",
                "copy",
                str(dest),
            ],
            capture_output=True,
            timeout=600,
        )
    except subprocess.TimeoutExpired:
        _cleanup_partial()
        shutil.rmtree(dest_dir, ignore_errors=True)
        raise RuntimeError("stitch timed out on the copy pass")
    if copy.returncode != 0 or not dest.is_file() or dest.stat().st_size < 1024:
        chains, concat_inputs = [], []
        cmd = [ffmpeg, "-hide_banner", "-loglevel", "error", "-y"]
        for src in sources:
            cmd += ["-i", str(src)]
        for index in range(len(sources)):
            chains.append(
                f"[{index}:v]scale=1280:720:force_original_aspect_ratio=decrease,"
                f"pad=1280:720:(ow-iw)/2:(oh-ih)/2,setsar=1,fps=24,format=yuv420p[v{index}]"
            )
            chains.append(
                f"[{index}:a]aresample=48000,aformat=channel_layouts=stereo,"
                f"loudnorm=I=-16:TP=-1.5:LRA=11,aresample=48000,afade=t=in:st=0:d=0.04[a{index}]"
            )
            concat_inputs.append(f"[v{index}][a{index}]")
        chains.append(f"{''.join(concat_inputs)}concat=n={len(sources)}:v=1:a=1[vc][ac]")
        chains.append("[ac]loudnorm=I=-16:TP=-1.0:LRA=11,aresample=48000[aout]")
        cmd += [
            "-filter_complex",
            ";".join(chains),
            "-map",
            "[vc]",
            "-map",
            "[aout]",
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-crf",
            "20",
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            str(dest),
        ]
        try:
            encode = subprocess.run(cmd, capture_output=True, timeout=1800)
        except subprocess.TimeoutExpired:
            _cleanup_partial()
            shutil.rmtree(dest_dir, ignore_errors=True)
            raise RuntimeError("stitch timed out on the re-encode pass")
        if encode.returncode != 0 or not dest.is_file():
            _cleanup_partial()
            shutil.rmtree(dest_dir, ignore_errors=True)
            raise RuntimeError(f"stitch failed: {encode.stderr.decode(errors='replace')[-300:]}")
    listing.unlink(missing_ok=True)
    stat = dest.stat()
    return {
        "session": join_id,
        "id": _item_id(join_id, "film"),
        "bytes": stat.st_size,
        "modified": _iso(stat),
        "url": f"/api/director-films/{join_id}",
        "joined_from": item_ids,
    }
