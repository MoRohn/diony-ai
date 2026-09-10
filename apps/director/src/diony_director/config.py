"""Local key loading.

Provider keys live in a local, untracked, chmod-600 file — never in the repo,
never in code. Default location ``~/.diony/keys.env`` (override with
``DIONY_KEYS_FILE``), simple ``KEY=VALUE`` lines, ``#`` comments. Explicit
environment always wins over the file.
"""

from __future__ import annotations

import os
from pathlib import Path

KEY_NAMES = (
    "XAI_API_KEY",
    "USEAPI_TOKEN",
    "DIONY_FLOW_EMAIL",
    "DIONY_FLOW_PLAN",
    "DIONY_DIRECTOR_BRAIN_URL",
    "DIONY_DIRECTOR_BRAIN_MODEL",
    "GEMINI_API_KEY",
    "DIONY_GEMINI_BASE",
    "DIONY_GEMINI_BRAIN_MODEL",
    "DIONY_GEMINI_TTS_MODEL",
    "DIONY_GEMINI_VOICE",
)


def keys_file() -> Path:
    raw = os.environ.get("DIONY_KEYS_FILE", "").strip()
    if raw:
        return Path(raw).expanduser()
    return Path.home() / ".diony" / "keys.env"


def load_keys_env() -> dict[str, str]:
    """Read the keys file into the environment (setdefault only). Returns what was applied."""

    path = keys_file()
    if not path.is_file():
        return {}
    applied: dict[str, str] = {}
    for raw_line in path.read_text().splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if not key or not value:
            continue
        if key not in os.environ:
            os.environ[key] = value
            applied[key] = value
    return applied
