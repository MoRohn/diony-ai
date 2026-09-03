"""Session state and Guided-mode orchestration.

Single-shot proposals (M1) plus the scripted production flow (M2–M5):
produce → script gate → split → compile → priced ops → review → revisions
inside budgets. The raw idea is never the compiled prompt, and no priced op
exists without a locked estimate.
"""

from __future__ import annotations

import math
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
from diony_contracts.production import LookBible, MasterPrompt
from diony_director import compiler, packagebuilder, revisions, screenwriter
from diony_director import estimate as estimate_mod
from diony_director import master_prompt as master_mod
from diony_director.screenwriter import ScreenplayDraft

DEFAULT_MODELS = {
    CloudProvider.GROK: "grok-imagine-video-1.5",
    # Omni 1.1 Flash is the Flow lane's model: audio-native video with voices,
    # characters, start + end frames, v2v editing, 10s clips at 360p/720p
    # (verified against useapi.net docs 2026-09-02). Veo tiers stay reachable
    # by explicit model choice and as the extension lane.
    CloudProvider.GOOGLE_FLOW: "omni-flash",
}

PROVIDER_LABELS = {
    CloudProvider.GROK: "Grok Imagine",
    CloudProvider.GOOGLE_FLOW: "Google Flow (Omni)",
}

_GROK_STYLE = (
    " Cinematic single shot: one motivated camera move, natural physics and weight, "
    "filmic light, ambient sound that belongs to the scene. "
    "No morphing, no floating motion, no extra limbs, no text overlays."
)
# The Veo style slot is filled by the Flow lexicon (world template or inline
# /world token); this guard rides every Flow chat proposal regardless of world.
_VEO_GUARD = "No morphing, no warped anatomy, no on-screen text."


@dataclass
class Proposal:
    op: DirectorOp
    reply: str
    # chained one-shot: every link in play order (op is the first); a
    # single-clip proposal leaves this empty
    ops: list[DirectorOp] = field(default_factory=list)
    # structured facts behind the reply — the room renders these as a plan
    # card (headline, stat tiles, notes) instead of a wall of prose; the
    # prose reply stays the canonical record and the fallback
    plan: dict[str, Any] | None = None


@dataclass
class SessionState:
    id: str
    op_ids: list[str] = field(default_factory=list)
    # The user walked away from this room on purpose (＋ NEW SESSION) — it
    # keeps its ledger and media but never claims the resume slot again.
    dismissed: bool = False


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
# Blueprint 27: the pre-split finalized artifact behind each one-shot op
_MASTERS: dict[str, MasterPrompt] = {}
_ACCEPTED: set[str] = set()

DEFAULT_BUDGET = {"max_usd_micros": 0, "max_revisions_per_clip": 3, "autopilot": False}

# The program raster is a session-level lock: every generation, bridge frame,
# plate conform, and the final assembly all cut to the SAME shape.
PROGRAM_ASPECTS: dict[str, tuple[int, int]] = {"16:9": (1280, 720), "9:16": (720, 1280)}
# The 1080p finish raster: the cut a film takes once its clips carry the
# server-side 1080p prints (Blueprint 30). Same shape lock, 1080 lines.
FINISH_ASPECTS: dict[str, tuple[int, int]] = {"16:9": (1920, 1080), "9:16": (1080, 1920)}
FINISH_RESOLUTION = "1080p"
# Upscales released per submit wave: Flow throttles an account that runs too
# many parallel requests (PUBLIC_ERROR_USER_REQUESTS_THROTTLED, ~30 min), and
# a 225-clip production must never fire 225 upscales in one wave.
DEFAULT_UPSCALE_PARALLELISM = 4
DEFAULT_ASPECT = "16:9"
_ASPECTS: dict[str, str] = {}


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
        _MASTERS,
        _ACCEPTED,
        _ASPECTS,
    ):
        store.clear()
    from diony_director import flow_lexicon, grok_lexicon

    flow_lexicon.reset_templates()
    grok_lexicon.reset_templates()


def create_session() -> str:
    sid = f"s-{uuid.uuid4().hex[:10]}"
    _SESSIONS[sid] = SessionState(id=sid)
    return sid


def session_exists(sid: str) -> bool:
    """Whether the sid names a live or rehydrated session — rehydration runs
    at boot, so an unknown sid here is a typo or a deleted room, not a
    not-yet-loaded one."""
    return sid in _SESSIONS


def get_session(sid: str) -> SessionState:
    return _SESSIONS[sid]


def resumable_sessions() -> list[dict[str, Any]]:
    """Sessions with unfinished business, newest first: failed clips to retry,
    ops still rolling, or approved work that never rolled. A finished session
    (everything completed) is not resumable — the Gallery has its film. A
    dismissed session is never resumable: failed clips have no expiry, so
    without the flag one dead take would reclaim the room on every boot."""
    rows: list[dict[str, Any]] = []
    for sid, state in _SESSIONS.items():
        if state.dismissed:
            continue
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


def dismiss_session(sid: str) -> None:
    """Retire the room from the resume slot — the ledger, media, and any
    still-rolling polls keep going exactly as before."""
    state = _SESSIONS[sid]
    state.dismissed = True
    persist(sid)


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
# A still is a LOOK/IDENTITY reference unless the user says it opens the
# frame. First-upload-opens was the 2026-09-02 film opening on a flat
# infographic meant as a structural reference for clip 6 (s-e790706066).
UPLOAD_ROLES = ("reference", "opener")


def _sniff_image(data: bytes) -> str:
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return ".png"
    if data.startswith(b"\xff\xd8\xff"):
        return ".jpg"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return ".webp"
    return ""


def add_upload(
    sid: str, *, name: str, data: bytes, role: str = "reference"
) -> dict[str, Any]:
    """Persist a user-supplied still. Every upload is a look-and-identity
    reference; exactly one may be marked the OPENER (the first frame of a
    one-shot). Draft Script: uploads anchor plates and clip one."""
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
        "role": role if role in UPLOAD_ROLES else "reference",
    }
    _SESSIONS.setdefault(sid, SessionState(id=sid))
    records = _UPLOADS.setdefault(sid, [])
    if record["role"] == "opener":
        for other in records:
            other["role"] = "reference"  # one opener, ever
    records.append(record)
    persist(sid)
    return record


def set_upload_role(sid: str, upload_id: str, role: str) -> dict[str, Any] | None:
    """Mark a still as the opener (the first frame) or back to a reference.
    Only one opener exists at a time."""
    if role not in UPLOAD_ROLES:
        raise ValueError(f"unknown still role {role!r} — expected one of {UPLOAD_ROLES}")
    records = _UPLOADS.get(sid) or []
    target = next((r for r in records if r["id"] == upload_id), None)
    if target is None:
        return None
    if role == "opener":
        for other in records:
            other["role"] = "reference"
    target["role"] = role
    persist(sid)
    return target


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
    """(the opener's path or '', every other still's path). Nothing opens the
    frame unless the user marked it — every unmarked still is a reference."""
    ups = _UPLOADS.get(sid) or []
    opener = next((u for u in ups if u.get("role") == "opener"), None)
    return (
        opener["path"] if opener else "",
        [u["path"] for u in ups if u is not opener],
    )


def all_upload_paths(sid: str) -> list[str]:
    return [u["path"] for u in (_UPLOADS.get(sid) or [])]


