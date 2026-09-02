"""Idea → shooting script + bibles.

With a Director brain configured (``DIONY_DIRECTOR_BRAIN_URL``, an
OpenAI-compatible endpoint), the script is authored by the model and parsed
strictly. Offline — and whenever the brain fails — a deterministic template
takes over, and the degradation is always named in ``warnings``: a production
can never silently become template-only.
"""

from __future__ import annotations

import json
import os
import uuid
from dataclasses import dataclass, field

import httpx

from diony_contracts.production import (
    CharacterProfile,
    MasterPrompt,
    Scene,
    Script,
    ScriptLine,
    SettingProfile,
    VoiceProfile,
)

def brain_timeout_s() -> float:
    raw = os.environ.get("DIONY_DIRECTOR_BRAIN_TIMEOUT", "").strip()
    return float(raw) if raw.replace(".", "", 1).isdigit() else 600.0


CALL_TIMEOUT_S = 120.0  # legacy constant; the brain uses brain_timeout_s()

VOICE_PRESETS = ("Achird", "Achernar", "Algieba", "Alnilam", "Ankaa")

_SETTING_KEYWORDS = {
    "diner": ("INT", "Neon Diner", "low diner hum, fridge compressor, distant traffic"),
    "bar": ("INT", "Back-Street Bar", "murmur, glassware, a jukebox two rooms away"),
    "office": ("INT", "Empty Office", "HVAC hiss, fluorescent buzz"),
    "warehouse": ("INT", "Dockside Warehouse", "cavernous air, dripping water, gulls outside"),
    "desert": ("EXT", "Desert Flats", "wind over sand, insect ticks"),
    "town": ("EXT", "Small Town Main Street", "night wind, a loose sign creaking"),
    "street": ("EXT", "Wet City Street", "rain easing, tires on asphalt"),
    "beach": ("EXT", "Empty Beach", "surf, wind, distant bell buoy"),
    "forest": ("EXT", "Pine Forest", "wind in needles, one far-off bird"),
    "rooftop": ("EXT", "City Rooftop", "wind, ventilation fans, city floor below"),
}


@dataclass
class ScreenplayDraft:
    script: Script
    characters: list[CharacterProfile]
    settings: list[SettingProfile]
    warnings: list[str] = field(default_factory=list)
    brain_usage: dict = field(default_factory=dict)  # token spend, surfaced honestly
    # Blueprint 27: the pre-split finalized artifact this screenplay realizes
    master: MasterPrompt | None = None


XAI_BRAIN_BASE = "https://api.x.ai"
DEFAULT_BRAIN_MODEL = "grok-4.6"


def brain_url() -> str:
    """The Director brain endpoint (OpenAI-compatible chat completions).

    Unset → defaults to xAI when a key is present (zero-config Grok brain).
    Explicitly empty → the brain is off and the deterministic template runs.
    """
    raw = os.environ.get("DIONY_DIRECTOR_BRAIN_URL")
    if raw is not None:
        return raw.strip().rstrip("/")
    if os.environ.get("XAI_API_KEY", "").strip():
        return XAI_BRAIN_BASE
    return ""


def brain_model() -> str:
    return os.environ.get("DIONY_DIRECTOR_BRAIN_MODEL", "").strip() or DEFAULT_BRAIN_MODEL


def _brain_headers() -> dict[str, str]:
    key = os.environ.get("DIONY_DIRECTOR_BRAIN_KEY", "").strip()
    if not key and "x.ai" in brain_url():
        key = os.environ.get("XAI_API_KEY", "").strip()
    return {"Authorization": f"Bearer {key}"} if key else {}


def _voice(char_id: str, index: int) -> VoiceProfile:
    return VoiceProfile(
        voice_id=f"vox-{char_id}",
        engine_ref=VOICE_PRESETS[index % len(VOICE_PRESETS)],
    )


