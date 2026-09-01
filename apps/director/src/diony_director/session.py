"""Session state and Guided-mode orchestration.

Single-shot proposals (M1) plus the scripted production flow (M2–M5):
produce → script gate → split → compile → priced ops → review → revisions
inside budgets. The raw idea is never the compiled prompt, and no priced op
exists without a locked estimate.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any

from diony_contracts.director import (
    CloudGenerationMode,
    CloudProvider,
    DirectorOp,
    DirectorOpType,
)
from diony_contracts.production import ClipReview, ClipUnit, ProductionPackage
from diony_director import compiler, packagebuilder, revisions, screenwriter
from diony_director import estimate as estimate_mod
from diony_director.screenwriter import ScreenplayDraft

DEFAULT_MODELS = {
    CloudProvider.GROK: "grok-imagine-video-1.5",
    CloudProvider.GOOGLE_FLOW: "veo-3.1-fast",
}

PROVIDER_LABELS = {
    CloudProvider.GROK: "Grok Imagine",
    CloudProvider.GOOGLE_FLOW: "Google Flow (Veo)",
}

_GROK_STYLE = (
    " Cinematic single shot: one motivated camera move, natural physics and weight, "
    "filmic light, ambient sound that belongs to the scene. "
    "No morphing, no floating motion, no extra limbs, no text overlays."
)
_VEO_STYLE = (
    " Shot on a cinema camera, 35mm lens, shallow depth of field, motivated practical "
    "light, natural color, restrained camera movement. No morphing, no warped anatomy, "
    "no on-screen text."
)


@dataclass
class Proposal:
    op: DirectorOp
    reply: str


@dataclass
class SessionState:
    id: str
    op_ids: list[str] = field(default_factory=list)


_SESSIONS: dict[str, SessionState] = {}
_OPS: dict[str, DirectorOp] = {}
_RUNTIME: dict[str, dict[str, Any]] = {}
_DRAFTS: dict[str, ScreenplayDraft] = {}
_PACKAGES: dict[str, ProductionPackage] = {}
_BUDGETS: dict[str, dict[str, Any]] = {}
_REVIEWS: dict[str, ClipReview] = {}
_REVISION_COUNTS: dict[str, int] = {}
_FAILURE_MEMORY: dict[str, set[str]] = {}
_SEQUENCES: dict[str, dict[str, Any]] = {}
_BATCH_TARGETS: dict[str, set[str]] = {}
_SUBMITTER_ACTIVE: dict[str, bool] = {}
_HELD_LOG: dict[str, list[dict[str, str]]] = {}
_UPLOADS: dict[str, list[dict[str, Any]]] = {}
_ACCEPTED: set[str] = set()

DEFAULT_BUDGET = {"max_usd_micros": 0, "max_revisions_per_clip": 3, "autopilot": False}


def reset() -> None:
    """Clear all in-process session state (test isolation and dev restarts)."""

    global _REHYDRATED
    _REHYDRATED = False
    for store in (
        _SESSIONS,
        _OPS,
        _RUNTIME,
        _DRAFTS,
        _PACKAGES,
        _BUDGETS,
        _REVIEWS,
        _REVISION_COUNTS,
        _FAILURE_MEMORY,
        _SEQUENCES,
        _BATCH_TARGETS,
        _SUBMITTER_ACTIVE,
        _HELD_LOG,
        _UPLOADS,
        _ACCEPTED,
    ):
        store.clear()


def create_session() -> str:
    sid = f"s-{uuid.uuid4().hex[:10]}"
    _SESSIONS[sid] = SessionState(id=sid)
    return sid


def get_session(sid: str) -> SessionState:
    return _SESSIONS[sid]


def resumable_sessions() -> list[dict[str, Any]]:
    """Sessions with unfinished business, newest first: failed clips to retry,
    ops still rolling, or approved work that never rolled. A finished session
    (everything completed) is not resumable — the Gallery has its film."""
    rows: list[dict[str, Any]] = []
    for sid, state in _SESSIONS.items():
        ops = [_OPS[i] for i in state.op_ids if i in _OPS]
        if not ops:
            continue
        counts = {"completed": 0, "failed": 0, "in_flight": 0, "proposed": 0}
        for op in ops:
            if op.status == "completed":
                counts["completed"] += 1
            elif op.status == "failed":
                counts["failed"] += 1
            elif op.status in {"submitted", "submitting"}:
                counts["in_flight"] += 1
            elif op.status == "proposed":
                counts["proposed"] += 1
        if counts["failed"] or counts["in_flight"]:
            rows.append({"session_id": sid, **counts})

    def _mtime(row: dict[str, Any]) -> float:
        try:
            return _state_path(row["session_id"]).stat().st_mtime
        except OSError:
            return 0.0

    rows.sort(key=_mtime, reverse=True)
    return rows


def get_op(op_id: str) -> DirectorOp:
    return _OPS[op_id]


def ops_for(sid: str) -> list[DirectorOp]:
    state = _SESSIONS.get(sid)
    return [_OPS[i] for i in state.op_ids] if state else []


def runtime(op_id: str) -> dict[str, Any]:
    return _RUNTIME.setdefault(op_id, {})


def committed_usd_micros(sid: str | None = None) -> int:
    """In-flight spend. Scoped to one session when `sid` is given — a budget
    ceiling is a per-session promise, never a workspace-lifetime one."""
    return sum(
        op.estimate.total_usd_micros
        for op in _OPS.values()
        if op.status in {"submitted", "submitting"}
        and op.estimate is not None
        and (sid is None or op.session_id == sid)
    )


def mark_accepted(op_id: str) -> None:
    """A human (or the settle rung of the fix ladder) stands behind this take."""
    _ACCEPTED.add(op_id)
    op = _OPS.get(op_id)
    if op is not None:
        persist(op.session_id)


def is_accepted(op_id: str) -> bool:
    return op_id in _ACCEPTED


def usable_take(op: DirectorOp) -> bool:
    """A completed take that may enter the film or seed a bridge frame: its
    review did not fail, or a human explicitly accepted it anyway."""
    if op.status != "completed":
        return False
    review = _REVIEWS.get(op.id)
    if review is not None and review.verdict == "fail" and op.id not in _ACCEPTED:
        return False
    return True


def _usd(micros: int) -> str:
    return f"${micros / 1_000_000:.2f}"


def _n(count: int, word: str) -> str:
    return f"{count} {word}" + ("" if count == 1 else "s")


def _runtime(seconds: float) -> str:
    minutes, secs = divmod(int(round(seconds)), 60)
    return f"{minutes}:{secs:02d}"


# --- Uploaded reference stills (the user's own look anchors) ----------------

MAX_UPLOAD_BYTES = 10 * 1024 * 1024


def _sniff_image(data: bytes) -> str:
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return ".png"
    if data.startswith(b"\xff\xd8\xff"):
        return ".jpg"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return ".webp"
    return ""


def add_upload(sid: str, *, name: str, data: bytes) -> dict[str, Any]:
    """Persist a user-supplied reference still. One-shot: the first upload
    opens the frame. Draft Script: uploads ride clip one as reference images
    for character/scene consistency."""
    ext = _sniff_image(data)
    if not ext:
        raise ValueError("only PNG, JPEG, or WebP stills can ride a shot")
    if len(data) > MAX_UPLOAD_BYTES:
        raise ValueError(
            f"that still is {len(data) / 1e6:.1f}MB — keep reference images under "
            f"{MAX_UPLOAD_BYTES // (1024 * 1024)}MB (export a JPEG)"
        )
    from diony_contracts.cluster import cluster_root

    uid = f"img-{uuid.uuid4().hex[:10]}"
    dest = cluster_root() / "director" / sid / "uploads" / f"{uid}{ext}"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(data)
    record = {
        "id": uid,
        "name": (name or "reference").strip()[:120] or "reference",
        "path": str(dest),
        "bytes": len(data),
    }
    _SESSIONS.setdefault(sid, SessionState(id=sid))
    _UPLOADS.setdefault(sid, []).append(record)
    persist(sid)
    return record


def uploads_for(sid: str) -> list[dict[str, Any]]:
    return list(_UPLOADS.get(sid) or [])


def remove_upload(sid: str, upload_id: str) -> bool:
    records = _UPLOADS.get(sid) or []
    for record in records:
        if record["id"] == upload_id:
            records.remove(record)
            from pathlib import Path

            Path(record["path"]).unlink(missing_ok=True)
            persist(sid)  # or the record resurrects on reboot pointing at a deleted file
            return True
    return False


def upload_refs(sid: str) -> tuple[str, list[str]]:
    ups = _UPLOADS.get(sid) or []
    return (ups[0]["path"] if ups else "", [u["path"] for u in ups[1:]])


def apply_uploads_to_refs(
    sid: str, clip: ClipUnit, package: ProductionPackage, refs: dict
) -> dict:
    """User-supplied stills anchor the film's first clip for character and
    scene consistency: they ride as reference images, never as a forced first
    frame. Grok drops references whenever a start_image is present, so a plate
    start_image gives way here — visibly in the refs, never silently at the
    adapter. Applied wherever refs are (re)computed, so late-binding at submit
    never loses them."""
    start, extras = upload_refs(sid)
    if not start or not package.clips or clip.clip_id != package.clips[0].clip_id:
        return refs
    uploads = [start] + extras
    merged = uploads + [r for r in (refs.get("references") or []) if r not in uploads]
    refs["references"] = merged[:3]
    refs.pop("start_image", None)
    return refs


def _idea_with_uploads(sid: str, idea: str) -> str:
    """Tell the screenwriter about supplied stills so it writes characters
    and settings consistent with them instead of inventing contradictory ones."""
    ups = _UPLOADS.get(sid) or []
    if not ups:
        return idea
    names = ", ".join(u["name"] for u in ups)
    return (
        idea
        + f"\n\n[Production note: the user supplied {_n(len(ups), 'reference image')} "
        f"({names}) showing the intended characters and setting. Keep the people, "
        "wardrobe, place, and light consistent with those images throughout — the "
        "film does not have to open on that exact frame.]"
    )


def resolve_provider(requested: str) -> CloudProvider:
    normalized = requested.strip().lower()
    if normalized in {"", "auto", "director"}:
        from diony_contracts.cluster import read_engine_choice

        choice = read_engine_choice()
        if choice == "google-flow":
            return CloudProvider.GOOGLE_FLOW
        return CloudProvider.GROK
    return CloudProvider(normalized)


def compile_prompt(idea: str, provider: CloudProvider) -> str:
    from diony_director.compiler import _fit_prompt, prompt_limit

    seed = idea.strip().rstrip(".")
    style = _GROK_STYLE if provider is CloudProvider.GROK else _VEO_STYLE
    return _fit_prompt([(f"{seed}.", 1), (style.strip(), 0)], prompt_limit(provider))


def propose(
    sid: str,
    text: str,
    *,
    provider: str = "auto",
    model: str = "",
    duration_s: float = 6.0,
    resolution: str = "720p",
    spent_usd_micros: int = 0,
) -> Proposal:
    chosen_provider = resolve_provider(provider)
    chosen_model = model or DEFAULT_MODELS[chosen_provider]
    duration = estimate_mod.quantize_duration(chosen_provider, chosen_model, duration_s)
    est = estimate_mod.estimate_op(
        provider=chosen_provider,
        model=chosen_model,
        mode=CloudGenerationMode.T2V,
        duration_s=duration,
        resolution=resolution,
    )
    op = DirectorOp(
        id=f"op-{uuid.uuid4().hex[:10]}",
        session_id=sid,
        op=DirectorOpType.GENERATE,
        provider=chosen_provider,
        model=chosen_model,
        mode=CloudGenerationMode.T2V,
        prompt=compile_prompt(text, chosen_provider),
        prompt_version="v1",
        duration_s=duration,
        resolution=resolution,
        estimate=est,
        rationale="single-shot draft; Grok is the iteration camera"
        if chosen_provider is CloudProvider.GROK
        else "single-shot draft on the requested Flow lane",
    )
    start, extras = upload_refs(sid)
    if start:
        op.artifact_refs["start_image"] = start
        if extras:
            op.artifact_refs["references"] = extras[:3]
    _OPS[op.id] = op
    _SESSIONS.setdefault(sid, SessionState(id=sid)).op_ids.append(op.id)
    persist(sid)

    total_after = spent_usd_micros + committed_usd_micros(sid) + est.total_usd_micros
    reply = (
        f"Reading that as one {duration}s {chosen_model} shot on "
        f"{PROVIDER_LABELS[chosen_provider]} at {resolution}. "
        f"This pass adds about {_usd(est.total_usd_micros)}; "
        f"session total becomes {_usd(total_after)}."
    )
    if start:
        count = 1 + len(extras)
        reply += (
            " Your uploaded still opens the frame."
            if count == 1
            else f" Your {count} uploaded stills ride along — the first opens the frame."
        )
    if est.rate_cards_stale:
        reply += " Heads up: the rate card is stale — re-verify provider pricing before running."
    return Proposal(op=op, reply=reply)


# --- Scripted production flow (M2–M5) --------------------------------------


def budget_for(sid: str) -> dict[str, Any]:
    return _BUDGETS.setdefault(sid, dict(DEFAULT_BUDGET))


def set_budget(sid: str, *, max_usd_micros: int, max_revisions_per_clip: int, autopilot: bool) -> None:
    _BUDGETS[sid] = {
        "max_usd_micros": max_usd_micros,
        "max_revisions_per_clip": max_revisions_per_clip,
        "autopilot": autopilot,
    }
    if sid in _SESSIONS:
        persist(sid)


def budget_allows(sid: str, next_cost: int, spent_and_committed: int) -> tuple[bool, str]:
    budget = budget_for(sid)
    ceiling = budget["max_usd_micros"]
    if ceiling and spent_and_committed + next_cost > ceiling:
        return (
            False,
            f"budget ceiling {_usd(ceiling)} would be breached "
            f"({_usd(spent_and_committed)} spent+committed, next {_usd(next_cost)})",
        )
    return True, ""


def _draft_reply(draft: ScreenplayDraft, runtime_s: int) -> str:
    scenes = len(draft.script.scenes)
    speaking = sum(1 for l in draft.script.lines if l.kind in {"dialogue", "vo"})
    reply = ""
    if draft.warnings:
        reply += "⚠ Template draft (no brain configured). "
    reply += (
        f"Script drafted: {_n(scenes, 'scene')}, {_n(speaking, 'spoken line')}, "
        f"targeting {_runtime(runtime_s)}. Review it — lock the script and I'll "
        f"split it into priced clips. Nothing rolls before the lock."
    )
    usage = getattr(draft, "brain_usage", None) or {}
    if usage.get("prompt_tokens") or usage.get("completion_tokens"):
        reply += (
            f" (Brain: {usage.get('prompt_tokens', 0):,} tokens in / "
            f"{usage.get('completion_tokens', 0):,} out — LLM spend is not yet on the receipt.)"
        )
    if draft.warnings:
        reply += " (" + "; ".join(draft.warnings) + ")"
    return reply


def start_production(sid: str, idea: str, runtime_s: int) -> tuple[ScreenplayDraft, str]:
    """Synchronous draft (template path and tests). Stores ready state."""
    draft = screenwriter.write_script(
        _idea_with_uploads(sid, idea),
        runtime_target_s=runtime_s,
        reference_images=[u["path"] for u in (_UPLOADS.get(sid) or [])],
    )
    reply = _draft_reply(draft, runtime_s)
    _DRAFTS[sid] = {"status": "ready", "draft": draft, "reply": reply, "error": ""}
    _SESSIONS.setdefault(sid, SessionState(id=sid))
    persist(sid)
    return draft, reply


def begin_draft(sid: str, idea: str, runtime_s: int) -> dict[str, Any]:
    """Drafting never blocks a request: a reasoning brain takes minutes, and a
    gateway timeout must never read as a failed draft. Template runs inline."""
    from diony_director.screenwriter import brain_model, brain_url

    _SESSIONS.setdefault(sid, SessionState(id=sid))
    if not brain_url():
        start_production(sid, idea, runtime_s)
        return draft_state(sid)

    import threading

    _DRAFTS[sid] = {"status": "drafting", "draft": None, "reply": "", "error": ""}

    def _worker() -> None:
        try:
            draft = screenwriter.write_script(
                _idea_with_uploads(sid, idea),
                runtime_target_s=runtime_s,
                reference_images=[u["path"] for u in (_UPLOADS.get(sid) or [])],
            )
            _DRAFTS[sid] = {
                "status": "ready",
                "draft": draft,
                "reply": _draft_reply(draft, runtime_s),
                "error": "",
            }
            persist(sid)
        except Exception as err:  # noqa: BLE001 - a failed draft is a named state
            _DRAFTS[sid] = {
                "status": "failed",
                "draft": None,
                "reply": "",
                "error": f"{type(err).__name__}: {err}",
            }

    threading.Thread(target=_worker, name=f"diony-draft-{sid}", daemon=True).start()
    state = draft_state(sid)
    state["reply"] = (
        f"Writing the screenplay on {brain_model()} — a reasoning model takes a "
        "minute or two. I'll put it up the moment it lands."
    )
    return state


def draft_state(sid: str) -> dict[str, Any]:
    record = _DRAFTS.get(sid)
    if not isinstance(record, dict):
        return {"status": "none", "reply": "", "error": "", "script": None,
                "characters": [], "settings": [], "warnings": []}
    draft = record.get("draft")
    return {
        "status": record["status"],
        "reply": record.get("reply", ""),
        "error": record.get("error", ""),
        "script": draft.script.model_dump(mode="json") if draft else None,
        "characters": [c.model_dump(mode="json") for c in draft.characters] if draft else [],
        "settings": [x.model_dump(mode="json") for x in draft.settings] if draft else [],
        "warnings": draft.warnings if draft else [],
    }


def _op_from_clip(sid: str, clip: ClipUnit, package: ProductionPackage) -> DirectorOp:
    if not clip.prompt_versions:
        clip.add_prompt(compiler.compile_clip_prompt(clip, package))
    version = clip.latest_prompt()
    est = estimate_mod.estimate_op(
        provider=clip.provider,
        model=clip.model,
        mode=clip.mode,
        duration_s=clip.duration_s,
        resolution=clip.resolution,
        image_inputs=estimate_mod.image_inputs_for(clip),
    )
    op = DirectorOp(
        id=f"op-{uuid.uuid4().hex[:10]}",
        session_id=sid,
        op=DirectorOpType.GENERATE,
        clip_id=clip.clip_id,
        provider=clip.provider,
        model=clip.model,
        mode=clip.mode,
        prompt=version.prompt,
        prompt_version=version.version,
        duration_s=clip.duration_s,
        resolution=clip.resolution,
        artifact_refs=apply_uploads_to_refs(
            sid, clip, package, compiler.provider_artifact_refs(clip, package)
        ),
        estimate=est,
        rationale=clip.camera_idea,
    )
    _OPS[op.id] = op
    _SESSIONS.setdefault(sid, SessionState(id=sid)).op_ids.append(op.id)
    return op


class DraftNotReady(RuntimeError):
    pass


class DraftLocked(RuntimeError):
    pass


class DraftInvalid(RuntimeError):
    pass


class ScriptAlreadyLocked(RuntimeError):
    pass


def _validate_draft(draft: ScreenplayDraft) -> None:
    """Refuse a structurally broken draft BEFORE anything locks — a brain that
    names a setting by its display name instead of its id must produce a named
    422, never a KeyError that bricks the session after the lock."""
    setting_ids = {s.setting_id for s in draft.settings}
    for scene in draft.script.scenes:
        if scene.setting_ref not in setting_ids:
            raise DraftInvalid(
                f"scene {scene.scene_id} names setting {scene.setting_ref!r}, which is not "
                f"one of the draft's setting ids ({', '.join(sorted(setting_ids))}) — "
                "redraft, or fix the scene"
            )
    has_speech = any(line.kind in {"dialogue", "vo"} for line in draft.script.lines)
    if has_speech and not draft.characters:
        raise DraftInvalid("the script has spoken lines but the draft names no characters")


EDITABLE_LINE_KINDS = {"action", "dialogue", "vo", "parenthetical"}


def edit_draft_line(sid: str, line_id: str, text: str) -> dict[str, Any]:
    """Change one line of a READY, UNLOCKED draft — the writer's pass before
    the money pass. Headings and cues hold the structure and stay fixed."""
    record = _DRAFTS.get(sid)
    if not isinstance(record, dict) or record.get("status") != "ready" or not record.get("draft"):
        raise DraftNotReady("no ready draft to edit — draft a script first")
    draft = record["draft"]
    if draft.script.locked:
        raise DraftLocked("the script is locked — nothing changes after the lock")
    line = draft.script.line(line_id)  # KeyError → 404 upstream
    if line.kind not in EDITABLE_LINE_KINDS:
        raise ValueError(f"a {line.kind} line holds the structure and cannot be edited")
    cleaned = " ".join(text.split())[:500]
    if not cleaned:
        raise ValueError("an empty line is a deletion, and deletions need a redraft")
    line.text = cleaned
    persist(sid)
    return draft_state(sid)


def lock_script(
    sid: str,
    spent_usd_micros: int = 0,
    *,
    lifetime_usd_micros: int = 0,
) -> tuple[ProductionPackage, list[DirectorOp], list[DirectorOp], str]:
    """Lock the draft: split, plan the bible, and compose ONE estimate that
    covers everything the batch will actually spend — clips, plates & sheets,
    and the revision allowance. `spent_usd_micros` is THIS session's spend;
    lifetime workspace spend is reported separately, never mixed in."""
    record = _DRAFTS.get(sid)
    if record is None:
        raise KeyError(f"session {sid} has no drafted script to lock")
    if isinstance(record, dict):
        if record["status"] == "drafting":
            raise DraftNotReady("the screenplay is still being written — a moment more")
        if record["status"] == "failed":
            raise DraftNotReady(f"the draft failed: {record.get('error', 'unknown')}")
        draft = record["draft"]
    else:
        draft = record
    if _PACKAGES.get(sid) is not None:
        # A second lock would re-split the same script into a duplicate set of
        # priced ops — and under autopilot, pay for the film twice.
        raise ScriptAlreadyLocked(
            "this script is already locked and priced — run the batch, or start "
            "a new session to re-plan"
        )
    _validate_draft(draft)
    from diony_contracts.cluster import read_engine_choice

    choice = read_engine_choice()
    policy = choice if choice in {"grok", "google-flow"} else "auto"
    state = _SESSIONS.setdefault(sid, SessionState(id=sid))
    ops_before = len(state.op_ids)
    try:
        package = packagebuilder.build_package(
            sid, draft.script.logline, draft, provider_policy=policy
        )
        _PACKAGES[sid] = package
        ops = [_op_from_clip(sid, clip, package) for clip in package.clips]
    except Exception:
        _rollback_partial_lock(sid, ops_before)
        raise
    # The lock lands only once the split has succeeded — a failed split must
    # leave the draft editable and re-lockable, never a bricked session.
    draft.script.lock()

    from diony_director import artifacts

    try:
        bible_ops = artifacts.plan_sheet_ops(sid, package) + artifacts.plan_plate_ops(sid, package)
    except Exception:
        draft.script.locked = False
        draft.script.locked_at = None
        _rollback_partial_lock(sid, ops_before)
        raise
    for bible_op in bible_ops:
        _OPS[bible_op.id] = bible_op
        _SESSIONS.setdefault(sid, SessionState(id=sid)).op_ids.append(bible_op.id)
    scene_chains: dict[str, list[str]] = {}
    for op in sorted(ops, key=lambda o: clip_for_op(o).order if clip_for_op(o) else 0):
        clip = clip_for_op(op)
        if clip is not None:
            scene_chains.setdefault(clip.scene_id, []).append(op.id)
    # Each character's sheet is a chain: the anchor still lands first, so every
    # later angle can reference it (five parallel portraits are five strangers).
    # Plates are independent single-op chains, mapped to the scenes they lock.
    sheet_chains: dict[str, list[str]] = {}
    plate_by_setting: dict[str, str] = {}
    plate_chains: list[list[str]] = []
    for bible_op in bible_ops:
        bible = bible_op.artifact_refs.get("bible") or {}
        if bible.get("char_id"):
            sheet_chains.setdefault(str(bible["char_id"]), []).append(bible_op.id)
        elif bible.get("setting_id"):
            plate_by_setting[str(bible["setting_id"])] = bible_op.id
            plate_chains.append([bible_op.id])
    scene_plates = {
        scene.scene_id: plate_by_setting[scene.setting_ref]
        for scene in package.script.scenes
        if scene.setting_ref in plate_by_setting
    }
    set_sequence(
        sid,
        [b.id for b in bible_ops],
        scene_chains,
        bible_chains=list(sheet_chains.values()) + plate_chains,
        scene_plates=scene_plates,
    )

    bible_cost = sum(b.estimate.total_usd_micros for b in bible_ops if b.estimate)
    packagebuilder.add_bible_line(package, bible_ops)
    total = package.estimate.total_usd_micros if package.estimate else 0
    clips_cost = sum(op.estimate.total_usd_micros for op in ops if op.estimate)
    allowance = package.estimate.revision_allowance_usd_micros if package.estimate else 0
    planned_s = sum(clip.duration_s for clip in package.clips)
    lines = [
        f"Script locked — {_n(len(ops), 'clip')} across "
        f"{_n(len(package.script.scenes), 'scene')}, about {_runtime(planned_s)} "
        "of picture planned.",
        f"Locked estimate {_usd(total)}:",
        f"   · {_n(len(ops), 'clip')} — {_usd(clips_cost)}",
    ]
    if bible_ops:
        lines.append(
            f"   · {_n(len(bible_ops), 'plate & character sheet still')} — {_usd(bible_cost)}"
        )
    lines.append(
        f"   · revision allowance ({package.revision_allowance_bp / 100:.0f}% of clips) "
        f"— {_usd(allowance)}"
    )
    lines.append(
        "Bible stills roll first; each clip chains from the last frame of the one before it."
    )
    persist(sid)
    ups = _UPLOADS.get(sid) or []
    if ups:
        lines.append(
            f"{_n(len(ups), 'uploaded reference image')} attached — "
            "anchoring clip one's characters and setting."
        )
    if spent_usd_micros > 0:
        lines.append(
            f"This session already has {_usd(spent_usd_micros)} on the ledger — "
            f"running everything brings it to {_usd(spent_usd_micros + total)}."
        )
    if lifetime_usd_micros > spent_usd_micros:
        lines.append(f"Workspace lifetime spend to date: {_usd(lifetime_usd_micros)}.")
    reply = "\n".join(lines)
    return package, ops, bible_ops, reply


def _rollback_partial_lock(sid: str, ops_before: int) -> None:
    """Undo everything a failed lock created, so a retry starts clean."""
    _PACKAGES.pop(sid, None)
    _SEQUENCES.pop(sid, None)
    state = _SESSIONS.get(sid)
    if state is not None:
        for op_id in state.op_ids[ops_before:]:
            _OPS.pop(op_id, None)
            _RUNTIME.pop(op_id, None)
        del state.op_ids[ops_before:]


def register_op(sid: str, op: DirectorOp) -> DirectorOp:
    """The one way an op joins a session — registered AND persisted, so no
    endpoint can mint a priced op that a restart forgets."""
    _OPS[op.id] = op
    _SESSIONS.setdefault(sid, SessionState(id=sid)).op_ids.append(op.id)
    persist(sid)
    return op


def package_for(sid: str) -> ProductionPackage | None:
    return _PACKAGES.get(sid)


def clip_for_op(op: DirectorOp) -> ClipUnit | None:
    package = _PACKAGES.get(op.session_id)
    if package is None or not op.clip_id:
        return None
    try:
        return package.clip(op.clip_id)
    except KeyError:
        return None


def register_review(op_id: str, review: ClipReview) -> None:
    _REVIEWS[op_id] = review
    op = _OPS.get(op_id)
    if op is not None:
        persist(op.session_id)


def review_for(op_id: str) -> ClipReview | None:
    return _REVIEWS.get(op_id)


def next_revision(sid: str, failed_op: DirectorOp, review: ClipReview) -> tuple[DirectorOp | None, str]:
    """Record the failure, then plan the cheapest lock-preserving fix."""

    package = _PACKAGES.get(sid)
    clip = clip_for_op(failed_op)
    memory = _FAILURE_MEMORY.setdefault(sid, set())
    memory.add(revisions.failure_key(failed_op))
    if package is None or clip is None:
        return None, "no production package behind this op — nothing to restage from"

    count = _REVISION_COUNTS.get(clip.clip_id, 0)
    op, narration = revisions.plan_revision(
        review,
        clip,
        package,
        session_id=sid,
        revision_count=count,
        failure_memory=memory,
        max_revisions_per_clip=int(budget_for(sid)["max_revisions_per_clip"]),
    )
    if op is None:
        # Settled: the best delivered take ships with its WARN on the record.
        if _RUNTIME.get(failed_op.id, {}).get("clip") is not None:
            mark_accepted(failed_op.id)
        return None, narration

    # Edit/extend need source media; without it the honest fallback is a
    # regenerate — stated, never silent. Sources are provider-shaped:
    # Flow extend wants the Flow media id of a Veo take; Flow v2v can also
    # ride an uploaded local clip; Grok chains from the persisted vault copy
    # (provider URLs expire before a revision ever runs).
    runtime = _RUNTIME.get(failed_op.id, {})
    clip_record = runtime.get("clip")
    local_path = str(getattr(clip_record, "path", "") or "")
    media_id = str(runtime.get("media_generation_id") or "")
    if op.op in {DirectorOpType.EDIT, DirectorOpType.EXTEND}:
        if op.provider is CloudProvider.GOOGLE_FLOW:
            if op.op is DirectorOpType.EXTEND:
                source = media_id
            else:
                source = media_id or local_path
        else:
            source = local_path or str(runtime.get("result_url") or "")
        if source:
            op.artifact_refs["source_video"] = source
        else:
            # No source media survived: re-plan a proper regenerate — the
            # clip's own model back (an extend had downgraded it), a fresh
            # estimate with no phantom input-video line, and honest narration.
            op.op = DirectorOpType.GENERATE
            op.mode = CloudGenerationMode.T2V
            op.model = clip.model
            op.resolution = clip.resolution
            op.artifact_refs = compiler.provider_artifact_refs(clip, package)
            op.estimate = estimate_mod.estimate_op(
                provider=op.provider,
                model=op.model,
                mode=op.mode,
                duration_s=op.duration_s,
                resolution=op.resolution,
                image_inputs=estimate_mod.image_inputs_for(clip, op.provider),
            )
            op.rationale += " (no source media survived — regenerating instead of editing)"
            narration = (
                f"{clip.clip_id} failed review, and no source media survived for an "
                f"edit — regenerating on {op.model} instead ({op.prompt_version}). "
                f"This pass adds about {_usd(op.estimate.total_usd_micros)}."
            )

    _REVISION_COUNTS[clip.clip_id] = count + 1
    clip.add_prompt(revisions.as_prompt_version(op))
    _OPS[op.id] = op
    _SESSIONS.setdefault(sid, SessionState(id=sid)).op_ids.append(op.id)
    # The revision takes the failed op's place in its scene chain: it inherits
    # the bridge position, and the successor re-bridges from the NEW take
    # instead of continuing from the rejected one.
    replace_in_sequences(sid, failed_op.id, op.id)
    persist(sid)
    return op, narration


def completed_clip_ops(sid: str) -> list[DirectorOp]:
    """Latest completed op per clip, in package clip order — the cut list."""

    package = _PACKAGES.get(sid)
    if package is None:
        return []
    latest: dict[str, DirectorOp] = {}
    for op in ops_for(sid):
        if op.clip_id and usable_take(op):
            latest[op.clip_id] = op
    return [latest[c.clip_id] for c in package.clips if c.clip_id in latest]


# --- Directed sequencing (Blueprint 26 bridge-lock, wired) ------------------


def _terminal(op: DirectorOp) -> bool:
    return op.status in {"completed", "failed", "discarded"}


def set_sequence(
    sid: str,
    bible_ids: list[str],
    scene_chains: dict[str, list[str]],
    *,
    bible_chains: list[list[str]] | None = None,
    scene_plates: dict[str, str] | None = None,
) -> None:
    """Directed order for a batch. `bible_chains` sequences the bible itself —
    a character's anchor still must land before its later angles roll, so the
    later slots can chain the anchor as a reference (an unanchored sheet is
    five unrelated portraits). `scene_plates` maps scene → its plate op, so a
    failed plate holds the scene instead of rolling it unlocked."""
    if bible_chains is None:
        bible_chains = [[op_id] for op_id in bible_ids]
    _SEQUENCES[sid] = {
        "bible": list(bible_ids),
        "bible_chains": [list(chain) for chain in bible_chains],
        "scenes": dict(scene_chains),
        "scene_plates": dict(scene_plates or {}),
        "held_scenes": set(),
    }


def sequence_for(sid: str) -> dict[str, Any] | None:
    return _SEQUENCES.get(sid)


def set_batch_targets(sid: str, op_ids: set[str]) -> set[str]:
    """Target ops for a directed batch; pending bible ops always ride along —
    no hero pixel rolls before its plates and sheets exist."""
    seq = _SEQUENCES.get(sid) or {"bible": [], "scenes": {}}
    targets = set(op_ids)
    for bible_id in seq["bible"]:
        op = _OPS.get(bible_id)
        if op is not None and not _terminal(op):
            targets.add(bible_id)
    _BATCH_TARGETS[sid] = targets
    return targets


def batch_targets(sid: str) -> set[str]:
    return _BATCH_TARGETS.get(sid, set())


def add_batch_target(sid: str, op_id: str) -> None:
    _BATCH_TARGETS.setdefault(sid, set()).add(op_id)


def queued_count(sid: str) -> int:
    return sum(
        1
        for op_id in batch_targets(sid)
        if (op := _OPS.get(op_id)) is not None and op.status == "proposed"
    )


def ready_ops(sid: str) -> list[DirectorOp]:
    """Targeted ops safe to submit now: bible first; then, per scene, the next
    clip only once its targeted predecessor is terminal (so the bridge frame
    from the previous accepted clip exists before the next clip rolls)."""
    seq = _SEQUENCES.get(sid)
    targets = batch_targets(sid)
    if not targets:
        return []
    if seq is None:
        return [
            _OPS[i] for i in targets if _OPS.get(i) is not None and _OPS[i].status == "proposed"
        ]
    ready: list[DirectorOp] = []
    bible_pending = False
    for chain in seq.get("bible_chains") or [[i] for i in seq["bible"]]:
        blocked = False
        for bible_id in chain:
            op = _OPS.get(bible_id)
            if op is None:
                continue
            if not _terminal(op):
                # A non-terminal bible op gates the clips whether or not it is
                # still targeted — a budget-HELD plate must protect its scenes
                # exactly the way a failed one does; clips never roll unplated.
                bible_pending = True
            if bible_id not in targets:
                continue
            if op.status == "proposed":
                if not blocked:
                    ready.append(op)
                blocked = True
            elif not _terminal(op):
                blocked = True
    if bible_pending:
        return ready
    for scene_id, chain in seq["scenes"].items():
        if _plate_failed(sid, seq, scene_id, chain):
            continue
        blocked = False
        for op_id in chain:
            op = _OPS.get(op_id)
            if op is None:
                continue
            if op.status == "proposed":
                if op_id in targets:
                    if not blocked:
                        ready.append(op)
                    blocked = True
                # an untargeted proposed op (e.g. a drafted revision awaiting a
                # manual run) does not block the chain behind it
            elif not _terminal(op):
                # anything in flight blocks its successors, targeted or not —
                # a revision mid-generation must land before the next bridge
                blocked = True
    return ready


def _plate_failed(sid: str, seq: dict[str, Any], scene_id: str, chain: list[str]) -> bool:
    """A scene whose setting plate failed is HELD, loudly — it never rolls
    unplated in silence. Its clips leave the batch targets so the batch can
    settle; the hold reason lands in the held log exactly once."""
    plate_id = (seq.get("scene_plates") or {}).get(scene_id, "")
    plate = _OPS.get(plate_id) if plate_id else None
    if plate is None or plate.status != "failed":
        return False
    held: set = seq.setdefault("held_scenes", set())
    targets = batch_targets(sid)
    if scene_id not in held:
        held.add(scene_id)
        log_held(
            sid,
            [{
                "op_id": chain[0] if chain else scene_id,
                "reason": (
                    f"scene {scene_id} held: its setting plate failed — "
                    "the scene will not roll unlocked; rerun the plate first"
                ),
            }],
        )
    for op_id in chain:
        targets.discard(op_id)
    return True


def replace_in_sequences(sid: str, old_id: str, new_id: str) -> None:
    seq = _SEQUENCES.get(sid)
    if seq is None:
        return
    for chain in seq["scenes"].values():
        for index, op_id in enumerate(chain):
            if op_id == old_id:
                chain[index] = new_id


def chain_position(sid: str, op_id: str) -> int | None:
    """This op's position inside its scene chain (0 = the scene opener)."""
    seq = _SEQUENCES.get(sid)
    if seq is None:
        return None
    for chain in seq["scenes"].values():
        if op_id in chain:
            return chain.index(op_id)
    return None


