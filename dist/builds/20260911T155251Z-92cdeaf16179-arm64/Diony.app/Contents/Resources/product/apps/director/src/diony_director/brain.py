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
import re
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
# gemini-2.5-pro answers 404 to keys created after Google closed it to new
# users ("This model models/gemini-2.5-pro is no longer available to new
# users. Please update your code to use models/gemini-3.1-pro-preview"), so
# the default is the model Google names — and follow_retirement() below keeps
# the desk on whatever Google names next without a rebuild.
DEFAULT_GEMINI_BRAIN_MODEL = "gemini-3.1-pro-preview"
GEMINI_OPENAI_PATH = "/v1beta/openai"

# Google's retirement rejection names the successor as ``models/<id>``.
_RETIREMENT_POINTER = re.compile(r"use\s+models/([A-Za-z0-9][A-Za-z0-9._-]*)")
# provider ("grok" | "gemini" | "custom" | "gemini-tts") -> the model a
# rejection named; process memory, so the next attempt already uses it
_learned_models: dict[str, str] = {}


def retirement_pointer(text: str) -> str:
    """The successor model a provider's rejection names ("Please update your
    code to use models/gemini-3.1-pro-preview"), or "" when the rejection
    is about something else."""
    match = _RETIREMENT_POINTER.search(text or "")
    return match.group(1) if match else ""


def follow_retirement(provider: str, status_code: int, text: str, current: str = "") -> str:
    """When a rejected call says the model was retired and names its
    successor, remember the successor for `provider` (the rest of this
    process resolves to it) and return it; "" when there is nothing to
    follow. An explicit model in the environment is never overridden."""
    if status_code not in (400, 404) or not provider:
        return ""
    successor = retirement_pointer(text)
    current = current or resolve().model
    if not successor or successor == current:
        return ""
    _learned_models[provider] = successor
    return successor


def learned_model(provider: str) -> str:
    return _learned_models.get(provider, "")


def forget_learned_models() -> None:
    _learned_models.clear()


def _with_learned(provider: str, env_name: str, default: str, reason: str) -> tuple[str, str]:
    """The model a house resolves to and the reason that names a switch:
    the environment's choice, else the successor a retirement named, else
    the default."""
    explicit = os.environ.get(env_name, "").strip()
    if explicit:
        return explicit, reason
    learned = learned_model(provider)
    if learned and learned != default:
        note = f"{default} retired by the provider; {learned} named in its place"
        return learned, f"{reason}; {note}" if reason else note
    return default, reason


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
    model, reason = _with_learned("grok", GROK_BRAIN_MODEL_ENV, DEFAULT_GROK_BRAIN_MODEL, reason)
    return BrainTarget(
        provider="grok",
        url=XAI_BRAIN_BASE,
        endpoint=f"{XAI_BRAIN_BASE}/v1/chat/completions",
        model=model,
        key=grok_key(),
        reason=reason,
    )


def _gemini_target(reason: str) -> BrainTarget:
    base = gemini_base() + GEMINI_OPENAI_PATH
    model, reason = _with_learned(
        "gemini", GEMINI_BRAIN_MODEL_ENV, DEFAULT_GEMINI_BRAIN_MODEL, reason
    )
    return BrainTarget(
        provider="gemini",
        url=base,
        endpoint=f"{base}/chat/completions",
        model=model,
        key=gemini_key(),
        reason=reason,
    )


def _custom_target(base: str) -> BrainTarget:
    key = os.environ.get("DIONY_DIRECTOR_BRAIN_KEY", "").strip()
    if not key and "x.ai" in base:
        key = grok_key()
    if not key and "googleapis.com" in base:
        key = gemini_key()
    default = DEFAULT_GEMINI_BRAIN_MODEL if "googleapis.com" in base else DEFAULT_GROK_BRAIN_MODEL
    model, reason = _with_learned(
        "custom", GROK_BRAIN_MODEL_ENV, default, "DIONY_DIRECTOR_BRAIN_URL"
    )
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
        reason=reason,
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
        # what each house would run — the desk's Brain pill names them
        "models": {"grok": _grok_target("").model, "gemini": _gemini_target("").model},
    }
