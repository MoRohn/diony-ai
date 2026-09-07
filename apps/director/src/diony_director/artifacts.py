"""Character sheet generation: cheap pixels anchor expensive pixels.

The angle board (front, ¾ left, ¾ right, profile, full body) is generated as
priced image ops through the Flow nano-banana lane, each later slot chaining
the already-approved stills as references so the set self-coheres. Registered
stills land in the bible: local paths for Grok reference packs, Flow media ids
for ingredients and character objects.
"""

from __future__ import annotations

import uuid
from pathlib import Path

from diony_contracts.director import (
    CloudGenerationMode,
    CloudProvider,
    DirectorOp,
    DirectorOpType,
)
from diony_contracts.production import CharacterProfile, ProductionPackage
from diony_director import estimate as estimate_mod

SHEET_SLOTS = ("front", "q34l", "q34r", "profile", "full")
SHEET_MODEL = "nano-banana-2"
GROK_SHEET_MODEL = "grok-imagine-image-2.0"


def sheet_slots(package: ProductionPackage | None) -> tuple[str, ...]:
    """Blueprint 32: a Locked package casts with the anchor portrait and ONE
    turnaround board drawn from it; Standard keeps the five chained angles."""
    from diony_director import cast as cast_mod

    if package is not None and getattr(package, "cast_lock", "standard") == "locked":
        return cast_mod.LOCKED_SHEET_SLOTS
    return SHEET_SLOTS


def sheet_lane() -> tuple[CloudProvider, str]:
    """Flow's nano-banana when available (server-hosted ingredients); Grok images otherwise."""

    from diony_director.providers import flow as flow_adapter
    from diony_director.providers import grok as grok_adapter

    if flow_adapter.configured():
        return CloudProvider.GOOGLE_FLOW, SHEET_MODEL
    if grok_adapter.configured():
        return CloudProvider.GROK, GROK_SHEET_MODEL
    return CloudProvider.GOOGLE_FLOW, SHEET_MODEL

_SLOT_DIRECTION = {
    "front": "straight-on head-and-shoulders portrait, eye level, neutral expression",
    "q34l": "three-quarter view from the subject's left, head and shoulders",
    "q34r": "three-quarter view from the subject's right, head and shoulders",
    "profile": "true profile portrait, subject facing left of frame",
    "full": "full body, standing, whole wardrobe visible, plain ground",
}


def _look_of(package: ProductionPackage | None):
    master = package.master_prompt if package is not None else None
    return master.look if master is not None else None


def _sheet_prompt(char: CharacterProfile, slot: str, package: ProductionPackage | None = None) -> str:
    wardrobe = next(iter(char.wardrobe.values()), "practical, lived-in wardrobe")
    look = _look_of(package)
    if package is not None and getattr(package, "cast_lock", "standard") == "locked":
        # Blueprint 32: the identity card is the casting brief — verbatim on
        # the anchor portrait, verbatim on the board drawn from it, verbatim
        # on every clip that shows the face. One text, one person.
        from diony_director import cast as cast_mod

        if slot == cast_mod.BOARD_SLOT:
            return cast_mod.board_prompt(char, look)
        card = cast_mod.identity_block(char)
        if look is not None and not look.photoreal:
            return (
                f"Anchor portrait of {char.name} ({char.role or 'principal cast'}) in the film's "
                f"medium: {_SLOT_DIRECTION['front']}. {card} Plain mid-grey backdrop, nothing in "
                f"the hands, no props, no text. {look.block()}"
            )
        return (
            f"Anchor casting photograph of {char.name} ({char.role or 'principal cast'}): "
            f"{_SLOT_DIRECTION['front']}. {card} Soft neutral studio light, plain mid-grey "
            "backdrop, nothing in the hands, no props, no text, sharp focus on the face, "
            f"photorealistic.{(' ' + look.block()) if look is not None else ''}"
        )
    if look is not None and not look.photoreal:
        # An animated film casts in its own medium — a photographic head
        # shot as the identity anchor of a 2.5D character is a different
        # studio's hand on every shot that references it.
        return (
            f"Character sheet of {char.name} ({char.role or 'principal cast'}) in the film's "
            f"medium: {_SLOT_DIRECTION[slot]}. {char.age_body_notes or 'a distinct, consistent character'}. "
            f"Wardrobe: {wardrobe}. Plain mid-grey backdrop, the character fully in focus. "
            f"{look.block()} Same character in every frame of this set. "
            f"{'; '.join(char.forbidden_drift)}."
        )
    realism = f" {look.block()}" if look is not None else ""
    return (
        f"Casting photograph of {char.name} ({char.role or 'principal cast'}): "
        f"{_SLOT_DIRECTION[slot]}. {char.age_body_notes or 'a distinct, consistent person'}. "
        f"Wardrobe: {wardrobe}. Soft neutral studio light, plain mid-grey backdrop, "
        f"sharp focus on the face, photorealistic.{realism} Same person in every frame of this set. "
        f"{'; '.join(char.forbidden_drift)}."
    )


