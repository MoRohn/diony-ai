"""Two-tier model storage for leased Blackwell sites.

The attached volume is canonical and survives Pod stops.  The container overlay
is a bounded, disposable hot tier for the checkpoint families used by FARM.
Only Hugging Face snapshots with complete metadata are eligible for staging.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import shutil
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from diony_contracts.live import ckpt_dir


_GIB = 1024**3
_PREFETCH_LOCK = threading.Lock()
_PREFETCH: dict[str, threading.Thread] = {}


@dataclass(frozen=True)
class SnapshotContract:
    signature: str
    revision: str
    payload_bytes: int
    payload_files: int


def _number(name: str, default: float, *, minimum: float, maximum: float) -> float:
    try:
        value = float(os.environ.get(name, str(default)))
    except ValueError:
        value = default
    return min(maximum, max(minimum, value))


def _enabled() -> bool:
    raw = os.environ.get("DIONY_MODEL_HOT_CACHE", "auto").strip().lower()
    if raw in {"0", "false", "no", "off"}:
        return False
    if raw in {"1", "true", "yes", "on"}:
        return True
    try:
        from diony_contracts.cluster import site_status

        return str(site_status().get("path") or "").lower() in {"b200", "b300"}
    except Exception:
        return False


def _snapshot_contract(root: Path) -> SnapshotContract | None:
    if not root.is_dir():
        return None
    try:
        if any(path.is_file() for path in root.rglob("*.incomplete")):
            return None
    except OSError:
        return None
    metadata = sorted(root.rglob("*.metadata"))
    if not metadata:
        return None
    revisions: set[str] = set()
    rows: list[str] = []
    payload_bytes = 0
    payload_files = 0
    for meta in metadata:
        try:
            lines = meta.read_text(encoding="utf-8", errors="replace").splitlines()
            if len(lines) < 2:
                return None
            revision = lines[0].strip().lower()
            etag = lines[1].strip().lower()
            if len(revision) != 40 or any(ch not in "0123456789abcdef" for ch in revision):
                return None
            if len(etag) < 32 or any(ch not in "0123456789abcdef" for ch in etag):
                return None
            download = meta.parent
            while download.name != "download" and download != root:
                download = download.parent
            if download == root:
                return None
            relative = meta.relative_to(download)
            target = root / str(relative).removesuffix(".metadata")
            if not target.is_file() or target.stat().st_size <= 0:
                return None
            size = target.stat().st_size
        except (OSError, ValueError):
            return None
        revisions.add(revision)
        payload_bytes += size
        payload_files += 1
        rows.append(f"{relative}:{revision}:{etag}:{size}")
    if len(revisions) != 1:
        return None
    signature = hashlib.sha256("\n".join(rows).encode("utf-8")).hexdigest()
    return SnapshotContract(signature, next(iter(revisions)), payload_bytes, payload_files)


def snapshot_contract(root: Path) -> dict[str, object]:
    """Public, JSON-safe snapshot evidence used by readiness/diagnostics."""
    contract = _snapshot_contract(root)
    if contract is None:
        return {"complete": False, "signature": "", "revision": "", "bytes": 0, "files": 0}
    return {
        "complete": True,
        "signature": contract.signature,
        "revision": contract.revision,
        "bytes": contract.payload_bytes,
        "files": contract.payload_files,
    }


def _cache_root() -> Path:
    raw = os.environ.get("DIONY_MODEL_HOT_CACHE_ROOT", "/var/cache/diony/models/wan")
    return Path(raw).expanduser()


def _receipt(path: Path) -> dict:
    try:
        payload = json.loads((path / ".diony-hot-cache.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _cache_valid(path: Path, source: Path, contract: SnapshotContract) -> bool:
    row = _receipt(path)
    if row.get("source") != str(source.resolve()) or row.get("signature") != contract.signature:
        return False
    cached = _snapshot_contract(path)
    return bool(cached and cached.signature == contract.signature)


def _cached_entries(root: Path, *, exclude: Path) -> list[tuple[float, int, Path]]:
    rows: list[tuple[float, int, Path]] = []
    if not root.is_dir():
        return rows
    for path in root.iterdir():
        if not path.is_dir() or path == exclude or ".partial-" in path.name:
            continue
        receipt = _receipt(path)
        try:
            used = int(receipt.get("payload_bytes") or 0)
            touched = float(receipt.get("last_used_at") or path.stat().st_mtime)
        except (OSError, TypeError, ValueError):
            continue
        rows.append((touched, used, path))
    return sorted(rows)


def _room_for(
    root: Path,
    destination: Path,
    need: int,
    *,
    allow_evict: bool,
) -> bool:
    budget = int(_number("DIONY_MODEL_HOT_CACHE_GB", 320, minimum=0, maximum=2048) * _GIB)
    floor = int(
        _number("DIONY_MODEL_HOT_CACHE_MIN_FREE_GB", 64, minimum=0, maximum=512) * _GIB
    )
    if need <= 0 or need > budget:
        return False
    usage = shutil.disk_usage("/")
    if usage.free - need < floor:
        return False
    entries = _cached_entries(root, exclude=destination)
    used = sum(row[1] for row in entries)
    if used + need <= budget:
        return True
    if not allow_evict:
        return False
    for _touched, size, path in entries:
        shutil.rmtree(path, ignore_errors=True)
        used -= size
        if used + need <= budget:
            return True
    return used + need <= budget


def _copy_snapshot(source: Path, pending: Path) -> None:
    rsync = shutil.which("rsync")
    if rsync:
        proc = subprocess.run(
            [
                rsync,
                "-aH",
                "--delete",
                "--exclude=*.incomplete",
                "--exclude=*.lock",
                f"{source}/",
                f"{pending}/",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        if proc.returncode != 0:
            raise OSError((proc.stderr or proc.stdout or "rsync failed")[-1000:])
        return
    shutil.copytree(
        source,
        pending,
        symlinks=True,
        copy_function=shutil.copy2,
        dirs_exist_ok=True,
        ignore=shutil.ignore_patterns("*.incomplete", "*.lock"),
    )


def _prepare_checkpoint(
    task: str,
    *,
    source: Path,
    allow_evict: bool,
) -> Path:
    if not _enabled():
        return source
    contract = _snapshot_contract(source)
    if contract is None:
        return source
    root = _cache_root()
    destination = root / source.name
    root.mkdir(parents=True, exist_ok=True)
    lock_path = root / ".diony-hot-cache.lock"
    with lock_path.open("a+") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        if _cache_valid(destination, source, contract):
            receipt = _receipt(destination)
            receipt["last_used_at"] = time.time()
            (destination / ".diony-hot-cache.json").write_text(
                json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8"
            )
            return destination
        if not _room_for(root, destination, contract.payload_bytes, allow_evict=allow_evict):
            return source
        pending = root / f"{source.name}.partial-{os.getpid()}-{threading.get_ident()}"
        shutil.rmtree(pending, ignore_errors=True)
        pending.mkdir(parents=True)
        try:
            _copy_snapshot(source, pending)
            copied = _snapshot_contract(pending)
            if copied is None or copied.signature != contract.signature:
                raise OSError("hot-cache verification did not match the persistent snapshot")
            receipt = {
                "schema_version": 1,
                "task": task,
                "source": str(source.resolve()),
                "signature": contract.signature,
                "revision": contract.revision,
                "payload_bytes": contract.payload_bytes,
                "payload_files": contract.payload_files,
                "last_used_at": time.time(),
            }
            (pending / ".diony-hot-cache.json").write_text(
                json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8"
            )
            shutil.rmtree(destination, ignore_errors=True)
            pending.replace(destination)
            return destination
        except (OSError, subprocess.SubprocessError):
            shutil.rmtree(pending, ignore_errors=True)
            return source


def prepare_checkpoint(task: str, *, source: Path | None = None) -> Path:
    """Return a verified overlay checkpoint, or the persistent source on any miss."""
    canonical = source or ckpt_dir(task)
    return _prepare_checkpoint(task, source=canonical, allow_evict=True)


def prefetch_checkpoint(task: str, *, source: Path | None = None) -> None:
    """Stage one upcoming family without evicting anything a live job may hold."""
    canonical = source or ckpt_dir(task)
    with _PREFETCH_LOCK:
        active = _PREFETCH.get(task)
        if active is not None and active.is_alive():
            return

        def work() -> None:
            _prepare_checkpoint(task, source=canonical, allow_evict=False)

        thread = threading.Thread(target=work, name=f"diony-prefetch-{task}", daemon=True)
        _PREFETCH[task] = thread
        thread.start()


def wait_for_prefetch(task: str, *, timeout: float | None = None) -> None:
    """Test/diagnostic join; production never blocks on speculative prefetch."""
    with _PREFETCH_LOCK:
        thread = _PREFETCH.get(task)
    if thread is not None:
        thread.join(timeout=timeout)
