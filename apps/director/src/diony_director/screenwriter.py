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
    "You are the Diony Director's screenwriter and its heads of costume and "
    "photography. Return ONLY a JSON object with keys: logline (str), beats "
    "(list of {beat, story_time}), settings (list of {setting_id, name, kind: "
    "INT|EXT, room_tone, time_weather, grade_language, camera_kit}), characters "
    "(list of {char_id, name, role?, age_body_notes?, wardrobe}), scenes (list "
    "of {heading, setting_ref, story_time, cast, emotional_job, lines: list of "
    "{kind: action|dialogue|vo|parenthetical|sound, text, speaker?}}). "
    "Headings follow screenplay form 'INT. NAME - TIME'. Dialogue is speakable "
    "and short. Craft fields are MANDATORY and concrete: wardrobe is one "
    "specific outfit the character wears for the whole film (garments, colors, "
    "fabrics — e.g. 'worn grey wool coat over a black turtleneck, dark denim'); "
    "time_weather names the light ('Night 1, sodium streetlight, light rain'); "
    "grade_language is the color/contrast of the setting ('warm practicals "
    "against teal shadow'); camera_kit is lens and movement language "
    "('35-50mm, chest height, slow push-ins'); emotional_job is what the scene "
    "must do to the audience. Every action line is ONE beat — one thing "
    "happening, one complete sentence."
)


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


def _from_brain(
    idea: str, runtime_target_s: int, reference_images: list[str] | None = None
) -> ScreenplayDraft:
    scene_target = max(2, round((runtime_target_s - 18) / 16))
    user_text = (
        f"Idea: {idea}\nRuntime target: {runtime_target_s}s.\n"
        f"Write approximately {scene_target} scenes; each plays "
        "roughly 16-20 seconds on screen (one establishing beat, "
        "one short spoken line, one reaction). Vary settings, "
        "escalate stakes scene to scene, and keep every spoken "
        "line under 20 words."
    )
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
    response = httpx.post(
        f"{brain_url()}/v1/chat/completions",
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
    usage = payload.get("usage") or {}

    warnings: list[str] = []

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

    def _add(kind: str, text: str, scene_id: str, speaker: str = "") -> str:
        nonlocal counter
        counter += 1
        line = ScriptLine(
            line_id=f"ln-{counter}", kind=kind, text=text, speaker=speaker, scene_id=scene_id
        )
        lines.append(line)
        return line.line_id

    for index, scene_body in enumerate(body["scenes"], start=1):
        scene_id = f"sc-{index}"
        line_ids = [_add("heading", str(scene_body["heading"]), scene_id)]
        for raw in scene_body["lines"]:
            kind = str(raw["kind"])
            speaker = str(raw.get("speaker", ""))
            if kind == "dialogue" and speaker:
                line_ids.append(_add("cue", speaker, scene_id, speaker))
            line_ids.append(_add(kind, str(raw["text"]), scene_id, speaker))
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


def _template(idea: str, runtime_target_s: int, cast_size: int) -> ScreenplayDraft:
    lowered = idea.lower()
    kind, name, tone = ("EXT", "Open Ground", "night wind, far traffic")
    for keyword, mapped in _SETTING_KEYWORDS.items():
        if keyword in lowered:
            kind, name, tone = mapped
            break
    primary = SettingProfile(
        setting_id="set-primary",
        name=name,
        room_tone_id="set-primary/tone",
        architecture_notes=tone,
        time_weather="Night 1, clear",
        grade_language="warm practicals against cool night",
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

    def _add(kind_: str, text: str, scene_id: str, speaker: str = "") -> str:
        nonlocal counter
        counter += 1
        line = ScriptLine(
            line_id=f"ln-{counter}", kind=kind_, text=text, speaker=speaker, scene_id=scene_id
        )
        lines.append(line)
        return line.line_id

    heading_1 = f"{kind}. {name.upper()} - NIGHT"
    sc1 = [
        _add("heading", heading_1, "sc-1"),
        _add("action", f"{_snip(idea, 240)}. The place holds its breath.", "sc-1"),
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
         "SECOND holds the look, then nods once."),
        ("SECOND spreads the map across the table.", "quick, certain",
         "Two ways in. Only one way back out.",
         "LEAD traces the route without touching it."),
        ("Headlights sweep the window and pass.", "barely above the hum",
         "They are already looking for us.",
         "Neither of them moves until the light dies."),
        ("LEAD counts what is left. It is not much.", "flat, honest",
         "After this, we start clean. All of it.",
         "SECOND laughs once, without any joy in it."),
        ("A phone buzzes face-down. Nobody reaches for it.", "measured",
         "Whoever that is, they can wait until morning.",
         "The buzzing gives up. The room settles."),
        ("SECOND checks the clock over the pass.", "urgent, hushed",
         "Nineteen minutes. Then the shift changes.",
         "LEAD stands and rolls a shoulder loose."),
        ("Rain starts against the glass, soft then certain.", "quiet",
         "Weather like this hides a multitude of sins.",
         "SECOND pulls a coat from the booth."),
        ("LEAD sets the keys on the table between them.", "even, final",
         "Last chance to walk away from this one.",
         "SECOND slides the keys straight back."),
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
        before, direction, line, after = beats[index % len(beats)]
        speaker = speakers[index % len(speakers)].name
        cycle = index // len(beats)
        if cycle:
            before = f"{before} Later still."
        line_ids = [
            _add("heading", heading, scene_id),
            _add("action", before.replace("LEAD", characters[0].name).replace(
                "SECOND", speakers[-1].name), scene_id),
            _add("cue", speaker, scene_id, speaker),
            _add("parenthetical", direction, scene_id, speaker),
            _add("dialogue", line, scene_id, speaker),
            _add("action", after.replace("LEAD", characters[0].name).replace(
                "SECOND", speakers[-1].name), scene_id),
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
            _add("action", "Headlights carve the dark. The town shrinks behind.", close_id),
            _add("vo", "Some towns you leave. Some you escape.", close_id, characters[0].name),
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
) -> ScreenplayDraft:
    if brain_url():
        last_error: Exception | None = None
        for _attempt in range(BRAIN_ATTEMPTS):
            try:
                return _from_brain(idea, runtime_target_s, reference_images)
            except Exception as err:  # noqa: BLE001 — retried, then raised as a named state
                last_error = err
        raise BrainUnavailable(
            f"{brain_model()} could not deliver after {BRAIN_ATTEMPTS} attempts: {last_error}"
        )
    return _template(idea, runtime_target_s, cast_size)
