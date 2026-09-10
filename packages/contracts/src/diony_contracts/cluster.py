"""The production-engine choice, the platform choice, and the vault root (Blueprint 25).

Studio's opening page writes the choice; the Director and the desk read it.
The vault is where every clip, frame, film, and the control plane live.
"""

from __future__ import annotations

import json
import os
import sys
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


# --- the computer this desk runs on ------------------------------------------
# The runtime already branches on the real OS (hosts file, privilege prompt,
# Python discovery, data directory). The CHOICE is what the product shows the
# user: paths, commands, launcher names. It defaults to the detected OS and
# the Studio toggle may override it (a Windows user reading on a Mac, a
# screenshot for the other desk).

PLATFORMS = ("mac", "windows")


def detected_platform() -> str:
    if sys.platform == "darwin":
        return "mac"
    if sys.platform.startswith("win") or os.name == "nt":
        return "windows"
    return "linux"


def platform_choice_path() -> Path:
    return cluster_root() / ".cluster" / "platform.json"


def read_platform_choice() -> str:
    """The platform the product speaks for: the stored toggle, else the
    detected OS (Linux reads as the Mac/Unix instructions)."""
    try:
        payload = json.loads(platform_choice_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        payload = {}
    choice = str(payload.get("platform", "")) if isinstance(payload, dict) else ""
    if choice in PLATFORMS:
        return choice
    return "windows" if detected_platform() == "windows" else "mac"


def write_platform_choice(platform: str) -> Path:
    normalized = str(platform or "").strip().lower()
    if normalized not in PLATFORMS:
        raise ValueError(f"unknown platform {platform!r}; choose one of {PLATFORMS}")
    path = platform_choice_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"platform": normalized, "chosen_at": datetime.now(UTC).isoformat()}
    path.write_text(json.dumps(payload, indent=2) + chr(10), encoding="utf-8")
    return path


def platform_hints(platform: str | None = None) -> dict:
    """Everything the product says that differs between a Mac and a Windows
    desk — one place, so every surface reads the same words."""
    which = platform or read_platform_choice()
    if which == "windows":
        return {
            "platform": "windows",
            "label": "Windows",
            "keys_path": r"C:\Users\<you>\.diony\keys.env",
            "keys_setup": [
                r"New-Item -ItemType Directory -Force $env:USERPROFILE\.diony | Out-Null",
                r"notepad $env:USERPROFILE\.diony\keys.env",
            ],
            "keys_setup_note": "PowerShell: make the folder, then paste the lines into keys.env and save.",
            "ffmpeg_install": "winget install Gyan.FFmpeg",
            "ffmpeg_check": "ffmpeg -version",
            "ffmpeg_hint": "install it (winget install Gyan.FFmpeg, then reopen the app) or set DIONY_FFMPEG to ffmpeg.exe",
            "launcher": "Open Diony AI.bat",
            "app": "the Windows installer (DionyAI-Setup-…-windows-x64.exe)",
            "names_prompt": "Windows asks once (User Account Control) so Diony can add studio, director, and producer .diony.local to the hosts file.",
            "data_dir": r"%LOCALAPPDATA%\Diony AI",
            "terminal": "PowerShell",
        }
    return {
        "platform": "mac",
        "label": "Mac",
        "keys_path": "~/.diony/keys.env",
        "keys_setup": [
            "mkdir -p ~/.diony && chmod 700 ~/.diony",
            "nano ~/.diony/keys.env",
            "chmod 600 ~/.diony/keys.env",
        ],
        "keys_setup_note": "Terminal: make the folder, paste the lines into keys.env, and keep it private.",
        "ffmpeg_install": "brew install ffmpeg",
        "ffmpeg_check": "ffmpeg -version",
        "ffmpeg_hint": "install it (brew install ffmpeg) or set DIONY_FFMPEG to its path",
        "launcher": "Open Diony AI.command",
        "app": "Diony.app (Build Diony AI.command builds it from a checkout)",
        "names_prompt": "macOS asks for your password once so Diony can add studio, director, and producer .diony.local to /etc/hosts.",
        "data_dir": "~/Library/Application Support/Diony AI",
        "terminal": "Terminal",
    }


def ffmpeg_install_hint(platform: str | None = None) -> str:
    return platform_hints(platform)["ffmpeg_hint"]