def previous_completed_clip_op(sid: str, op: DirectorOp) -> DirectorOp | None:
    """The nearest earlier completed clip in this op's scene chain — the bridge source."""
    seq = _SEQUENCES.get(sid)
    if seq is None:
        return None
    for chain in seq["scenes"].values():
        if op.id in chain:
            best: DirectorOp | None = None
            for op_id in chain:
                if op_id == op.id:
                    return best
                candidate = _OPS.get(op_id)
                if candidate is not None and usable_take(candidate):
                    best = candidate  # a review-failed take never seeds a bridge
    return None


# --- Persistence: a restart must never orphan paid work ---------------------
#
# Everything the audit's restart table listed as "in memory only" writes
# through to {vault}/director/<sid>/state.json (atomic tmp+rename) and
# rehydrates on boot: ops, job handles, fetched-clip records, the package,
# reviews, acceptances, revision counts, sequences, budgets, uploads, and a
# ready draft. The control plane keeps jobs and usage exactly as before —
# state.json is the Director's own working memory, beside the media it names.

_REHYDRATED = False
_PERSIST_LOCK: Any = None


def _persist_lock():
    global _PERSIST_LOCK
    if _PERSIST_LOCK is None:
        import threading

        _PERSIST_LOCK = threading.Lock()
    return _PERSIST_LOCK