def references_for(package: ProductionPackage, op: DirectorOp) -> list[str]:
    """Earlier approved stills of the same character, as Flow media ids.

    A SETTING plate is anchored to the user's uploaded stills when they exist:
    the uploads ARE the master look of the place — a plate imagined fresh from
    text gave the same location a different room per scene (s-2e0a74644b shot
    one studio as three unrelated sets)."""

    bible = op.artifact_refs.get("bible") or {}
    if bible.get("setting_id"):
        from diony_director import session as _session

        # The plate is the scene's style frame: every look anchor the film
        # has — the user's stills first, then the frames the film already
        # established its look with — so one plate can never be a
        # different studio's room (Blueprint 29).
        refs: list[str] = list(_session.all_upload_paths(op.session_id))
        look = _look_of(package)
        for asset in (look.anchor_assets if look is not None else []):
            if asset and asset not in refs:
                refs.append(asset)
        return refs[:7]
    try:
        char = package.character(str(bible.get("char_id", "")))
    except KeyError:
        return []
    slot = str(bible.get("slot", ""))
    refs: list[str] = []
    for earlier in sheet_slots(package):
        if earlier == slot:
            break
        if op.provider is CloudProvider.GROK:
            still = char.hero_stills.get(earlier)
            if still:
                refs.append(still)
        else:
            media_id = char.flow_media_ids.get(earlier)
            if media_id:
                refs.append(media_id)
    return refs[:7]


def plan_sheet_ops(sid: str, package: ProductionPackage) -> list[DirectorOp]:
    provider, model = sheet_lane()
    resolution = "2k" if provider is CloudProvider.GROK else "image"
    ops: list[DirectorOp] = []
    for char in package.characters:
        if char.face_approved:
            continue
        for slot in sheet_slots(package):
            if slot in char.hero_stills:
                continue
            est = estimate_mod.estimate_op(
                provider=provider,
                model=model,
                mode=CloudGenerationMode.IMAGE,
                duration_s=1,
                resolution=resolution,
            )
            ops.append(
                DirectorOp(
                    id=f"op-{uuid.uuid4().hex[:10]}",
                    session_id=sid,
                    op=DirectorOpType.REIMAGE,
                    provider=provider,
                    model=model,
                    mode=CloudGenerationMode.IMAGE,
                    prompt=_sheet_prompt(char, slot, package),
                    prompt_version="v1",
                    duration_s=1,
                    resolution=resolution,
                    artifact_refs={"bible": {"char_id": char.char_id, "slot": slot}},
                    estimate=est,
                    rationale=f"character sheet · {char.name} · {slot}",
                )
            )
    return ops


PLATE_MODEL_FLOW = "nano-banana-2"


def _plate_prompt(setting, look) -> str:
    """The establishing plate in the film's look. A plate was always a
    'photorealistic cinema still' — under an animated look that made every
    scene's master frame a photograph the clips then had to abandon."""
    if look is not None and not look.photoreal:
        return (
            f"Establishing frame of {setting.name}: wide, empty of people, "
            f"{setting.time_weather or 'night'}. {setting.architecture_notes or ''} "
            f"{setting.grade_language or ''} {look.block()} Composed as a still frame of the "
            f"film, {setting.camera_kit or '35mm-equivalent framing, chest height'}. This exact "
            "place recurs in every shot of the scene. No text."
        )
    look_clause = f" {look.block()}" if look is not None else ""
    return (
        f"Establishing plate of {setting.name}: wide, empty of people, "
        f"{setting.time_weather or 'night'}. {setting.architecture_notes or ''} "
        f"{setting.grade_language or ''} Photorealistic cinema still, "
        f"{setting.camera_kit or '35mm, chest height'}.{look_clause} This exact place recurs "
        "in every shot of the scene."
    )


def plan_plate_ops(sid: str, package: ProductionPackage) -> list[DirectorOp]:
    """One establishing plate per setting: the scene lock every shot hangs from."""
    provider, model = sheet_lane()
    resolution = "2k" if provider is CloudProvider.GROK else "image"
    ops: list[DirectorOp] = []
    for setting in package.settings:
        if setting.plate_asset_id:
            continue
        est = estimate_mod.estimate_op(
            provider=provider,
            model=model,
            mode=CloudGenerationMode.IMAGE,
            duration_s=1,
            resolution=resolution,
        )
        prompt = _plate_prompt(setting, _look_of(package))
        ops.append(
            DirectorOp(
                id=f"op-{uuid.uuid4().hex[:10]}",
                session_id=sid,
                op=DirectorOpType.REIMAGE,
                provider=provider,
                model=model,
                mode=CloudGenerationMode.IMAGE,
                prompt=prompt,
                prompt_version="v1",
                duration_s=1,
                resolution=resolution,
                artifact_refs={"bible": {"setting_id": setting.setting_id}},
                estimate=est,
                rationale=f"setting plate · {setting.name}",
            )
        )
    return ops


def register_plate(package: ProductionPackage, op: DirectorOp, *, path: Path) -> None:
    bible = op.artifact_refs.get("bible") or {}
    setting = package.setting(str(bible.get("setting_id", "")))
    setting.plate_asset_id = str(path)


def register_still(
    package: ProductionPackage,
    op: DirectorOp,
    *,
    path: Path,
    media_generation_id: str = "",
) -> CharacterProfile:
    bible = op.artifact_refs.get("bible") or {}
    char = package.character(str(bible.get("char_id", "")))
    slot = str(bible.get("slot", ""))
    char.hero_stills[slot] = str(path)
    if str(path) not in char.grok_ref_pack:
        char.grok_ref_pack.append(str(path))
    if media_generation_id:
        char.flow_media_ids[slot] = media_generation_id
    return char