def _json_object(raw: str) -> dict:
    text = raw.strip()
    if text.startswith("```"):
        text = text.strip("`\n")
        if text.startswith("json"):
            text = text[4:]
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("no JSON object in brain reply")
    return json.loads(text[start : end + 1])


_BRAIN_INSTRUCTIONS = (
    "You are the Director's head of story, cinematography, costume, and sound, "
    "writing a shooting script for AI video generation. Every clip is generated "
    "from your words alone — ONLY what you write exists on screen, so vague "
    "writing becomes vague pictures. Return ONLY a JSON object with keys: "
    "logline (str), beats (list of {beat, story_time}), settings (list of "
    "{setting_id, name, kind: INT|EXT, room_tone, time_weather, grade_language, "
    "camera_kit}), characters (list of {char_id, name, role?, age_body_notes, "
    "wardrobe}), scenes (list of {heading, setting_ref, story_time, cast, "
    "emotional_job, lines: list of {kind: action|dialogue|vo|parenthetical|"
    "sound, text, speaker?, shot?}}).\n\n"
    "SHOTS — every action, dialogue, and vo line carries 'shot': the exact "
    "frame in one sentence — size, angle, movement, and what dominates the "
    "picture (e.g. 'Low-angle medium close-up; the camera arcs slowly left as "
    "she rises, rain-streaked window burning sodium-orange behind her'). "
    "Movement is MOTIVATED by the action, never decorative. Never repeat the "
    "same size+movement on consecutive shots. Within a scene, coverage "
    "tightens as the tension rises — wide to close, not a random shuffle.\n\n"
    "ACTION lines are physical and filmable: concrete bodies, props, and light "
    "— never abstractions. 'Tension fills the room' is banned; write the "
    "white-knuckled hand, the untouched glass, the doorway someone won't walk "
    "through. One beat per line, one complete present-tense sentence.\n\n"
    "DIALOGUE is subtext-forward, speakable aloud, under 18 words, and each "
    "character's diction is distinct in word choice and rhythm. No exposition "
    "dumps, no announcing feelings.\n\n"
    "SOUND: each scene names its room in 1-2 foley events tied to on-screen "
    "action. If the film carries music, write exactly ONE sound line per "
    "scored scene naming a single piece — genre, tempo, instrumentation, and "
    "where it plays from — the same piece continuing across the whole scene.\n\n"
    "CHARACTERS: age_body_notes is a REAL, castable face and body — age, "
    "build, skin, hair texture and length, one distinguishing physical detail. "
    "wardrobe is one specific outfit worn for the whole film (garments, "
    "colors, fabrics — 'worn grey wool coat over a black turtleneck, dark "
    "denim').\n\n"
    "SETTINGS: time_weather names the light ('Night 1, sodium streetlight, "
    "light rain'); grade_language the palette and contrast ('warm practicals "
    "against teal shadow'); camera_kit the glass and movement grammar "
    "('32/50/85mm primes, chest height, slow arcs and push-ins').\n\n"
    "STRUCTURE: every scene changes something irreversible; stakes escalate "
    "scene to scene; scene shapes VARY — never the same establishing/line/"
    "reaction template twice; the film ends on an image, not a summary. "
    "Headings follow screenplay form 'INT. NAME - TIME'."
)

# Deterministic craft lint: the notes a script supervisor would write before
# anyone spends a cent on pixels. Feeds the brain's revision round verbatim.
_ABSTRACTION_TELLS = (
    "tension fills", "a sense of", "palpable", "we see", "we hear",
    "atmosphere of", "feeling of", "an air of", "emotions run",
)