def _state_path(sid: str):
    from diony_contracts.cluster import cluster_root

    return cluster_root() / "director" / sid / "state.json"


def persist(sid: str) -> None:
    """Write-through snapshot. Serialized under one lock: the submitter worker,
    the draft worker, and request threads all persist, and two concurrent
    writers on the same tmp file would corrupt the snapshot."""
    import json as _json

    state = _SESSIONS.get(sid)
    if state is None:
        return
    package = _PACKAGES.get(sid)
    clip_ids = {c.clip_id for c in package.clips} if package else set()
    draft_record = _DRAFTS.get(sid)
    draft_doc = None
    if (
        isinstance(draft_record, dict)
        and draft_record.get("status") == "ready"
        and draft_record.get("draft") is not None
    ):
        draft = draft_record["draft"]
        draft_doc = {
            "script": draft.script.model_dump(mode="json"),
            "characters": [c.model_dump(mode="json") for c in draft.characters],
            "settings": [s.model_dump(mode="json") for s in draft.settings],
            "warnings": list(draft.warnings),
            "brain_usage": dict(getattr(draft, "brain_usage", {}) or {}),
            "reply": draft_record.get("reply", ""),
        }
    runtime_doc: dict[str, dict] = {}
    for op_id in state.op_ids:
        rt = _RUNTIME.get(op_id) or {}
        entry: dict = {}
        handle = rt.get("handle")
        if handle is not None:
            entry["handle"] = {
                "provider_id": handle.provider_id,
                "external_id": handle.external_id,
                "model": handle.model,
                "mode": handle.mode,
                "resolution": handle.resolution,
                "duration_s": handle.duration_s,
                # copy, and drop sync_result_url: xAI result URLs expire, and a
                # rehydrated handle short-circuiting to a dead URL wedged batches
                "metadata": {
                    k: v for k, v in dict(handle.metadata).items() if k != "sync_result_url"
                },
            }
        clip = rt.get("clip")
        if clip is not None:
            entry["clip"] = {
                "path": str(clip.path),
                "sha256": clip.sha256,
                "bytes": clip.bytes,
                "media_url": clip.media_url,
                "metadata": dict(clip.metadata),
            }
        for key in ("media_generation_id", "result_url"):
            if rt.get(key):
                entry[key] = rt[key]
        if entry:
            runtime_doc[op_id] = entry
    seq = _SEQUENCES.get(sid)
    seq_doc = None
    if seq is not None:
        seq_doc = {**seq, "held_scenes": sorted(seq.get("held_scenes", set()))}
    doc = {
        "version": 1,
        "session_id": sid,
        "op_ids": list(state.op_ids),
        "ops": [_OPS[i].model_dump(mode="json") for i in state.op_ids if i in _OPS],
        "package": package.model_dump(mode="json") if package else None,
        "reviews": {i: _REVIEWS[i].model_dump(mode="json") for i in state.op_ids if i in _REVIEWS},
        "accepted": [i for i in state.op_ids if i in _ACCEPTED],
        "revision_counts": {c: n for c, n in _REVISION_COUNTS.items() if c in clip_ids},
        "failure_memory": sorted(_FAILURE_MEMORY.get(sid, set())),
        "sequence": seq_doc,
        "batch_targets": sorted(_BATCH_TARGETS.get(sid, set())),
        "budget": _BUDGETS.get(sid),
        "uploads": list(_UPLOADS.get(sid) or []),
        "runtime": runtime_doc,
        "draft": draft_doc,
    }
    path = _state_path(sid)
    try:
        with _persist_lock():
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_name("state.json.tmp")
            tmp.write_text(_json.dumps(doc))
            tmp.replace(path)
    except Exception:  # noqa: BLE001
        # A snapshot must never kill the path that called it — not on a disk
        # hiccup (OSError), and not on a collection mutated mid-dump by another
        # thread (RuntimeError): losing one write is fine, losing a submitted
        # op's handle because persist() blew up after the provider call is not.
        # The plane and the media on disk still hold the money truth.
        pass


