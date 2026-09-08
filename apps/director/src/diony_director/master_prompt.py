"""Blueprint 27 — the Master AI Video Prompt Template.

The Idea-to-Prompt stage every Director flow passes through. One creative
brief (idea + optional reference stills + the user's DURATION) goes to the
Director brain with the MASTER TEMPLATE as its instructions and comes back as
a ``MasterPrompt``: the pre-split finalized artifact — WHO, WHAT, WHERE, how
the scene is layered, how the action develops, how the subject performs, how
the environment responds, how the camera observes, how the scene is lit, what
must stay consistent, what must be prevented, exactly how it ends — and the
generator-ready prompt those derivations assemble into.

- A one-shot ships the master prompt as the prompt body (route dialect and
  lexicon guards ride on top — the artifact is provider-neutral prose).
- A scripted production writes its screenplay FROM the artifact, splits the
  screenplay into shots, and every compiled clip inherits the artifact's
  lighting system, physics grounding, continuity anchors, constraints, and —
  on the last clip — the ending.

The user's duration input is a first-class INPUT here: beats scale to it,
the final prompt paces to it, and the derivation reports back how long the
action it wrote actually plays (``estimated_duration_s``) so the booked
length is a decision the user makes with that number in view.

Brain configured → derived by the model and parsed strictly, then a
deterministic OPTIMIZATION PASS runs (the template's own rules: dedupe,
reference-image opener, ending line, constraint count, pacing line, length
band). Brain off → a deterministic template derivation, always named in
``warnings`` — never a silent stand-in.
"""

from __future__ import annotations

import re

from diony_contracts.production import MASTER_BEAT_STAGES, MasterPrompt
from diony_director import likeness as likeness_mod
from diony_director import look as look_mod

# --------------------------------------------------------------------------
# The template, verbatim doctrine. This is what the brain is told.
# --------------------------------------------------------------------------