def _script_lint(body: dict) -> list[str]:
    notes: list[str] = []
    shapes: dict[str, list[str]] = {}
    prev_shot = ""
    for index, scene in enumerate(body.get("scenes") or [], start=1):
        lines = scene.get("lines") or []
        shape = ",".join(str(l.get("kind", "")) for l in lines)
        shapes.setdefault(shape, []).append(f"scene {index}")
        has_sound = False
        for line in lines:
            kind = str(line.get("kind", ""))
            text = str(line.get("text", ""))
            shot = str(line.get("shot", "") or "").strip()
            if kind in {"action", "dialogue", "vo"} and not shot:
                notes.append(f"scene {index}: a {kind} line has no authored shot — the frame is unwritten")
            if shot and shot.lower() == prev_shot.lower():
                notes.append(f"scene {index}: the same shot repeats back-to-back ({shot[:60]})")
            if shot:
                prev_shot = shot
            if kind == "action":
                lowered = text.lower()
                for tell in _ABSTRACTION_TELLS:
                    if tell in lowered:
                        notes.append(
                            f"scene {index}: action line is abstract, not filmable ('{text[:60]}')"
                        )
                        break
            if kind == "dialogue" and len(text.split()) > 18:
                notes.append(f"scene {index}: dialogue runs {len(text.split())} words — trim under 18")
            if kind == "sound":
                has_sound = True
        if not has_sound:
            notes.append(f"scene {index}: no sound line — the room is silent on paper")
    for shape, where in shapes.items():
        if len(where) > 1 and shape:
            notes.append(
                f"scenes share an identical shape ({', '.join(where)}) — vary the construction"
            )
    return notes


_REFINE_INSTRUCTIONS = (
    "You are the studio's script doctor on the same production. Below is the "
    "draft JSON and the supervisor's notes. Rewrite the draft to resolve every "
    "note while keeping what already works — sharper shots, more physical "
    "action, tighter dialogue, distinct scene shapes. Keep the exact same JSON "
    "schema, the same setting_ids and char_ids, and return ONLY the full "
    "revised JSON object."
)


def brain_refine_enabled() -> bool:
    return os.environ.get("DIONY_DIRECTOR_BRAIN_REFINE", "").strip() != "0"


def _image_part(path: str) -> dict:
    """A local reference still as an OpenAI-content image part (data URI)."""

    import base64 as _b64
    from pathlib import Path as _Path

    candidate = _Path(path)
    suffix = candidate.suffix.lstrip(".") or "png"
    encoded = _b64.b64encode(candidate.read_bytes()).decode()
    return {
        "type": "image_url",
        "image_url": {"url": f"data:image/{suffix};base64,{encoded}"},
    }


def master_brief(master: MasterPrompt | None) -> str:
    """The master prompt as the screenplay's creative law (Blueprint 27).

    The artifact already settled WHO, WHAT, WHERE, the camera philosophy, the
    lighting system, the continuity anchors, the constraints, and the ending.
    The screenplay REALIZES it across scenes and shots — it never
    reinterprets it, and its last scene lands the ending exactly."""
    if master is None:
        return ""
    beats = " → ".join(
        f"{b.get('stage', '').replace('_', ' ')}: {b.get('text', '')}" for b in master.beats
    )
    light = master.lighting
    lighting = ", ".join(x for x in (light.get("source"), light.get("quality"), light.get("shadows")) if x)
    parts = [
        "MASTER PROMPT — the pre-split finalized artifact this screenplay REALIZES. "
        "Its subject, setting, camera philosophy, lighting system, continuity "
        "anchors, constraints, and ending are already established: preserve them, "
        "do not reinterpret them. Scenes follow the beats in order; the final scene "
        "lands the ending exactly.",
        f"Generator-ready prompt: {master.prompt}",
    ]
    if master.primary_subject:
        parts.append(f"Subject: {master.primary_subject}.")
    if master.setting:
        parts.append(f"Setting: {master.setting}.")
    if beats:
        parts.append(f"Beats: {beats}.")
    if master.camera:
        parts.append(f"Camera philosophy: {master.camera}.")
    if lighting:
        parts.append(f"Lighting system: {lighting}.")
    if master.mood:
        parts.append(f"Mood: {master.mood}.")
    if master.continuity_anchors:
        parts.append("Continuity anchors: " + "; ".join(master.continuity_anchors) + ".")
    if master.constraints:
        parts.append("Constraints: " + "; ".join(master.constraints) + ".")
    if master.ending:
        parts.append(f"Ending: {master.ending}.")
    return "\n".join(parts)


