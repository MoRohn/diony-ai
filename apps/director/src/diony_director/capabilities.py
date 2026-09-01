"""The provider feature matrix, encoded — never guessed at submit time.

Verified against live docs (docs.x.ai + useapi.net, 2026-09-01):
- Grok: reference-to-video is 1.5-only (≤3 refs, ≤720p); edit and extension run
  only on base grok-imagine-video (≤720p, inherit input props); no seed.
- Flow: every Veo 3.1 tier can be an extension *source* (the extend request's
  own model performs it); omni-flash output can NEVER be extended. Veo
  characters are fast/lite only, 8s clips only, inside a 3-ref budget, 1 voice.
  Quality has no refs/characters/voices. omni-flash: 7 refs, 5 voices, v2v
  edit; frames (start/end image) are exclusive with refs/characters/voices.

An illegal combination is refused at compile or before submit — never
discovered as a provider 400 after money is committed.
"""

from __future__ import annotations

from dataclasses import dataclass

from diony_contracts.director import CloudGenerationMode, DirectorOp


@dataclass(frozen=True)
class ModelCaps:
    modes: frozenset[str]
    durations: tuple[int, ...] | None  # None → integer range 1..15 (Grok)
    resolutions: frozenset[str]
    max_reference_images: int
    max_characters: int
    character_durations: tuple[int, ...]  # () → characters never legal
    max_voices: int
    extendable_source: bool  # can this model's OUTPUT be extended later
    extension_lane: bool  # can this model PERFORM an extension request
    supports_edit: bool
    frames_exclusive_with_refs: bool  # start/end frames reject refs/characters/voices


_VIDEO = {"t2v", "i2v", "r2v", "first_last"}

CAPS: dict[tuple[str, str], ModelCaps] = {
    ("grok", "grok-imagine-video-1.5"): ModelCaps(
        modes=frozenset(_VIDEO - {"first_last"}),
        durations=None,
        resolutions=frozenset({"480p", "720p", "1080p"}),
        max_reference_images=3,
        max_characters=0,
        character_durations=(),
        max_voices=3,  # preset reference_audios, addressed as <AUDIO_n>
        extendable_source=True,  # base-model /extensions accepts the video
        extension_lane=False,  # extension itself runs on base only
        supports_edit=False,
        frames_exclusive_with_refs=True,  # image and reference_images are exclusive
    ),
    ("grok", "grok-imagine-video"): ModelCaps(
        modes=frozenset({"t2v", "i2v", "extension"}),
        durations=None,
        resolutions=frozenset({"480p", "720p"}),
        max_reference_images=0,
        max_characters=0,
        character_durations=(),
        max_voices=0,
        extendable_source=True,
        extension_lane=True,
        supports_edit=True,
        frames_exclusive_with_refs=True,
    ),
    ("google-flow", "veo-3.1-lite"): ModelCaps(
        modes=frozenset(_VIDEO | {"extension"}),
        durations=(4, 6, 8),
        resolutions=frozenset({"720p"}),
        max_reference_images=3,
        max_characters=3,
        character_durations=(8,),
        max_voices=1,
        extendable_source=True,
        extension_lane=True,
        supports_edit=False,
        frames_exclusive_with_refs=True,
    ),
    ("google-flow", "veo-3.1-fast"): ModelCaps(
        modes=frozenset(_VIDEO | {"extension"}),
        durations=(4, 6, 8),
        resolutions=frozenset({"720p"}),
        max_reference_images=3,
        max_characters=3,
        character_durations=(8,),
        max_voices=1,
        extendable_source=True,
        extension_lane=True,
        supports_edit=False,
        frames_exclusive_with_refs=True,
    ),
    ("google-flow", "veo-3.1-quality"): ModelCaps(
        modes=frozenset({"t2v", "i2v", "first_last", "extension"}),
        durations=(4, 6, 8),
        resolutions=frozenset({"720p"}),
        max_reference_images=0,
        max_characters=0,
        character_durations=(),
        max_voices=0,
        extendable_source=True,
        extension_lane=True,
        supports_edit=False,
        frames_exclusive_with_refs=True,
    ),
    ("google-flow", "omni-flash"): ModelCaps(
        modes=frozenset(_VIDEO | {"v2v_edit"}),
        durations=(4, 6, 8, 10),
        resolutions=frozenset({"360p", "720p"}),
        max_reference_images=7,
        max_characters=7,
        character_durations=(4, 6, 8, 10),
        max_voices=5,
        extendable_source=False,  # verified: omni output cannot be extended
        extension_lane=False,
        supports_edit=True,  # v2v edit lane
        frames_exclusive_with_refs=True,
    ),
}
CAPS[("google-flow", "veo-3.1-lite-low-priority")] = CAPS[("google-flow", "veo-3.1-lite")]