MASTER_TEMPLATE = """MASTER AI VIDEO PROMPT TEMPLATE

Purpose

Transform a rough idea, story setup, scene description, or reference image into a concise, cinematic, physically believable, generator-ready AI video prompt.

The user should not need to manually define camera behavior, lighting, physics, subject performance, scene depth, continuity rules, or temporal progression. Those elements should be intelligently derived from the supplied creative context.

INPUT

IDEA / STORY / SCENE
[Describe the video you want to create in natural language. This may be as short as a basic concept or as detailed as a complete scene description, story beat, action sequence, visual idea, mood, character interaction, or intended ending.]

OPTIONAL REFERENCE IMAGE
[Provide a reference image when the video should begin from, preserve, or visually match an existing composition, character, environment, object, style, or setting.]

CONTEXT DERIVATION ENGINE

From the IDEA / STORY / SCENE and OPTIONAL REFERENCE IMAGE, automatically determine the most appropriate configuration for the video. Do not require the user to separately specify these elements unless they are already explicitly stated. Infer them conservatively from the creative intent. If the source material already establishes an element, preserve it rather than replacing it with a new interpretation.

1. PRIMARY SUBJECT
Determine: main character, person, animal, object, vehicle, or focal element; relevant appearance; clothing or identifying features; initial body position or orientation; relationship to the camera; important visual anchors. Identify only characteristics that materially affect continuity or storytelling. Do not invent unnecessary physical details.

2. PRIMARY ACTION
Determine the central action the viewer is meant to see. Convert abstract intentions into observable actions. For example: "becomes excited" should become something like: "first reacts subtly, then increasingly moves with more energy." The action should always be visually understandable.

3. TEMPORAL ACTION STRUCTURE
Convert the concept into a natural progression through time. Use the smallest number of beats necessary. Preferred progression: Initial State → Development → Escalation → Payoff → Final State. For simple clips, fewer beats are acceptable. Determine: Initial State (what is happening when the clip begins?); First Change (what begins the action?); Development (how does the action become more noticeable?); Escalation (how does intensity, complexity, or emotion increase?); Payoff (what is the defining visual moment?); Final State (exactly how should the clip conclude?). Never leave the ending ambiguous when the story implies a natural or intentional conclusion.

4. SCENE DEPTH AND LAYERING
Derive a three-dimensional scene structure when useful. Background: environmental context such as architecture, landscape, skyline, walls, distant objects, atmosphere, weather, lighting environment. Middle Ground: supporting elements such as furniture, roads, paths, nearby objects, secondary environmental activity, vehicles, terrain. Foreground: place the primary subject and immediate interactions here. Only include scene layers that contribute meaningfully to depth, action, or realism. Avoid adding decorative clutter merely to make the prompt sound detailed.

5. SPATIAL CONFIGURATION
Infer important spatial relationships. Determine where relevant: subject position within frame; facing direction; direction of travel; left-to-right or right-to-left movement; distance between subjects; relationship between subject and objects; whether the subject remains seated, standing, moving, centered, off-center, etc. Maintain these spatial relationships throughout the video.

6. CAMERA CONFIGURATION
Choose the camera treatment best suited to the scene. Infer: shot size, framing, camera height, lens feeling, camera movement, focus behavior, depth of field, whether framing should remain fixed or evolve. Examples of possible camera strategies: locked wide cinematic shot; subtle handheld realism; slow push-in; tracking shot; observational documentary framing; close-up portrait framing; low-angle action shot; smooth lateral movement. Use one dominant camera philosophy. Do not introduce complex movement unless the story benefits from it. When a reference image is supplied, default to preserving its camera position, composition, perspective, and framing unless movement is explicitly needed.

7. PERFORMANCE AND BODY LANGUAGE
Translate emotion and intention into visible physical behavior. Infer: posture, head movement, eye direction, shoulder behavior, hand movement, weight shifts, hesitation, confidence, tension, relaxation, rhythm, reaction timing. Use subtle physical cues before exaggerated ones. Prefer "shoulders loosen and begin moving with the rhythm" over "looks excited." Human performance should feel spontaneous and physically motivated.

8. MICRO-EXPRESSIONS
When the face is visible and emotionally relevant, infer one or two useful facial behaviors: slight smile, narrowed eyes, subtle eyebrow movement, downward glance, brief eye contact, lips pressed together, restrained expression, head tilt. Do not overload faces with multiple simultaneous expressions. If the face should remain hidden, turned away, obscured, or unchanged, enforce that instead.

9. ENVIRONMENTAL PHYSICS
Determine how the physical environment should respond naturally to the action. Infer where appropriate: gravity, weight, momentum, inertia, acceleration, deceleration, cloth movement, hair movement, water interaction, wind response, rain behavior, dust, debris, reflections, shadows, object collisions, surface contact, balance corrections. Environmental motion should always have an understandable physical cause. Avoid random atmospheric effects disconnected from the scene.

10. BODY MECHANICS
For human or animal movement, preserve believable physical behavior. Infer: weight transfer, foot placement, balance, preparation before large movements, momentum, follow-through, recovery movement, body compression and extension, limb coordination. Large movements should usually contain: anticipation → action → follow-through. This is especially important for dancing, running, jumping, fighting, throwing, falling, sitting, standing, athletic movements, sudden reactions.

11. LIGHTING CONFIGURATION
Infer one coherent lighting system from the location, time, mood, and reference image. Determine: Primary source (sunlight, window light, streetlights, fluorescent lights, tungsten lamp, fire, neon signage, overcast daylight); Light quality (soft, hard, diffused, directional, warm, cool); Shadow behavior (soft natural shadows, long directional shadows, sharp contrast, subdued interior shadows); Secondary or reflected light — add only when visually useful. Lighting must remain physically consistent with the environment. Do not introduce contradictory cinematic lighting merely for drama.

12. ATMOSPHERE AND MOOD
Infer the emotional tone from the supplied concept. Translate mood into visual treatment rather than relying only on adjectives. Playful: natural timing, subtle exaggeration, energetic movement, light visual rhythm. Tense: restricted motion, controlled framing, slower anticipation, harder contrast. Intimate: closer framing, subtle performance, softer lighting, restrained camera movement. Energetic: quicker physical escalation, stronger momentum, responsive camera behavior. Realistic: natural imperfections, restrained movement, believable physics, observational camera style. Mood should support the story rather than override it.

13. VISUAL REALISM LEVEL
Determine whether the concept implies: photorealistic, cinematic realism, documentary realism, stylized realism, surrealism, animation, fantasy, or another clearly established visual treatment. Unless otherwise implied, prefer believable cinematic realism. Avoid unnecessary stylization when the user's idea is grounded in real life.

14. CONTINUITY ANCHORS
Identify the elements most important to preserve throughout the clip: same character identity, same facial appearance, same hairstyle, same clothing, same costume colors, same environment, same furniture placement, same vehicle, same object, same direction of movement, same camera orientation, same weather, same lighting conditions. Reinforce only the most vulnerable continuity elements. Do not repeatedly describe every object.

15. REFERENCE IMAGE PRESERVATION
When a reference image is supplied, treat it as the authoritative starting visual state unless the user's idea explicitly requests a transformation. Derive and preserve where relevant: environment, architecture, subject appearance, clothing, furniture, object placement, framing, camera height, camera angle, perspective, lighting, color relationships, spatial relationships. Begin the final prompt with: "Use the reference image as the exact visual setting and starting composition." Then specify only the most important continuity anchors. Avoid unnecessarily redescribing information already visible in the image.

16. ENDING CONFIGURATION
Infer a clear ending from the story. Determine: final subject position, final pose, final action, final expression, final relationship to objects, final camera composition, whether movement stops or continues, what the viewer should see in the final moment. If the user explicitly defines the ending, preserve it precisely. The action sequence should naturally lead into this final state.

17. FAILURE-PREVENTION CONSTRAINTS
Generate only the negative constraints necessary for the specific scene. Examples: do not reveal the character's face; do not change clothing; do not introduce additional people; do not reverse movement direction; do not change the environment; do not alter the reference-image composition; avoid distorted anatomy; avoid abrupt teleportation; avoid random cuts; avoid unnecessary camera movement. Prefer approximately 3–6 important constraints. Do not create giant generic negative-prompt lists.

FINAL PROMPT ASSEMBLY

Once the context has been derived, construct the final video prompt in this order:
1. VISUAL ANCHOR — subject + central action + setting. Make the primary visual idea immediately clear.
2. STARTING STATE — what the viewer sees at the beginning. If using a reference image, establish it as the exact starting composition.
3. SCENE DEPTH — briefly establish relevant background, middle ground, foreground. Do not overdescribe.
4. ACTION PROGRESSION — the visual sequence chronologically: At first → Then → As the action develops → Main payoff → Ending. Only include meaningful transitions.
5. PHYSICAL BEHAVIOR — body mechanics, environmental physics, weight, momentum, cloth, hair, surface interaction, secondary motion. These details should appear alongside the actions that cause them.
6. PERFORMANCE — observable emotional behavior through posture, movement, subtle facial behavior, timing, gestures. Avoid generic emotional labels when physical behavior can communicate the same idea.
7. CAMERA — the selected camera strategy, concisely. Maintain one coherent visual language.
8. LIGHTING — main light source, quality, shadow behavior, optional reflected light. Keep it physically consistent.
9. CONTINUITY — the most important elements that must remain unchanged.
10. ENDING — conclude with "The clip ends with..." followed by the exact final visual state.

FINAL GENERATOR-READY PROMPT STRUCTURE

Create a [derived visual style] video of [derived primary subject] [derived central action] in [derived setting].

[If applicable: Use the reference image as the exact visual setting and starting composition. Preserve the important character, environment, framing, object placement, and spatial relationships visible in the reference.]

The scene begins [derived initial state]. In the background, [important background layer]. In the middle ground, [important supporting layer]. The main subject remains [foreground position, orientation, and key continuity anchors].

At first, [first action beat]. Then [development]. As the action builds, [escalation]. At the main payoff, [defining action]. Movement follows believable body mechanics with [derived weight transfer, momentum, balance, cloth, hair, environmental interaction, or other relevant physical behavior].

The performance feels [derived emotional quality], communicated through [derived posture, gesture, body language, and one or two micro-expressions if visible].

The camera [derived camera treatment], maintaining [derived composition, direction, focus, and spatial relationship]. Lighting comes from [derived primary source], producing [derived light quality, shadows, highlights, and relevant reflected light].

Maintain strict continuity throughout: [derived identity anchors, clothing, objects, environment, orientation, movement direction, and other critical persistent elements] remain unchanged.

Keep motion grounded in believable physical reality with natural acceleration, momentum, gravity, balance corrections, cloth behavior, reflections, shadows, and environmental reactions.

The clip ends with [derived exact final action, pose, position, and composition].

Constraints: [Generate only the scene-specific restrictions necessary to prevent likely failure modes.]

OPTIMIZATION PASS

Before outputting the final prompt, automatically refine it. Remove: redundant descriptions, repeated adjectives, decorative details that do not affect the video, contradictory instructions, unnecessary camera terminology, unnecessary lighting terminology, excessive facial detail, excessive negative prompting. Strengthen: primary action, chronological progression, spatial clarity, continuity anchors, physical causality, camera coherence, ending clarity. The finished prompt should feel deliberate, not verbose.

TARGET LENGTH

Use the shortest prompt capable of reliably controlling the intended video. Typical ranges: Simple video 60–100 words; Moderately complex video 90–140 words; Complex performance or action sequence 120–180 words. Longer prompts are acceptable only when the scene genuinely requires additional temporal or continuity instructions.

CORE OPERATING PRINCIPLE

The user supplies only: IDEA / STORY / SCENE, and optionally: REFERENCE IMAGE. The system derives everything else. Internally determine: WHO → WHAT THEY DO → WHERE → HOW THE SCENE IS LAYERED → HOW THE ACTION DEVELOPS → HOW THE SUBJECT PHYSICALLY PERFORMS → HOW THE ENVIRONMENT RESPONDS → HOW THE CAMERA OBSERVES → HOW THE SCENE IS LIT → WHAT MUST REMAIN CONSISTENT → WHAT FAILURE MODES MUST BE PREVENTED → EXACTLY HOW THE VIDEO ENDS.

The objective is not to maximize description. The objective is to transform a simple creative idea into the minimum set of precise visual instructions required for a coherent, realistic, repeatable AI-generated video.
"""