def apply_uploads_to_refs(
    sid: str, clip: ClipUnit, package: ProductionPackage, refs: dict
) -> dict:
    """User-supplied stills anchor the film's first clip for character and
    scene consistency: they ride as reference images, never as a forced first
    frame. Grok drops references whenever a start_image is present, so a plate
    start_image gives way here — visibly in the refs, never silently at the
    adapter. Applied wherever refs are (re)computed, so late-binding at submit
    never loses them."""
    uploads = all_upload_paths(sid)
    if not uploads or not package.clips or clip.clip_id != package.clips[0].clip_id:
        return refs
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


def authored_elements(sid: str, idea: str, provider: CloudProvider) -> tuple[str, dict[str, str]]:
    """What the user established outside the prose, in the route's dialect —
    the idea with its inline /shorthand expanded, and the session template
    clauses (camera, atmosphere, world) the master derivation must preserve
    rather than reinterpret. A token the route's lexicon doesn't know rides
    literally, exactly as it would in the compiled prompt."""
    seed = idea.strip()
    authored: dict[str, str] = {}
    if provider is CloudProvider.GROK:
        from diony_director import grok_lexicon

        expanded, camera_seen, atmo_seen = grok_lexicon.expand(seed)
        expanded = grok_lexicon.harden_camera_locks(expanded).strip()
        if not expanded and not (camera_seen or atmo_seen):
            expanded = seed
        if not camera_seen and not grok_lexicon.mentions_camera(expanded):
            camera = grok_lexicon.session_camera_clause(sid)
            if camera:
                authored["camera"] = grok_lexicon.sentence(camera)
        if not atmo_seen:
            atmosphere = grok_lexicon.session_atmosphere_clause(sid)
            if atmosphere:
                authored["atmosphere"] = grok_lexicon.sentence(atmosphere)
        return expanded or seed, authored
    from diony_director import flow_lexicon

    expanded, inline_world, has_camera = flow_lexicon.expand(seed)
    expanded = expanded.strip()
    if not expanded and not (inline_world or has_camera):
        expanded = seed
    if not has_camera and not flow_lexicon.mentions_camera(expanded):
        camera = flow_lexicon.session_camera_clause(sid)
        if camera:
            authored["camera"] = camera
    world = inline_world or flow_lexicon.template_for(sid)["world"]
    if world and world != flow_lexicon.BASELINE_WORLD:
        clause = flow_lexicon.worlds().get(world, {}).get("clause", "")
        if clause:
            authored["world"] = clause
    return expanded or seed, authored


def compile_master_body(
    body: str,
    idea: str,
    provider: CloudProvider,
    sid: str = "",
    *,
    has_start_image: bool = False,
    narration: str = "",
    look: LookBible | None = None,
) -> str:
    """Derived master prose (the whole-action prompt, or one chained clip's
    prompt) + the film's LOOK BIBLE, verbatim + the route's guards → the
    shippable prompt. The look rides at priority 0: length pressure shrinks
    the prose, never the one clause that keeps the film one film. A segment
    whose narration is recorded in post (Blueprint 28) tells the generator its
    location track carries no speech — the words never ride the prompt."""
    from diony_director.compiler import NO_SPEECH, _fit_prompt, prompt_limit

    body = body.strip()
    look_block = look.block() if look is not None else ""
    if provider is CloudProvider.GROK:
        from diony_director import grok_lexicon

        pieces: list[tuple[str, int]] = [(grok_lexicon.harden_camera_locks(body), 1)]
        if has_start_image:
            pieces.append((grok_lexicon.I2V_DIRECTIVE, 1))
        pieces.append((look_block, 0))
        if narration.strip():
            pieces.append((NO_SPEECH, 0))
        pieces.append((_GROK_STYLE.strip(), 0))
        return _fit_prompt(pieces, prompt_limit(provider))
    from diony_director import flow_lexicon

    _, inline_world, _ = flow_lexicon.expand(idea)
    pieces = [
        (body, 1),
        (look_block, 0),
        (_flow_style_slot(sid, inline_world, look), 0),
    ]
    if narration.strip():
        pieces.append((NO_SPEECH, 0))
    pieces.append((_VEO_GUARD, 0))
    return _fit_prompt(pieces, prompt_limit(provider))


def _flow_style_slot(sid: str, inline_world: str, look: LookBible | None) -> str:
    """An authored world (inline or chosen) always rides beside the look; the
    BASELINE cinema clause yields to a non-photoreal look — 'shot on a cinema
    camera, 35mm lens' over an animated film is a contradiction."""
    from diony_director import flow_lexicon

    clause = flow_lexicon.style_clause(sid, override_world=inline_world)
    if look is None or look.photoreal:
        return clause
    chosen = inline_world or flow_lexicon.template_for(sid)["world"]
    if chosen and chosen != flow_lexicon.BASELINE_WORLD:
        return clause
    return ""


def compile_prompt(
    idea: str,
    provider: CloudProvider,
    sid: str = "",
    *,
    has_start_image: bool = False,
    master: MasterPrompt | None = None,
) -> str:
    from diony_director.compiler import _fit_prompt, prompt_limit

    if master is not None:
        # The master prompt IS the body (Blueprint 27): provider-neutral prose
        # already carrying camera, lighting, physics, continuity, and ending.
        # Only the route's guards ride on top — the Grok lock hardening and
        # i2v directive, or the Flow world clause and anatomy guard.
        return compile_master_body(
            master.prompt,
            idea,
            provider,
            sid,
            has_start_image=has_start_image,
            narration=master.narration,
            look=master.look,
        )

    seed = idea.strip().rstrip(".")
    if provider is CloudProvider.GROK:
        # Grok speaks the Grok lexicon (never the Flow one — a Flow token
        # stays literal here). Motion/camera lead the prompt: the model
        # weighs the earliest words most. An opening still adds the i2v
        # directive — the model sees the frame; redescription is drift.
        from diony_director import grok_lexicon

        expanded, camera_seen, atmo_seen = grok_lexicon.expand(seed)
        expanded = grok_lexicon.harden_camera_locks(expanded).strip().rstrip(".")
        if not expanded and not (camera_seen or atmo_seen):
            expanded = seed  # nothing was shorthand — the idea stands as written
        pieces: list[tuple[str, int]] = []
        if expanded:
            pieces.append((f"{expanded}.", 1))
        if not camera_seen and not grok_lexicon.mentions_camera(expanded):
            # authored camera prose rules — a template camera stacked over
            # "camera pans across the harbor" is two moves in one prompt
            camera = grok_lexicon.session_camera_clause(sid)
            if camera:
                pieces.append((f"{grok_lexicon.sentence(camera)}.", 1))
        if not atmo_seen:
            atmosphere = grok_lexicon.session_atmosphere_clause(sid)
            if atmosphere:
                pieces.append((f"{grok_lexicon.sentence(atmosphere)}.", 1))
        if has_start_image:
            pieces.append((grok_lexicon.I2V_DIRECTIVE, 1))
        pieces.append((_GROK_STYLE.strip(), 0))
        return _fit_prompt(pieces, prompt_limit(provider))
    from diony_director import flow_lexicon

    expanded, inline_world, has_camera = flow_lexicon.expand(seed)
    expanded = expanded.strip().rstrip(".")
    if not expanded and not (inline_world or has_camera):
        expanded = seed  # nothing was shorthand — the idea stands as written
    pieces: list[tuple[str, int]] = []
    if expanded:
        # an idea that was ONLY tokens leaves this slot to the clauses below
        pieces.append((f"{expanded}.", 1))
    if not has_camera and not flow_lexicon.mentions_camera(expanded):
        # authored camera prose rules — same doctrine as the Grok lane
        camera = flow_lexicon.session_camera_clause(sid)
        if camera:
            pieces.append((f"{camera}.", 1))
    # The world/style clause is as inviolable as the old _VEO_STYLE was:
    # priority 0 so length pressure shrinks the idea, never ships a
    # half-truncated cinematography clause or drops the look entirely.
    pieces.append((flow_lexicon.style_clause(sid, override_world=inline_world), 0))
    pieces.append((_VEO_GUARD, 0))
    return _fit_prompt(pieces, prompt_limit(provider))


