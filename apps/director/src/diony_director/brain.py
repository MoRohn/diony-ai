"""The Director brain: which model writes the film, and where it is reached.

The brain is any OpenAI-compatible chat-completions endpoint. Two are built
in — Grok on the xAI key the Grok camera uses, and Gemini on a Google AI
Studio key — so a desk that shoots on Google Flow can think on Google too.

Resolution, in order:

1. ``DIONY_DIRECTOR_BRAIN_URL`` set → that endpoint, verbatim (an empty value
   switches the brain OFF and the deterministic template writer runs).
2. The desk's brain choice (``{vault}/.cluster/brain.json``: ``auto`` |
   ``grok`` | ``gemini``) — a named brain is used when its key exists.
3. ``auto``: the camera's own house — Gemini on the Google Flow camera, Grok
   on the Grok camera — falling back to whichever brain key exists.

Nothing here guesses a key: a brain without its key is not configured, and
the Director says so instead of writing with the wrong account.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from diony_contracts.cluster import cluster_root, read_engine_choice

BRAIN_CHOICES = ("auto", "grok", "gemini")

XAI_BRAIN_BASE = "https://api.x.ai"
DEFAULT_GROK_BRAIN_MODEL = "grok-4.6"
GROK_BRAIN_MODEL_ENV = "DIONY_DIRECTOR_BRAIN_MODEL"

GEMINI_KEY_ENV = "GEMINI_API_KEY"
GEMINI_BASE_ENV = "DIONY_GEMINI_BASE"
GEMINI_BASE_DEFAULT = "https://generativelanguage.googleapis.com"
GEMINI_BRAIN_MODEL_ENV = "DIONY_GEMINI_BRAIN_MODEL"
DEFAULT_GEMINI_BRAIN_MODEL = "gemini-3.1-pro-preview"
RETIRED_GEMINI_BRAIN_MODELS = frozenset({"gemini-2.5-pro", "models/gemini-2.5-pro"})
GEMINI_OPENAI_PATH = "/v1beta/openai"


@dataclass(frozen=True)
class BrainTarget:
    provider: str  # "grok" | "gemini" | "custom" | "" (off)
    url: str  # the base the rest of the Director reports and checks for truthiness
    endpoint: str  # the chat-completions URL the calls go to
    model: str
    key: str
    reason: str = ""

    @property
    def configured(self) -> bool:
        return bool(self.url)


OFF = BrainTarget(provider="", url="", endpoint="", model="", key="", reason="brain off")


def gemini_base() -> str:
    return os.environ.get(GEMINI_BASE_ENV, "").strip().rstrip("/") or GEMINI_BASE_DEFAULT


def gemini_key() -> str:
    return os.environ.get(GEMINI_KEY_ENV, "").strip()


def gemini_configured() -> bool:
    return bool(gemini_key())


def gemini_model() -> str:
    """Resolve the Gemini writer model and migrate the retired 2.5 default.

    Existing keys files may pin Diony's former default. Google now returns a
    hard 404 for that model to new users, so retaining the obsolete value is
    never useful and would make an otherwise valid configuration fail forever.
    """
    configured = os.environ.get(GEMINI_BRAIN_MODEL_ENV, "").strip()
    if not configured or configured in RETIRED_GEMINI_BRAIN_MODELS:
        return DEFAULT_GEMINI_BRAIN_MODEL
    return configured


def grok_key() -> str:
    return os.environ.get("XAI_API_KEY", "").strip()


def grok_configured() -> bool:
    return bool(grok_key())


def available() -> dict[str, bool]:
    return {"grok": grok_configured(), "gemini": gemini_configured()}


def brain_choice_path() -> Path:
    return cluster_root() / ".cluster" / "brain.json"


def read_brain_choice() -> str:
    """The desk's brain preference, or ``auto`` when nothing valid is on disk."""
    try:
        payload = json.loads(brain_choice_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return "auto"
    choice = str(payload.get("brain", "")) if isinstance(payload, dict) else ""
    return choice if choice in BRAIN_CHOICES else "auto"


def write_brain_choice(choice: str) -> Path:
    normalized = str(choice or "auto").strip().lower() or "auto"
    if normalized not in BRAIN_CHOICES:
        raise ValueError(f"unknown brain {choice!r}; choose one of {BRAIN_CHOICES}")
    path = brain_choice_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"brain": normalized, "chosen_at": datetime.now(UTC).isoformat()}
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return path


def _grok_target(reason: str) -> BrainTarget:
    return BrainTarget(
        provider="grok",
        url=XAI_BRAIN_BASE,
        endpoint=f"{XAI_BRAIN_BASE}/v1/chat/completions",
        model=os.environ.get(GROK_BRAIN_MODEL_ENV, "").strip() or DEFAULT_GROK_BRAIN_MODEL,
        key=grok_key(),
        reason=reason,
    )


def _gemini_target(reason: str) -> BrainTarget:
    base = gemini_base() + GEMINI_OPENAI_PATH
    return BrainTarget(
        provider="gemini",
        url=base,
        endpoint=f"{base}/chat/completions",
        model=gemini_model(),
        key=gemini_key(),
        reason=reason,
    )


def _custom_target(base: str) -> BrainTarget:
    key = os.environ.get("DIONY_DIRECTOR_BRAIN_KEY", "").strip()
    if not key and "x.ai" in base:
        key = grok_key()
    if not key and "googleapis.com" in base:
        key = gemini_key()
    model = os.environ.get(GROK_BRAIN_MODEL_ENV, "").strip()
    if not model:
        model = gemini_model() if "googleapis.com" in base else DEFAULT_GROK_BRAIN_MODEL
    # an OpenAI-compatible base that already ends in /openai (Gemini's shape)
    # speaks at /chat/completions; every other base keeps the /v1 contract
    endpoint = (
        f"{base}/chat/completions" if base.endswith("/openai") else f"{base}/v1/chat/completions"
    )
    return BrainTarget(
        provider="custom",
        url=base,
        endpoint=endpoint,
        model=model,
        key=key,
        reason="DIONY_DIRECTOR_BRAIN_URL",
    )


def resolve() -> BrainTarget:
    raw = os.environ.get("DIONY_DIRECTOR_BRAIN_URL")
    if raw is not None:
        base = raw.strip().rstrip("/")
        return _custom_target(base) if base else OFF
    choice = read_brain_choice()
    camera = read_engine_choice()
    if choice == "grok":
        order = ["grok"]
    elif choice == "gemini":
        order = ["gemini"]
    elif camera == "google-flow":
        order = ["gemini", "grok"]
    else:
        order = ["grok", "gemini"]
    for name in order:
        if name == "gemini" and gemini_configured():
            why = "chosen" if choice == "gemini" else "the Google Flow camera's own brain"
            if choice == "auto" and camera != "google-flow":
                why = "the only brain key on this desk"
            return _gemini_target(why)
        if name == "grok" and grok_configured():
            why = "chosen" if choice == "grok" else "the Grok camera's own brain"
            if choice == "auto" and camera == "google-flow":
                why = "no GEMINI_API_KEY — the xAI key writes for the Google Flow camera"
            return _grok_target(why)
    if choice in {"grok", "gemini"}:
        return BrainTarget(
            provider="",
            url="",
            endpoint="",
            model="",
            key="",
            reason=f"{choice} chosen but its key is missing",
        )
    return OFF


def status() -> dict:
    target = resolve()
    return {
        "configured": target.configured,
        "provider": target.provider,
        "model": target.model,
        "url": target.url,
        "choice": read_brain_choice(),
        "available": available(),
        "reason": target.reason,
    }