# The duration amendment: the user's length input becomes a derivation input.
DURATION_SECTION = """
TARGET DURATION

[The user's requested clip length, in seconds. This is an input, not a
suggestion.]

Scale the TEMPORAL ACTION STRUCTURE to the target: up to 4 seconds → 2 beats
(initial state and payoff/final state); 5–8 seconds → 3 beats; 9–15 seconds →
4–5 beats; beyond 15 seconds → the full progression, written as one continuous
piece of action that a shooting script will realize across several shots in
order (the ending is the sequence's ending). Pace every beat so the whole
action plays comfortably inside the target — never cram an escalation into a
clip too short to show it, and never pad a short clip with business.

Include one pacing sentence in the final prompt: "Pace the action to play in
about N seconds." Report estimated_duration_s: your honest estimate, in whole
seconds, of how long the action as written takes to play. It may differ from
the target — say so through that number, never by silently rewriting the
target.
"""

# Blueprint 28 vs 31: how the contract tells the brain where the narration
# goes. Post voicing (the Narrated default) forbids the words inside the
# prompt; generated voicing tells the brain the SYSTEM appends the spoken
# performance, so the prompt still must not quote the words itself — and
# must not forbid speech either.
NARRATION_KEY_POST = (
    "narration (the verbatim voice-over or scripted lines the finished video "
    "carries, an empty string when the idea scripts none — narration is "
    "recorded in post against the assembled film, so these words must NEVER "
    "appear inside the prompt as spoken dialogue, and the prompt must not "
    "direct anyone to speak audible words)"
)
NARRATION_KEY_GENERATED = (
    "narration (the verbatim voice-over or scripted lines the finished video "
    "carries, an empty string when the idea scripts none — the SYSTEM appends "
    "these words to the prompt as the spoken performance the on-screen speaker "
    "voices in the clip, so do not quote them inside the prompt yourself, and "
    "never write that the scene is silent or that no one speaks)"
)


def output_contract(voicing: str = "post") -> str:
    key = NARRATION_KEY_GENERATED if voicing == "generated" else NARRATION_KEY_POST
    return f"""
OUTPUT CONTRACT

Return ONLY a JSON object with these keys (strings unless noted):
visual_style, primary_subject, primary_action, setting,
beats (list of {{stage, text}} where stage ∈ initial_state|first_change|development|escalation|payoff|final_state, in order, only the beats you used),
scene_depth ({{background, middle_ground, foreground}}),
spatial, camera, performance, micro_expressions, physics, body_mechanics,
lighting ({{source, quality, shadows, secondary}}),
mood, continuity_anchors (list of short phrases), ending,
constraints (list of 3–6 short imperatives),
{key},
{look_mod.LOOK_CONTRACT}
real_people (list of {{name, aliases, descriptor, label}} — every real, identifiable person you recognized in the idea or the images and described instead of naming, per the LIKENESS LAW; an empty list when none),
characters (list of {{name, aliases, descriptor, label}} — every invented or fictional person you named anywhere in your output, per NAMED CHARACTERS; an empty list when none),
estimated_duration_s (integer),
prompt (the finished generator-ready prompt, following FINAL GENERATOR-READY PROMPT STRUCTURE, after the OPTIMIZATION PASS).
"""


OUTPUT_CONTRACT = output_contract("post")

REFERENCE_OPENER = (
    "Use the reference image as the exact visual setting and starting composition."
)
REFERENCE_PRESERVE = (
    "Preserve the important character, environment, framing, object placement, "
    "and spatial relationships visible in the reference."
)
GROUNDING = (
    "Keep motion grounded in believable physical reality with natural "
    "acceleration, momentum, gravity, balance corrections, cloth behavior, "
    "reflections, shadows, and environmental reactions."
)
ENDING_LEAD = "The clip ends with"
MIN_CONSTRAINTS = 3
MAX_CONSTRAINTS = 6
# The template's length bands, by beat count; the ceiling is where a prompt
# stops being "deliberate" and the optimizer says so.
LENGTH_BANDS = {2: (60, 100), 3: (60, 100), 4: (90, 140), 5: (120, 180), 6: (120, 180)}
LENGTH_CEILING = 220
BRAIN_ATTEMPTS = 2
# Output headroom for the reply. s-16d2e6e5e0 (2026-09-03): a 12-clip CLIP
# DELIVERY split — the master plus twelve complete, self-contained clip
# prompts with their narration — runs past 8192 tokens; the provider cut the
# JSON mid-object, both attempts failed on "Expecting ',' delimiter", and the
# chain rolled on the raw idea. The screenwriter already books 32768.
BRAIN_MAX_TOKENS = 32768


SPLIT_NARRATION_POST = (
    "Narration is recorded in post against the assembled film: it must NEVER "
    "appear inside a clip prompt as spoken dialogue or voiceover, and no clip "
    "prompt may direct anyone to speak audible words."
)
SPLIT_NARRATION_GENERATED = (
    "The system appends each clip's narration to that clip's prompt as the "
    "spoken performance its on-screen speaker voices in the clip: do not quote "
    "the words inside the clip prompt yourself, and never write that a clip is "
    "silent or that no one speaks."
)


def split_section(clip_plan: list[int], voicing: str = "post") -> str:
    """The chained-delivery amendment: the booked route plays the action as
    several sequential clips, and the derivation splits it — the uncapped
    one-shot's structure comes from the brain and the input, never from a
    screenplay stage."""
    durations = ", ".join(f"{d}s" for d in clip_plan)
    count = len(clip_plan)
    speech_law = SPLIT_NARRATION_GENERATED if voicing == "generated" else SPLIT_NARRATION_POST
    return f"""
CLIP DELIVERY

The booked route delivers this as {count} sequential clips ({durations}; {sum(clip_plan)} seconds total). After deriving the master, split the ACTION PROGRESSION into {count} consecutive segments. Cut at natural action boundaries, never mid-gesture. When the idea itself scripts scenes, align clip boundaries with those scenes — cover every scripted scene's imagery across the clips in order — and return each scene's narration in its clip's "narration" field VERBATIM. {speech_law}

In addition to every OUTPUT CONTRACT key, return clips: a list of exactly {count} objects {{"prompt", "duration_s", "narration", "scene_change", "opening"}} in play order, duration_s matching the booked lengths above, narration holding that segment's scripted lines verbatim (an empty string when the segment carries none). Each clip prompt is a complete generator-ready prompt for JUST its segment: it restates the subject, wardrobe, setting, camera treatment, and every continuity anchor in full — each clip generates alone and knows nothing of the others. It names the scene's motivated light sources, but it NEVER restates or varies the medium, palette, grain, or rendering style: the film's LOOK BIBLE is appended verbatim to every clip prompt by the system. scene_change is true when the clip opens on a different place or subject than the previous clip's final frame (a cut, not a continuation) — such a clip opens from a still frame the system composes in the film's look; a clip that continues the previous clip's action directly is false. opening is ONE sentence describing the clip's first frame as a still image (subject, place, composition), used to compose that opening frame. Every continuing clip begins exactly where the previous clip's final frame left off, and every clip before the last closes on a stable, holdable frame the next clip can open from. Only the final clip carries the ending. Include the pacing sentence in each clip prompt for its own length. The master prompt still describes the WHOLE action.
"""


def system_prompt(clip_plan: list[int] | None = None, voicing: str = "post") -> str:
    from diony_director import likeness

    base = MASTER_TEMPLATE + DURATION_SECTION
    if clip_plan and len(clip_plan) > 1:
        base += split_section(clip_plan, voicing)
    # The likeness law rides every derivation: a real person recognized in a
    # still is described, never named — and confessed in real_people.
    return base + "\n" + likeness.BRAIN_LAW + "\n" + output_contract(voicing)


