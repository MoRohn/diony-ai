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


def _sheet_prompt(char: CharacterProfile, slot: str) -> str:
    wardrobe = next(iter(char.wardrobe.values()), "practical, lived-in wardrobe")
    return (
        f"Casting photograph of {char.name} ({char.role or 'principal cast'}): "
        f"{_SLOT_DIRECTION[slot]}. {char.age_body_notes or 'a distinct, consistent person'}. "
        f"Wardrobe: {wardrobe}. Soft neutral studio light, plain mid-grey backdrop, "
        f"sharp focus on the face, photorealistic. Same person in every frame of this set. "
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

        start, extras = _session.upload_refs(op.session_id)
        return [r for r in [start] + extras if r][:1]
    try:
        char = package.character(str(bible.get("char_id", "")))
    except KeyError:
        return []
    slot = str(bible.get("slot", ""))
    refs: list[str] = []
    for earlier in SHEET_SLOTS:
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
        for slot in SHEET_SLOTS:
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
                    prompt=_sheet_prompt(char, slot),
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
        prompt = (
            f"Establishing plate of {setting.name}: wide, empty of people, "
            f"{setting.time_weather or 'night'}. {setting.architecture_notes or ''} "
            f"{setting.grade_language or ''} Photorealistic cinema still, "
            f"{setting.camera_kit or '35mm, chest height'}. This exact place recurs "
            "in every shot of the scene."
        )
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