def _clip_plan(provider: CloudProvider, model: str, requested_s: float) -> list[int]:
    """The chained one-shot delivery plan. A booked runtime past the route's
    single-clip length is never silently capped: it splits into sequential
    links on the route's grid — [] means a single clip covers it."""
    requested = max(1, math.ceil(requested_s))
    if provider is CloudProvider.GROK:
        cap = estimate_mod.GROK_MAX_S
        if requested <= cap:
            return []
        count = math.ceil(requested / cap)
        base, remainder = divmod(requested, count)
        return [base + 1] * remainder + [base] * (count - remainder)
    grid = (
        estimate_mod.OMNI_DURATIONS
        if model in estimate_mod.OMNI_MODELS
        else estimate_mod.veo_grid()
    )
    unit = grid[-1]
    if requested <= unit:
        return []
    full, remainder = divmod(requested, unit)
    plan = [unit] * full
    if remainder:
        plan.append(estimate_mod.quantize_duration(provider, model, remainder))
    return plan


def _route_note_list(provider: CloudProvider, text: str) -> list[str]:
    """The lexicon advisories every proposal reply carries for its route."""
    notes: list[str] = []
    if provider is CloudProvider.GOOGLE_FLOW:
        from diony_director import flow_lexicon

        unknown = flow_lexicon.unknown_tokens(text)
        if unknown:
            notes.append(
                f"Note: {', '.join(unknown)} isn't Flow shorthand I know — "
                "it rides the prompt as literal text."
            )
        notes.extend(f"Note: {note}" for note in flow_lexicon.advisories(text))
    elif provider is CloudProvider.GROK:
        from diony_director import grok_lexicon

        unknown = grok_lexicon.unknown_tokens(text)
        if unknown:
            notes.append(
                f"Note: {', '.join(unknown)} isn't Grok shorthand I know — "
                "it rides the prompt as literal text."
            )
        notes.extend(f"Note: {note}" for note in grok_lexicon.advisories(text))
    return notes


def _plan_summary(
    *,
    headline: str,
    provider: CloudProvider,
    model: str,
    resolution: str,
    clip_seconds: list[int],
    pass_usd_micros: int,
    session_after_usd_micros: int,
    notes: list[str],
) -> dict[str, Any]:
    """The structured facts behind a proposal reply. The room renders these
    as a plan card — headline, stat tiles, note lines — so the numbers read
    at a glance; the prose reply remains the canonical record."""
    return {
        "headline": headline,
        "provider_label": PROVIDER_LABELS[provider],
        "model": model,
        "resolution": resolution,
        "clip_seconds": clip_seconds,
        "pass_usd_micros": pass_usd_micros,
        "session_after_usd_micros": session_after_usd_micros,
        "notes": notes,
    }


def _attach_stills(op: DirectorOp, start: str, extras: list[str], opens: str) -> str:
    """Attach the session's stills legally. The first still opens the frame;
    extras ride as identity refs ONLY on a model that accepts refs alongside a
    frame — on every current lane frames and identity refs are exclusive, and
    an op proposed with both could never submit (the legality gate refuses it
    after the money was already promised in the reply). Returns the reply
    fragment that says exactly what rode along."""
    from diony_director import capabilities

    if not start:
        if not extras:
            return ""
        # No opener marked: the stills ride as look-and-identity references
        # (legal without a frame on every lane) — the model composes the
        # shot IN their look instead of morphing away from one of them.
        op.artifact_refs["references"] = extras[:3]
        return (
            f" Your {plural_stills(len(extras))} ride {opens} as look and identity "
            "references — mark one as the opener if it should be the first frame."
        )
    op.artifact_refs["start_image"] = start
    if not extras:
        return f" Your uploaded still opens {opens}."
    caps = capabilities.caps_for(op.provider.value if op.provider else "", op.model)
    if caps is not None and not caps.frames_exclusive_with_refs:
        op.artifact_refs["references"] = extras[:3]
        return f" Your {1 + len(extras)} uploaded stills ride along — the first opens {opens}."
    return (
        f" Your first still opens {opens}; the other "
        f"{plural_stills(len(extras))} shaped the master derivation but can't ride the "
        f"shot — {op.model} takes an opening frame or identity refs, never both."
    )


def plural_stills(count: int) -> str:
    return "still" if count == 1 else f"{count} stills"


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
    plan = _clip_plan(chosen_provider, chosen_model, duration_s)
    if len(plan) > 1:
        return _propose_chain(
            sid,
            text,
            provider=chosen_provider,
            model=chosen_model,
            plan=plan,
            resolution=resolution,
            spent_usd_micros=spent_usd_micros,
        )
    duration = estimate_mod.quantize_duration(chosen_provider, chosen_model, duration_s)
    est = estimate_mod.estimate_op(
        provider=chosen_provider,
        model=chosen_model,
        mode=CloudGenerationMode.T2V,
        duration_s=duration,
        resolution=resolution,
    )
    # Uploads resolve BEFORE the prompt compiles: a Grok shot opening from a
    # still carries the i2v directive — the model sees the frame already.
    start, extras = upload_refs(sid)
    refs = ([start] if start else []) + list(extras)
    from diony_director.screenwriter import brain_model, brain_url

    # Blueprint 27: the idea never ships raw. The master prompt — the pre-
    # split finalized artifact — is derived from the idea, the stills, and
    # the user's DURATION, and becomes the prompt body. Brain off: inline,
    # deterministic. Brain on: the op is priced now and holds as "deriving"
    # (not runnable) until the artifact lands — a reasoning brain must never
    # ride a request through the name router.
    expanded, authored = authored_elements(sid, text, chosen_provider)
    async_master = bool(brain_url())
    master: MasterPrompt | None = None
    if not async_master:
        master = master_mod.derive(
            expanded, target_duration_s=duration, reference_images=refs, authored=authored
        )
    op = DirectorOp(
        id=f"op-{uuid.uuid4().hex[:10]}",
        session_id=sid,
        op=DirectorOpType.GENERATE,
        provider=chosen_provider,
        model=chosen_model,
        mode=CloudGenerationMode.T2V,
        prompt=compile_prompt(
            text, chosen_provider, sid, has_start_image=bool(start), master=master
        ),
        prompt_version="v1" if master is not None else "v0-provisional",
        status="deriving" if async_master else "proposed",
        duration_s=duration,
        resolution=resolution,
        estimate=est,
        rationale="single-shot draft; Grok is the iteration camera"
        if chosen_provider is CloudProvider.GROK
        else "single-shot draft on the requested Flow lane",
    )
    stills_note = _attach_stills(op, start, list(extras), "the frame")
    if master is not None:
        _MASTERS[op.id] = master
        op.narration = master.narration
        op.rationale += " · " + master_mod.duration_note(
            master, int(duration), cap_s=estimate_mod.GROK_MAX_S if chosen_provider is CloudProvider.GROK else 0
        )
    _OPS[op.id] = op
    _SESSIONS.setdefault(sid, SessionState(id=sid)).op_ids.append(op.id)
    persist(sid)
    if async_master:
        _kick_master_derivation(
            op.id, sid, text, expanded, chosen_provider, int(duration), refs, authored, bool(start)
        )

    total_after = spent_usd_micros + committed_usd_micros(sid) + est.total_usd_micros
    notes: list[str] = []
    if master is not None:
        notes.append(master_mod.summary_line(master))
        notes.append(
            master_mod.duration_note(
                master,
                int(duration),
                cap_s=estimate_mod.GROK_MAX_S if chosen_provider is CloudProvider.GROK else 0,
            )
        )
    else:
        notes.append(
            f"Deriving the master prompt on {brain_model()} — the card unlocks the "
            "moment the artifact lands; nothing can roll before it."
        )
    if stills_note:
        notes.append(stills_note.strip())
    if est.rate_cards_stale:
        notes.append("Heads up: the rate card is stale — re-verify provider pricing before running.")
    notes.extend(_route_note_list(chosen_provider, text))
    reply = " ".join(
        [
            f"Reading that as one {duration}s {chosen_model} shot on "
            f"{PROVIDER_LABELS[chosen_provider]} at {resolution}. "
            f"This pass adds about {_usd(est.total_usd_micros)}; "
            f"session total becomes {_usd(total_after)}.",
            *notes,
        ]
    )
    plan = _plan_summary(
        headline=f"Reading that as a single {duration}s take — one shot, priced before anything rolls.",
        provider=chosen_provider,
        model=chosen_model,
        resolution=resolution,
        clip_seconds=[int(duration)],
        pass_usd_micros=est.total_usd_micros,
        session_after_usd_micros=total_after,
        notes=notes,
    )
    return Proposal(op=op, reply=reply, plan=plan)