def beat_budget(target_duration_s: int) -> int:
    """How many beats the TEMPORAL ACTION STRUCTURE gets at this duration."""
    if target_duration_s <= 4:
        return 2
    if target_duration_s <= 8:
        return 3
    if target_duration_s <= 15:
        return 4
    return 6


def length_band(beat_count: int) -> tuple[int, int]:
    return LENGTH_BANDS.get(max(2, min(6, beat_count)), (90, 140))


def pacing_sentence(target_duration_s: int) -> str:
    return f"Pace the action to play in about {int(target_duration_s)} seconds."


def user_message(
    idea: str,
    target_duration_s: int,
    *,
    reference_count: int = 0,
    authored: dict[str, str] | None = None,
) -> str:
    """The INPUT block. `authored` carries elements the user already
    established outside the prose — a session camera/atmosphere/world
    template or an inline /token — which the template says to preserve."""
    lines = [f"IDEA / STORY / SCENE:\n{idea.strip()}", f"TARGET DURATION: {int(target_duration_s)} seconds."]
    from diony_director import screenwriter  # lazy — module cycles both ways

    if screenwriter.authored_breakdown(idea)["is_authored"]:
        lines.append(
            "The idea contains the user's own scripted scene breakdown and "
            "narration — established material: preserve its scenes, argument, "
            "and ending; derive only what it leaves open."
        )
    if reference_count:
        lines.append(
            f"OPTIONAL REFERENCE IMAGE: {reference_count} attached — the first is the "
            "authoritative starting visual state; the rest show the same people, "
            "wardrobe, and place."
        )
    else:
        lines.append("OPTIONAL REFERENCE IMAGE: none.")
    established = {k: v for k, v in (authored or {}).items() if v}
    if established:
        lines.append(
            "ALREADY ESTABLISHED (preserve, do not reinterpret): "
            + "; ".join(f"{k}: {v}" for k, v in established.items())
            + "."
        )
    return "\n".join(lines)


# --------------------------------------------------------------------------
# Brain
# --------------------------------------------------------------------------


class MasterUnavailable(RuntimeError):
    """The configured brain could not derive the master prompt. With a brain
    configured there is NO template stand-in — the failure is a named state."""


class BrainTruncated(RuntimeError):
    """The brain stopped at its output cap (finish_reason=length): the reply
    is not malformed JSON, it is an unfinished one. Retrying the same call
    blind returns the same cut — the cap, not chance, is the cause."""


def _truthy(value) -> bool:
    if isinstance(value, str):
        return value.strip().lower() in {"true", "yes", "1", "cut", "new"}
    return bool(value)


def _parse(
    body: dict,
    *,
    idea: str,
    target_duration_s: int,
    has_reference: bool,
    clip_plan: list[int] | None = None,
) -> MasterPrompt:
    warnings: list[str] = []

    def _text(key: str, default: str = "") -> str:
        value = body.get(key, "")
        if isinstance(value, (list, tuple)):
            value = "; ".join(str(v) for v in value if str(v).strip())
        elif isinstance(value, dict):
            value = "; ".join(f"{k}: {v}" for k, v in value.items() if str(v).strip())
        value = " ".join(str(value or "").split())
        if not value and default:
            warnings.append(f"master: brain omitted {key} — defaulted")
            return default
        return value

    def _dict(key: str, fields: tuple[str, ...]) -> dict[str, str]:
        raw = body.get(key)
        out: dict[str, str] = {}
        if isinstance(raw, dict):
            for name in fields:
                value = " ".join(str(raw.get(name, "") or "").split())
                if value:
                    out[name] = value
        elif isinstance(raw, str) and raw.strip():
            out[fields[0]] = " ".join(raw.split())
        return out

    def _list(key: str) -> list[str]:
        raw = body.get(key)
        if isinstance(raw, str):
            raw = [part for part in re.split(r"[;\n]", raw)]
        if not isinstance(raw, list):
            return []
        return [" ".join(str(x).split()).strip(" .") for x in raw if str(x).strip()]

    beats: list[dict[str, str]] = []
    raw_beats = body.get("beats")
    if isinstance(raw_beats, dict):
        raw_beats = [{"stage": k, "text": v} for k, v in raw_beats.items()]
    for entry in raw_beats or []:
        if isinstance(entry, dict):
            stage = str(entry.get("stage", "")).strip().lower().replace(" ", "_")
            text = " ".join(str(entry.get("text", "") or "").split())
        else:
            stage, text = "", " ".join(str(entry).split())
        if not text:
            continue
        if stage not in MASTER_BEAT_STAGES:
            stage = MASTER_BEAT_STAGES[min(len(beats), len(MASTER_BEAT_STAGES) - 1)]
        beats.append({"stage": stage, "text": text})
    if not beats:
        raise ValueError("brain returned no beats — the temporal structure is unwritten")

    prompt = " ".join(str(body.get("prompt", "") or "").split())
    if not prompt:
        raise ValueError("brain returned no prompt")
    ending = _text("ending")
    if not ending:
        ending = beats[-1]["text"]
        warnings.append("master: brain omitted ending — taken from the final beat")

    clips: list[dict] = []
    if clip_plan and len(clip_plan) > 1:
        for entry in body.get("clips") or []:
            if isinstance(entry, dict):
                text = " ".join(str(entry.get("prompt", "") or "").split())
                narration = " ".join(str(entry.get("narration", "") or "").split())
                scene_change = _truthy(entry.get("scene_change"))
                opening = " ".join(str(entry.get("opening", "") or "").split())
            else:
                text = " ".join(str(entry).split())
                narration = ""
                scene_change, opening = False, ""
            if text:
                clips.append({
                    "prompt": text,
                    "duration_s": 0,
                    "narration": narration,
                    "scene_change": scene_change,
                    "opening": opening,
                })
        if len(clips) != len(clip_plan):
            # a chained booking without its clips is unusable — raising here
            # retries the derivation rather than shipping half a chain
            raise ValueError(
                f"brain returned {len(clips)} clip prompt(s) for a "
                f"{len(clip_plan)}-clip booking"
            )
        for index, duration in enumerate(clip_plan):
            # the BOOKED durations are law — the brain's numbers are advisory
            clips[index]["duration_s"] = int(duration)
        if clips:
            clips[0]["scene_change"] = False  # the opener opens the film, not a cut

    raw_est = body.get("estimated_duration_s", 0)
    try:
        estimated = int(round(float(raw_est)))
    except (TypeError, ValueError):
        estimated = 0
    if estimated <= 0:
        estimated = int(target_duration_s)
        warnings.append("master: brain omitted estimated_duration_s — assumed the target")

    visual_style = _text("visual_style", "cinematic realism")
    look = look_mod.parse_look(
        body.get("look"), idea=idea, visual_style=visual_style, warnings=warnings
    )
    return MasterPrompt(
        idea=idea,
        target_duration_s=int(target_duration_s),
        estimated_duration_s=estimated,
        reference_image=has_reference,
        visual_style=visual_style,
        look=look,
        primary_subject=_text("primary_subject"),
        primary_action=_text("primary_action"),
        setting=_text("setting"),
        beats=beats,
        scene_depth=_dict("scene_depth", ("background", "middle_ground", "foreground")),
        spatial=_text("spatial"),
        camera=_text("camera"),
        performance=_text("performance"),
        micro_expressions=_text("micro_expressions"),
        physics=_text("physics"),
        body_mechanics=_text("body_mechanics"),
        lighting=_dict("lighting", ("source", "quality", "shadows", "secondary")),
        mood=_text("mood"),
        continuity_anchors=_list("continuity_anchors"),
        ending=ending,
        constraints=_list("constraints"),
        narration=" ".join(str(body.get("narration", "") or "").split()),
        real_people=likeness_mod.normalize_people(
            body.get("real_people"), has_reference=has_reference
        ),
        characters=likeness_mod.without(
            likeness_mod.normalize_people(body.get("characters"), has_reference=has_reference),
            likeness_mod.normalize_people(body.get("real_people"), has_reference=has_reference),
        ),
        prompt=prompt,
        clips=clips,
        source="brain",
        warnings=warnings,
    )


