"""DeepSeek planning and prompt refinement on the signed site. Never Dashscope.

Planning is batched (one health check and ceil(n/8)+2 chat calls per roll, not
one call per shot), runs a draft -> critique -> revise loop, and reports every
outcome to the production activity feed. A brain failure falls back to the
compiled plans, visibly — a production can never silently become template-only.
"""

from __future__ import annotations

import json
import os
import re
import time

import httpx

from diony_contracts.enums import ReferenceLock
from diony_contracts.live import brain_base, inference_live
from diony_contracts.models import Bible, Project, Shot, Take
from diony_contracts.studio import expand_shot_prompt
from diony_contracts.wan_prompt import (
    make_wan_prompt_plan,
    render_negative_prompt,
    render_wan_prompt,
)
from diony_producer.vault import record_activity

# Blueprint 04: agentic sampling, wire-level reasoning_effort, generous output
# budget for high effort. `reasoning_effort` is a top-level request field on the
# vLLM OpenAI route — the SDK-style `extra_body` nesting never reaches the server.
BRAIN_TEMPERATURE = 1.0
BRAIN_TOP_P = 0.95
BATCH_SIZE = 8
SINGLE_MAX_TOKENS = 1536
BATCH_MAX_TOKENS = 6144
CALL_TIMEOUT = 180.0

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

BATCH_SYSTEM = (
    "You are the on-site DeepSeek shot planner for Diony AI, refining typed Wan2.2 "
    "prompt layers for a batch of shots in one production. Return one JSON object "
    'only: {"shots": [{"id", "subject", "motion", "scene", "environment_dynamics"}]}. '
    "For every shot: motion names pace, direction, and body mechanics as 2-3 temporal "
    "beats with physical weight; scene names motivated light and physical texture; "
    "environment_dynamics adds subtle parallax that never competes with the subject. "
    "Where a shot's locks mark subject locked, repeat the supplied subject exactly; "
    "where unlocked, you may sharpen the subject but every supplied identity anchor "
    "phrase must remain verbatim. Camera is always a production lock — never restate "
    "or change focal length, T-stop, or the camera move. Vary phrasing across shots so "
    "no two shots read as the same sentence. Never include production metadata, "
    "wardrobe authoring notes, or delivery-spec language. Do not mention cloud APIs."
)

CRITIC_SYSTEM = (
    "You are the prompt critic for Diony AI. You receive refined Wan2.2 prompt layers "
    "for a batch of shots. Audit each against this checklist: (1) no production "
    "metadata, sample labels, or wardrobe authoring notes; (2) no contradiction with "
    "the locked camera or the shot's negative conditioning; (3) motion carries pace, "
    "direction, body mechanics, and a temporal arc; (4) scene names motivated light "
    "and physical texture; (5) phrasing is not repeated across shots. Return one JSON "
    'object only: {"shots": [{"id", "verdict": "pass"|"revise", and when revising, '
    'any of "motion", "scene", "environment_dynamics" with the corrected text}]}. '
    "Revise only what the checklist fails; never touch subject or camera."
)

BIBLE_SYSTEM = (
    "You are the on-site DeepSeek showrunner for Diony AI. Given the production idea, "
    "write the project's own creative voice. Return one JSON object only: "
    '{"logline", "visual_grammar"}. The logline is one sentence, at most 240 '
    "characters. The visual grammar is 2-4 sentences of concrete, project-specific "
    "camera and light discipline (lens attitude, movement motivation, palette, "
    "texture) — not a generic style statement. Do not mention cloud APIs."
)