def caps_for(provider_id: str, model: str) -> ModelCaps | None:
    return CAPS.get((provider_id, model))


def duration_legal(caps: ModelCaps, duration_s: float) -> bool:
    if caps.durations is None:
        return 1 <= duration_s <= 15
    if caps.durations == (4, 6, 8):  # Veo tiers: 4/6s are Ultra-plan-only
        from diony_director.estimate import veo_grid

        return int(duration_s) in veo_grid()
    return int(duration_s) in caps.durations


def violations(op: DirectorOp) -> list[str]:
    """Everything about this op that no model on the routed lane supports.

    Image and upscale ops (and unknown models) are out of scope here — they are
    validated by their own estimate/adapter paths.
    """
    if op.provider is None or op.mode in {CloudGenerationMode.IMAGE, CloudGenerationMode.UPSCALE}:
        return []
    caps = caps_for(op.provider.value, op.model)
    if caps is None:
        return []
    problems: list[str] = []
    refs = op.artifact_refs or {}

    if op.mode is CloudGenerationMode.EXTENSION and not caps.extension_lane:
        if op.provider.value == "google-flow" and not caps.extendable_source:
            problems.append(
                f"{op.model} output cannot be extended — re-route the shot to a Veo lane "
                "or fix it with a v2v edit"
            )
        else:
            problems.append(f"{op.model} cannot perform an extension — use grok-imagine-video")
    if op.op is not None and op.op.value == "edit" and not caps.supports_edit:
        problems.append(f"{op.model} has no edit lane")

    if op.mode in {CloudGenerationMode.EXTENSION, CloudGenerationMode.UPSCALE}:
        # Extensions and upscales transmit only their source video — reference
        # and voice budgets do not apply to payloads that never carry them.
        return problems

    characters = list(refs.get("characters") or [])
    if characters:
        if not caps.character_durations:
            problems.append(f"{op.model} does not support characters")
        else:
            if len(characters) > caps.max_characters:
                problems.append(
                    f"{len(characters)} characters exceed the {op.model} budget of {caps.max_characters}"
                )
            if int(op.duration_s) not in caps.character_durations:
                problems.append(
                    f"characters on {op.model} require a duration in {caps.character_durations}, "
                    f"not {int(op.duration_s)}s"
                )
    references = list(refs.get("references") or [])
    if len(references) > caps.max_reference_images:
        problems.append(
            f"{len(references)} reference images exceed the {op.model} cap of {caps.max_reference_images}"
        )
    voices = list(refs.get("voices") or refs.get("grok_voices") or [])
    if len(voices) > caps.max_voices:
        problems.append(f"{len(voices)} voices exceed the {op.model} cap of {caps.max_voices}")
    if op.resolution and op.resolution not in caps.resolutions:
        problems.append(f"{op.model} does not render {op.resolution}")
    if op.mode is not CloudGenerationMode.EXTENSION and not duration_legal(caps, op.duration_s):
        problems.append(f"{int(op.duration_s)}s is not on the {op.model} duration grid")
    return problems