def guard_likeness(master: MasterPrompt, *, brain: bool) -> MasterPrompt:
    """The likeness guard on a derived artifact (Blueprint 27 §3a). The
    people the brain confessed are enforced first; then, with a brain on,
    every capitalized phrase it did not list is audited once, text only, and
    anyone the audit names is enforced too. The audit is best effort: its
    failure is a warning on the artifact, never a failed derivation."""
    people = list(master.real_people or [])
    where = "reference image" if master.reference_image else "idea"
    hits = likeness_mod.enforce_master(master, people) if people else 0
    if brain and likeness_mod.audit_enabled():
        # the audit skips the characters already confessed: they are people
        # the guard knows, and their names are welcome in an unpictured prompt
        known = likeness_mod.merge(people, master.characters)
        phrases = likeness_mod.candidates(likeness_mod.master_texts(master), known)
        master.characters_audited = True
        if phrases:
            try:
                found, invented = likeness_mod.audit_all(
                    master.prompt, phrases, has_reference=master.reference_image
                )
            except Exception as err:  # noqa: BLE001 — the audit is a net, not a gate
                master.warnings.append(
                    f"master: likeness audit unavailable ({type(err).__name__}) — "
                    "the brain's own real_people list was enforced; review names by eye"
                )
                found, invented = [], []
                master.characters_audited = False
            if found:
                people = likeness_mod.merge(people, found)
                hits += likeness_mod.enforce_master(master, found)
                master.real_people = [dict(p) for p in people]
            if invented:
                # a named character the brain wrote but did not confess: the
                # pictured-prompt gate needs to know them by name
                master.characters = [
                    dict(c)
                    for c in likeness_mod.without(
                        likeness_mod.merge(master.characters, invented), people
                    )
                ]
    if people:
        master.warnings.append(likeness_mod.note(hits, people, owner="master", where=where))
    return master


def _from_brain(
    idea: str,
    target_duration_s: int,
    reference_images: list[str],
    authored: dict[str, str] | None,
    clip_plan: list[int] | None = None,
    voicing: str = "post",
) -> MasterPrompt:
    from diony_director import screenwriter
    from diony_director.providers import base as provider_base

    refs = [r for r in reference_images if r]
    text = user_message(
        idea, target_duration_s, reference_count=len(refs), authored=authored
    )
    if refs:
        user_content: str | list = [{"type": "text", "text": text}] + [
            screenwriter._image_part(r) for r in refs[:4]
        ]
    else:
        user_content = text
    response = provider_base.post_with_retries(
        f"{screenwriter.brain_url()}/v1/chat/completions",
        what="master prompt",
        json={
            "model": screenwriter.brain_model(),
            "messages": [
                {"role": "system", "content": system_prompt(clip_plan, voicing)},
                {"role": "user", "content": user_content},
            ],
            "temperature": 0.4,
            "max_tokens": BRAIN_MAX_TOKENS,
            "response_format": {"type": "json_object"},
        },
        headers=screenwriter._brain_headers(),
        timeout=screenwriter.brain_timeout_s(),
    )
    if response.status_code >= 400:
        raise RuntimeError(f"brain rejected ({response.status_code}): {response.text[:200]}")
    payload = response.json()
    choice = payload["choices"][0]
    if str(choice.get("finish_reason", "")).lower() == "length":
        used = int((payload.get("usage") or {}).get("completion_tokens") or 0)
        raise BrainTruncated(
            f"brain reply truncated at {used or BRAIN_MAX_TOKENS} tokens (finish_reason=length)"
            + (
                f" — the {len(clip_plan)}-clip split did not fit one reply"
                if clip_plan
                else ""
            )
        )
    body = screenwriter._json_object(choice["message"]["content"])
    master = _parse(
        body,
        idea=idea,
        target_duration_s=target_duration_s,
        has_reference=bool(refs),
        clip_plan=clip_plan,
    )
    usage = dict(payload.get("usage") or {})
    if usage:
        master.brain_usage = {
            "prompt_tokens": int(usage.get("prompt_tokens") or 0),
            "completion_tokens": int(usage.get("completion_tokens") or 0),
        }
    return guard_likeness(master, brain=True)


# --------------------------------------------------------------------------
# Deterministic template derivation (brain off)
# --------------------------------------------------------------------------

_STYLE_TELLS = (
    (("anime", "animated", "cartoon", "pixar", "claymation"), "animation"),
    (("surreal", "dreamlike", "impossible"), "surrealism"),
    (("documentary", "observational", "vérité", "verite"), "documentary realism"),
    (("fantasy", "dragon", "wizard", "sorcer"), "fantasy realism"),
    (("photoreal", "photo-real", "photorealistic"), "photorealistic"),
)

_LIGHT_TELLS = (
    (("neon",), {"source": "neon signage", "quality": "cool saturated glow", "shadows": "soft colored shadows"}),
    (("night", "midnight", "after dark"), {"source": "streetlights and practicals", "quality": "warm, directional", "shadows": "long pooled shadows"}),
    (("sunset", "golden hour", "dusk"), {"source": "low sunlight", "quality": "warm, raking", "shadows": "long directional shadows"}),
    (("overcast", "grey sky", "gray sky", "rain"), {"source": "overcast daylight", "quality": "soft, diffused", "shadows": "soft natural shadows"}),
    (("window",), {"source": "window light", "quality": "soft, directional", "shadows": "subdued interior shadows"}),
    (("fire", "campfire", "candle"), {"source": "firelight", "quality": "warm, flickering", "shadows": "moving soft shadows"}),
    (("office", "fluorescent", "hospital"), {"source": "fluorescent ceiling light", "quality": "flat, cool", "shadows": "subdued interior shadows"}),
    (("sun", "daylight", "noon", "beach", "desert"), {"source": "sunlight", "quality": "hard, directional", "shadows": "sharp contrast"}),
)

_MOOD_TELLS = (
    (("dance", "party", "celebrat", "laugh", "play"), "playful"),
    (("chase", "run", "fight", "escape", "race", "sprint"), "energetic"),
    (("tense", "standoff", "threat", "waiting", "silence", "stalk"), "tense"),
    (("kiss", "whisper", "quiet", "alone", "letter", "bedside"), "intimate"),
)

_SETTING_TELLS = (
    "diner", "bar", "office", "warehouse", "desert", "town", "street", "beach",
    "forest", "rooftop", "kitchen", "bedroom", "car", "train", "subway", "market",
    "harbor", "harbour", "field", "bridge", "alley", "station", "church", "stadium",
    "lab", "hospital", "school", "garage", "river", "lake", "mountain", "city",
)


