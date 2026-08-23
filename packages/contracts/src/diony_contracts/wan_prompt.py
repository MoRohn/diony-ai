"""Deterministic Wan2.2 prompt compiler and dispatch guardrails.

The persisted contract follows Subject -> Motion -> Camera -> Scene/Lighting.
The on-site brain may enrich motion and atmosphere, but identity and camera
facts remain program-owned.
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import TYPE_CHECKING

from diony_contracts.enums import CameraMove, ContinuityMode, ReferenceLock
from diony_contracts.models import WanPromptPlan

if TYPE_CHECKING:
    from diony_contracts.models import Shot


WAN22_PROMPT_STRATEGY_VERSION = "wan22-five-layer-v3"

_PACE_CUES = (
    "slow",
    "gradual",
    "gentle",
    "brisk",
    "quick",
    "steady",
    "steadily",
    "measured",
    "relaxed pace",
    "continuous",
)
_DIRECTION_CUES = (
    "left",
    "right",
    "toward",
    "away",
    "forward",
    "back",
    "upward",
    "downward",
    "up",
    "down",
    "across",
)
_MECHANICS_CUES = (
    "hand",
    "head",
    "eye",
    "shoulder",
    "weight",
    "step",
    "breath",
    "turn",
    "settle",
    "hold",
    "background",
    "fabric",
)
_LIGHT_CUES = (
    "light",
    "lighting",
    "shadow",
    "practical",
    "sunlight",
    "overcast",
    "neon",
    "candle",
)
_CAMERA_GROUPS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("static", ("static", "locked-off")),
    ("pan", ("pan left", "pan right", "panning")),
    ("tilt", ("tilt up", "tilt down", "tilting")),
    ("push", ("push-in", "push in", "dolly in")),
    ("pull", ("pull-back", "pull back", "dolly out")),
    ("track", ("tracking move", "tracking shot", "track left", "track right")),
    ("crane", ("crane move", "crane shot")),
    ("handheld", ("handheld movement", "handheld camera")),
    ("steadicam", ("steadicam",)),
    ("zoom", ("optical zoom", "zoom in", "zoom out")),
)
_ACTION_VERBS = re.compile(
    r"\b(acts?|adjusts?|animates?|answers?|approaches|arrives?|assembles?|awakens?|"
    r"builds|carries|chases|circles?|collaborat(?:es|ing)|cooks?|crosses|dances?|"
    r"delivers|discovers|drives|enters|examines?|explores?|finds|fixes|follows|"
    r"gathers?|guides?|holds|lifts?|looks|opens|paints|raises|reads|repairs?|"
    r"restores?|reveals?|runs|searches|smiles?|sorts?|speaks|steps|studies|"
    r"touches|turns|unlocks?|walks|watches|writes)\b",
    re.IGNORECASE,
)
_ENVIRONMENT_CUE = re.compile(
    r"\b(across|along|at|beside|during|in|inside|near|on|outside|through|under|within)\b",
    re.IGNORECASE,
)

_GENERIC_NEGATIVE_TERMS = (
    "blurry",
    "low quality",
    "warping",
    "face deformation",
    "distorted face",
    "distorted hands",
    "distorted fingers",
    "bad anatomy",
    "extra limbs",
    "extra fingers",
    "fused fingers",
    "morphing face",
    "inconsistent face",
    "identity change",
    "identity drift",
    "flickering",
    "flicker",
    "jitter",
    "teleporting props",
    "teleports",
    "still image",
    "static motion",
    "frozen motion",
    "text",
    "logo",
    "watermark",
    "jpeg artifacts",
    "plastic skin",
    "wax face",
    "airbrushed",
    "cgi render",
    "unwanted cuts",
    "sudden cuts",
    "crushed blacks",
    "illegal broadcast levels",
    "720p as finished look",
)
_REFERENCE_IDENTITY_NEGATIVE_TERMS = (
    "live-action presenter",
    "different character",
    "medium change",
    "photorealism",
    "screen within a screen",
    "picture-in-picture",
    "framed artwork",
    "gallery display",
)
_REFERENCE_BACKGROUND_NEGATIVE_TERMS = (
    "new background",
    "palette shift",
    "title card",
    "subtitle",
    "caption",
    "typography",
    "cutaway",
    "montage",
    "split screen",
)


def _clean_layer(value: str) -> str:
    return re.sub(r"\s+", " ", value.strip().strip(".;,- "))


def _limited_words(value: str, limit: int) -> str:
    words = _clean_layer(value).split()
    return " ".join(words[:limit])


def _idea_subject(idea: str, reference_lock: ReferenceLock, wardrobe: str) -> str:
    if reference_lock != ReferenceLock.NONE:
        return (
            "The exact supplied subject, preserving the same face or design, silhouette, "
            "proportions, wardrobe, and identifying details"
        )
    normalized = _clean_layer(idea)
    match = _ACTION_VERBS.search(normalized)
    core = normalized[: match.start()].strip(" ,;:-") if match else normalized
    core = _limited_words(core, 10)
    if len(core.split()) < 2:
        core = "The Bible-locked lead subject"
    return _clean_layer(
        f"{core}, same face, silhouette, wardrobe, and identifying details throughout"
    )


def _identity_anchors(reference_lock: ReferenceLock, wardrobe: str) -> list[str]:
    wardrobe_anchor = _limited_words(wardrobe, 8) or "Bible-locked wardrobe"
    if reference_lock != ReferenceLock.NONE:
        return [
            "identical face or original character design",
            "stable silhouette and body proportions",
            f"unchanged {wardrobe_anchor}",
            "original rendering medium and identifying marks",
        ]
    return [
        "same face or design geometry",
        "stable silhouette and body proportions",
        f"unchanged {wardrobe_anchor}",
        "consistent identifying details",
    ]


def _idea_action(idea: str) -> str:
    normalized = _clean_layer(idea)
    lowered = normalized.casefold()
    if lowered.startswith(("animate this image as ", "animate the image as ")):
        performance = normalized.split(" as ", 1)[1].split(" References:", 1)[0]
        gerund = re.search(r"\b(brainstorming|collaborating|directing|running|shaping|thinking|walking)\b", performance, re.IGNORECASE)
        if gerund:
            finite = {
                "brainstorming": "brainstorms",
                "collaborating": "collaborates",
                "directing": "directs",
                "running": "runs",
                "shaping": "shapes",
                "thinking": "thinks",
                "walking": "walks",
            }[gerund.group(0).casefold()]
            return _limited_words(finite + performance[gerund.end() :], 8)
    match = _ACTION_VERBS.search(normalized)
    if not match:
        return "continues the story beat"
    action = normalized[match.start() :].split(" References:", 1)[0].split(";", 1)[0]
    environment = _ENVIRONMENT_CUE.search(action, match.end() - match.start())
    if environment and len(action[: environment.start()].split()) >= 2:
        action = action[: environment.start()]
    action = _limited_words(action, 10)
    dangling = {"a", "an", "and", "at", "for", "from", "in", "of", "on", "the", "to", "with"}
    words = action.split()
    while words and words[-1].casefold() in dangling:
        words.pop()
    return " ".join(words) or "continues the story beat"


def _environment_from_world(world: str) -> str:
    normalized = _clean_layer(world).split(" References:", 1)[0]
    if ":" in normalized:
        normalized = normalized.split(":", 1)[1].strip()
    camera_matches = [
        match
        for _, cues in _CAMERA_GROUPS
        for cue in cues
        if (match := re.search(rf"\b{re.escape(cue)}\b", normalized, re.IGNORECASE))
    ]
    if camera_matches:
        camera = min(camera_matches, key=lambda match: match.start())
        comma = normalized.find(",", camera.end())
        if comma >= 0:
            visual_tail = _limited_words(normalized[comma + 1 :], 10)
            if len(visual_tail.split()) >= 2:
                return visual_tail
    action = _ACTION_VERBS.search(normalized)
    search_from = action.end() if action else 0
    camera_spans = [match.span() for match in camera_matches]
    for environment in _ENVIRONMENT_CUE.finditer(normalized, search_from):
        if any(start <= environment.start() < end for start, end in camera_spans):
            continue
        return _limited_words(normalized[environment.start() :], 10)
    words = normalized.split()
    return " ".join(words[-8:]) or "the Bible-locked world"


def _motion_layer(idea: str, shot_index: int) -> str:
    action = _idea_action(idea)
    patterns = (
        f"{action} at a measured pace toward camera, shifting weight naturally before a final hold",
        f"{action} slowly from left to right, turning the head gently before a stable final eyeline",
        f"{action} at a steady pace from right to left, with one precise hand adjustment before rest",
        f"{action} gradually forward, shifting body weight before a gentle turn toward frame right",
        f"{action} continuously toward the listener, with controlled eye and hand motion through the final beat",
        f"{action} slowly away from camera, shoulders settling naturally into a clean final hold",
    )
    return patterns[shot_index % len(patterns)]


def _framing(focal_mm: float) -> str:
    if focal_mm <= 24:
        return "wide shot"
    if focal_mm <= 40:
        return "medium shot"
    if focal_mm <= 65:
        return "medium close-up"
    return "close-up"


def _camera_layer(camera_move: CameraMove, focal_mm: float, t_stop: float) -> str:
    moves = {
        CameraMove.STATIC: "static camera with no movement",
        CameraMove.PAN: "slow steady pan right",
        CameraMove.TILT: "slow steady tilt up",
        CameraMove.DOLLY_IN: "slow steady push-in",
        CameraMove.DOLLY_OUT: "slow steady pull-back",
        CameraMove.TRACK: "slow tracking move left to right",
        CameraMove.CRANE: "slow crane move upward",
        CameraMove.HANDHELD: "slight controlled handheld movement",
        CameraMove.STEADICAM: "steady steadicam move forward",
        CameraMove.ZOOM: "slow optical zoom in",
    }
    return (
        f"{_framing(focal_mm)}, {focal_mm:g}mm lens at T{t_stop:g}, "
        f"{moves[camera_move]}"
    )


def _scene_layers(
    *,
    world: str,
    lighting_ratio: str,
    reference_lock: ReferenceLock,
    continuity_mode: ContinuityMode,
    sample_label: str,
) -> tuple[str, str]:
    world_summary = _environment_from_world(world)
    if reference_lock == ReferenceLock.CHARACTER_AND_BACKGROUND:
        texture = (
            "original rendering medium, palette, background texture, and subject identity; "
            "no presenter or display frame"
        )
        setting = "supplied background"
    elif reference_lock == ReferenceLock.CHARACTER:
        texture = "original rendering medium and subject identity; natural texture and depth"
        setting = _limited_words(world_summary, 6)
    else:
        texture = "natural texture, fabric detail, atmospheric depth, and restrained film grain"
        setting = world_summary
    continuity = ""
    if continuity_mode == ContinuityMode.PREVIOUS_FRAME:
        continuity = "; begin from the previous final frame"
    elif continuity_mode == ContinuityMode.REFERENCE:
        continuity = "; supplied reference remains the visual anchor"
    sample = f"; {sample_label}" if sample_label else ""
    aesthetic = _clean_layer(
        f"{lighting_ratio} motivated lighting remains stable; {setting}; {texture}; "
        "cinematic Rec.709 color, natural contrast, consistent exposure, restrained film grain"
    )
    environment = _clean_layer(
        "Foreground atmosphere moves gently against stable background geometry, creating natural "
        "parallax without competing with the subject"
        f"{continuity}{sample}"
    )
    if reference_lock == ReferenceLock.CHARACTER_AND_BACKGROUND:
        environment = _clean_layer(
            "The supplied background keeps composition, palette, scale, and geometry while original "
            "ambient details move subtly for natural parallax"
            f"{continuity}{sample}"
        )
    return aesthetic, environment


def negative_terms_for(
    reference_lock: ReferenceLock,
    camera_move: CameraMove,
) -> list[str]:
    terms = list(_GENERIC_NEGATIVE_TERMS)
    if camera_move == CameraMove.STATIC:
        terms.extend(("unwanted camera movement", "shaky camera", "motion blur"))
    else:
        terms.extend(("camera jump", "abrupt zoom", "random pan"))
    if reference_lock != ReferenceLock.NONE:
        terms.extend(_REFERENCE_IDENTITY_NEGATIVE_TERMS)
    if reference_lock == ReferenceLock.CHARACTER_AND_BACKGROUND:
        terms.extend(_REFERENCE_BACKGROUND_NEGATIVE_TERMS)
    return list(dict.fromkeys(terms))


def render_wan_prompt(plan: WanPromptPlan) -> str:
    layers = (plan.subject, plan.motion, plan.camera, plan.scene)
    if plan.environment_dynamics:
        layers = (*layers, plan.environment_dynamics)
    return "; ".join(_clean_layer(layer) for layer in layers) + "."


def render_negative_prompt(plan: WanPromptPlan) -> str:
    return ", ".join(_clean_layer(term) for term in plan.negative_terms if _clean_layer(term))


def validate_wan_prompt_plan(plan: WanPromptPlan) -> list[str]:
    diagnostics: list[str] = []
    prompt = render_wan_prompt(plan)
    word_count = len(prompt.split())
    is_v3 = plan.strategy_version == WAN22_PROMPT_STRATEGY_VERSION
    minimum = 80 if is_v3 else 25
    maximum = 140 if is_v3 else 80
    if word_count < minimum:
        diagnostics.append(f"prompt is shorter than the {minimum}-word quality floor")
    if word_count > maximum:
        diagnostics.append(f"prompt exceeds the {maximum}-word attention budget")
    if is_v3:
        if not 2 <= len(plan.identity_anchors) <= 4:
            diagnostics.append("subject needs two to four explicit identity anchors")
        subject = plan.subject.casefold()
        if any(anchor.casefold() not in subject for anchor in plan.identity_anchors):
            diagnostics.append("subject must contain every persisted identity anchor")
        if not plan.environment_dynamics.strip():
            diagnostics.append("environment dynamics are required")
        if not plan.high_noise_plan.strip():
            diagnostics.append("high-noise layout and motion intent is required")
        if not plan.low_noise_plan.strip():
            diagnostics.append("low-noise detail and identity intent is required")
    motion = plan.motion.casefold()
    if not any(cue in motion for cue in _PACE_CUES):
        diagnostics.append("motion needs an explicit pace")
    if not any(cue in motion for cue in _DIRECTION_CUES):
        diagnostics.append("motion needs an explicit direction")
    if not any(cue in motion for cue in _MECHANICS_CUES):
        diagnostics.append("motion needs explicit body or environmental mechanics")
    camera = plan.camera.casefold()
    found_moves = [
        name for name, cues in _CAMERA_GROUPS if any(cue in camera for cue in cues)
    ]
    if len(found_moves) == 0:
        diagnostics.append("camera needs one explicit movement instruction")
    elif len(found_moves) > 1:
        diagnostics.append("camera instructions conflict")
    if not any(cue in camera for cue in ("wide shot", "medium shot", "close-up")):
        diagnostics.append("camera needs explicit framing")
    if not re.search(r"\b\d+(?:\.\d+)?mm\b", camera):
        diagnostics.append("camera needs a focal length")
    if not re.search(r"\bT\d+(?:\.\d+)?\b", plan.camera):
        diagnostics.append("camera needs a T-stop")
    scene = plan.scene.casefold()
    if not any(cue in scene for cue in _LIGHT_CUES):
        diagnostics.append("scene needs an explicit light source or lighting plan")
    return diagnostics


def make_wan_prompt_plan(
    *,
    source_idea_sha256: str,
    subject: str,
    motion: str,
    camera: str,
    scene: str,
    environment_dynamics: str = "",
    identity_anchors: list[str] | None = None,
    high_noise_plan: str = "",
    low_noise_plan: str = "",
    generation_method: str = "",
    audio_strategy: str = "silent_plate",
    quality_lora_ids: list[str] | None = None,
    continuity_risks: list[str] | None = None,
    negative_terms: list[str],
    strategy_version: str = WAN22_PROMPT_STRATEGY_VERSION,
) -> WanPromptPlan:
    plan = WanPromptPlan(
        strategy_version=strategy_version,
        source_idea_sha256=source_idea_sha256,
        subject=_clean_layer(subject),
        identity_anchors=[_clean_layer(anchor) for anchor in (identity_anchors or [])],
        motion=_clean_layer(motion),
        camera=_clean_layer(camera),
        scene=_clean_layer(scene),
        environment_dynamics=_clean_layer(environment_dynamics),
        high_noise_plan=_clean_layer(high_noise_plan),
        low_noise_plan=_clean_layer(low_noise_plan),
        generation_method=_clean_layer(generation_method),
        audio_strategy=_clean_layer(audio_strategy) or "silent_plate",
        quality_lora_ids=list(dict.fromkeys(quality_lora_ids or [])),
        continuity_risks=list(dict.fromkeys(continuity_risks or [])),
        negative_terms=list(dict.fromkeys(_clean_layer(term) for term in negative_terms)),
    )
    plan.word_count = len(render_wan_prompt(plan).split())
    plan.diagnostics = validate_wan_prompt_plan(plan)
    return plan


def compile_wan_prompt_plan(
    *,
    idea: str,
    world: str,
    wardrobe: str,
    shot_index: int,
    focal_mm: float,
    t_stop: float,
    lighting_ratio: str,
    camera_move: CameraMove,
    reference_lock: ReferenceLock,
    continuity_mode: ContinuityMode,
    generation_method: str = "",
    audio_strategy: str = "silent_plate",
    sample_seconds: int | None = None,
) -> WanPromptPlan:
    source_material = json.dumps(
        {
            "idea": _clean_layer(idea),
            "world": _clean_layer(world),
            "wardrobe": _clean_layer(wardrobe),
            "reference_lock": reference_lock.value,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    source_hash = hashlib.sha256(source_material.encode("utf-8")).hexdigest()
    sample_label = (
        f"{sample_seconds}s Sample shot {shot_index + 1}"
        if sample_seconds
        else ""
    )
    identity_anchors = _identity_anchors(reference_lock, wardrobe)
    subject = _idea_subject(idea, reference_lock, wardrobe)
    subject = _clean_layer(f"{subject}; identity anchors: {', '.join(identity_anchors)}")
    scene, environment_dynamics = _scene_layers(
        world=world,
        lighting_ratio=lighting_ratio,
        reference_lock=reference_lock,
        continuity_mode=continuity_mode,
        sample_label=sample_label,
    )
    continuity_risks = []
    if continuity_mode == ContinuityMode.PREVIOUS_FRAME:
        continuity_risks = ["boundary identity drift", "exposure mismatch", "motion discontinuity"]
    plan = make_wan_prompt_plan(
        source_idea_sha256=source_hash,
        subject=subject,
        identity_anchors=identity_anchors,
        motion=_motion_layer(idea, shot_index),
        camera=_camera_layer(camera_move, focal_mm, t_stop),
        scene=scene,
        environment_dynamics=environment_dynamics,
        high_noise_plan=(
            "Resolve subject layout, spatial composition, primary motion path, body mechanics, "
            "camera trajectory, and foreground-to-background parallax"
        ),
        low_noise_plan=(
            "Preserve facial or design identity, hands, wardrobe texture, lighting continuity, "
            "fine material detail, clean edges, and natural motion blur"
        ),
        generation_method=generation_method,
        audio_strategy=audio_strategy,
        continuity_risks=continuity_risks,
        negative_terms=negative_terms_for(reference_lock, camera_move),
    )
    if plan.diagnostics:
        raise ValueError("Wan prompt compiler produced an invalid plan: " + "; ".join(plan.diagnostics))
    return plan


def validate_wan_prompt_dispatch(shot: Shot) -> list[str]:
    """Reject a typed prompt if persisted text drifted after compilation."""
    plan = shot.prompt_plan
    if plan is None:
        return []
    diagnostics = validate_wan_prompt_plan(plan)
    if plan.word_count != len(render_wan_prompt(plan).split()):
        diagnostics.append("prompt plan word count is stale")
    if shot.prompt != render_wan_prompt(plan):
        diagnostics.append("positive prompt does not match its typed plan")
    if shot.negative_prompt != render_negative_prompt(plan):
        diagnostics.append("negative prompt does not match its typed plan")
    return list(dict.fromkeys(diagnostics))


def repair_wan_prompt_plan(plan: WanPromptPlan, failure_notes: str) -> WanPromptPlan | None:
    """Repair the diagnosed layer only; unknown failures require human evidence."""
    notes = failure_notes.casefold()
    subject = plan.subject
    motion = plan.motion
    camera = plan.camera
    scene = plan.scene
    environment_dynamics = plan.environment_dynamics
    negative_terms = list(plan.negative_terms)
    if any(cue in notes for cue in ("boundary continuity", "temporal jump")):
        motion = (
            "continues steadily from the previous motion toward frame right, shifts weight "
            "naturally, then holds without a temporal jump"
        )
        negative_terms.append("temporal discontinuity")
        environment_dynamics = (
            f"{plan.environment_dynamics}; preserve the incoming final-frame geometry, exposure, "
            "motion direction, and subject placement through the handoff"
        )
    elif any(cue in notes for cue in ("identity", "face", "morph", "reference lock")):
        subject = (
            "The exact supplied subject with identical face or design, silhouette, proportions, "
            "wardrobe, and defining details in every frame; identity anchors: "
            + ", ".join(plan.identity_anchors)
        )
        negative_terms.extend(("morphing features", "late-shot identity replacement"))
    elif any(
        cue in notes
        for cue in (
            "froze",
            "frozen",
            "no movement",
            "motion stops",
            "static subject",
            "measured motion",
            "temporal coherence",
            "temporal residual",
        )
    ):
        motion = (
            "moves continuously at a steady pace toward frame right, shifts weight through each "
            "step, then completes the action without pausing"
        )
        negative_terms.extend(("motion loop", "mid-clip freeze"))
    elif any(cue in notes for cue in ("camera", "reframe", "unexpected zoom", "unexpected pan")):
        camera = f"{plan.camera}, one uninterrupted take with no reframing"
        negative_terms.extend(("camera jump", "unwanted cut"))
    elif any(cue in notes for cue in ("plastic", "airbrush", "painting", "cgi")):
        scene = f"{plan.scene}; visible material texture and natural imperfections"
        negative_terms.extend(("plastic texture", "synthetic surface"))
    elif any(cue in notes for cue in ("flicker", "flat background", "flat lighting", "exposure")):
        scene = f"{plan.scene}; practical lighting and exposure remain stable frame to frame"
        negative_terms.extend(("lighting flicker", "exposure pumping"))
    else:
        return None
    repaired = make_wan_prompt_plan(
        source_idea_sha256=plan.source_idea_sha256,
        subject=subject,
        motion=motion,
        camera=camera,
        scene=scene,
        environment_dynamics=environment_dynamics,
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
    return repaired if not repaired.diagnostics else None


def repair_wan_prompt_for_qc(shot: Shot, failure_notes: str) -> bool:
    if shot.prompt_plan is None:
        return False
    repaired = repair_wan_prompt_plan(shot.prompt_plan, failure_notes)
    if repaired is None:
        return False
    shot.prompt_plan = repaired
    shot.prompt = render_wan_prompt(repaired)
    shot.negative_prompt = render_negative_prompt(repaired)
    return True


def prompt_receipt_sha256(shot: Shot) -> str:
    """Hash the complete dispatch contract; preserve legacy positive-only receipts."""
    if shot.prompt_plan is None:
        return hashlib.sha256(shot.prompt.encode("utf-8")).hexdigest()
    payload = {
        "prompt": shot.prompt,
        "negative_prompt": shot.negative_prompt,
        "prompt_plan": shot.prompt_plan.model_dump(mode="json"),
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
