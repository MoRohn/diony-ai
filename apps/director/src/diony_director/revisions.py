"""The revision planner: cheapest fix that preserves lock, with memory and budgets.

Ladder: edit > extend > regenerate from the same stills > new stills > provider
swap. A (provider, model, mode, prompt) tuple that already failed is never
resubmitted verbatim; the revision budget is a hard stop that settles the best
take instead of grinding.
"""

from __future__ import annotations

import hashlib
import uuid

from diony_contracts.director import (
    CloudGenerationMode,
    CloudProvider,
    DirectorOp,
    DirectorOpType,
)
from diony_contracts.production import ClipReview, ClipUnit, ProductionPackage, PromptVersion
from diony_director import compiler, estimate as estimate_mod

DEFAULT_MAX_REVISIONS_PER_CLIP = 3

_NOTE_BY_FAMILY = (
    ("identity", "fix-face"),
    ("audio", "fix-audio"),
    ("script_conformance", "fix-audio"),
    ("continuity", "fix-continuity"),
    ("frames", "fix-frames"),
)

_NOTE_BY_ISSUE = (
    ("face", "fix-face"),
    ("identity", "fix-face"),
    ("audio", "fix-audio"),
    ("voice", "fix-audio"),
    ("light", "fix-light"),
)


def failure_key(op: DirectorOp) -> str:
    basis = f"{op.provider}|{op.model}|{op.mode}|{op.prompt}"
    return hashlib.sha256(basis.encode()).hexdigest()


def as_prompt_version(op: DirectorOp) -> PromptVersion:
    return PromptVersion(version=op.prompt_version, prompt=op.prompt, note=op.rationale)


def _usd(micros: int) -> str:
    return f"${micros / 1_000_000:.2f}"


def _note_for(review: ClipReview) -> str:
    """The revision directive keys on the FAILED FAMILY first — the measured
    evidence — and only falls back to scanning issue prose."""
    for family, note in _NOTE_BY_FAMILY:
        if review.families.get(family) == "fail":
            return note
    text = " ".join(review.issues).lower()
    for needle, note in _NOTE_BY_ISSUE:
        if needle in text:
            return note
    return "quality"


def plan_revision(
    review: ClipReview,
    clip: ClipUnit,
    package: ProductionPackage,
    *,
    session_id: str,
    revision_count: int,
    failure_memory: set[str],
    max_revisions_per_clip: int = DEFAULT_MAX_REVISIONS_PER_CLIP,
) -> tuple[DirectorOp | None, str]:
    if revision_count >= max_revisions_per_clip:
        return (
            None,
            f"Revision budget spent on {clip.clip_id} ({revision_count} passes) — "
            "settling the best take with a WARN instead of grinding.",
        )

    families = review.families
    op_type = DirectorOpType.GENERATE
    mode = clip.mode or CloudGenerationMode.T2V
    provider = clip.provider or CloudProvider.GROK
    model = clip.model
    note = _note_for(review)
    duration = clip.duration_s
    resolution = clip.resolution

    if families.get("duration") == "fail" and "vs booked" in " ".join(review.issues):
        if model in estimate_mod.OMNI_MODELS:
            # omni-flash output can never be extended (verified live docs) —
            # the lock-preserving fix on that lane is a v2v edit.
            op_type = DirectorOpType.EDIT
            mode = CloudGenerationMode.V2V_EDIT
        else:
            op_type = DirectorOpType.EXTEND
            mode = CloudGenerationMode.EXTENSION
            if provider is CloudProvider.GROK:
                # Extension runs on the base model only (≤720p); pricing and
                # the request must agree on that, not on the 1.5 card.
                model = "grok-imagine-video"
                if resolution == "1080p":
                    resolution = "720p"
        note = "extend-motion"
    elif families.get("audio") == "fail":
        if provider is CloudProvider.GOOGLE_FLOW and model in estimate_mod.OMNI_MODELS:
            op_type = DirectorOpType.EDIT
            mode = CloudGenerationMode.V2V_EDIT
        note = "fix-audio"

    version = compiler.revise_prompt(clip, package, note=note)

    def _build(provider_: CloudProvider, model_: str, mode_: CloudGenerationMode, prompt: str, prompt_version: str) -> DirectorOp:
        from diony_director import capabilities

        is_video_input = provider_ is CloudProvider.GROK and op_type in {
            DirectorOpType.EDIT,
            DirectorOpType.EXTEND,
        }
        # The op books what the target lane can actually deliver: durations
        # re-quantize to the new grid on a provider swap, and a resolution the
        # lane cannot render clamps to one it can — the estimate, the request,
        # and the capability gate must all describe the same op.
        duration_ = estimate_mod.quantize_duration(provider_, model_, duration)
        caps = capabilities.caps_for(provider_.value, model_)
        resolution_ = resolution
        if caps is not None and resolution_ not in caps.resolutions:
            resolution_ = "720p" if "720p" in caps.resolutions else sorted(caps.resolutions)[0]
        # An extension transmits no reference images — carrying the ref pack
        # would trip the capability gate on a payload that does not exist.
        refs = (
            {}
            if op_type is DirectorOpType.EXTEND
            else compiler.provider_artifact_refs(clip, package)
        )
        est = estimate_mod.estimate_op(
            provider=provider_,
            model=model_,
            mode=mode_,
            duration_s=duration_,
            resolution=resolution_,
            image_inputs=0
            if is_video_input
            else estimate_mod.image_inputs_for(clip, provider_),
            video_input_s=duration_ if is_video_input else 0,
        )
        return DirectorOp(
            id=f"op-{uuid.uuid4().hex[:10]}",
            session_id=session_id,
            op=op_type,
            clip_id=clip.clip_id,
            provider=provider_,
            model=model_,
            mode=mode_,
            prompt=prompt,
            prompt_version=prompt_version,
            duration_s=duration_,
            resolution=resolution_,
            artifact_refs=refs,
            estimate=est,
            rationale=f"{note}: {'; '.join(review.issues) or review.verdict}",
        )

    op = _build(provider, model, mode, version.prompt, version.version)

    if failure_key(op) in failure_memory:
        # Same exact attempt already failed. Swap provider when the lock allows
        # it; otherwise force a differently-worded revision.
        if not clip.audio_packet.voice_ref:
            if provider is CloudProvider.GROK:
                swapped = (CloudProvider.GOOGLE_FLOW, "veo-3.1-quality")
            else:
                swapped = (CloudProvider.GROK, "grok-imagine-video-1.5")
            op_type = DirectorOpType.GENERATE
            retry = compiler.revise_prompt(clip, package, note=f"{note}-retry")
            op = _build(swapped[0], swapped[1], CloudGenerationMode.T2V, retry.prompt, retry.version)
        else:
            retry = compiler.revise_prompt(
                clip, package, note=f"{note}-take{revision_count + 2}"
            )
            op = _build(provider, model, mode, retry.prompt, retry.version)

    narration = (
        f"{clip.clip_id} failed review ({'; '.join(review.issues) or review.verdict}). "
        f"Cheapest fix that preserves the lock: {op.op.value} on {op.model} "
        f"({op.prompt_version}). This pass adds about {_usd(op.estimate.total_usd_micros)}; "
        f"{max_revisions_per_clip - revision_count - 1} revision(s) left on this clip."
    )
    return op, narration
