"""DeepSeek prompt expand on the signed site. Never Dashscope."""

from __future__ import annotations

import os

import httpx

from diony_contracts.live import brain_base, inference_live
from diony_contracts.models import Bible, Shot
from diony_contracts.studio import expand_shot_prompt

SYSTEM = (
    "You write cinematic Wan2.2 camera prompts for Diony AI. "
    "Keep focal length, T-stop, lighting ratio, camera move, continuity. "
    "1080 finish-intent. No morph, no identity drift. "
    "Do not mention cloud APIs."
)


def brain_up() -> bool:
    if os.environ.get("DASH_API_KEY"):
        return False
    url = brain_base() + "/v1/models"
    try:
        res = httpx.get(url, timeout=2.0)
        return res.status_code < 500
    except httpx.HTTPError:
        return False


def expand_on_site(shot: Shot, bible: Bible) -> str:
    """High-effort expand via vLLM if live brain is up; else local grammar."""
    local = expand_shot_prompt(shot, bible)
    if not inference_live() and not os.environ.get("DIONY_BRAIN_URL"):
        return local
    if not brain_up():
        return local
    payload = {
        "model": os.environ.get("DIONY_BRAIN_MODEL", "DeepSeek-V4-Flash-0731"),
        "messages": [
            {"role": "system", "content": SYSTEM},
            {
                "role": "user",
                "content": (
                    f"World: {bible.world[:400]}\n"
                    f"Grammar: {bible.visual_grammar[:400]}\n"
                    f"Shot {shot.id} {shot.slug}: {shot.prompt}\n"
                    "Return only the extended prompt."
                ),
            },
        ],
        "temperature": 1.0,
        "top_p": 0.95,
        "max_tokens": 800,
        "extra_body": {"reasoning_effort": "high"},
    }
    try:
        res = httpx.post(
            brain_base() + "/v1/chat/completions",
            json=payload,
            timeout=120.0,
        )
        res.raise_for_status()
        text = res.json()["choices"][0]["message"]["content"].strip()
        return text or local
    except (httpx.HTTPError, KeyError, IndexError, TypeError):
        return local
