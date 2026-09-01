"""Shared adapter types and the one download path.

Provider result URLs are ephemeral. Nothing in Diony ever plays or stores a
provider URL: completed media is downloaded to the vault immediately and
content-hashed, and everything downstream refers to the local file.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

import httpx

DOWNLOAD_TIMEOUT_S = 300.0

# Provider prompt-length limits. xAI rejects over 4096 (seen live:
# "Prompt length exceeds the maximum allowed length of 4096"). Flow gets the
# same conservative cap until a documented figure says otherwise. Env-overridable.
PROMPT_LIMIT_DEFAULTS = {"grok": 4096, "google-flow": 4096}


def prompt_limit(provider_id: str) -> int:
    import os

    env_key = f"DIONY_PROMPT_LIMIT_{provider_id.replace('-', '_').upper()}"
    raw = os.environ.get(env_key, "").strip()
    if raw.isdigit() and int(raw) > 0:
        return int(raw)
    return PROMPT_LIMIT_DEFAULTS.get(provider_id, 4096)


def ensure_prompt_fits(prompt: str, provider_id: str, *, what: str = "prompt") -> None:
    """Last line of defense: an oversize prompt is a compiler bug, refused
    before any network call ever sees it."""
    limit = prompt_limit(provider_id)
    if len(prompt) > limit:
        raise CloudAdapterError(
            f"{what} is {len(prompt)} chars, over the {provider_id} limit of {limit} — "
            "the compiler must fit prompts before submit"
        )


class CloudAdapterError(RuntimeError):
    pass


class ModerationRefused(CloudAdapterError):
    pass


@dataclass
class CloudJobHandle:
    provider_id: str
    external_id: str
    model: str
    mode: str = "t2v"
    resolution: str = "720p"
    duration_s: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class CloudJobStatus:
    state: Literal["queued", "running", "done", "failed", "moderated", "expired"]
    detail: str = ""
    result_url: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def terminal(self) -> bool:
        return self.state in {"done", "failed", "moderated", "expired"}


@dataclass
class FetchedClip:
    path: Path
    sha256: str
    bytes: int
    media_url: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


def download(url: str, dest: Path) -> FetchedClip:
    response = httpx.get(url, timeout=DOWNLOAD_TIMEOUT_S, follow_redirects=True)
    if response.status_code >= 400:
        raise CloudAdapterError(f"media download rejected ({response.status_code}) from {url[:120]}")
    payload = response.content
    if not payload:
        raise CloudAdapterError(f"empty media body from {url}")
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(payload)
    return FetchedClip(
        path=dest,
        sha256=hashlib.sha256(payload).hexdigest(),
        bytes=len(payload),
        media_url=url,
    )
