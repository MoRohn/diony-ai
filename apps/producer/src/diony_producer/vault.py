"""Local vault for idea refs and generated clip artifacts.

Laptop dry-run writes under apps/producer/data (gitignored).
On cluster this is the same layout on /diony/vault.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path

from diony_contracts.cluster import cluster_root
from diony_contracts.enums import ArtifactKind, AssetKind
from diony_contracts.models import Artifact, Asset, Project, Take

MAX_BYTES = 80 * 1024 * 1024
ALLOWED: dict[str, tuple[AssetKind, str]] = {
    ".jpg": (AssetKind.IMAGE, "image/jpeg"),
    ".jpeg": (AssetKind.IMAGE, "image/jpeg"),
    ".png": (AssetKind.IMAGE, "image/png"),
    ".webp": (AssetKind.IMAGE, "image/webp"),
    ".mp4": (AssetKind.VIDEO, "video/mp4"),
    ".mov": (AssetKind.VIDEO, "video/quicktime"),
    ".webm": (AssetKind.VIDEO, "video/webm"),
    ".wav": (AssetKind.AUDIO, "audio/wav"),
    ".flac": (AssetKind.AUDIO, "audio/flac"),
    ".m4a": (AssetKind.AUDIO, "audio/mp4"),
    ".mp3": (AssetKind.AUDIO, "audio/mpeg"),
    ".pdf": (AssetKind.DOCUMENT, "application/pdf"),
    ".txt": (AssetKind.DOCUMENT, "text/plain"),
    ".md": (AssetKind.DOCUMENT, "text/markdown"),
    ".docx": (
        AssetKind.DOCUMENT,
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ),
}

_SAFE = re.compile(r"[^A-Za-z0-9._-]+")


def vault_root() -> Path:
    """Same root as Studio site lock: DIONY_VAULT, then DIONY_PRODUCER_VAULT, then laptop data."""
    return cluster_root()


def _safe_name(filename: str) -> str:
    name = Path(filename).name
    if not name or name in {".", ".."}:
        raise ValueError("bad filename")
    cleaned = _SAFE.sub("-", name).strip(".-")
    if not cleaned:
        raise ValueError("bad filename")
    return cleaned[:120]


def classify(filename: str) -> tuple[AssetKind, str]:
    suffix = Path(filename).suffix.lower()
    if suffix not in ALLOWED:
        raise ValueError(f"unsupported file type {suffix or filename}")
    return ALLOWED[suffix]


def extract_text(filename: str, payload: bytes) -> str:
    suffix = Path(filename).suffix.lower()
    if suffix in {".txt", ".md"}:
        return payload.decode("utf-8", errors="replace")[:8000]
    if suffix == ".pdf":
        return ""
    if suffix == ".docx":
        return ""
    return ""


def save_upload(filename: str, payload: bytes, role: str = "idea_ref") -> Asset:
    if len(payload) > MAX_BYTES:
        raise ValueError("file is over 80 MB")
    if not payload:
        raise ValueError("empty file")
    kind, mime = classify(filename)
    safe = _safe_name(filename)
    digest = hashlib.sha256(payload).hexdigest()
    asset_id = f"ast-{digest[:12]}"
    rel = Path("assets") / asset_id / safe
    dest = vault_root() / rel
    dest.parent.mkdir(parents=True, exist_ok=True)
    if not dest.exists():
        dest.write_bytes(payload)
    return Asset(
        id=asset_id,
        kind=kind,
        filename=safe,
        mime=mime,
        bytes=len(payload),
        path=str(rel).replace("\\", "/"),
        sha256=digest,
        extracted_text=extract_text(safe, payload),
        role=role or "idea_ref",
    )


def abs_path(rel: str) -> Path:
    root = vault_root().resolve()
    path = (root / rel).resolve()
    if root not in path.parents and path != root:
        raise ValueError("path escapes vault")
    return path


def write_stub(rel: str, take_id: str, kind: str) -> int:
    dest = abs_path(rel)
    suffix = Path(rel).suffix.lower()
    if suffix in {".mp4", ".mov", ".webm", ".mkv"}:
        if dest.is_file() and dest.stat().st_size > 32:
            return dest.stat().st_size
        return 0
    dest.parent.mkdir(parents=True, exist_ok=True)
    body = (
        f"DIONY DRY-RUN ARTIFACT\n"
        f"take: {take_id}\n"
        f"kind: {kind}\n"
        f"not a master\n"
    ).encode()
    dest.write_bytes(body)
    return len(body)


def materialize_take(take: Take) -> None:
    if not take.artifacts:
        take.artifacts.append(
            Artifact(
                id=f"art-{take.id}-720",
                take_id=take.id,
                kind=ArtifactKind.NEGATIVE_720,
                path=f"takes/{take.id}/negative_720.txt",
                duration_frames=0,
            )
        )
        if take.asset_1080p_id:
            take.artifacts.append(
                Artifact(
                    id=f"art-{take.id}-1080",
                    take_id=take.id,
                    kind=ArtifactKind.FINISH_1080,
                    path=f"takes/{take.id}/finish_1080.txt",
                    width=1920,
                    height=1080,
                )
            )
    for art in take.artifacts:
        dest = vault_root() / art.path
        suffix = Path(art.path).suffix.lower()
        if suffix in {".mp4", ".mov", ".webm", ".mkv"}:
            if dest.is_file() and dest.stat().st_size > 32:
                art.bytes = dest.stat().st_size
                if not art.sha256:
                    art.sha256 = hashlib.sha256(dest.read_bytes()).hexdigest()
            continue
        size = write_stub(art.path, take.id, art.kind.value)
        art.bytes = size
        if not art.sha256:
            art.sha256 = hashlib.sha256((vault_root() / art.path).read_bytes()).hexdigest()


def materialize_project(project: Project) -> None:
    vault_root().mkdir(parents=True, exist_ok=True)
    for take in project.takes:
        materialize_take(take)


def list_vault_projects() -> list[Project]:
    """Every `projects/{id}/project.json` in this vault. Skip broken files."""
    root = vault_root() / "projects"
    if not root.is_dir():
        return []
    found: list[Project] = []
    for folder in sorted(root.iterdir()):
        path = folder / "project.json"
        if not path.is_file():
            continue
        try:
            found.append(Project.model_validate_json(path.read_text()))
        except (OSError, ValueError):
            continue
    return found


def persist_project(project: Project, *, set_current: bool = True) -> Path:
    """Write a project snapshot and optionally make it the current project."""
    root = vault_root()
    dest = root / "projects" / project.id / "project.json"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(project.model_dump_json(indent=2) + "\n")
    if set_current:
        pointer = root / ".cluster" / "current.json"
        pointer.parent.mkdir(parents=True, exist_ok=True)
        pointer.write_text(json.dumps({"project_id": project.id}) + "\n")
    return dest


def load_current_project() -> Project | None:
    pointer = vault_root() / ".cluster" / "current.json"
    if not pointer.is_file():
        return None
    try:
        data = json.loads(pointer.read_text())
    except json.JSONDecodeError:
        return None
    pid = str(data.get("project_id") or "").strip()
    if not pid:
        return None
    path = vault_root() / "projects" / pid / "project.json"
    if not path.is_file():
        return None
    return Project.model_validate_json(path.read_text())


def record_activity(src: str, text: str, *, level: str = "info", shot_id: str = "") -> None:
    """Append one camera-report line. Never uvicorn access logs."""
    msg = (text or "").strip()
    if not msg:
        return
    path = vault_root() / ".cluster" / "activity.jsonl"
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        row = {
            "at": datetime.now(timezone.utc).strftime("%H:%M:%S"),
            "src": src,
            "level": level,
            "text": msg[:400],
            "shot_id": shot_id,
        }
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
        raw = path.read_text(encoding="utf-8").splitlines()
        if len(raw) > 240:
            path.write_text("\n".join(raw[-160:]) + "\n", encoding="utf-8")
    except OSError:
        return


def clear_activity() -> None:
    """Drop camera-report lines so a new Idea does not inherit the last show."""
    path = vault_root() / ".cluster" / "activity.jsonl"
    try:
        if path.is_file():
            path.write_text("")
    except OSError:
        return