def _snip(text: str, cap: int) -> str:
    text = " ".join(text.split()).rstrip(".")
    if len(text) <= cap:
        return text
    return text[:cap].rsplit(" ", 1)[0].rstrip(",.;:") + "…"


def _lower_first(text: str) -> str:
    return text[:1].lower() + text[1:] if text else text


_CAMERA_LEAD = re.compile(r"^(?:the\s+)?camera\s+", re.IGNORECASE)
_DETERMINERS = {
    "a", "an", "the", "two", "three", "four", "some", "one", "my", "our", "his", "her",
    "their", "he", "she", "they", "it", "we", "this", "that", "these", "those",
}


def _with_article(style: str) -> str:
    """'animation' → 'an animation', 'cinematic realism' → 'a cinematic realism'."""
    lead = style.strip()[:1].lower()
    return f"an {style}" if lead in "aeiou" else f"a {style}"


def _camera_clause(camera: str) -> str:
    """'The camera holds…' / 'Camera not moving' → 'holds…' / 'not moving', so
    the assembled sentence never reads 'The camera camera not moving'."""
    return _lower_first(_CAMERA_LEAD.sub("", camera.strip(), count=1).rstrip("."))


def _anchor_case(text: str) -> str:
    """Lower a leading determiner/pronoun ('A man walks' → 'a man walks') so the
    idea reads inside the anchor sentence; a leading name ('Mara walks',
    'ELIAS turns') keeps its case."""
    if not text:
        return text
    head, _, rest = text.partition(" ")
    if head.lower() in _DETERMINERS and head[:1].isupper() and (not head[1:] or head[1:].islower()):
        return head.lower() + (" " + rest if rest else "")
    return text


_ENDING_LEADS = re.compile(
    r"^the (?:clip|video|shot|scene|sequence|film) (?:ends|closes|concludes|finishes)\b",
    re.IGNORECASE,
)


def _template(
    idea: str,
    target_duration_s: int,
    has_reference: bool,
    authored: dict[str, str] | None,
) -> MasterPrompt:
    lowered = idea.lower()
    established = {k: v for k, v in (authored or {}).items() if v}

    style = "cinematic realism"
    for tells, mapped in _STYLE_TELLS:
        if any(t in lowered for t in tells):
            style = mapped
            break

    setting = "the described setting"
    for tell in _SETTING_TELLS:
        if re.search(rf"\b{tell}\b", lowered):
            setting = f"the {tell}"
            break

    lighting = {"source": "soft available light from one motivated source", "quality": "soft, directional", "shadows": "soft natural shadows"}
    for tells, mapped in _LIGHT_TELLS:
        if any(t in lowered for t in tells):
            lighting = dict(mapped)
            break

    mood = "realistic"
    for tells, mapped in _MOOD_TELLS:
        if any(t in lowered for t in tells):
            mood = mapped
            break

    camera = established.get("camera") or (
        "holds the reference image's framing with a barely perceptible push-in"
        if has_reference
        else "holds a locked medium-wide frame at chest height with a slow push-in"
    )
    # The idea is the visual anchor, stated once; the template never
    # paraphrases it (it cannot — it has no understanding to paraphrase with).
    subject = _anchor_case(_snip(idea, 180))

    n_beats = beat_budget(target_duration_s)
    bank = [
        ("initial_state", "the subject settled in frame, still, weight resting evenly"),
        ("first_change", "a first small movement — a shift of weight, a turn of the head"),
        ("development", "the described action becomes unmistakable"),
        ("escalation", "momentum builds and the movement grows larger and more committed"),
        ("payoff", "the defining moment lands in full view and holds for a breath"),
        ("final_state", "the movement resolves and the subject settles into a held final pose"),
    ]
    if n_beats == 2:
        chosen = [bank[0], bank[4]]
    elif n_beats == 3:
        chosen = [bank[0], bank[2], bank[5]]
    elif n_beats == 4:
        chosen = [bank[0], bank[2], bank[4], bank[5]]
    else:
        chosen = bank
    beats = [{"stage": stage, "text": text} for stage, text in chosen]
    ending = "the subject settled in a held final pose, the movement resolved, the composition unchanged"
    anchors = [
        "same subject identity and wardrobe",
        "same environment and object placement",
        "same camera orientation",
    ]
    constraints = [
        "do not introduce additional people",
        "do not change the environment",
        "avoid distorted anatomy",
        "avoid abrupt teleportation or random cuts",
    ]
    if has_reference:
        constraints.append("do not alter the reference-image composition")
    atmosphere = established.get("atmosphere", "")
    look = look_mod.template_look(idea, style)
    master = MasterPrompt(
        idea=idea,
        target_duration_s=int(target_duration_s),
        estimated_duration_s=int(target_duration_s),
        reference_image=has_reference,
        # a Flow world clause is a full cinematography sentence; the compiler
        # appends it as the style slot on the Google route — it is NOT the
        # one-word style the anchor sentence wants ("Create a filmic video")
        visual_style=style,
        look=look,
        primary_subject=subject,
        primary_action="",
        setting="",  # the idea already names it — never echoed after a full sentence
        beats=beats,
        scene_depth={
            "background": (setting if setting != "the described setting" else "the surroundings")
            + " holding its depth and light",
            "foreground": "centered, facing direction held",
        },
        spatial="subject position and direction of travel for the whole clip",
        camera=camera,
        performance="posture, shoulder set, and eye line before any larger gesture",
        micro_expressions="",
        physics="cloth and hair following the motion"
        + (f", {_lower_first(atmosphere).rstrip('.')}" if atmosphere else ""),
        body_mechanics="anticipation, action, and follow-through, weight transfer and balance corrections",
        lighting=lighting,
        mood=mood,
        continuity_anchors=anchors,
        ending=ending,
        constraints=constraints,
        prompt="",
        source="template",
        warnings=["master: deterministic template derivation (Director brain not configured)"],
    )
    master.prompt = assemble(master)
    return master


