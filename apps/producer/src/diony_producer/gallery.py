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
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote

from diony_contracts.cluster import cluster_root
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
    logline = ""
    if state:
        package = state.get("package") or {}
        logline = str(package.get("idea") or package.get("logline") or "")
    item_id = _item_id(session, "film")
    return {
        "id": item_id,
        "kind": "film",
        "session": session,
        "title": f"Film · {session}",
        "plate": "assembled film",
        "prompt": logline,
        "provider": "",
        "model": "",
        "duration_s": None,
        "resolution": "",
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


def gallery_payload() -> dict:
    items = gallery_items()
    return {
        "items": items,
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
    """The record behind one card: the prompt that rolled, the model, the
    estimate, the reference stills the session carried, the deliverables."""
    session_dir, media = _resolve(item_id)
    state = _load_state(session_dir) or {}
    session = session_dir.name
    ops = state.get("ops") or []
    op = next(
        (o for o in ops if isinstance(o, dict) and str(o.get("job_id") or "") == media.stem), None
    )
    package = state.get("package") or {}
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
    details: dict[str, Any] = {
        "clip_id": item_id,
        "session": session,
        "kind": "film" if media.name == "film.mp4" else "clip",
        "idea": str(package.get("idea") or package.get("logline") or ""),
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
        "references": uploads,
        "deliverables": sorted(
            p.name
            for p in session_dir.iterdir()
            if p.is_file() and p.suffix in {".srt", ".vtt", ".json"} and p.name != "state.json"
        ),
        "bytes": media.stat().st_size,
    }
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


def stitch_items(item_ids: list[str], *, title: str = "") -> dict:
    """Join selected clips and films, in the given order, into one new film
    session (``join-*``) that lists and serves like any other."""
    if len(item_ids) < 2:
        raise ValueError("select at least two clips to stitch")
    ffmpeg = resolve_media_tool("ffmpeg")
    if not ffmpeg:
        raise RuntimeError(
            "ffmpeg was not found — install it (brew install ffmpeg) or set DIONY_FFMPEG"
        )
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