def _from_brain(
    idea: str,
    runtime_target_s: int,
    reference_images: list[str] | None = None,
    master: MasterPrompt | None = None,
) -> ScreenplayDraft:
    scene_target = max(2, round((runtime_target_s - 18) / 16))
    user_text = (
        f"Idea: {idea}\nRuntime target: {runtime_target_s}s.\n"
        f"Write approximately {scene_target} scenes; each plays roughly 16-20 "
        "seconds on screen, built from 2-4 beats whose construction VARIES "
        "scene to scene — open some on a detail, some on movement, some mid-"
        "action; not every scene needs dialogue. Vary settings, escalate "
        "stakes, and author every shot."
    )
    brief = master_brief(master)
    if brief:
        user_text += "\n\n" + brief
    refs = [r for r in (reference_images or []) if r]
    if refs:
        # The brain SEES the user's reference stills — wardrobe, faces, and
        # places are described from the actual pixels, never from filenames.
        user_content: str | list = [
            {
                "type": "text",
                "text": user_text
                + "\nThe attached reference images show the intended people, "
                "wardrobe, and places. Describe wardrobe, age_body_notes, and "
                "settings to MATCH what the images actually show.",
            }
        ] + [_image_part(r) for r in refs[:4]]
    else:
        user_content = user_text
    from diony_director.providers import base as provider_base

    # retried on transport blips (SSL bad_record_mac killed drafts with
    # attached stills — large uploads are the most exposed requests we make)
    response = provider_base.post_with_retries(
        f"{brain_url()}/v1/chat/completions",
        what="brain draft",
        json={
            "model": brain_model(),
            "messages": [
                {"role": "system", "content": _BRAIN_INSTRUCTIONS},
                {"role": "user", "content": user_content},
            ],
            "temperature": 0.6,
            "max_tokens": 32768,
            "response_format": {"type": "json_object"},
        },
        headers=_brain_headers(),
        timeout=brain_timeout_s(),
    )
    if response.status_code >= 400:
        raise RuntimeError(
            f"brain rejected ({response.status_code}): {response.text[:200]}"
        )
    payload = response.json()
    body = _json_object(payload["choices"][0]["message"]["content"])
    usage = dict(payload.get("usage") or {})

    warnings: list[str] = []

    # The iterative craft pass: a supervisor's lint of the draft, and one
    # script-doctor round with the brain to resolve it. Script tokens are
    # pennies against the video seconds they steer — a flat draft that ships
    # unrevised is the expensive path.
    notes = _script_lint(body)
    if notes and brain_refine_enabled():
        try:
            refine_response = provider_base.post_with_retries(
                f"{brain_url()}/v1/chat/completions",
                what="brain refine",
                json={
                    "model": brain_model(),
                    "messages": [
                        {"role": "system", "content": _BRAIN_INSTRUCTIONS},
                        {
                            "role": "user",
                            "content": (
                                _REFINE_INSTRUCTIONS
                                + "\n\nDRAFT:\n"
                                + json.dumps(body)
                                + "\n\nSUPERVISOR NOTES:\n- "
                                + "\n- ".join(notes[:24])
                            ),
                        },
                    ],
                    "temperature": 0.4,
                    "max_tokens": 32768,
                    "response_format": {"type": "json_object"},
                },
                headers=_brain_headers(),
                timeout=brain_timeout_s(),
            )
            if refine_response.status_code >= 400:
                raise RuntimeError(f"refine rejected ({refine_response.status_code})")
            refine_payload = refine_response.json()
            revised = _json_object(refine_payload["choices"][0]["message"]["content"])
            if revised.get("scenes"):
                body = revised
                warnings.append(
                    f"screenwriter: script-doctor pass resolved {len(notes)} supervisor note(s)"
                )
                for key, value in (refine_payload.get("usage") or {}).items():
                    if isinstance(value, int):
                        usage[key] = int(usage.get(key) or 0) + value
        except Exception as err:  # noqa: BLE001 - the first draft still ships, named
            warnings.append(
                f"screenwriter: script-doctor pass unavailable ({type(err).__name__}) — "
                f"first draft shipped with {len(notes)} open note(s)"
            )
    elif notes:
        warnings.append(
            f"screenwriter: {len(notes)} supervisor note(s) open (refine disabled)"
        )

    def _craft(source: dict, key: str, default: str, owner: str) -> str:
        value = str(source.get(key, "") or "").strip()
        if value:
            return value
        warnings.append(f"screenwriter: brain omitted {key} for {owner} — defaulted")
        return default

    settings = [
        SettingProfile(
            setting_id=str(s["setting_id"]),
            name=str(s["name"]),
            room_tone_id=f"{s['setting_id']}/tone",
            architecture_notes=str(s.get("room_tone", "")),
            time_weather=_craft(s, "time_weather", "night, practical light", str(s["name"])),
            grade_language=_craft(
                s,
                "grade_language",
                "warm practicals against cool shadow"
                if str(s.get("kind", "INT")).upper() == "INT"
                else "available light, cool ambient sky",
                str(s["name"]),
            ),
            camera_kit=_craft(
                s, "camera_kit", "35-50mm, chest height, deliberate slow moves", str(s["name"])
            ),
        )
        for s in body["settings"]
    ]
    characters = [
        CharacterProfile(
            char_id=str(c["char_id"]),
            name=str(c["name"]),
            role=str(c.get("role", "")),
            age_body_notes=str(c.get("age_body_notes", "")),
            wardrobe={
                "default": _craft(
                    c,
                    "wardrobe",
                    "one consistent, practical outfit worn for the entire film",
                    str(c["name"]),
                )
            },
            voice=_voice(str(c["char_id"]), i),
        )
        for i, c in enumerate(body["characters"])
    ]

    lines: list[ScriptLine] = []
    scenes: list[Scene] = []
    counter = 0

    def _add(kind: str, text: str, scene_id: str, speaker: str = "", shot: str = "") -> str:
        nonlocal counter
        counter += 1
        line = ScriptLine(
            line_id=f"ln-{counter}",
            kind=kind,
            text=text,
            speaker=speaker,
            scene_id=scene_id,
            shot=shot,
        )
        lines.append(line)
        return line.line_id

    for index, scene_body in enumerate(body["scenes"], start=1):
        scene_id = f"sc-{index}"
        line_ids = [_add("heading", str(scene_body["heading"]), scene_id)]
        for raw in scene_body["lines"]:
            kind = str(raw["kind"])
            speaker = str(raw.get("speaker", ""))
            shot = str(raw.get("shot", "") or "").strip()
            if kind == "dialogue" and speaker:
                line_ids.append(_add("cue", speaker, scene_id, speaker))
            line_ids.append(_add(kind, str(raw["text"]), scene_id, speaker, shot))
        scenes.append(
            Scene(
                scene_id=scene_id,
                heading=str(scene_body["heading"]),
                setting_ref=str(scene_body["setting_ref"]),
                cast=[str(c) for c in scene_body.get("cast", [])],
                story_time=str(scene_body.get("story_time", "")),
                line_ids=line_ids,
                emotional_job=str(scene_body.get("emotional_job", "")),
            )
        )

    script = Script(
        id=f"scr-{uuid.uuid4().hex[:8]}",
        logline=str(body["logline"]),
        runtime_target_s=runtime_target_s,
        beats=[{"beat": str(b["beat"]), "story_time": str(b["story_time"])} for b in body["beats"]],
        scenes=scenes,
        lines=lines,
    )
    return ScreenplayDraft(
        script=script,
        characters=characters,
        settings=settings,
        warnings=warnings,
        master=master,
        brain_usage={
            "prompt_tokens": int(usage.get("prompt_tokens") or 0),
            "completion_tokens": int(usage.get("completion_tokens") or 0),
        }
        if usage
        else {},
    )