def _load_state(sid: str, doc: dict) -> None:
    from diony_director.providers import base as provider_base
    from pathlib import Path as _Path

    from diony_director.screenwriter import ScreenplayDraft
    from diony_contracts.production import CharacterProfile, Script, SettingProfile

    _SESSIONS[sid] = SessionState(id=sid, op_ids=list(doc.get("op_ids") or []))
    for raw in doc.get("ops") or []:
        op = DirectorOp.model_validate(raw)
        _OPS[op.id] = op
        # a restart killed any in-flight submit thread; the claim is stale
        if op.status == "submitting":
            op.status = "proposed"
    if doc.get("package"):
        _PACKAGES[sid] = ProductionPackage.model_validate(doc["package"])
    for op_id, raw in (doc.get("reviews") or {}).items():
        _REVIEWS[op_id] = ClipReview.model_validate(raw)
    _ACCEPTED.update(doc.get("accepted") or [])
    _REVISION_COUNTS.update(doc.get("revision_counts") or {})
    if doc.get("failure_memory"):
        _FAILURE_MEMORY[sid] = set(doc["failure_memory"])
    if doc.get("sequence"):
        seq = dict(doc["sequence"])
        seq["held_scenes"] = set(seq.get("held_scenes") or [])
        _SEQUENCES[sid] = seq
    if doc.get("batch_targets"):
        _BATCH_TARGETS[sid] = set(doc["batch_targets"])
    if doc.get("budget"):
        _BUDGETS[sid] = dict(doc["budget"])
    if doc.get("uploads"):
        _UPLOADS[sid] = list(doc["uploads"])
    for op_id, entry in (doc.get("runtime") or {}).items():
        rt = _RUNTIME.setdefault(op_id, {})
        if entry.get("handle"):
            rt["handle"] = provider_base.CloudJobHandle(**entry["handle"])
        if entry.get("clip"):
            clip_doc = dict(entry["clip"])
            clip_doc["path"] = _Path(clip_doc["path"])
            rt["clip"] = provider_base.FetchedClip(**clip_doc)
        for key in ("media_generation_id", "result_url"):
            if entry.get(key):
                rt[key] = entry[key]
    if doc.get("draft"):
        draft_doc = doc["draft"]
        draft = ScreenplayDraft(
            script=Script.model_validate(draft_doc["script"]),
            characters=[CharacterProfile.model_validate(c) for c in draft_doc["characters"]],
            settings=[SettingProfile.model_validate(s) for s in draft_doc["settings"]],
            warnings=list(draft_doc.get("warnings") or []),
            brain_usage=dict(draft_doc.get("brain_usage") or {}),
        )
        # A rehydrated draft whose script was already locked keeps working:
        # lock_script re-locks idempotently and the package doc is authoritative.
        _DRAFTS[sid] = {
            "status": "ready",
            "draft": draft,
            "reply": draft_doc.get("reply", ""),
            "error": "",
        }