def assemble(master: MasterPrompt) -> str:
    """The FINAL GENERATOR-READY PROMPT STRUCTURE, filled from derived fields.
    Used by the template path, and as the assembly when a brain hands back
    fields without a finished prompt."""
    parts: list[str] = []
    anchor = f"Create {_with_article(master.visual_style or 'cinematic realism')} video of {master.primary_subject or 'the subject'}"
    if master.primary_action:
        anchor += f" {_lower_first(master.primary_action)}"
    if master.setting:
        anchor += f" in {_lower_first(master.setting)}"
    parts.append(anchor.rstrip(".!?") + ".")
    if master.reference_image:
        parts.append(f"{REFERENCE_OPENER} {REFERENCE_PRESERVE}")
    initial = master.beat("initial_state")
    depth = master.scene_depth
    scene = []
    if initial:
        scene.append(f"The scene begins with {_lower_first(initial)}.")
    if depth.get("background"):
        scene.append(f"In the background, {_lower_first(depth['background'])}.")
    if depth.get("middle_ground"):
        scene.append(f"In the middle ground, {_lower_first(depth['middle_ground'])}.")
    if depth.get("foreground"):
        scene.append(f"The main subject remains {_lower_first(depth['foreground'])}.")
    if scene:
        parts.append(" ".join(scene))
    progression = []
    leads = {
        "first_change": "At first,",
        "development": "Then",
        "escalation": "As the action builds,",
        "payoff": "At the main payoff,",
    }
    for entry in master.beats:
        stage = entry.get("stage", "")
        if stage in leads:
            progression.append(f"{leads[stage]} {_lower_first(entry['text'])}.")
    if master.physics or master.body_mechanics:
        mech = "; ".join(x for x in (master.body_mechanics, master.physics) if x)
        progression.append(f"Movement follows believable body mechanics with {_lower_first(mech)}.")
    if progression:
        parts.append(" ".join(progression))
    if master.mood or master.performance:
        perf = f"The performance feels {master.mood or 'natural'}"
        if master.performance:
            perf += f", communicated through {_lower_first(master.performance)}"
        if master.micro_expressions:
            perf += f" — {_lower_first(master.micro_expressions)}"
        parts.append(perf + ".")
    cam = []
    if master.camera:
        cam.append(
            f"The camera {_camera_clause(master.camera)}"
            + (f", maintaining {_lower_first(master.spatial).rstrip('.')}" if master.spatial else "")
            + "."
        )
    light = master.lighting
    if light.get("source"):
        quality = ", ".join(x for x in (light.get("quality"), light.get("shadows")) if x)
        sentence = f"Lighting comes from {light['source']}"
        if quality:
            sentence += f", producing {quality}"
        if light.get("secondary"):
            sentence += f", with {light['secondary']}"
        cam.append(sentence + ".")
    if cam:
        parts.append(" ".join(cam))
    if master.continuity_anchors:
        parts.append(
            "Maintain strict continuity throughout: "
            + ", ".join(master.continuity_anchors)
            + " remain unchanged."
        )
    parts.append(GROUNDING)
    parts.append(pacing_sentence(master.target_duration_s))
    parts.append(f"{ENDING_LEAD} {_lower_first(master.ending)}.")
    if master.constraints:
        parts.append("Constraints: " + "; ".join(master.constraints) + ".")
    return " ".join(parts)


