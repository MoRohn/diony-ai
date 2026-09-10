"""Locate the ffmpeg tools the Director and the review families shell out to.

Resolution order: an explicit ``DIONY_FFMPEG`` / ``DIONY_FFPROBE`` (a PATH
name or an absolute file), then the shell PATH, then the packaged runtime and
the Finder-safe Mac locations a double-clicked app does not inherit.
"""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

MEDIA_TOOL_DIRS = (
    Path(sys.executable).resolve().parent,
    Path(sys.executable).resolve().parent.parent / "Resources" / "bin",
    Path("/opt/homebrew/bin"),
    Path("/usr/local/bin"),
)


def resolve_media_tool(name: str) -> str:
    """Find FFmpeg tools in shell, packaged, and Finder-safe Mac locations."""
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
    for folder in MEDIA_TOOL_DIRS:
        candidate = folder / name
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate)
    return ""