def _propose_chain(
    sid: str,
    text: str,
    *,
    provider: CloudProvider,
    model: str,
    plan: list[int],
    resolution: str,
    spent_usd_micros: int,
) -> Proposal:
    """The uncapped one-shot: a booked runtime past the route's single-clip
    length becomes a CHAIN of sequential clips. The brain splits the input
    itself (master derivation + CLIP DELIVERY) — no screenplay stage, no
    draft enhancement. Every link is priced now; each later link opens from
    the previous link's final frame at submit time."""
    from diony_director.screenwriter import brain_model, brain_url

    total_s = sum(plan)
    count = len(plan)
    start, extras = upload_refs(sid)
    refs = ([start] if start else []) + list(extras)
    expanded, authored = authored_elements(sid, text, provider)
    async_master = bool(brain_url())
    master: MasterPrompt | None = None
    if not async_master:
        master = master_mod.derive(
            expanded,
            target_duration_s=total_s,
            reference_images=refs,
            authored=authored,
            clip_plan=plan,
        )
    ops: list[DirectorOp] = []
    total_cost = 0
    stale = False
    stills_note = ""
    for index, clip_s in enumerate(plan):
        est = estimate_mod.estimate_op(
            provider=provider,
            model=model,
            mode=CloudGenerationMode.T2V,
            duration_s=clip_s,
            resolution=resolution,
        )
        total_cost += est.total_usd_micros
        stale = stale or est.rate_cards_stale
        first = index == 0
        has_start = bool(start) if first else True
        narration = ""
        if master is not None:
            narration = str(master.clips[index].get("narration", "") or "")
            prompt = compile_master_body(
                master.clips[index]["prompt"],
                text,
                provider,
                sid,
                has_start_image=has_start,
                narration=narration,
                look=master.look,
            )
        else:
            prompt = compile_prompt(text, provider, sid, has_start_image=has_start)
        op = DirectorOp(
            id=f"op-{uuid.uuid4().hex[:10]}",
            session_id=sid,
            op=DirectorOpType.GENERATE,
            provider=provider,
            model=model,
            mode=CloudGenerationMode.T2V,
            prompt=prompt,
            narration=narration,
            prompt_version="v1" if master is not None else "v0-provisional",
            status="deriving" if async_master else "proposed",
            duration_s=clip_s,
            resolution=resolution,
            estimate=est,
            rationale=(
                f"clip {index + 1}/{count} of a chained one-shot ({clip_s}s)"
                + (
                    " — opens the chain"
                    if first
                    else " — opens from the previous clip's final frame"
                )
            ),
        )
        if first and (start or extras):
            stills_note = _attach_stills(op, start, list(extras), "clip 1")
        _OPS[op.id] = op
        if master is not None:
            _MASTERS[op.id] = master
        ops.append(op)
    state = _SESSIONS.setdefault(sid, SessionState(id=sid))
    state.op_ids.extend(op.id for op in ops)
    _register_oneshot_chain(sid, [op.id for op in ops])
    frames = _plan_style_frames(sid, ops, master) if master is not None else []
    persist(sid)
    if async_master:
        _kick_chain_derivation(
            [op.id for op in ops],
            sid,
            text,
            expanded,
            provider,
            plan,
            refs,
            authored,
            bool(start),
        )

    total_after = spent_usd_micros + committed_usd_micros(sid) + total_cost
    plan_words = " + ".join(f"{d}s" for d in plan)
    notes: list[str] = []
    if master is not None:
        notes.append(master_mod.summary_line(master))
        notes.append(master_mod.duration_note(master, total_s))
        frames_note = _style_frames_note(frames).strip()
        if frames_note:
            notes.append(frames_note)
    else:
        notes.append(
            f"Deriving the master prompt and its {count}-clip split on {brain_model()} — "
            "the cards unlock the moment the artifact lands; nothing can roll before it. "
            "Every clip carries the film's Look Bible verbatim, and a clip that cuts to a "
            "new place opens from a style frame composed in that look (a still, priced "
            "when the split lands)."
        )
    if stills_note:
        notes.append(stills_note.strip())
    if stale:
        notes.append("Heads up: the rate card is stale — re-verify provider pricing before running.")
    notes.extend(_route_note_list(provider, text))
    reply = " ".join(
        [
            f"That plays past a single clip on this route — reading it as a chained "
            f"one-shot: {count} sequential {model} clips on {PROVIDER_LABELS[provider]} "
            f"({plan_words} = {total_s}s) at {resolution}, each clip opening from the "
            f"previous clip's final frame. This pass adds about {_usd(total_cost)}; "
            f"session total becomes {_usd(total_after)}. Run the clips in order — "
            "each unlocks when the one before it lands.",
            *notes,
        ]
    )
    plan_card = _plan_summary(
        headline=(
            "That plays past a single clip — reading it as a chained one-shot, each "
            "clip opening from the previous clip's final frame. Run them in order; "
            "each unlocks when the one before it lands."
        ),
        provider=provider,
        model=model,
        resolution=resolution,
        clip_seconds=[int(s) for s in plan],
        pass_usd_micros=total_cost,
        session_after_usd_micros=total_after,
        notes=notes,
    )
    return Proposal(op=ops[0], reply=reply, ops=ops, plan=plan_card)