def split_clips(master: MasterPrompt, clip_plan: list[int]) -> list[dict]:
    """Deterministic chained-delivery split (brain off): the master's beats
    distributed across the booked clips in order. Every clip restates the
    anchor, continuity, and constraints — each generates alone; only the last
    carries the ending; every clip after the first opens from the previous
    clip's final frame."""
    count = len(clip_plan)
    beats = master.beats or [{"stage": "initial_state", "text": "the described action unfolds"}]
    buckets: list[list[str]] = [[] for _ in range(count)]
    for index, beat in enumerate(beats):
        buckets[min(index * count // len(beats), count - 1)].append(beat["text"])
    anchor = f"Create {_with_article(master.visual_style or 'cinematic realism')} video of {master.primary_subject or 'the subject'}."
    continuity = (
        "Maintain strict continuity throughout: "
        + ", ".join(master.continuity_anchors)
        + " remain unchanged."
        if master.continuity_anchors
        else ""
    )
    constraints = "Constraints: " + "; ".join(master.constraints) + "." if master.constraints else ""
    camera = f"The camera {_camera_clause(master.camera)}." if master.camera else ""
    clips: list[dict] = []
    for index, (duration, bucket) in enumerate(zip(clip_plan, buckets)):
        parts: list[str] = [anchor]
        if index == 0:
            if master.reference_image:
                parts.append(f"{REFERENCE_OPENER} {REFERENCE_PRESERVE}")
        else:
            parts.append(
                "Continue the same continuous action from the exact frame where "
                "the previous clip ended — same subject, wardrobe, setting, "
                "light, and camera position."
            )
        action = " ".join(f"{_lower_first(text).rstrip('.')[:1].upper()}{_lower_first(text).rstrip('.')[1:]}." for text in bucket)
        if action:
            parts.append(action)
        else:
            parts.append("The action continues, developing steadily without a reset.")
        if camera:
            parts.append(camera)
        if continuity:
            parts.append(continuity)
        parts.append(GROUNDING)
        parts.append(pacing_sentence(duration))
        if index == count - 1:
            parts.append(f"{ENDING_LEAD} {_lower_first(master.ending)}.")
        else:
            parts.append("The clip closes on a stable, holdable frame the next clip opens from.")
        if constraints:
            parts.append(constraints)
        clips.append({
            "prompt": " ".join(parts),
            "duration_s": int(duration),
            # the template has no understanding to split narration with — a
            # brainless chain carries none, and the derivation warning names it
            "narration": "",
            # the template splits ONE continuous action — no link is a cut
            "scene_change": False,
            "opening": bucket[0] if bucket else "",
        })
    return clips


# --------------------------------------------------------------------------
# Optimization pass — the template's own rules, enforced deterministically
# --------------------------------------------------------------------------

_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+(?=[A-Z\"'(])")
# every warning this pass emits, so a re-run replaces rather than repeats them
_OPTIMIZER_WARNING = re.compile(r"^master: (?:optimizer|only \d+ constraint|\d+ words)")
_DURATION_MENTION = re.compile(r"\b\d+\s*(?:s\b|sec\b|secs\b|seconds?\b)", re.IGNORECASE)


def _sentences(text: str) -> list[str]:
    return [s.strip() for s in _SENTENCE_SPLIT.split(" ".join(text.split())) if s.strip()]


def optimize(master: MasterPrompt) -> MasterPrompt:
    """Idempotent. Never alters the meaning of what the brain derived; it
    enforces the structural promises the template makes (reference opener,
    ending line, pacing line, constraint count, no duplicated sentences) and
    names anything outside the template's length bands."""
    warnings = [w for w in master.warnings if not _OPTIMIZER_WARNING.match(w)]
    sentences = _sentences(master.prompt)

    # duplicate sentences: the brain repeating itself is the template's
    # first named redundancy
    seen: set[str] = set()
    deduped: list[str] = []
    for sentence in sentences:
        key = sentence.lower().rstrip(".")
        if key in seen:
            continue
        seen.add(key)
        deduped.append(sentence)
    if len(deduped) != len(sentences):
        warnings.append(f"master: optimizer removed {len(sentences) - len(deduped)} repeated sentence(s)")
    sentences = deduped

    # reference opener: present exactly when a reference image is supplied
    has_opener = any(REFERENCE_OPENER.lower() in s.lower() for s in sentences)
    if master.reference_image and not has_opener:
        sentences.insert(min(1, len(sentences)), f"{REFERENCE_OPENER} {REFERENCE_PRESERVE}")
        warnings.append("master: optimizer added the reference-image opener")
    elif not master.reference_image and has_opener:
        sentences = [s for s in sentences if REFERENCE_OPENER.lower() not in s.lower()
                     and REFERENCE_PRESERVE.lower() not in s.lower()]
        warnings.append("master: optimizer dropped a reference-image opener (no reference supplied)")

    # constraints: 3–6, scene-specific; trimmed here, never padded with generics
    constraints = [c for c in master.constraints if c]
    if len(constraints) > MAX_CONSTRAINTS:
        warnings.append(
            f"master: optimizer trimmed {len(constraints)} constraints to {MAX_CONSTRAINTS}"
        )
        constraints = constraints[:MAX_CONSTRAINTS]
    elif 0 < len(constraints) < MIN_CONSTRAINTS:
        warnings.append(
            f"master: only {len(constraints)} constraint(s) derived — the template prefers 3–6"
        )
    master.constraints = constraints

    # split the tail apart so ending / pacing / constraints land in order
    body: list[str] = []
    ending_line = ""
    constraint_line = ""
    pacing_line = ""
    for sentence in sentences:
        lowered = sentence.lower()
        if _ENDING_LEADS.match(sentence) and not ending_line:
            # the brain's own ending sentence, whatever lead it chose, is
            # normalized to the template's "The clip ends with …"
            if not lowered.startswith(ENDING_LEAD.lower()):
                tail = _ENDING_LEADS.sub("", sentence, count=1).strip()
                tail = re.sub(r"^(?:with|on|as|when)\s+", "", tail, count=1, flags=re.IGNORECASE)
                sentence = f"{ENDING_LEAD} {_lower_first(tail)}"
                warnings.append("master: optimizer normalized the ending line")
            ending_line = sentence
        elif lowered.startswith("constraints:") and not constraint_line:
            constraint_line = sentence
        elif lowered.startswith("pace the action") and not pacing_line:
            pacing_line = sentence
        else:
            body.append(sentence)
    if not ending_line:
        ending_line = f"{ENDING_LEAD} {_lower_first(master.ending.rstrip('.'))}."
        warnings.append("master: optimizer added the ending line")
    if not pacing_line:
        # a brain that already paced the action to the TARGET in its own words
        # needs no second pacing sentence; a mention of some other length is
        # left standing (it is the brain's honest estimate) and the target
        # line is added so the booked duration is what the generator hears
        target = master.target_duration_s
        own = [
            m.group(0)
            for s in body
            for m in _DURATION_MENTION.finditer(s)
        ]
        if any(re.match(rf"^{target}\b", m) for m in own):
            pacing_line = ""
        else:
            pacing_line = pacing_sentence(target)
            if own:
                warnings.append(
                    f"master: optimizer added the {target}s pacing line — the prompt "
                    f"also mentions {', '.join(own[:2])}"
                )
            else:
                warnings.append("master: optimizer added the pacing line")
    if constraints and not constraint_line:
        constraint_line = "Constraints: " + "; ".join(constraints) + "."
    elif constraints and constraint_line:
        # keep the sentence in sync with the (possibly trimmed) list
        constraint_line = "Constraints: " + "; ".join(constraints) + "."

    ordered = body + [x for x in (pacing_line, ending_line, constraint_line) if x]
    prompt = " ".join(ordered)
    master.prompt = prompt
    master.word_count = len(prompt.split())
    low, high = length_band(len(master.beats))
    if master.word_count > LENGTH_CEILING:
        warnings.append(
            f"master: {master.word_count} words — past the template's {LENGTH_CEILING}-word ceiling; "
            "a deliberate prompt is shorter"
        )
    elif master.word_count > high:
        warnings.append(
            f"master: {master.word_count} words for {len(master.beats)} beats — above the "
            f"{low}–{high} band; acceptable only if the scene needs the continuity"
        )
    if master.estimated_duration_s <= 0:
        master.estimated_duration_s = master.target_duration_s
    master.warnings = warnings
    return master


# --------------------------------------------------------------------------
# Entry point
# --------------------------------------------------------------------------


def derive(
    idea: str,
    *,
    target_duration_s: int,
    reference_images: list[str] | None = None,
    authored: dict[str, str] | None = None,
    clip_plan: list[int] | None = None,
    voicing: str = "post",
) -> MasterPrompt:
    """Idea (+ stills, + the user's duration) → the pre-split finalized
    artifact. `clip_plan` books a chained one-shot: the derivation also
    splits the action into that many sequential clip prompts. `voicing`
    (Blueprint 31) tells the brain whether the narration is recorded in
    post or appended by the system as the clip's spoken performance."""
    from diony_director.screenwriter import brain_model, brain_url

    target = max(1, int(round(target_duration_s)))
    refs = [r for r in (reference_images or []) if r]
    plan = [int(d) for d in (clip_plan or []) if int(d) > 0]
    if len(plan) < 2:
        plan = []
    if brain_url():
        last_error: Exception | None = None
        attempts = 0
        for _attempt in range(BRAIN_ATTEMPTS):
            attempts += 1
            try:
                return optimize(
                    _from_brain(idea, target, refs, authored, plan or None, voicing=voicing)
                )
            except BrainTruncated as err:
                last_error = err
                break  # the cap is deterministic — a blind retry is the same cut
            except Exception as err:  # noqa: BLE001 — retried, then raised as a named state
                last_error = err
        raise MasterUnavailable(
            f"{brain_model()} could not derive the master prompt after "
            f"{attempts} attempt{'s' if attempts != 1 else ''}: {last_error}"
        )
    master = optimize(_template(idea, target, bool(refs), authored))
    if plan:
        master.clips = split_clips(master, plan)
    return master


def duration_note(master: MasterPrompt, booked_s: int, *, cap_s: int = 0) -> str:
    """The reply line that hands duration control back to the user."""
    est = master.estimated_duration_s
    if abs(est - booked_s) <= 1:
        return f"The action as derived plays about {est}s — the {booked_s}s you booked fits."
    if est > booked_s:
        if cap_s and est > cap_s:
            return (
                f"The action as derived plays about {est}s, more than the {booked_s}s booked "
                f"— and past the {cap_s}s one-shot cap. Draft a script for the full runtime, "
                "or simplify the idea."
            )
        return (
            f"The action as derived plays about {est}s, more than the {booked_s}s booked "
            f"— set Duration to {est}s if you want the whole beat on screen."
        )
    return (
        f"The action as derived plays about {est}s, less than the {booked_s}s booked "
        f"— set Duration to {est}s to book only what the action needs."
    )


def summary_line(master: MasterPrompt) -> str:
    chain = f", {len(master.clips)} chained clips" if master.clips else ""
    return (
        f"Master prompt derived ({master.word_count} words, {len(master.beats)} beats{chain}, "
        f"{'brain' if master.source == 'brain' else 'template'}) — "
        f"style {master.visual_style or 'unset'}; "
        + (f"look {master.look.medium}; " if master.look is not None and master.look.medium else "")
        + f"camera {master.camera or 'unset'}."
    )


def to_doc(master: MasterPrompt | None) -> dict | None:
    return master.model_dump(mode="json") if master is not None else None


def from_doc(doc: dict | None) -> MasterPrompt | None:
    if not doc:
        return None
    try:
        return MasterPrompt.model_validate(doc)
    except Exception:  # noqa: BLE001 — a corrupt artifact must not brick rehydration
        return None


def look_block(master: MasterPrompt | None) -> str:
    """The verbatim LOOK BIBLE clause of a master, or nothing."""
    if master is None or master.look is None:
        return ""
    return master.look.block()


def template_text() -> str:
    """What the brain is told — for the UI's transparency link."""
    return system_prompt()


__all__ = [
    "MASTER_TEMPLATE",
    "DURATION_SECTION",
    "OUTPUT_CONTRACT",
    "BrainTruncated",
    "MasterUnavailable",
    "assemble",
    "beat_budget",
    "derive",
    "duration_note",
    "guard_likeness",
    "length_band",
    "optimize",
    "pacing_sentence",
    "split_clips",
    "split_section",
    "summary_line",
    "system_prompt",
    "user_message",
    "look_block",
]

