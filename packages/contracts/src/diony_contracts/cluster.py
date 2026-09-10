"""The production-engine choice and the vault root (Blueprint 25).

Studio's opening page writes the choice; the Director and the desk read it.
The vault is where every clip, frame, film, and the control plane live.
"""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path

# The two cameras. Studio requires an explicit choice; there is no automatic route.
ENGINE_CHOICES = ("grok", "google-flow")


def engine_choice_path() -> Path:
    return cluster_root() / ".cluster" / "engine.json"


def engine_is_cloud() -> bool:
    """Cloud is the engine. ``DIONY_ENGINE=local_legacy`` is honoured only as
    the switch that makes the Director refuse every paid run."""
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
    payload = {"engine": engine, "chosen_at": datetime.now(UTC).isoformat()}
    path.write_text(json.dumps(payload, indent=2) + chr(10), encoding="utf-8")
    return path


def bind_host() -> str:
    """Listen address; loopback unless ``DIONY_BIND`` says otherwise."""
    return os.environ.get("DIONY_BIND", "127.0.0.1").strip() or "127.0.0.1"


def cluster_root() -> Path:
    """The vault: ``DIONY_VAULT``, then ``DIONY_PRODUCER_VAULT``, then
    ``/diony/vault`` when it exists, else ``apps/producer/data`` in a checkout."""
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