def _plan_style_frames(sid: str, ops: list[DirectorOp], master: MasterPrompt) -> list[DirectorOp]:
    """Blueprint 29: every chain link that CUTS to a new place or subject opens
    from a STYLE FRAME — a still the image lane composes in the film's look,
    with the look's anchors and the previous link's last frame as references
    — instead of the previous link's last frame itself, which is something to
    morph away from. Priced image ops, one per scene change, registered in
    the session and tied to their link; they roll at the link's submit.
    Idempotent: a link that already has its frame op keeps it."""
    from diony_director import artifacts, look as look_mod

    look = master.look
    if look is None or not look.block():
        return []
    lane_provider, lane_model = artifacts.sheet_lane()
    resolution = "2k" if lane_provider is CloudProvider.GROK else "image"
    state = _SESSIONS.setdefault(sid, SessionState(id=sid))
    created: list[DirectorOp] = []
    for index, op in enumerate(ops):
        if index == 0 or index >= len(master.clips):
            continue  # the opener opens the film; it is never a cut
        clip = master.clips[index]
        if not clip.get("scene_change"):
            continue
        rt = runtime(op.id)
        if rt.get("style_frame_op") in _OPS:
            continue
        est = estimate_mod.estimate_op(
            provider=lane_provider,
            model=lane_model,
            mode=CloudGenerationMode.IMAGE,
            duration_s=1,
            resolution=resolution,
        )
        frame_op = DirectorOp(
            id=f"op-{uuid.uuid4().hex[:10]}",
            session_id=sid,
            op=DirectorOpType.REIMAGE,
            provider=lane_provider,
            model=lane_model,
            mode=CloudGenerationMode.IMAGE,
            prompt=look_mod.style_frame_prompt(str(clip.get("opening") or ""), look),
            prompt_version="v1",
            duration_s=1,
            resolution=resolution,
            artifact_refs={"style_frame": {"for_op": op.id, "clip_index": index}},
            estimate=est,
            rationale=(
                f"style frame · clip {index + 1}/{len(ops)} cuts to a new place — its "
                "opening frame is composed in the film's look before the clip rolls"
            ),
        )
        _OPS[frame_op.id] = frame_op
        state.op_ids.append(frame_op.id)
        rt["style_frame_op"] = frame_op.id
        if "opens from a style frame" not in op.rationale:
            op.rationale += " · opens from a style frame (scene change), not the previous clip's last frame"
        created.append(frame_op)
    return created


def _style_frames_note(frames: list[DirectorOp]) -> str:
    if not frames:
        return ""
    cost = sum(f.estimate.total_usd_micros for f in frames if f.estimate)
    return (
        f" {_n(len(frames), 'scene change')} open from a generated style frame "
        f"({_n(len(frames), 'still')}, about {_usd(cost)}) so every scene is drawn by the same hand."
    )


def style_frame_op_for(op: DirectorOp) -> DirectorOp | None:
    """The style-frame image op tied to a chain link, when the link cuts."""
    frame_id = runtime(op.id).get("style_frame_op")
    return _OPS.get(frame_id) if frame_id else None


def chain_scene_change(op: DirectorOp) -> bool:
    """Whether this chain link opens on a cut (a new place or subject)."""
    master = _MASTERS.get(op.id)
    position = chain_position(op.session_id, op.id)
    if master is None or position is None or position >= len(master.clips):
        return False
    return bool(master.clips[position].get("scene_change"))


def register_look_anchor(op: DirectorOp, frame_path: str) -> None:
    """A frame the film established its look with — the chain opener's first
    frame — joins the look's anchors so every later style frame and plate
    references it."""
    master = _MASTERS.get(op.id)
    if master is None or master.look is None or not frame_path:
        return
    if frame_path not in master.look.anchor_assets:
        master.look.anchor_assets.append(frame_path)
        persist(op.session_id)


def retake_chain_link(op: DirectorOp) -> None:
    """A chain link that printed but failed its look review rolls again: back
    to proposed with a clean runtime; the rejected take stays on the ledger
    as a discarded take. Its style frame (which passed) is kept."""
    op.status = "proposed"
    op.job_id = ""
    rt = runtime(op.id)
    for stale in (
        "handle", "clip", "media_generation_id", "result_url", "last_poll_state",
        "last_progress_ts", "submit_error", "ledger_error", "transient_holds",
        "quota_hold_until", "narration", "next_op", "advance_error", "advance_failures",
    ):
        rt.pop(stale, None)
    _REVIEWS.pop(op.id, None)
    _ACCEPTED.discard(op.id)
    if "retake after a failed look review" not in op.rationale:
        op.rationale += " · retake after a failed look review"
    persist(op.session_id)


def _kick_chain_derivation(
    op_ids: list[str],
    sid: str,
    idea: str,
    expanded: str,
    provider: CloudProvider,
    plan: list[int],
    refs: list[str],
    authored: dict[str, str],
    has_start_image: bool,
) -> None:
    """One derivation for the whole chain: the master plus its CLIP DELIVERY
    split, landed on every link. Failure releases every link with its
    provisional raw-idea prompt — labeled, never silent."""
    import threading

    def _worker() -> None:
        ops = [op for op_id in op_ids if (op := _OPS.get(op_id)) is not None]
        if not ops:
            return
        try:
            master = master_mod.derive(
                expanded,
                target_duration_s=sum(plan),
                reference_images=refs,
                authored=authored,
                clip_plan=plan,
            )
            for index, op in enumerate(ops):
                _MASTERS[op.id] = master
                if index < len(master.clips):
                    op.narration = str(master.clips[index].get("narration", "") or "")
                    op.prompt = compile_master_body(
                        master.clips[index]["prompt"],
                        idea,
                        provider,
                        sid,
                        has_start_image=has_start_image if index == 0 else True,
                        narration=op.narration,
                        look=master.look,
                    )
                    op.prompt_version = "v1"
            frames = _plan_style_frames(sid, ops, master)
            ops[0].rationale = (
                ops[0].rationale.split(" · ")[0]
                + " · "
                + master_mod.summary_line(master)
                + " "
                + master_mod.duration_note(master, sum(plan))
                + _style_frames_note(frames)
            )
        except Exception as err:  # noqa: BLE001 — a failed derivation is a named state
            for op in ops:
                op.rationale = (
                    op.rationale.split(" · ")[0]
                    + f" · master derivation failed ({type(err).__name__}: {str(err)[:160]}) — "
                    "this is the provisional raw-idea prompt; send the idea again to retry"
                )
        finally:
            for op in ops:
                if op.status == "deriving":
                    op.status = "proposed"
            persist(sid)

    threading.Thread(target=_worker, name=f"diony-chain-{op_ids[0]}", daemon=True).start()


def master_for_op(op_id: str) -> MasterPrompt | None:
    return _MASTERS.get(op_id)


def _kick_master_derivation(
    op_id: str,
    sid: str,
    idea: str,
    expanded: str,
    provider: CloudProvider,
    duration: int,
    refs: list[str],
    authored: dict[str, str],
    has_start_image: bool,
) -> None:
    """Derive the master prompt off-request and land it on the priced op.
    Success: prompt v1 from the artifact, status back to proposed. Failure:
    the op is released as proposed with its provisional raw-idea prompt —
    labeled v0-provisional and named in the rationale, never silently."""
    import threading

    def _worker() -> None:
        op = _OPS.get(op_id)
        if op is None:
            return
        try:
            master = master_mod.derive(
                expanded, target_duration_s=duration, reference_images=refs, authored=authored
            )
            _MASTERS[op_id] = master
            op.prompt = compile_prompt(
                idea, provider, sid, has_start_image=has_start_image, master=master
            )
            op.narration = master.narration
            op.prompt_version = "v1"
            op.rationale = (
                op.rationale.split(" · ")[0]
                + " · "
                + master_mod.summary_line(master)
                + " "
                + master_mod.duration_note(
                    master,
                    duration,
                    cap_s=estimate_mod.GROK_MAX_S if provider is CloudProvider.GROK else 0,
                )
            )
        except Exception as err:  # noqa: BLE001 — a failed derivation is a named state
            op.rationale = (
                op.rationale.split(" · ")[0]
                + f" · master derivation failed ({type(err).__name__}: {str(err)[:160]}) — "
                "this is the provisional raw-idea prompt; send the idea again to retry"
            )
        finally:
            if op.status == "deriving":
                op.status = "proposed"
            persist(sid)

    threading.Thread(target=_worker, name=f"diony-master-{op_id}", daemon=True).start()


