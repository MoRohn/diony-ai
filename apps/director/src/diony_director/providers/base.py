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


class CloudAdapterTransientError(CloudAdapterError):
    """The network failed before the provider answered; the op stays runnable."""


class CloudAdapterQuotaError(CloudAdapterTransientError):
    """The provider's quota refused the request (HTTP 429). Nothing was spent
    and nothing is wrong with the op: it stays runnable and the wave resumes
    once the quota window resets — a quota pause must never fail a run."""

    def __init__(self, message: str, *, retry_at: float | None = None) -> None:
        super().__init__(message)
        self.retry_at = retry_at  # epoch seconds when the provider says to retry


def quota_retry_at(response: httpx.Response) -> float | None:
    """The reset time a 429 names, when it names one: an ISO `retryAfter` in
    the JSON body (useapi's Flow shape, top-level or nested under `response`)
    or a seconds-valued Retry-After header."""
    import time as time_mod
    from datetime import datetime, timezone

    try:
        body = response.json()
    except ValueError:
        body = None
    raw = ""
    if isinstance(body, dict):
        raw = str(body.get("retryAfter") or "")
        nested = body.get("response")
        if not raw and isinstance(nested, dict):
            raw = str(nested.get("retryAfter") or "")
    if raw:
        try:
            stamp = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            if stamp.tzinfo is None:
                stamp = stamp.replace(tzinfo=timezone.utc)
            return stamp.timestamp()
        except ValueError:
            pass
    header = response.headers.get("retry-after", "").strip()
    if header.isdigit():
        return time_mod.time() + int(header)
    return None


# Transport-level failures (SSL bad_record_mac, resets, timeouts) are retried
# in place with backoff. POSTs too: a mid-body TLS abort means the provider
# never parsed the request, and the bounded worst case of one duplicate clip
# beats permanently failing a chained wave — the same call the OpenAI-style
# SDKs make by default.
TRANSIENT_TRIES = 3
TRANSIENT_BACKOFF_S = 1.0


def _with_transient_retries(send, what: str):
    import time

    last: Exception | None = None
    for attempt in range(TRANSIENT_TRIES):
        try:
            return send()
        except httpx.TransportError as err:
            last = err
            if attempt + 1 < TRANSIENT_TRIES:
                time.sleep(TRANSIENT_BACKOFF_S * (2**attempt))
    raise CloudAdapterTransientError(
        f"{what} failed after {TRANSIENT_TRIES} attempts — {type(last).__name__}: {last}"
    ) from last


def get_with_retries(url: str, *, what: str = "provider request", **kwargs) -> httpx.Response:
    return _with_transient_retries(lambda: httpx.get(url, **kwargs), what)


def post_with_retries(url: str, *, what: str = "provider request", **kwargs) -> httpx.Response:
    return _with_transient_retries(lambda: httpx.post(url, **kwargs), what)


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
    response = get_with_retries(
        url, what="media download", timeout=DOWNLOAD_TIMEOUT_S, follow_redirects=True
    )
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
