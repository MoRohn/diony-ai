"""Signed cluster site lock. Studio writes it. Producer and Conductor read it.

Lives at `{vault}/.cluster/site.json`. Laptop dry-run uses the Producer data dir
unless DIONY_VAULT / DIONY_PRODUCER_VAULT is set.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

from diony_contracts.enums import ComputePath, FleetMode
from diony_contracts.live import inference_live
from diony_contracts.models import ClusterSite


# --- Production-engine choice (Blueprint 25) --------------------------------
# Written by Studio's opening screen, read by the Director and the desk.

# "local_legacy" was retired from the chooser (2026-08-31): the classic local
# rail is no longer a selectable production engine. A stale on-disk choice of
# it reads as no choice at all.
ENGINE_CHOICES = ("auto", "grok", "google-flow")


def engine_choice_path() -> Path:
    return cluster_root() / ".cluster" / "engine.json"


def engine_is_cloud() -> bool:
    """Blueprint 25: cloud is the primary engine unless DIONY_ENGINE says
    legacy. The ONE definition — desk, Producer, and Director all read this."""
    return os.environ.get("DIONY_ENGINE", "").strip().lower() != "local_legacy"


def read_engine_choice() -> str:
    """The chosen production engine, or "" when nothing valid is on disk."""
    try:
        payload = json.loads(engine_choice_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ""
    choice = str(payload.get("engine", "")) if isinstance(payload, dict) else ""
    return choice if choice in ENGINE_CHOICES else ""


def write_engine_choice(engine: str) -> Path:
    if engine not in ENGINE_CHOICES:
        raise ValueError(f"unknown production engine {engine!r}; known: {ENGINE_CHOICES}")
    path = engine_choice_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "engine": engine,
        "chosen_at": datetime.now(timezone.utc).isoformat(),
    }
    path.write_text(json.dumps(payload, indent=2) + chr(10), encoding="utf-8")
    return path

SITE_LOCK_REL = Path(".cluster") / "site.json"
MODE_LOCK_REL = Path(".cluster") / "mode.lock"

SITE_LABELS = {
    "local": "Local Quad-Spark Cluster",
    "dual": "Leased Dual-Spark Cluster",
    "b300": "Leased B300",
    "b200": "Leased B200",
    "lease": "Leased GPU",
}

SEQUENTIAL_PATHS = frozenset(
    {ComputePath.DUAL.value, ComputePath.B300.value, ComputePath.B200.value}
)
SINGLE_GPU_PATHS = frozenset({ComputePath.B300.value, ComputePath.B200.value})
WIZARD_APP_PATHS = frozenset(
    {ComputePath.DUAL.value, ComputePath.B300.value, ComputePath.B200.value}
)
RETAKE_CAP = 4
ENGINE_FOR_MODE = {
    "BRAIN": "brain",
    "SPLIT": "brain",
    "FARM": "wan",
    "FINISH": "finish",
}


def bind_host() -> str:
    """Listen address. Laptop dry-run is loopback. Dual-Spark spark-01 uses 0.0.0.0."""
    return os.environ.get("DIONY_BIND", "127.0.0.1").strip() or "127.0.0.1"


def cluster_root() -> Path:
    for key in ("DIONY_VAULT", "DIONY_PRODUCER_VAULT"):
        raw = os.environ.get(key, "").strip()
        if raw:
            return Path(raw)
    vault = Path("/diony/vault")
    if vault.is_dir():
        return vault
    # Editable checkout: packages/contracts/src/diony_contracts → repo
    repo = Path(__file__).resolve().parents[4]
    laptop = repo / "apps" / "producer" / "data"
    if (repo / "AGENTS.md").is_file() and (repo / "apps" / "producer").is_dir():
        return laptop
    return Path.cwd() / "apps" / "producer" / "data"


def site_lock_path() -> Path:
    return cluster_root() / SITE_LOCK_REL


def mode_lock_path() -> Path:
    return cluster_root() / MODE_LOCK_REL


def clear_local_locks() -> None:
    """Remove this desk's site.json and mode.lock. Does not touch the GPU vault."""
    for path in (site_lock_path(), mode_lock_path()):
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass


def write_site_lock(payload: dict) -> Path:
    path = site_lock_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    body = dict(payload)
    body.setdefault("written_at", datetime.now(timezone.utc).isoformat())
    path.write_text(json.dumps(body, indent=2) + "\n")
    return path


def read_site_lock() -> dict | None:
    path = site_lock_path()
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text())
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def site_from_lock(lock: dict | None = None) -> ClusterSite:
    lock = lock if lock is not None else read_site_lock()
    if not lock:
        return ClusterSite()
    compute = lock.get("compute") or {}
    path = str(lock.get("compute_path") or compute.get("path") or "local")
    try:
        compute_path = ComputePath(path)
    except ValueError:
        return ClusterSite()
    profile = str(lock.get("sku") or compute.get("profile") or "")
    kwargs: dict = {"path": compute_path}
    if profile:
        kwargs["profile"] = profile
    for key in ("provider", "gpu_class", "exception_by"):
        value = str(compute.get(key) or lock.get(key) or "").strip()
        if value:
            kwargs[key] = value
    if compute.get("exception_at"):
        kwargs["exception_at"] = compute["exception_at"]
    try:
        return ClusterSite(**kwargs)
    except Exception:
        return ClusterSite()


def site_status(lock: dict | None = None) -> dict:
    lock = lock if lock is not None else read_site_lock()
    site = site_from_lock(lock)
    path = site.path.value
    sequential = path in SEQUENTIAL_PATHS
    recipe = {
        "local": "spark-4x-v1 TP=4",
        "dual": "spark-2x-v1 TP=2",
        "b300": "b300-v1 TP=1",
        "b200": "b200-v1 TP=1",
        "lease": "lease-gpu-v1",
    }.get(path, site.profile)
    if lock and lock.get("recipe"):
        recipe = str(lock["recipe"])
    nodes = list(lock.get("nodes") or []) if lock else []
    if not nodes:
        if path == "dual":
            nodes = ["spark-01", "spark-02"]
        elif path == "b300":
            nodes = ["b300-01"]
        elif path == "b200":
            nodes = ["b200-01"]
        elif path == "local":
            nodes = ["spark-01", "spark-02", "spark-03", "spark-04"]
    clock = (
        "2–5 days for a 1-hour episode on spark-4x-v1"
        if path == "local"
        else "Sequential site. Do not quote the Quad-Spark 2–5 day SLA. Measure this pair."
    )
    return {
        "label": SITE_LABELS.get(path, path),
        "path": path,
        "profile": site.profile,
        "gpu_class": site.gpu_class,
        "nodes": nodes,
        "vault": str((lock or {}).get("vault") or "/diony/vault"),
        "recipe": recipe,
        "sequential": sequential,
        "split": (
            "One engine at a time (drain → unload → load)"
            if sequential
            else "2 brain + 2 Wan"
        ),
        "producer_url": str((lock or {}).get("producer_url") or "http://producer.diony.local"),
        "signed": bool(lock),
        "clock": clock,
        "negative": "720p Wan",
        "master": "1080 Diony finish",
        "dry_run": not inference_live(),
        "live": inference_live(),
    }


def write_mode_lock(mode: str, engine: str, *, reason: str = "") -> dict:
    site = site_from_lock()
    sequential = site.path.value in SEQUENTIAL_PATHS
    payload = {
        "mode": mode,
        "engine": engine,
        "sequential": sequential,
        "profile": site.profile,
        "reason": reason,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    path = mode_lock_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n")
    return payload


def read_mode_lock() -> dict | None:
    path = mode_lock_path()
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text())
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def refuse_concurrent(mode: str, engine: str) -> str | None:
    """Dual-Spark, B300, and B200 cannot run DeepSeek and Wan together."""
    site = site_from_lock()
    if site.path.value not in SEQUENTIAL_PATHS:
        return None
    current = read_mode_lock() or {}
    resident = str(current.get("engine") or "")
    if not resident or resident == engine or resident == "none":
        return None
    if {resident, engine} == {"brain", "wan"}:
        return (
            f"{SITE_LABELS.get(site.path.value, site.profile)} is sequential. "
            f"Drain {resident} before loading {engine}."
        )
    return None


def allowed_modes() -> list[str]:
    return [m.value for m in FleetMode]