# --- Scripted production flow (M2–M5) --------------------------------------


def budget_for(sid: str) -> dict[str, Any]:
    return _BUDGETS.setdefault(sid, dict(DEFAULT_BUDGET))


def aspect_for(sid: str) -> str:
    return _ASPECTS.get(sid, DEFAULT_ASPECT)


def program_raster(sid: str) -> tuple[int, int]:
    return PROGRAM_ASPECTS[aspect_for(sid)]


def finish_raster(sid: str) -> tuple[int, int]:
    return FINISH_ASPECTS[aspect_for(sid)]


def upscale_parallelism() -> int:
    import os

    raw = os.environ.get("DIONY_FLOW_UPSCALE_PARALLEL", "").strip()
    try:
        return max(1, int(raw)) if raw else DEFAULT_UPSCALE_PARALLELISM
    except ValueError:
        return DEFAULT_UPSCALE_PARALLELISM


def set_aspect(sid: str, aspect: str) -> None:
    if aspect not in PROGRAM_ASPECTS:
        raise ValueError(
            f"unknown aspect {aspect!r} — expected one of {sorted(PROGRAM_ASPECTS)}"
        )
    _ASPECTS[sid] = aspect
    if sid in _SESSIONS:
        persist(sid)


def flow_template_for(sid: str) -> dict[str, str]:
    from diony_director import flow_lexicon

    return flow_lexicon.template_for(sid)


def _flow_template_raw(sid: str) -> dict[str, str] | None:
    """The persisted form: only a non-default template earns a state.json key."""
    from diony_director import flow_lexicon

    template = flow_lexicon.template_for(sid)
    if template["world"] == flow_lexicon.BASELINE_WORLD and not template["camera"]:
        return None
    return template


def set_flow_template(sid: str, *, world: str = "", camera: str = "") -> dict[str, str]:
    from diony_director import flow_lexicon

    template = flow_lexicon.set_template(sid, world=world, camera=camera)
    if sid in _SESSIONS:
        persist(sid)
    return template


def grok_template_for(sid: str) -> dict[str, str]:
    from diony_director import grok_lexicon

    return grok_lexicon.template_for(sid)


def _grok_template_raw(sid: str) -> dict[str, str] | None:
    """The persisted form: only a non-default template earns a state.json key."""
    from diony_director import grok_lexicon

    template = grok_lexicon.template_for(sid)
    if not template["camera"] and not template["atmosphere"]:
        return None
    return template