def _snip(idea: str, cap: int) -> str:
    text = idea.strip().rstrip(".")
    if len(text) <= cap:
        return text
    return text[:cap].rsplit(" ", 1)[0].rstrip(",.;:") + "…"


def _template(
    idea: str, runtime_target_s: int, cast_size: int, master: MasterPrompt | None = None
) -> ScreenplayDraft:
    lowered = idea.lower()
    kind, name, tone = ("EXT", "Open Ground", "night wind, far traffic")
    for keyword, mapped in _SETTING_KEYWORDS.items():
        if keyword in lowered:
            kind, name, tone = mapped
            break
    # The master prompt's derivations are law where the template would
    # otherwise guess: its lighting system names the setting's light and
    # grade, its camera philosophy the opener's frame, its ending the last
    # image on screen.
    light = (master.lighting if master is not None else {}) or {}
    grade = ", ".join(x for x in (light.get("quality"), light.get("shadows")) if x)
    primary = SettingProfile(
        setting_id="set-primary",
        name=name,
        room_tone_id="set-primary/tone",
        architecture_notes=tone,
        time_weather=f"Night 1, {light['source']}" if light.get("source") else "Night 1, clear",
        grade_language=grade or "warm practicals against cool night",
        camera_kit="35-50mm, chest height, slow push-ins",
    )
    settings = [primary]
    road = None
    if runtime_target_s >= 60:
        road = SettingProfile(
            setting_id="set-road",
            name="Open Road",
            room_tone_id="set-road/tone",
            architecture_notes="engine drone, wind at speed",
            time_weather="Night 1, later",
            grade_language="headlights and horizon glow",
            camera_kit="wide and long lens alternating",
        )
        settings.append(road)

    characters = [
        CharacterProfile(
            char_id="chr-lead",
            name="LEAD",
            role="protagonist",
            wardrobe={"default": "charcoal work jacket over a grey tee, dark jeans, scuffed boots"},
            voice=_voice("chr-lead", 0),
        )
    ]
    if cast_size >= 2:
        characters.append(
            CharacterProfile(
                char_id="chr-second",
                name="SECOND",
                role="partner",
                wardrobe={"default": "olive canvas coat, black scarf, worn leather gloves"},
                voice=_voice("chr-second", 1),
            )
        )

    lines: list[ScriptLine] = []
    scenes: list[Scene] = []
    counter = 0

    def _add(kind_: str, text: str, scene_id: str, speaker: str = "", shot: str = "") -> str:
        nonlocal counter
        counter += 1
        line = ScriptLine(
            line_id=f"ln-{counter}",
            kind=kind_,
            text=text,
            speaker=speaker,
            scene_id=scene_id,
            shot=shot,
        )
        lines.append(line)
        return line.line_id

    heading_1 = f"{kind}. {name.upper()} - NIGHT"
    opener_action = f"{_snip(idea, 240)}. The place holds its breath."
    opener_shot = (
        f"Wide establishing frame of {name}, locked off at chest height, "
        "one practical light carving the dark"
    )
    if master is not None:
        initial = master.beat("initial_state")
        if initial:
            snipped = _snip(idea, 200)
            opener_action = (
                f"{initial[:1].upper()}{initial[1:].rstrip('.')}. "
                f"{snipped[:1].upper()}{snipped[1:]}."
            )
        if master.camera:
            opener_shot = f"The camera {master.camera[:1].lower()}{master.camera[1:].rstrip('.')}"
    sc1 = [
        _add("heading", heading_1, "sc-1"),
        _add("action", opener_action, "sc-1", shot=opener_shot),
        _add("sound", f"Room tone: {tone}.", "sc-1"),
    ]
    scenes.append(
        Scene(
            scene_id="sc-1",
            heading=heading_1,
            setting_ref=primary.setting_id,
            cast=[c.char_id for c in characters],
            story_time="Night 1",
            line_ids=sc1,
            emotional_job="establish the world and the pressure",
        )
    )

    # Development scenes scale with the runtime target. Calibration: an
    # opener/coverage flush (~6s) + one spoken line (~6s) + a reaction (~4s)
    # plans roughly 16 seconds per scene through the splitter.
    beats = [
        ("LEAD stops moving. A decision lands.", "low, deciding",
         "We finish this tonight. Then we are gone.",
         "SECOND holds the look, then nods once.",
         "Medium close-up, slow push-in as the stillness sets, face half in shadow",
         "Reverse single at eye level, holding on the nod, background falling soft"),
        ("SECOND spreads the map across the table.", "quick, certain",
         "Two ways in. Only one way back out.",
         "LEAD traces the route without touching it.",
         "Overhead top-shot of the map unrolling, hands entering from both sides",
         "Tight insert on a finger hovering over the route, lamplight raking the paper"),
        ("Headlights sweep the window and pass.", "barely above the hum",
         "They are already looking for us.",
         "Neither of them moves until the light dies.",
         "Wide from the back wall, the beam wiping across both figures",
         "Two-shot in profile, frozen, the last of the light sliding off their faces"),
        ("LEAD counts what is left. It is not much.", "flat, honest",
         "After this, we start clean. All of it.",
         "SECOND laughs once, without any joy in it.",
         "Close on hands counting worn bills, shallow focus, tabletop grain sharp",
         "Medium single, a laugh that dies early, eyes elsewhere"),
        ("A phone buzzes face-down. Nobody reaches for it.", "measured",
         "Whoever that is, they can wait until morning.",
         "The buzzing gives up. The room settles.",
         "Insert on the phone vibrating against wood, both figures blurred beyond",
         "Slow drift back from the table as the sound stops"),
        ("SECOND checks the clock over the pass.", "urgent, hushed",
         "Nineteen minutes. Then the shift changes.",
         "LEAD stands and rolls a shoulder loose.",
         "Low angle up at the clock, second hand crossing twelve",
         "Full-body single rising out of frame, chair scraping back"),
        ("Rain starts against the glass, soft then certain.", "quiet",
         "Weather like this hides a multitude of sins.",
         "SECOND pulls a coat from the booth.",
         "Macro on the first drops hitting the window, neon smearing through",
         "Medium two-shot, one figure shrugging into a coat, the other watching the rain"),
        ("LEAD sets the keys on the table between them.", "even, final",
         "Last chance to walk away from this one.",
         "SECOND slides the keys straight back.",
         "Top-down insert: keys landing between two pairs of hands",
         "Matching insert: the keys sliding back, fingertips releasing at frame edge"),
    ]
    per_dev_s = 16
    base_s = 18
    # No scene cap: the script scales to the runtime target. Cost governs
    # length, and the locked estimate makes that cost visible before anything rolls.
    dev_count = max(1, round((runtime_target_s - base_s) / per_dev_s))
    dev_settings = [primary] + ([road] if road is not None else [])
    speakers = [c for c in characters]
    for index in range(dev_count):
        scene_id = f"sc-{index + 2}"
        setting = dev_settings[index % len(dev_settings)]
        heading = f"{'EXT' if setting is road else kind}. {setting.name.upper()} - NIGHT"
        before, direction, line, after, before_shot, after_shot = beats[index % len(beats)]
        speaker = speakers[index % len(speakers)].name
        cycle = index // len(beats)
        if cycle:
            before = f"{before} Later still."
        line_ids = [
            _add("heading", heading, scene_id),
            _add("action", before.replace("LEAD", characters[0].name).replace(
                "SECOND", speakers[-1].name), scene_id, shot=before_shot),
            _add("cue", speaker, scene_id, speaker),
            _add("parenthetical", direction, scene_id, speaker),
            _add("dialogue", line, scene_id, speaker),
            _add("action", after.replace("LEAD", characters[0].name).replace(
                "SECOND", speakers[-1].name), scene_id, shot=after_shot),
        ]
        scenes.append(
            Scene(
                scene_id=scene_id,
                heading=heading,
                setting_ref=setting.setting_id,
                cast=[c.char_id for c in characters],
                story_time="Night 1",
                line_ids=line_ids,
                emotional_job="raise the pressure",
            )
        )

    if road is not None:
        close_id = f"sc-{dev_count + 2}"
        heading_close = "EXT. OPEN ROAD - NIGHT"
        close_lines = [
            _add("heading", heading_close, close_id),
            _add(
                "action", "Headlights carve the dark. The town shrinks behind.", close_id,
                shot="Long lens from the roadside as the car passes; taillights pull away "
                "into black, town glow dying on the horizon",
            ),
            _add(
                "vo", "Some towns you leave. Some you escape.", close_id, characters[0].name,
                shot="Slow aerial drift above the road, one car on it, high beams the only light",
            ),
        ]
        scenes.append(
            Scene(
                scene_id=close_id,
                heading=heading_close,
                setting_ref=road.setting_id,
                cast=[characters[0].char_id],
                story_time="Night 1, later",
                line_ids=close_lines,
                emotional_job="release",
            )
        )

    if master is not None and master.ending:
        # The artifact's ending is the last image on screen — appended to the
        # final scene as its closing beat, framed by the master camera.
        last = scenes[-1]
        ending_text = f"{master.ending[:1].upper()}{master.ending[1:].rstrip('.')}."
        last.line_ids.append(
            _add(
                "action",
                ending_text,
                last.scene_id,
                shot=f"The camera {master.camera[:1].lower()}{master.camera[1:].rstrip('.')}"
                if master.camera
                else "Held final frame, the composition unchanged",
            )
        )

    script = Script(
        id=f"scr-{uuid.uuid4().hex[:8]}",
        logline=f"{_snip(idea, 160)} — and tonight it ends.",
        runtime_target_s=runtime_target_s,
        beats=[
            {"beat": "the world under pressure", "story_time": "Night 1"},
            {"beat": "the decision", "story_time": "Night 1"},
            {"beat": "the road out", "story_time": "Night 1, later"},
        ],
        scenes=scenes,
        lines=lines,
    )
    return ScreenplayDraft(
        script=script,
        characters=characters,
        settings=settings,
        warnings=["screenwriter: deterministic template (Director brain not configured)"],
        master=master,
    )


BRAIN_ATTEMPTS = 2


class BrainUnavailable(RuntimeError):
    """The configured brain could not deliver a screenplay.

    With a brain configured there is NO template fallback: a failed draft is a
    named, retryable state — the user drafts again, and Grok thinks as long as
    it needs (brain_timeout_s, default 600s). The template exists only for
    setups with the brain off.
    """


def write_script(
    idea: str,
    runtime_target_s: int = 60,
    cast_size: int = 2,
    *,
    reference_images: list[str] | None = None,
    master: MasterPrompt | None = None,
) -> ScreenplayDraft:
    """`master` is the pre-split finalized artifact (Blueprint 27): when given,
    the screenplay realizes it — brain or template — and carries it forward."""
    if brain_url():
        last_error: Exception | None = None
        for _attempt in range(BRAIN_ATTEMPTS):
            try:
                return _from_brain(idea, runtime_target_s, reference_images, master)
            except Exception as err:  # noqa: BLE001 — retried, then raised as a named state
                last_error = err
        raise BrainUnavailable(
            f"{brain_model()} could not deliver after {BRAIN_ATTEMPTS} attempts: {last_error}"
        )
    return _template(idea, runtime_target_s, cast_size, master)
