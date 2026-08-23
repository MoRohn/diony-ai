"""DeepSeek prompt expand on the signed site. Never Dashscope."""

from __future__ import annotations

import json
import os
import re

import httpx

from diony_contracts.enums import ReferenceLock
from diony_contracts.live import brain_base, inference_live
from diony_contracts.models import Bible, Shot
from diony_contracts.studio import expand_shot_prompt
from diony_contracts.wan_prompt import (
    make_wan_prompt_plan,
    render_wan_prompt,
)

SYSTEM = (
    "You refine typed Wan2.2 motion-picture prompt layers for Diony AI. "
    "Return one JSON object only with subject, motion, camera, scene, and "
    "environment_dynamics string fields. Use Subject, Motion, Camera, Aesthetic/Lighting, "
    "then Environment Dynamics reasoning. Motion must name pace, direction, and body "
    "mechanics. Scene must name lighting and physical texture. Environment dynamics must "
    "add subtle parallax without competing with the subject. "
    "Repeat the supplied subject and camera fields exactly; they are production locks. "
    "Do not change identity, story outcome, focal length, T-stop, camera move, references, "
    "or continuity. Do not mention cloud APIs."
)

_TEXTURE_CUES = (
    "texture",
    "grain",
    "wrinkle",
    "pores",
    "dust",
    "rain",
    "wet",
    "reflection",
    "palette",
    "atmospheric",
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


def _json_object(text: str) -> dict[str, object]:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"\s*```$", "", cleaned)
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("brain returned no JSON object")
    value = json.loads(cleaned[start : end + 1])
    if not isinstance(value, dict):
        raise ValueError("brain response is not an object")
    return value


def _apply_refinement(shot: Shot, candidate: dict[str, object]) -> str:
    locked = shot.prompt_plan
    if locked is None or shot.reference_lock != ReferenceLock.NONE:
        return shot.prompt
    motion = candidate.get("motion")
    scene = candidate.get("scene")
    environment_dynamics = candidate.get("environment_dynamics")
    if not all(isinstance(value, str) for value in (motion, scene, environment_dynamics)):
        return shot.prompt
    refined = make_wan_prompt_plan(
        source_idea_sha256=locked.source_idea_sha256,
        subject=locked.subject,
        motion=motion,
        camera=locked.camera,
        scene=scene,
        environment_dynamics=str(environment_dynamics),
        identity_anchors=locked.identity_anchors,
        high_noise_plan=locked.high_noise_plan,
        low_noise_plan=locked.low_noise_plan,
        generation_method=locked.generation_method,
        audio_strategy=locked.audio_strategy,
        quality_lora_ids=locked.quality_lora_ids,
        continuity_risks=locked.continuity_risks,
        negative_terms=locked.negative_terms,
        strategy_version=locked.strategy_version,
    )
    if refined.diagnostics:
        return shot.prompt
    scene_lower = refined.scene.casefold()
    if not any(cue in scene_lower for cue in _TEXTURE_CUES):
        return shot.prompt
    shot.prompt_plan = refined
    shot.prompt = render_wan_prompt(refined)
    return shot.prompt


def expand_on_site(shot: Shot, bible: Bible) -> str:
    """Refine typed layers on site; invalid output cannot replace the local plan."""
    local = expand_shot_prompt(shot, bible)
    if shot.prompt_plan is None or shot.reference_lock != ReferenceLock.NONE:
        return local
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
                    f"Shot {shot.id} {shot.slug}\n"
                    f"Locked plan: {shot.prompt_plan.model_dump_json()}\n"
                    "Return the refined five-field JSON object."
                ),
            },
        ],
        "temperature": 0.4,
        "top_p": 0.9,
        "max_tokens": 400,
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
        return _apply_refinement(shot, _json_object(text))
    except (httpx.HTTPError, json.JSONDecodeError, KeyError, IndexError, TypeError, ValueError):
        return local