def set_grok_template(sid: str, *, camera: str = "", atmosphere: str = "") -> dict[str, str]:
    from diony_director import grok_lexicon

    template = grok_lexicon.set_template(sid, camera=camera, atmosphere=atmosphere)
    if sid in _SESSIONS:
        persist(sid)
    return template


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
    if any("deterministic template" in w for w in draft.warnings):
        reply += "⚠ Template draft (no brain configured). "
    master = getattr(draft, "master", None)
    if master is not None:
        reply += master_mod.summary_line(master) + " "
        if abs(master.estimated_duration_s - runtime_s) > max(2, runtime_s // 10):
            reply += (
                f"The master action as derived plays about {_runtime(master.estimated_duration_s)}; "
                f"the screenplay fills your {_runtime(runtime_s)} target from it. "
            )
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


def _derive_master_for_draft(sid: str, idea: str, runtime_s: int) -> MasterPrompt:
    """Stage one of a draft (Blueprint 27): the whole film's master prompt,
    derived from the idea, the stills, and the runtime target, in the dialect
    of the engine the session will roll on."""
    refs = [u["path"] for u in (_UPLOADS.get(sid) or [])]
    expanded, authored = authored_elements(sid, idea, resolve_provider("auto"))
    return master_mod.derive(
        expanded, target_duration_s=runtime_s, reference_images=refs, authored=authored
    )


def start_production(sid: str, idea: str, runtime_s: int) -> tuple[ScreenplayDraft, str]:
    """Synchronous draft (template path and tests). Stores ready state."""
    master = _derive_master_for_draft(sid, idea, runtime_s)
    draft = screenwriter.write_script(
        _idea_with_uploads(sid, idea),
        runtime_target_s=runtime_s,
        reference_images=[u["path"] for u in (_UPLOADS.get(sid) or [])],
        master=master,
    )
    reply = _draft_reply(draft, runtime_s)
    _DRAFTS[sid] = {
        "status": "ready", "stage": "ready", "draft": draft, "reply": reply, "error": "",
        "master": master,
    }
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

    _DRAFTS[sid] = {
        "status": "drafting", "stage": "deriving", "draft": None, "reply": "", "error": "",
        "master": None,
    }

    def _worker() -> None:
        try:
            # Stage one: the master prompt. Stage two: the screenplay that
            # realizes it. Both on the brain, both off-request.
            master = _derive_master_for_draft(sid, idea, runtime_s)
            record = _DRAFTS.get(sid)
            if isinstance(record, dict) and record.get("status") == "drafting":
                record["master"] = master
                record["stage"] = "writing"
            draft = screenwriter.write_script(
                _idea_with_uploads(sid, idea),
                runtime_target_s=runtime_s,
                reference_images=[u["path"] for u in (_UPLOADS.get(sid) or [])],
                master=master,
            )
            _DRAFTS[sid] = {
                "status": "ready",
                "stage": "ready",
                "draft": draft,
                "reply": _draft_reply(draft, runtime_s),
                "error": "",
                "master": master,
            }
            persist(sid)
        except Exception as err:  # noqa: BLE001 - a failed draft is a named state
            _DRAFTS[sid] = {
                "status": "failed",
                "stage": "failed",
                "draft": None,
                "reply": "",
                "error": f"{type(err).__name__}: {err}",
                "master": None,
            }

    threading.Thread(target=_worker, name=f"diony-draft-{sid}", daemon=True).start()
    state = draft_state(sid)
    state["reply"] = (
        f"Deriving the master prompt, then writing the screenplay from it, on "
        f"{brain_model()} — the master takes a minute or two, and a full "
        "screenplay with its refine pass can run ten or more. "
        "I'll put it up the moment it lands."
    )
    return state


def draft_state(sid: str) -> dict[str, Any]:
    record = _DRAFTS.get(sid)
    if not isinstance(record, dict):
        return {"status": "none", "stage": "none", "reply": "", "error": "", "script": None,
                "characters": [], "settings": [], "warnings": [], "master": None}
    draft = record.get("draft")
    master = record.get("master") or (getattr(draft, "master", None) if draft else None)
    return {
        "status": record["status"],
        "stage": record.get("stage", record["status"]),
        "reply": record.get("reply", ""),
        "error": record.get("error", ""),
        "script": draft.script.model_dump(mode="json") if draft else None,
        "characters": [c.model_dump(mode="json") for c in draft.characters] if draft else [],
        "settings": [x.model_dump(mode="json") for x in draft.settings] if draft else [],
        "warnings": draft.warnings if draft else [],
        "master": master_mod.to_doc(master),
    }


def master_for_session(sid: str) -> MasterPrompt | None:
    """The session's pre-split finalized artifact: the locked package's when
    there is one, else the ready draft's."""
    package = _PACKAGES.get(sid)
    if package is not None and package.master_prompt is not None:
        return package.master_prompt
    record = _DRAFTS.get(sid)
    if isinstance(record, dict):
        master = record.get("master")
        if master is not None:
            return master
        draft = record.get("draft")
        if draft is not None and getattr(draft, "master", None) is not None:
            return draft.master
    # a one-shot room: the newest priced shot's artifact is the session's
    state = _SESSIONS.get(sid)
    if state is not None:
        for op_id in reversed(state.op_ids):
            if op_id in _MASTERS:
                return _MASTERS[op_id]
    return None


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
SHOT_LINE_KINDS = {"action", "dialogue", "vo"}


def edit_draft_line(
    sid: str, line_id: str, text: str | None = None, *, shot: str | None = None
) -> dict[str, Any]:
    """Change one line of a READY, UNLOCKED draft — the writer's pass before
    the money pass. Headings and cues hold the structure and stay fixed.
    `shot` rewrites the authored frame for the beat (empty hands the beat
    back to the coverage rotation); /shorthand in it expands on the Veo
    lane at compile time and stays literal everywhere else."""
    record = _DRAFTS.get(sid)
    if not isinstance(record, dict) or record.get("status") != "ready" or not record.get("draft"):
        raise DraftNotReady("no ready draft to edit — draft a script first")
    draft = record["draft"]
    if draft.script.locked:
        raise DraftLocked("the script is locked — nothing changes after the lock")
    line = draft.script.line(line_id)  # KeyError → 404 upstream
    if text is None and shot is None:
        raise ValueError("nothing to edit — send text, shot, or both")
    if text is not None:
        if line.kind not in EDITABLE_LINE_KINDS:
            raise ValueError(f"a {line.kind} line holds the structure and cannot be edited")
        cleaned = " ".join(text.split())[:500]
        if not cleaned:
            raise ValueError("an empty line is a deletion, and deletions need a redraft")
        line.text = cleaned
    if shot is not None:
        if line.kind not in SHOT_LINE_KINDS:
            raise ValueError(f"a {line.kind} line carries no shot — direct an action or spoken beat")
        line.shot = " ".join(shot.split())[:300]
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


def script_locked(sid: str) -> bool:
    """Whether THIS session's CURRENT draft is locked (its prompts compiled).
    A stale package from a finished film is not a lock: a re-produce writes a
    fresh, unlocked draft over _DRAFTS[sid] while the old package lingers
    until the next lock, and that fresh draft may still be restyled."""
    record = _DRAFTS.get(sid)
    if isinstance(record, dict) and record.get("status") == "ready":
        draft = record.get("draft")
        if draft is not None:
            return bool(draft.script.locked)
    return False


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


def oneshot_chain_ops(sid: str) -> list[list[DirectorOp]]:
    """Every chained one-shot's links, in play order, per chain."""
    seq = _SEQUENCES.get(sid)
    if seq is None:
        return []
    chains: list[list[DirectorOp]] = []
    for scene_id, chain in (seq.get("scenes") or {}).items():
        if not scene_id.startswith("oneshot-"):
            continue
        ops = [op for op_id in chain if (op := _OPS.get(op_id)) is not None]
        if ops:
            chains.append(ops)
    return chains


def completed_clip_ops(sid: str) -> list[DirectorOp]:
    """The cut list. A scripted production cuts the latest usable take per
    clip in package order; a session without a locked script cuts its chained
    one-shot links in play order (the 2026-09-02 chain printed all 12 links
    and assembly still said "no completed clips" — the cut list only knew
    scripted clip_ids)."""

    package = _PACKAGES.get(sid)
    if package is None:
        return [
            op for chain in oneshot_chain_ops(sid) for op in chain if usable_take(op)
        ]
    latest: dict[str, DirectorOp] = {}
    for op in ops_for(sid):
        # a finish (upscale) is never a take of its own: it rides beside the
        # take it finished and substitutes its pixels at the cut
        if op.clip_id and op.op is not DirectorOpType.UPSCALE and usable_take(op):
            latest[op.clip_id] = op
    return [latest[c.clip_id] for c in package.clips if c.clip_id in latest]


# --- The 1080p finish (Blueprint 30) ----------------------------------------
#
# A server-side upscale is an UPSCALE op whose artifact_refs.source_op names
# the take it finishes. The take stays the unit of record (review, narration,
# cue sheet, EDL); at the cut its finished print replaces its pixels.


def finish_ops_for(op: DirectorOp) -> list[DirectorOp]:
    """Every upscale of this take, in registration order."""
    return [
        candidate
        for candidate in ops_for(op.session_id)
        if candidate.op is DirectorOpType.UPSCALE
        and str(candidate.artifact_refs.get("source_op") or "") == op.id
    ]


def finish_op_for(op: DirectorOp, resolution: str | None = None) -> DirectorOp | None:
    """The usable finished print of this take — completed, its finish review
    not failed, its clip on disk — at `resolution` when given, latest first."""
    from pathlib import Path

    for candidate in reversed(finish_ops_for(op)):
        if resolution and candidate.resolution != resolution:
            continue
        if not usable_take(candidate):
            continue
        stored = runtime(candidate.id).get("clip")
        if stored is not None and Path(stored.path).is_file():
            return candidate
    return None


def finish_in_flight_for(op: DirectorOp) -> DirectorOp | None:
    """An upscale of this take that is queued or rolling right now."""
    for candidate in reversed(finish_ops_for(op)):
        if candidate.status in {"proposed", "approved", "submitting", "submitted"}:
            return candidate
    return None


def finish_state_for(op: DirectorOp) -> dict[str, Any] | None:
    """What the board shows beside a take: its latest finish, if any."""
    ops = finish_ops_for(op)
    if not ops:
        return None
    usable = finish_op_for(op)
    latest = usable or ops[-1]
    review = _REVIEWS.get(latest.id)
    return {
        "op_id": latest.id,
        "status": latest.status,
        "resolution": latest.resolution,
        "usable": usable is not None,
        "verdict": review.verdict if review is not None else "",
        "issues": list(review.issues[:2]) if review is not None else [],
        "reason": str(_RUNTIME.get(latest.id, {}).get("submit_error") or ""),
    }


def can_upscale(op: DirectorOp) -> bool:
    """A completed Flow take with a hosted media id, no usable finish yet and
    none rolling — the one case a server-side upscale is worth proposing."""
    return bool(
        op.status == "completed"
        and op.op is not DirectorOpType.UPSCALE
        and op.provider is not None
        and op.provider.value == "google-flow"
        and _RUNTIME.get(op.id, {}).get("media_generation_id")
        and finish_op_for(op) is None
        and finish_in_flight_for(op) is None
    )


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
    # a locked production replaces the directed order — but never the chained
    # one-shots already sequenced in this room: their bridge order survives
    existing = (_SEQUENCES.get(sid) or {}).get("scenes") or {}
    kept = {key: list(chain) for key, chain in existing.items() if key.startswith("oneshot-")}
    _SEQUENCES[sid] = {
        "bible": list(bible_ids),
        "bible_chains": [list(chain) for chain in bible_chains],
        "scenes": {**kept, **dict(scene_chains)},
        "scene_plates": dict(scene_plates or {}),
        "held_scenes": set(),
    }


def _register_oneshot_chain(sid: str, op_ids: list[str]) -> None:
    """A chained one-shot rides the directed-sequencing rails: its links are a
    scene chain, so batch waves order them and every later link bridges from
    its predecessor's join frame. Registered additively — a production's
    sequence and a one-shot chain coexist."""
    seq = _SEQUENCES.get(sid)
    key = f"oneshot-{op_ids[0]}"
    if seq is None:
        _SEQUENCES[sid] = {
            "bible": [],
            "bible_chains": [],
            "scenes": {key: list(op_ids)},
            "scene_plates": {},
            "held_scenes": set(),
        }
    else:
        seq.setdefault("scenes", {})[key] = list(op_ids)


def sequence_for(sid: str) -> dict[str, Any] | None:
    return _SEQUENCES.get(sid)


def set_batch_targets(sid: str, op_ids: set[str]) -> set[str]:
    """Target ops for a directed batch; pending bible ops always ride along —
    no hero pixel rolls before its plates and sheets exist."""
    seq = _SEQUENCES.get(sid) or {"bible": [], "scenes": {}}
    targets = {
        op_id for op_id in op_ids
        if not ((op := _OPS.get(op_id)) is not None and op.artifact_refs.get("style_frame"))
    }
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
        return _ready_unsequenced(sid, {}, targets)
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
        if _oneshot_broken(sid, seq, scene_id, chain):
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
    ready.extend(_ready_unsequenced(sid, seq, targets))
    return ready


def _ready_unsequenced(sid: str, seq: dict[str, Any], targets: set[str]) -> list[DirectorOp]:
    """Targeted ops that ride no chain — the finish upscales — are ready once
    the bibles are settled, released a few at a time. A targeted op outside
    every chain used to sit "queued" forever and the batch never settled."""
    sequenced: set[str] = set(seq.get("bible") or [])
    for chain in seq.get("bible_chains") or []:
        sequenced.update(chain)
    for chain in (seq.get("scenes") or {}).values():
        sequenced.update(chain)
    loose = [
        op
        for op in ops_for(sid)
        if op.id in targets and op.id not in sequenced and op.status == "proposed"
    ]
    if not loose:
        return []
    in_flight = sum(
        1
        for op in ops_for(sid)
        if op.op is DirectorOpType.UPSCALE and op.status in {"submitting", "submitted"}
    )
    slots = max(0, upscale_parallelism() - in_flight)
    ready: list[DirectorOp] = []
    for op in loose:
        if op.op is DirectorOpType.UPSCALE:
            if slots <= 0:
                continue
            slots -= 1
        ready.append(op)
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


def _oneshot_broken(sid: str, seq: dict[str, Any], scene_id: str, chain: list[str]) -> bool:
    """A chained one-shot with a failed link stops AT the break. Every later
    link can only open from the failed link's missing frame, so offering them
    to the submitter cascaded into one chain-order hold per wave (the 12-held
    spam of 2026-09-02) and drained the batch into a premature assembly. The
    later links leave the batch loudly, ONCE; retrying the failed link
    re-arms the chain."""
    if not scene_id.startswith("oneshot-"):
        return False
    broken_at: int | None = None
    review_failed = False
    for index, op_id in enumerate(chain):
        op = _OPS.get(op_id)
        if op is None:
            continue
        if op.status == "failed":
            broken_at = index
            break
        if op.status == "completed" and not usable_take(op):
            # printed, but its look review failed and nobody accepted it —
            # every later link would open from a frame the film rejects
            broken_at = index
            review_failed = True
            break
    held: set = seq.setdefault("held_scenes", set())
    if broken_at is None:
        held.discard(scene_id)  # the failed link was retried — the chain is re-armed
        return False
    targets = batch_targets(sid)
    if scene_id not in held and any(op_id in targets for op_id in chain[broken_at + 1 :]):
        held.add(scene_id)
        if review_failed:
            review = _REVIEWS.get(chain[broken_at])
            why = "; ".join((review.issues if review else [])[:2]) or "see its review"
            reason = (
                f"clip {broken_at + 1} printed but failed its look review ({why}) — the "
                "chain stops there. Retake it, or Accept it, then Run & Assemble All again"
            )
        else:
            reason = (
                f"clip {broken_at + 1} failed — the chain stops there. Retry it, "
                "then Run & Assemble All again; every later clip opens from its "
                "predecessor's final frame"
            )
        log_held(sid, [{"op_id": chain[broken_at], "reason": reason}])
    for op_id in chain[broken_at + 1 :]:
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
# scalar runtime facts that survive a restart (handles and clips are
# serialized separately); style_frame_op/style_frame tie a chain link to
# the still it opens from (Blueprint 29)
_RUNTIME_KEYS = (
    "media_generation_id",
    "result_url",
    "last_poll_state",
    "last_progress_ts",
    "submit_error",
    "style_frame_op",
    "style_frame",
    "quota_hold_until",
)


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
            "master": master_mod.to_doc(
                draft_record.get("master") or getattr(draft, "master", None)
            ),
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
        for key in _RUNTIME_KEYS:
            if rt.get(key) is not None:
                entry[key] = rt[key]
        if op_id in _MASTERS:
            entry["master"] = master_mod.to_doc(_MASTERS[op_id])
        if entry:
            runtime_doc[op_id] = entry
    seq = _SEQUENCES.get(sid)
    seq_doc = None
    if seq is not None:
        seq_doc = {**seq, "held_scenes": sorted(seq.get("held_scenes", set()))}
    doc = {
        "version": 1,
        "session_id": sid,
        "dismissed": state.dismissed,
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
        "aspect": _ASPECTS.get(sid),
        "flow_template": _flow_template_raw(sid),
        "grok_template": _grok_template_raw(sid),
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

    _SESSIONS[sid] = SessionState(
        id=sid,
        op_ids=list(doc.get("op_ids") or []),
        dismissed=bool(doc.get("dismissed")),
    )
    for raw in doc.get("ops") or []:
        op = DirectorOp.model_validate(raw)
        _OPS[op.id] = op
        # a restart killed any in-flight submit thread; the claim is stale
        if op.status == "submitting":
            op.status = "proposed"
        # …and any in-flight master derivation: the op is released with its
        # provisional prompt, and says so — never a silent raw-idea roll
        if op.status == "deriving":
            op.status = "proposed"
            if "provisional" not in op.rationale:
                op.rationale += (
                    " · master derivation interrupted by a restart — this is the "
                    "provisional raw-idea prompt; send the idea again to re-derive"
                )
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
    if doc.get("aspect") in PROGRAM_ASPECTS:
        _ASPECTS[sid] = str(doc["aspect"])
    if doc.get("flow_template"):
        from diony_director import flow_lexicon

        flow_lexicon.restore_template(sid, doc["flow_template"])
    if doc.get("grok_template"):
        from diony_director import grok_lexicon

        grok_lexicon.restore_template(sid, doc["grok_template"])
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
        for key in _RUNTIME_KEYS:
            if entry.get(key) is not None:
                rt[key] = entry[key]
        restored = master_mod.from_doc(entry.get("master"))
        if restored is not None:
            _MASTERS[op_id] = restored
    if doc.get("draft"):
        draft_doc = doc["draft"]
        draft = ScreenplayDraft(
            script=Script.model_validate(draft_doc["script"]),
            characters=[CharacterProfile.model_validate(c) for c in draft_doc["characters"]],
            settings=[SettingProfile.model_validate(s) for s in draft_doc["settings"]],
            warnings=list(draft_doc.get("warnings") or []),
            brain_usage=dict(draft_doc.get("brain_usage") or {}),
            master=master_mod.from_doc(draft_doc.get("master")),
        )
        # A rehydrated draft whose script was already locked keeps working:
        # lock_script re-locks idempotently and the package doc is authoritative.
        _DRAFTS[sid] = {
            "status": "ready",
            "stage": "ready",
            "draft": draft,
            "reply": draft_doc.get("reply", ""),
            "error": "",
            "master": draft.master,
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
