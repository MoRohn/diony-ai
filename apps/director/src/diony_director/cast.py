"""Blueprint 32 — Cast Lock: character identity that holds across the film.

The Director's pre-32 identity path was real but soft: a prose character
block re-derived per clip and shrunk first under length pressure; a
five-slot sheet chained one still at a time (one reference on the Grok
image lane); no measurement of whether the sheet was one person; identity
references dropped on every bridged clip because frames and references are
exclusive on every lane; the Flow character object reachable only by an
unwired endpoint; and no way to write a character who speaks from outside
the frame.

Cast Lock is a per-session mode:

- ``standard`` — the pre-32 platform, byte-identical.
- ``locked`` — the studio doctrine the frontier tools reward: the image is
  the identity contract and the video prompt is the motion contract.

  1. **Identity card** (`IdentityCard`): one locked descriptor per character
     — anchor, non-negotiable features, wardrobe lock, negatives — carried
     BYTE-IDENTICAL at priority 0 on every prompt that shows the character,
     placed before scene and style so atmosphere never dilutes the face.
  2. **Board sheet**: the anchor portrait first, then ONE turnaround board
     (front, three-quarter, profile, full body in a 2×2 grid) drawn FROM the
     anchor on the image edit lane — a grid is one person by construction,
     and it packs four views into a single reference slot on lanes that
     take three.
  3. **Measured sheet**: every board is scored against the anchor with the
     Producer's YuNet+SFace evidence (cosine ≥ 0.363); a board that reads
     as another person is redrawn, never approved by hope. The models stage
     on the desk with ``diony-director-check --stage-face-models``.
  4. **Cast keyframes**: a clip that shows the cast opens from a keyframe
     the image lane composes from [identity board, setting plate, previous
     frame] — the still model holds the face, the video model animates it.
     The bridge stays fluid (the previous frame rides as a reference of the
     keyframe), and identity is never dropped for a frame again.
  5. **Provider identity markers**: Grok prompts tag ``<IMAGE_n>`` and Flow
     prompts tag ``@character_n`` / ``@referenceImage_n`` with what each
     reference IS, the way both providers document it.
  6. **Flow characters register themselves** from the anchor and the board
     (the endpoint's documented two-image maximum) once the sheet coheres.

Off-screen lines (screenplay ``(O.S.)``) are independent of the mode: a
dialogue line flagged ``off_screen`` holds the picture on who hears it, packs
the VISIBLE cast for identity, and voices the speaker from beyond the frame
edge — by the post narrator (Narrated), or in the clip as a voice arriving
from off-screen with no mouth forming the words (Dialogue, Mix).
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

from diony_contracts.production import CharacterProfile, IdentityCard

DEFAULT_MODE = "standard"
MODES = ("standard", "locked")

CATALOG: tuple[dict[str, str], ...] = (
    {
        "key": "standard",
        "label": "Standard",
        "hint": (
            "character prose on each prompt, a five-angle sheet, references "
            "where the lane allows them (the pre-32 platform)"
        ),
    },
    {
        "key": "locked",
        "label": "Locked",
        "hint": (
            "identity cards verbatim on every prompt, a measured anchor + "
            "turnaround board, cast keyframes composed from the sheet before "
            "each clip rolls, provider identity markers, Flow characters"
        ),
    },
)


def catalog() -> list[dict[str, str]]:
    return [dict(entry) for entry in CATALOG]


def normalize_mode(value: str | None) -> str:
    """A console/API cast-lock value as the platform knows it. '' resolves to
    the Standard default; an unknown name raises ValueError."""
    chosen = str(value or "").strip().lower()
    if not chosen:
        return DEFAULT_MODE
    if chosen not in MODES:
        raise ValueError(
            f"unknown cast lock {value!r} — choose one of: " + ", ".join(MODES)
        )
    return chosen


def describe(mode: str) -> str:
    resolved = normalize_mode(mode)
    return next(e["label"] for e in CATALOG if e["key"] == resolved)


# --------------------------------------------------------------------------
# The identity card
# --------------------------------------------------------------------------

_SPLIT = re.compile(r"[;,]\s*|\s+(?:and|with)\s+")
MAX_FEATURES = 5
MAX_ANCHOR_CHARS = 140


def derive_identity(char: CharacterProfile) -> IdentityCard:
    """A card written deterministically from the character's casting notes:
    the first clause is the anchor, the rest are the non-negotiables, the
    wardrobe is the film-long outfit, the negatives are the forbidden drift.
    Never a brain call — the same notes always yield the same card."""
    notes = " ".join(str(char.age_body_notes or "").split()).strip().rstrip(".")
    anchor = ""
    features: list[str] = []
    if notes:
        pieces = [p.strip().rstrip(".") for p in _SPLIT.split(notes) if p and p.strip()]
        if pieces:
            anchor = pieces[0]
            # a long first clause is the whole read — keep it, cap it honestly
            if len(anchor) > MAX_ANCHOR_CHARS:
                anchor = anchor[:MAX_ANCHOR_CHARS].rsplit(" ", 1)[0]
            features = [p for p in pieces[1:] if len(p) >= 3][:MAX_FEATURES]
    if not anchor:
        anchor = "one consistent, castable person"
    wardrobe = next(iter(char.wardrobe.values()), "").strip()
    return IdentityCard(
        anchor=anchor,
        features=features,
        wardrobe_lock=wardrobe,
        negatives=list(char.forbidden_drift),
        source="derived",
    )


def ensure_identity(char: CharacterProfile) -> IdentityCard:
    """The character's card, derived on first use and kept thereafter — a
    brain-written card is never overwritten by the derivation."""
    if char.identity is None:
        char.identity = derive_identity(char)
    return char.identity


def parse_brain_identity(raw: Any, char: CharacterProfile) -> IdentityCard | None:
    """A card the screenwriter brain returned (`identity: {anchor, features,
    negatives?}`); None when the brain wrote nothing usable."""
    if not isinstance(raw, dict):
        return None
    anchor = " ".join(str(raw.get("anchor", "") or "").split()).strip()
    features_raw = raw.get("features") or []
    features = [
        " ".join(str(f).split()).strip().rstrip(".")
        for f in (features_raw if isinstance(features_raw, list) else [features_raw])
        if str(f).strip()
    ][:MAX_FEATURES]
    if not anchor and not features:
        return None
    negatives_raw = raw.get("negatives") or []
    negatives = [
        " ".join(str(n).split()).strip()
        for n in (negatives_raw if isinstance(negatives_raw, list) else [negatives_raw])
        if str(n).strip()
    ]
    return IdentityCard(
        anchor=anchor or "one consistent, castable person",
        features=features,
        wardrobe_lock=next(iter(char.wardrobe.values()), "").strip(),
        negatives=negatives or list(char.forbidden_drift),
        source="brain",
    )


def identity_block(char: CharacterProfile) -> str:
    """The verbatim IDENTITY LOCK clause for this character."""
    return ensure_identity(char).block(char.name)


# --------------------------------------------------------------------------
# The board sheet
# --------------------------------------------------------------------------

#: Locked mode sheets: the anchor portrait, then ONE turnaround board drawn
#: from it. Standard keeps `artifacts.SHEET_SLOTS` (five chained angles).
LOCKED_SHEET_SLOTS = ("front", "board")
BOARD_SLOT = "board"

BOARD_DIRECTION = (
    "a character turnaround board: a clean 2 by 2 grid of four equal panels "
    "showing the SAME person from the reference — top left: straight-on "
    "head-and-shoulders, eye level, neutral expression; top right: "
    "three-quarter view from the subject's left, head and shoulders; bottom "
    "left: true profile, facing left of frame; bottom right: full body "
    "standing, whole wardrobe visible, plain ground. Nothing in the hands, "
    "no props, no captions, no logos, no text; the same neutral light and "
    "plain mid-grey backdrop in every panel; the face sharp in every panel"
)


def board_prompt(char: CharacterProfile, look=None) -> str:
    """The turnaround board, composed FROM the anchor portrait it references:
    the identity card verbatim, four named panels, one person by construction."""
    card = identity_block(char)
    if look is not None and not getattr(look, "photoreal", True):
        medium = f"In the film's medium. {look.block()}"
    else:
        medium = "Photorealistic casting photography, sharp focus on the face."
        if look is not None:
            medium += f" {look.block()}"
    return (
        f"Using the provided portrait of {char.name} as the exact same person, draw "
        f"{BOARD_DIRECTION}. {card} {medium}"
    )


def identity_refs(char: CharacterProfile) -> list[str]:
    """The character's reference stills for a video lane, best first: the
    board (four views in one slot), then the anchor face, then the rest."""
    ordered: list[str] = []
    for slot in (BOARD_SLOT, "front"):
        still = char.hero_stills.get(slot)
        if still and still not in ordered:
            ordered.append(still)
    for still in char.grok_ref_pack:
        if still not in ordered:
            ordered.append(still)
    return ordered


def flow_character_media(char: CharacterProfile) -> list[str]:
    """The two Flow-hosted stills a character object is built from (the
    endpoint's documented maximum is two): the anchor face, then the board."""
    ordered: list[str] = []
    for slot in ("front", BOARD_SLOT):
        media = char.flow_media_ids.get(slot)
        if media and media not in ordered:
            ordered.append(media)
    for media in char.flow_media_ids.values():
        if media not in ordered:
            ordered.append(media)
    return ordered[:2]


# --------------------------------------------------------------------------
# The measured sheet
# --------------------------------------------------------------------------

MAX_SLOT_REDRAWS = 2


def _still_identity(reference: Path, candidate: Path):
    try:
        from diony_producer.face_identity import measure_still_identity
    except ImportError:
        return None
    return measure_still_identity(reference, candidate)


def face_models_staged() -> bool:
    try:
        from diony_producer.face_identity import staged_face_models
    except ImportError:
        return False
    return staged_face_models() is not None


def measure_sheet(char: CharacterProfile) -> dict[str, Any]:
    """Score every other still of the sheet against the anchor (front) with
    the Producer's face evidence. The receipt is honest about what it could
    not measure: no models → ``measured: False``; a still with no usable
    face → named in ``notes`` and left out of the verdict."""
    anchor = char.hero_stills.get("front", "")
    receipt: dict[str, Any] = {
        "measured": False,
        "threshold": None,
        "scores": {},
        "min": None,
        "verified": None,
        "failed": [],
        "notes": [],
    }
    if not anchor or not Path(anchor).is_file():
        receipt["notes"].append("no anchor still yet")
        return receipt
    others = [(slot, path) for slot, path in char.hero_stills.items() if slot != "front" and path]
    if not others:
        receipt["notes"].append("only the anchor has landed")
        return receipt
    for slot, path in others:
        result = _still_identity(Path(anchor), Path(path))
        if result is None or not result.measured:
            receipt["notes"].append(
                "face models are not staged — run diony-director-check --stage-face-models"
                if result is None or "not staged" in (result.error or "")
                else f"{slot}: {result.error or 'unmeasurable'}"
            )
            return receipt
        receipt["threshold"] = result.threshold
        if result.verified is None:
            receipt["notes"].append(f"{slot}: {'; '.join(result.notes) or 'no usable face'}")
            continue
        receipt["scores"][slot] = result.similarity_min
        if not result.verified:
            receipt["failed"].append(slot)
    if receipt["scores"]:
        receipt["measured"] = True
        receipt["min"] = min(receipt["scores"].values())
        receipt["verified"] = not receipt["failed"]
    return receipt


def sheet_summary(char: CharacterProfile) -> str:
    """One line for the console and the bible card."""
    receipt = char.sheet_receipt or {}
    if not receipt.get("measured"):
        note = "; ".join(receipt.get("notes") or []) or "unmeasured"
        return f"sheet unmeasured ({note})"
    scores = receipt.get("scores") or {}
    if receipt.get("verified"):
        return (
            f"sheet coheres — {len(scores)}/{len(scores)} views read as {char.name} "
            f"(min {receipt.get('min')}, threshold {receipt.get('threshold')})"
        )
    failed = ", ".join(receipt.get("failed") or [])
    return (
        f"sheet does NOT cohere — {failed} read as another person "
        f"(min {receipt.get('min')}, threshold {receipt.get('threshold')})"
    )


# --------------------------------------------------------------------------
# Staging the face models on the desk
# --------------------------------------------------------------------------

OPENCV_ZOO_RAW = "https://raw.githubusercontent.com/opencv/opencv_zoo/main/models"
FACE_MODEL_SOURCES = {
    "face_detection_yunet_2023mar.onnx": f"{OPENCV_ZOO_RAW}/face_detection_yunet/face_detection_yunet_2023mar.onnx",
    "face_recognition_sface_2021dec.onnx": f"{OPENCV_ZOO_RAW}/face_recognition_sface/face_recognition_sface_2021dec.onnx",
}


def stage_face_models(dest: Path | None = None, *, fetch=None) -> tuple[Path, Path]:
    """Download OpenCV Zoo's YuNet detector and SFace recogniser (Apache-2.0,
    ~230 KB and ~37 MB) into the desk's model directory so the identity
    family measures here, not only on the GPU host. ``fetch(url) -> bytes``
    is injectable for tests; the default uses urllib. Existing files are
    kept. Returns the two paths."""
    from diony_producer.face_identity import default_face_model_dir

    folder = dest or default_face_model_dir()
    if folder is None:
        raise RuntimeError("no model directory — set DIONY_FACE_ID_DIR or DIONY_VAULT")
    folder.mkdir(parents=True, exist_ok=True)

    def _default_fetch(url: str) -> bytes:
        import urllib.request

        with urllib.request.urlopen(url, timeout=120) as response:  # noqa: S310 - pinned https
            return response.read()

    fetch = fetch or _default_fetch
    paths: list[Path] = []
    for name, url in FACE_MODEL_SOURCES.items():
        target = folder / name
        if not target.is_file() or target.stat().st_size == 0:
            payload = fetch(url)
            if not payload:
                raise RuntimeError(f"empty download for {name}")
            tmp = target.with_suffix(".part")
            tmp.write_bytes(payload)
            tmp.replace(target)
        paths.append(target)
    return paths[0], paths[1]


# --------------------------------------------------------------------------
# Provider identity markers (reference legends)
# --------------------------------------------------------------------------

ROLE_KINDS = ("character", "plate", "bridge", "look", "upload", "keyframe")


def role(kind: str, label: str = "") -> dict[str, str]:
    return {"kind": kind, "label": label}


_ROLE_TEXT = {
    "character": "{ref} is {label}'s identity sheet — match this exact face, build, hair, and wardrobe.",
    "plate": "{ref} is the setting plate — this exact place, unchanged.",
    "bridge": "{ref} is the previous frame — continue from this pose, light, and framing.",
    "look": "{ref} is the film's look reference — the same medium, palette, and grade.",
    "upload": "{ref} is a reference still supplied by the director — match what it shows.",
    "keyframe": "{ref} is the composed opening frame — the shot begins exactly here.",
}


def legend(roles: list[dict[str, str]], provider: str, *, characters: list[str] | None = None) -> str:
    """What each reference IS, in the marker dialect the provider documents:
    Grok reads ``<IMAGE_n>`` (docs.x.ai reference-to-video); Flow reads
    ``@referenceImage_n`` and ``@character_n`` (useapi inline markers — every
    marker must match a body slot, so only supplied slots are named)."""
    lines: list[str] = []
    if provider == "grok":
        for index, entry in enumerate(roles, start=1):
            template = _ROLE_TEXT.get(entry.get("kind", ""), "")
            if template:
                lines.append(template.format(ref=f"<IMAGE_{index}>", label=entry.get("label", "")))
    else:
        for index, name in enumerate(characters or [], start=1):
            lines.append(f"@character_{index} is {name} — this exact person, face and wardrobe.")
        for index, entry in enumerate(roles, start=1):
            template = _ROLE_TEXT.get(entry.get("kind", ""), "")
            if template:
                lines.append(template.format(ref=f"@referenceImage_{index}", label=entry.get("label", "")))
    return " ".join(lines)


# --------------------------------------------------------------------------
# Cast keyframes
# --------------------------------------------------------------------------


def keyframe_prompt(*, camera: str, action: str, chars: list[CharacterProfile], setting, look=None) -> str:
    """The clip's opening frame, composed as a still: the frame, the first
    beat frozen, every visible character's identity card verbatim, the place,
    the look. The video lane then only animates."""
    cards = " ".join(identity_block(c) for c in chars)
    place = f"{setting.name}"
    details = " ".join(
        x for x in (getattr(setting, "time_weather", ""), getattr(setting, "architecture_notes", "")) if x
    )
    if details:
        place += f" — {details}"
    first_beat = " ".join(str(action or "").split())
    if first_beat:
        first_beat = re.split(r"(?<=[.!?])\s+", first_beat, maxsplit=1)[0]
    if look is not None:
        medium = look.block() if not getattr(look, "photoreal", True) else f"Photorealistic cinema still. {look.block()}"
    else:
        medium = "Photorealistic cinema still."
    names = ", ".join(c.name for c in chars)
    return " ".join(
        x
        for x in (
            f"Opening frame of a shot: {camera.strip().rstrip('.')}.",
            f"{first_beat.rstrip('.')}, frozen at its first instant." if first_beat else "",
            f"{names} exactly as in the identity references — same face, build, hair, wardrobe." if names else "",
            cards,
            f"Setting: {place}. This exact place, as in the plate reference.",
            medium,
            "A single still frame of the film; no text, no captions, no grid, no split panels.",
        )
        if x
    )


# --------------------------------------------------------------------------
# Off-screen speakers
# --------------------------------------------------------------------------

_SPEAKER_TAG = re.compile(
    r"\s*\((?P<tag>o\.?\s?s\.?|off(?:[- ]?screen|[- ]?camera)?|v\.?\s?o\.?|voice[- ]?over|cont'?d|continuing)\)\s*$",
    re.IGNORECASE,
)


def parse_speaker(raw: str) -> tuple[str, str]:
    """A screenplay cue like ``MARA (O.S.)`` → ``("MARA", "os")``; ``(V.O.)``
    → ``"vo"``; ``(CONT'D)`` is stripped and ignored; a plain name → ``""``."""
    text = " ".join(str(raw or "").split())
    tag = ""
    while True:
        match = _SPEAKER_TAG.search(text)
        if not match:
            break
        found = match.group("tag").lower().replace(" ", "").replace(".", "").replace("-", "")
        if found in {"os", "off", "offscreen", "offcamera"}:
            tag = "os"
        elif found in {"vo", "voiceover"}:
            tag = tag or "vo"
        text = text[: match.start()].rstrip()
    return text, tag


def offscreen_camera(speaker: str, listeners: list[str], setting_name: str) -> str:
    """The default frame for an off-screen line: hold on who hears it."""
    if listeners:
        who = " and ".join(listeners[:2])
        return (
            f"Medium close-up on {who}, listening — {speaker} stays out of frame throughout; "
            "the reaction carries the line"
        )
    return (
        f"The empty room of {setting_name}, the camera holding on the space {speaker} speaks "
        f"into from off-screen — {speaker} stays out of frame throughout"
    )


def offscreen_delivery_clause(speaker: str, listeners: list[str]) -> str:
    """Post-voiced (Blueprint 28) off-screen line: the on-screen people
    listen; no mouth forms words; the voice is recorded separately."""
    who = " and ".join(listeners[:2]) if listeners else "The people on screen"
    return (
        f"{speaker} speaks from off-screen — out of frame for the whole shot. {who} "
        f"{'listen' if (len(listeners) != 1) else 'listens'} and react{'' if len(listeners) != 1 else 's'}; "
        "no one on screen forms words. The off-screen voice is recorded separately and is "
        "not heard on this track."
    )


def offscreen_spoken_clause(speaker: str, line: str, read: str, listeners: list[str]) -> str:
    """Generated (Blueprint 31) off-screen line: the words verbatim, arriving
    from beyond the frame edge, with no on-screen mouth forming them."""
    who = " and ".join(listeners[:2]) if listeners else "the people on screen"
    return (
        f"{speaker} speaks from OFF-SCREEN — out of frame for the whole shot — exactly these "
        f'words and nothing else: "{line}"{read}. The voice arrives from beyond the frame '
        f"edge, clear and close-miked; {who} listen and react, and no one on screen "
        "mouths the words."
    )


__all__ = [
    "BOARD_SLOT",
    "CATALOG",
    "DEFAULT_MODE",
    "LOCKED_SHEET_SLOTS",
    "MAX_SLOT_REDRAWS",
    "MODES",
    "board_prompt",
    "catalog",
    "derive_identity",
    "describe",
    "ensure_identity",
    "face_models_staged",
    "flow_character_media",
    "identity_block",
    "identity_refs",
    "keyframe_prompt",
    "legend",
    "measure_sheet",
    "normalize_mode",
    "offscreen_camera",
    "offscreen_delivery_clause",
    "offscreen_spoken_clause",
    "parse_brain_identity",
    "parse_speaker",
    "role",
    "sheet_summary",
    "stage_face_models",
]
