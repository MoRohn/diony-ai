"""Locate host ffmpeg tools used by assembly and the review families."""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path


def _windows_dirs() -> tuple[Path, ...]:
    env = os.environ
    homes = []
    if env.get("LOCALAPPDATA"):
        homes.append(Path(env["LOCALAPPDATA"]) / "Microsoft" / "WinGet" / "Links")
        homes.append(Path(env["LOCALAPPDATA"]) / "Programs" / "ffmpeg" / "bin")
    if env.get("ChocolateyInstall"):
        homes.append(Path(env["ChocolateyInstall"]) / "bin")
    if env.get("USERPROFILE"):
        homes.append(Path(env["USERPROFILE"]) / "scoop" / "shims")
    for root in (env.get("ProgramFiles"), env.get("ProgramFiles(x86)")):
        if root:
            homes.append(Path(root) / "ffmpeg" / "bin")
    homes.append(Path(r"C:\ffmpeg\bin"))
    return tuple(homes)


MEDIA_TOOL_DIRS = (Path("/opt/homebrew/bin"), Path("/usr/local/bin")) + _windows_dirs()


def resolve_media_tool(name: str) -> str:
    """Find an explicit tool, the shell PATH, or common host install locations."""
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
    names = (name, f"{name}.exe") if os.name == "nt" or sys.platform.startswith("win") else (name,)
    for folder in MEDIA_TOOL_DIRS:
        for filename in names:
            candidate = folder / filename
            if candidate.is_file() and os.access(candidate, os.X_OK):
                return str(candidate)
    return ""