def rehydrate_from_vault() -> int:
    """Load every persisted session once per process. Corrupt state never
    kills a boot — that session simply stays cold and its jobs get reaped."""
    import json as _json

    global _REHYDRATED
    if _REHYDRATED:
        return 0
    _REHYDRATED = True
    from diony_contracts.cluster import cluster_root

    root = cluster_root() / "director"
    if not root.is_dir():
        return 0
    count = 0
    for state_file in sorted(root.glob("*/state.json")):
        sid = state_file.parent.name
        if sid in _SESSIONS:
            continue
        try:
            _load_state(sid, _json.loads(state_file.read_text()))
            count += 1
        except Exception:  # noqa: BLE001 - one corrupt file must not kill boot
            continue
    return count


def in_flight_jobs() -> int:
    """Ops with money at the provider right now — the desk asks before it
    would kill this process."""
    return sum(1 for op in _OPS.values() if op.status in {"submitted", "submitting"})


def submitter_active(sid: str) -> bool:
    return bool(_SUBMITTER_ACTIVE.get(sid))


def set_submitter_active(sid: str, active: bool) -> None:
    _SUBMITTER_ACTIVE[sid] = active


def log_held(sid: str, entries: list[dict[str, str]]) -> None:
    if entries:
        _HELD_LOG.setdefault(sid, []).extend(entries)


def drain_held(sid: str) -> list[dict[str, str]]:
    return _HELD_LOG.pop(sid, [])