DIRECTOR_SYSTEM = (
    "You are the retake director for Diony AI. A Wan2.2 take failed measured QC. "
    "You receive the shot's five typed prompt layers, its negative conditioning, "
    "the QC failure notes with measured values, and what earlier attempts already "
    "tried. Address every reported symptom at once, not only the first. Return one "
    'JSON object only: {"motion"?, "scene"?, "environment_dynamics"?, "subject"?, '
    '"add_negative_terms"?: [up to 4 short visual terms], "rationale": one sentence}. '
    "Include only the fields you are changing. Camera is a production lock — never "
    "restate or change it. Subject may change only when locks.subject is false, and "
    "every identity anchor phrase must remain verbatim. Motion must keep pace, "
    "direction, and body mechanics. Scene must keep motivated light and physical "
    "texture. Never repeat direction an earlier attempt already tried. Do not "
    "mention cloud APIs."
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


def _chat(system: str, user: str, *, max_tokens: int) -> str:
    payload = {
        "model": os.environ.get("DIONY_BRAIN_MODEL", "DeepSeek-V4-Flash-0731"),
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "temperature": BRAIN_TEMPERATURE,
        "top_p": BRAIN_TOP_P,
        "max_tokens": max_tokens,
        "reasoning_effort": "high",
    }
    res = httpx.post(
        brain_base() + "/v1/chat/completions",
        json=payload,
        timeout=CALL_TIMEOUT,
    )
    res.raise_for_status()
    return str(res.json()["choices"][0]["message"]["content"]).strip()


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


def _apply_refinement(
    shot: Shot,
    candidate: dict[str, object],
    *,
    allow_subject: bool = False,
) -> str:
    locked = shot.prompt_plan
    if locked is None or shot.reference_lock != ReferenceLock.NONE:
        return shot.prompt
    motion = candidate.get("motion")
    scene = candidate.get("scene")
    environment_dynamics = candidate.get("environment_dynamics")
    if not all(isinstance(value, str) for value in (motion, scene, environment_dynamics)):
        return shot.prompt
    subject = locked.subject
    refined_subject = candidate.get("subject")
    if (
        allow_subject
        and isinstance(refined_subject, str)
        and refined_subject.strip()
        and all(
            anchor.casefold() in refined_subject.casefold()
            for anchor in locked.identity_anchors
        )
    ):
        subject = refined_subject.strip()
    refined = make_wan_prompt_plan(
        source_idea_sha256=locked.source_idea_sha256,
        subject=subject,
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


def _brain_reachable() -> bool:
    return bool(inference_live() or os.environ.get("DIONY_BRAIN_URL"))


def _dry_run() -> bool:
    return os.environ.get("DIONY_DRY_RUN", "").strip().lower() in {"1", "true", "yes", "on"}


def _planning_budget_seconds() -> float:
    try:
        value = float(os.environ.get("DIONY_BRAIN_PLANNING_BUDGET", "180"))
    except ValueError:
        return 180.0
    return min(1800.0, max(30.0, value))


def expand_on_site(shot: Shot, bible: Bible) -> str:
    """Refine typed layers on site; invalid output cannot replace the local plan."""
    local = expand_shot_prompt(shot, bible)
    if shot.prompt_plan is None or shot.reference_lock != ReferenceLock.NONE:
        return local
    if not _brain_reachable() or not brain_up():
        return local
    plan = shot.prompt_plan
    user = (
        f"World: {bible.world[:400]}\n"
        f"Grammar: {bible.visual_grammar[:400]}\n"
        f"Shot {shot.id} {shot.slug}\n"
        "Locked plan: "
        + json.dumps(
            {
                "subject": plan.subject,
                "motion": plan.motion,
                "camera": plan.camera,
                "scene": plan.scene,
                "environment_dynamics": plan.environment_dynamics,
                "identity_anchors": list(plan.identity_anchors),
            }
        )
        + "\nReturn the refined five-field JSON object."
    )
    try:
        text = _chat(SYSTEM, user, max_tokens=SINGLE_MAX_TOKENS)
        return _apply_refinement(shot, _json_object(text))
    except (httpx.HTTPError, json.JSONDecodeError, KeyError, IndexError, TypeError, ValueError):
        return local


def _shot_payload(shot: Shot) -> dict[str, object]:
    plan = shot.prompt_plan
    assert plan is not None
    return {
        "id": shot.id,
        "slug": shot.slug,
        "subject": plan.subject,
        "motion": plan.motion,
        "camera": plan.camera,
        "scene": plan.scene,
        "environment_dynamics": plan.environment_dynamics,
        "identity_anchors": list(plan.identity_anchors),
        "locks": {
            "subject": shot.reference_lock != ReferenceLock.NONE,
            "camera": True,
        },
    }


def _candidates_by_id(payload: dict[str, object]) -> dict[str, dict[str, object]]:
    rows = payload.get("shots")
    if not isinstance(rows, list):
        raise ValueError("brain batch returned no shots array")
    out: dict[str, dict[str, object]] = {}
    for row in rows:
        if isinstance(row, dict) and isinstance(row.get("id"), str):
            out[str(row["id"])] = row
    return out


def _refine_batch(batch: list[Shot], bible: Bible) -> int:
    """Draft -> critique -> revise for one batch. Returns refined-shot count."""
    context = (
        f"World: {bible.world[:400]}\n"
        f"Grammar: {bible.visual_grammar[:400]}\n"
        "Shots: "
    )
    draft_text = _chat(
        BATCH_SYSTEM,
        context + json.dumps([_shot_payload(shot) for shot in batch]),
        max_tokens=BATCH_MAX_TOKENS,
    )
    candidates = _candidates_by_id(_json_object(draft_text))
    refined = 0
    refined_shots: list[Shot] = []
    for shot in batch:
        candidate = candidates.get(shot.id)
        if candidate is None:
            continue
        before = shot.prompt
        after = _apply_refinement(shot, candidate, allow_subject=True)
        if after != before:
            refined += 1
            refined_shots.append(shot)
    if not refined_shots:
        return refined
    critique_rows = [
        {
            "id": shot.id,
            "subject": shot.prompt_plan.subject,
            "motion": shot.prompt_plan.motion,
            "camera": shot.prompt_plan.camera,
            "scene": shot.prompt_plan.scene,
            "environment_dynamics": shot.prompt_plan.environment_dynamics,
            "negative_terms": list(shot.prompt_plan.negative_terms)[:12],
        }
        for shot in refined_shots
        if shot.prompt_plan is not None
    ]
    try:
        critic_text = _chat(
            CRITIC_SYSTEM,
            "Shots: " + json.dumps(critique_rows),
            max_tokens=BATCH_MAX_TOKENS,
        )
        verdicts = _candidates_by_id(_json_object(critic_text))
    except (httpx.HTTPError, json.JSONDecodeError, KeyError, TypeError, ValueError):
        return refined
    for shot in refined_shots:
        verdict = verdicts.get(shot.id)
        if not verdict or verdict.get("verdict") != "revise":
            continue
        plan = shot.prompt_plan
        if plan is None:
            continue
        revision = {
            "motion": verdict.get("motion") if isinstance(verdict.get("motion"), str) else plan.motion,
            "scene": verdict.get("scene") if isinstance(verdict.get("scene"), str) else plan.scene,
            "environment_dynamics": (
                verdict.get("environment_dynamics")
                if isinstance(verdict.get("environment_dynamics"), str)
                else plan.environment_dynamics
            ),
        }
        _apply_refinement(shot, revision)
    return refined


def _refine_bible(project: Project) -> bool:
    if project.bible.locked:
        return False
    idea = project.brief.idea if project.brief is not None else project.bible.logline
    user = (
        f"Idea: {idea[:1200]}\n"
        f"World: {project.bible.world[:400]}\n"
        f"Current grammar: {project.bible.visual_grammar[:400]}\n"
        "Return the two-field JSON object."
    )
    payload = _json_object(_chat(BIBLE_SYSTEM, user, max_tokens=SINGLE_MAX_TOKENS))
    logline = payload.get("logline")
    grammar = payload.get("visual_grammar")
    changed = False
    if isinstance(logline, str) and 12 <= len(logline.strip()) <= 240:
        project.bible.logline = logline.strip()
        changed = True
    if isinstance(grammar, str) and 40 <= len(grammar.strip()) <= 900:
        project.bible.visual_grammar = grammar.strip()
        changed = True
    return changed


def _apply_directive(shot: Shot, directive: dict[str, object]) -> bool:
    """Apply a multi-symptom retake directive through the plan validators."""
    plan = shot.prompt_plan
    if plan is None:
        return False
    subject = plan.subject
    refined_subject = directive.get("subject")
    if (
        shot.reference_lock == ReferenceLock.NONE
        and isinstance(refined_subject, str)
        and refined_subject.strip()
        and all(
            anchor.casefold() in refined_subject.casefold()
            for anchor in plan.identity_anchors
        )
    ):
        subject = refined_subject.strip()
    motion = directive.get("motion") if isinstance(directive.get("motion"), str) else plan.motion
    scene = directive.get("scene") if isinstance(directive.get("scene"), str) else plan.scene
    environment_dynamics = (
        directive.get("environment_dynamics")
        if isinstance(directive.get("environment_dynamics"), str)
        else plan.environment_dynamics
    )
    negative_terms = list(plan.negative_terms)
    added = directive.get("add_negative_terms")
    if isinstance(added, list):
        for term in added[:4]:
            if isinstance(term, str) and 0 < len(term.strip()) <= 40:
                negative_terms.append(term.strip())
    if (
        subject == plan.subject
        and motion == plan.motion
        and scene == plan.scene
        and environment_dynamics == plan.environment_dynamics
        and negative_terms == list(plan.negative_terms)
    ):
        return False
    directed = make_wan_prompt_plan(
        source_idea_sha256=plan.source_idea_sha256,
        subject=subject,
        motion=str(motion),
        camera=plan.camera,
        scene=str(scene),
        environment_dynamics=str(environment_dynamics),
        identity_anchors=plan.identity_anchors,
        high_noise_plan=plan.high_noise_plan,
        low_noise_plan=plan.low_noise_plan,
        generation_method=plan.generation_method,
        audio_strategy=plan.audio_strategy,
        quality_lora_ids=plan.quality_lora_ids,
        continuity_risks=plan.continuity_risks,
        negative_terms=negative_terms,
        strategy_version=plan.strategy_version,
    )
    if directed.diagnostics:
        return False
    shot.prompt_plan = directed
    shot.prompt = render_wan_prompt(directed)
    shot.negative_prompt = render_negative_prompt(directed)
    return True


def direct_retake(project: Project, shot: Shot, take: Take) -> bool:
    """Evidence-driven retake direction; False falls back to the repair table."""
    if shot.prompt_plan is None or take.qc is None:
        return False
    if not _brain_reachable() or not brain_up():
        return False
    qc = take.qc
    prior_notes = [
        item.qc.notes[:200]
        for item in project.takes_for(shot.id)
        if item.id != take.id and item.qc is not None and item.qc.notes
    ]
    evidence = {
        "shot": _shot_payload(shot),
        "negative_terms": list(shot.prompt_plan.negative_terms),
        "attempt": take.attempt,
        "failure_notes": qc.notes[:600],
        "measurements": {
            "reference_similarity": qc.reference_similarity,
            "reference_similarities": dict(qc.reference_similarities),
            "continuity_similarity": qc.continuity_similarity,
            "temporal_delta_p95": qc.temporal_delta_p95,
            "temporal_residual_p95": qc.temporal_residual_p95,
            "flicker_luma_p95": qc.flicker_luma_p95,
            "lip_sync_confidence": qc.lip_sync_confidence,
            "audiovisual_offset_ms_p95": qc.audiovisual_offset_ms_p95,
        },
        "prior_attempt_notes": prior_notes[-3:],
    }
    try:
        payload = _json_object(
            _chat(
                DIRECTOR_SYSTEM,
                "Evidence: " + json.dumps(evidence),
                max_tokens=SINGLE_MAX_TOKENS,
            )
        )
    except (httpx.HTTPError, json.JSONDecodeError, KeyError, TypeError, ValueError):
        record_activity(
            "brain",
            f"{shot.id} · retake director unavailable; keyword repair table stands.",
            level="warn",
            shot_id=shot.id,
        )
        return False
    if not _apply_directive(shot, payload):
        return False
    rationale = payload.get("rationale")
    record_activity(
        "brain",
        (
            f"{shot.id} · DeepSeek retake directive applied"
            + (f": {str(rationale)[:140]}" if isinstance(rationale, str) else "")
        ),
        shot_id=shot.id,
    )
    return True


def plan_production(project: Project, shots: list[Shot]) -> None:
    """Batched DeepSeek planning before the farm rolls.

    Refines the project grammar and every pending shot's prompt plan in
    ceil(n/8) draft calls plus one critique call per productive batch. All
    outcomes land in the activity feed; compiled plans always stand as the
    validated fallback.
    """
    pending = [
        shot
        for shot in shots
        if shot.prompt_plan is not None and shot.reference_lock == ReferenceLock.NONE
    ]
    for shot in shots:
        # The compiled render is the guaranteed baseline for every shot,
        # including the reference-locked ones planning does not touch.
        shot.prompt = expand_shot_prompt(shot, project.bible)
    if not pending:
        return
    if _dry_run():
        # The laptop/desktop dry-run never has a brain by design; a warning on
        # every Produce would read as a fault to a test user.
        return
    if not _brain_reachable():
        record_activity(
            "brain",
            "DeepSeek planning skipped: no signed inference site; compiled plans stand.",
            level="warn",
        )
        return
    if not brain_up():
        record_activity(
            "brain",
            "DeepSeek planning skipped: brain is offline; compiled plans stand.",
            level="warn",
        )
        return
    # Planning runs inside the Produce request; a bounded wall-clock budget
    # keeps the roll responsive on large rundowns. Unplanned shots keep their
    # compiled plans — visibly, never silently.
    budget_seconds = _planning_budget_seconds()
    started = time.monotonic()
    try:
        if _refine_bible(project):
            record_activity("brain", "DeepSeek authored the project logline and visual grammar.")
    except (httpx.HTTPError, json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
        record_activity(
            "brain",
            f"DeepSeek bible pass fell back to the playbook grammar: {str(exc)[:160]}",
            level="warn",
        )
    refined_total = 0
    batches = [pending[i : i + BATCH_SIZE] for i in range(0, len(pending), BATCH_SIZE)]
    completed_batches = 0
    for index, batch in enumerate(batches, start=1):
        if time.monotonic() - started > budget_seconds:
            record_activity(
                "brain",
                (
                    f"DeepSeek planning budget ({budget_seconds:g}s) exhausted after "
                    f"{completed_batches}/{len(batches)} batches; remaining shots keep "
                    "compiled plans."
                ),
                level="warn",
            )
            break
        try:
            refined_total += _refine_batch(batch, project.bible)
        except (httpx.HTTPError, json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
            record_activity(
                "brain",
                f"DeepSeek batch {index}/{len(batches)} fell back to compiled plans: {str(exc)[:160]}",
                level="warn",
            )
        completed_batches = index
    record_activity(
        "brain",
        f"DeepSeek planning refined {refined_total}/{len(pending)} shots in {completed_batches} batches.",
        level="info" if refined_total else "warn",
    )
