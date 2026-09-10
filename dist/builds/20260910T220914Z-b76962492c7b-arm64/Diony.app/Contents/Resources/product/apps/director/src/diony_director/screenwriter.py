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
import re
import uuid
from dataclasses import dataclass, field

import httpx  # noqa: F401 - module-level seam: tests monkeypatch screenwriter.httpx.post

from diony_contracts.production import (
    CharacterProfile,
    MasterPrompt,
    Scene,
    Script,
    ScriptLine,
    SettingProfile,
    VoiceProfile,
)
from diony_director import brain as brain_mod
from diony_director import likeness as _likeness


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


XAI_BRAIN_BASE = brain_mod.XAI_BRAIN_BASE
DEFAULT_BRAIN_MODEL = brain_mod.DEFAULT_GROK_BRAIN_MODEL


def brain_url() -> str:
    """The Director brain base (OpenAI-compatible chat completions), resolved
    by `diony_director.brain`: an explicit DIONY_DIRECTOR_BRAIN_URL, else the
    desk's brain choice, else the camera's own house (Gemini on Google Flow,
    Grok on Grok) — empty when no brain is configured, and then the
    deterministic template runs."""
    return brain_mod.resolve().url


def chat_completions_url() -> str:
    """The URL a brain call posts to (Gemini's compatible endpoint has no /v1)."""
    return brain_mod.resolve().endpoint


def brain_model() -> str:
    return brain_mod.resolve().model


def _brain_headers() -> dict[str, str]:
    key = brain_mod.resolve().key
    return {"Authorization": f"Bearer {key}"} if key else {}


def brain_rejection(response, what: str = "brain") -> RuntimeError:
    """The error a rejected brain call raises. When the provider says the
    model was retired and names its successor (Google's 404 for
    gemini-2.5-pro names gemini-3.1-pro-preview), the successor is
    remembered first, so the caller's next attempt already runs on it."""
    switched = brain_mod.follow_retirement(
        brain_mod.resolve().provider, response.status_code, response.text
    )
    note = f" — switched to {switched} for the next attempt" if switched else ""
    return RuntimeError(f"{what} rejected ({response.status_code}): {response.text[:200]}{note}")


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


# --------------------------------------------------------------------------
# Authored material inside the idea. A user who submits "Scene 4 — …" with
# "Narration: '…'" lines has WRITTEN their film — the enhancement's job is to
# realize that material, never to re-derive it (s-2026-09-02: a 12-scene
# authored breakdown came back as 4 merged scenes with paraphrased narration).
# --------------------------------------------------------------------------

# "Scene 1 — Quiet Success" / "Scene 2:" — numbered headers survive even when
# a paste collapses newlines, so detection never anchors on line starts alone.
_AUTHORED_SCENE_NUM = re.compile(r"(?i)\bscene\s+(\d+)\s*[—–:\-]\s*")
_AUTHORED_HEADING = re.compile(r"(?m)^[ \t]*(?:INT|EXT)\.\s")
# One narration line: a label, an optional stage direction, then either a
# quoted span (curly or straight) or the rest of the line.
_NARRATION_LINE = re.compile(
    r"(?is)\b(?:narration|voice[- ]?over|narrator|v\.\s?o\.)\s*(?:\([^)\n]{0,40}\))?\s*:\s*"
    r"(?:[“\"]\s*(.+?)\s*[”\"]|([^\n]+))"
)
_VISUAL_LINE = re.compile(
    r"(?is)\bvisual\s*:\s*(.+?)(?=\bnarration|\bvoice[- ]?over|\bnarrator\b|$)"
)
_SPEECH_STRIP = re.compile(r"[^a-z0-9 ]+")


def authored_breakdown(idea: str) -> dict:
    """The user's own scripted structure inside the idea — scene headers,
    per-scene visuals, and narration lines the screenplay must realize
    verbatim, not re-derive. Conservative on purpose: only numbered scene
    headers, screenplay sluglines, and labeled narration count as authored."""
    text = idea or ""
    heading_count = len(_AUTHORED_HEADING.findall(text))
    narration: list[str] = []
    for match in _NARRATION_LINE.finditer(text):
        line = " ".join((match.group(1) or match.group(2) or "").split()).strip()
        if line:
            narration.append(line)
    # Structured blocks: "Scene N — Title … Visual: … Narration: …" — the
    # segments between numbered headers, each with its title, its visual, and
    # its own narration lines (order preserved).
    scenes: list[dict] = []
    marks = list(_AUTHORED_SCENE_NUM.finditer(text))
    for index, mark in enumerate(marks):
        segment = text[
            mark.end() : marks[index + 1].start() if index + 1 < len(marks) else len(text)
        ]
        title = " ".join(segment.split("\n", 1)[0].split())
        visual_match = _VISUAL_LINE.search(segment)
        if visual_match:
            title = " ".join(segment[: visual_match.start()].split()) or title
        scenes.append(
            {
                "title": _snip(title, 60),
                "visual": " ".join(visual_match.group(1).split()) if visual_match else "",
                "narration": [
                    " ".join((m.group(1) or m.group(2) or "").split()).strip()
                    for m in _NARRATION_LINE.finditer(segment)
                    if (m.group(1) or m.group(2) or "").strip()
                ],
            }
        )
    scene_count = len(marks) + heading_count
    return {
        "is_authored": scene_count >= 2 or len(narration) >= 2,
        "scene_count": scene_count,
        "narration": narration,
        "scenes": scenes,
    }


def _norm_speech(text: str) -> str:
    text = text.lower().replace("’", "'").replace("‘", "'")
    return " ".join(_SPEECH_STRIP.sub(" ", text).split())


def _narration_gaps(body: dict, narration: list[str]) -> list[str]:
    """Authored narration lines missing from the draft's spoken text. The
    check runs over the vo/dialogue stream in order, so a line the brain split
    across two consecutive vo lines still counts as carried."""
    stream = _norm_speech(
        " ".join(
            str(line.get("text", ""))
            for scene in body.get("scenes") or []
            for line in scene.get("lines") or []
            if str(line.get("kind")) in {"vo", "dialogue"}
        )
    )
    return [line for line in narration if _norm_speech(line) not in stream]


def _restore_narration(body: dict, narration: list[str]) -> tuple[int, int, int]:
    """Force the user's words back into the final body, deterministically,
    and never speak a line twice.

    In order: (1) a spoken line that already IS the authored line is claimed;
    (2) a run of consecutive spoken lines that concatenate to the authored
    line is merged into its first line (the shot survives, the fragments go);
    (3) a paraphrase — the closest unclaimed narration line at difflib ratio
    >= 0.5 — gets its text back verbatim, keeping its scene, shot, and
    speaker; (4) a line with no survivor is inserted as a new vo line beside
    its neighbours in authored order; (5) any narration line still unclaimed
    that is a fragment or a paraphrase of an authored line is dropped — the
    live brain left "The software firm. The contractor." beside the restored
    full line, and the film spoke the sentence twice. Returns
    (restored, inserted, dropped) counts."""
    import difflib

    scenes = body.get("scenes") or []
    spoken: list[tuple[dict, dict]] = [
        (scene, line)
        for scene in scenes
        for line in scene.get("lines") or []
        if str(line.get("kind")) in {"vo", "dialogue"}
    ]
    norms = [_norm_speech(str(line.get("text", ""))) for _, line in spoken]
    targets = [_norm_speech(line) for line in narration]
    # lines the narrator owns: vo, or dialogue with no speaker / the narrator
    # as speaker (narration the brain mislabeled). A named character's line is
    # never merged or overwritten — "Abundance is not access." from the
    # driver is the writer's echo, not a paraphrase to correct.
    owned = [
        str(line.get("kind")) == "vo"
        or str(line.get("speaker", "") or "").strip().upper().startswith("NARRATOR")
        or not str(line.get("speaker", "") or "").strip()
        for _, line in spoken
    ]
    matched: dict[int, dict] = {}
    claimed: set[int] = set()
    restored = 0

    def _remove(scene: dict, line: dict) -> None:
        lines = scene.get("lines") or []
        scene["lines"] = [candidate for candidate in lines if candidate is not line]

    # (1) exact carries
    for a_index, target in enumerate(targets):
        for s_index, norm in enumerate(norms):
            if s_index not in claimed and norm == target:
                claimed.add(s_index)
                matched[a_index] = spoken[s_index][1]
                break
    # (2) a line the brain split across consecutive spoken lines → one line
    for a_index, target in enumerate(targets):
        if a_index in matched:
            continue
        for start in range(len(spoken)):
            if start in claimed or a_index in matched or not owned[start]:
                continue
            acc = ""
            for end in range(start, min(start + 4, len(spoken))):
                if end in claimed or not owned[end]:
                    break
                acc = f"{acc} {norms[end]}".strip()
                if acc == target and end > start:
                    first = spoken[start][1]
                    first["text"] = narration[a_index]
                    for k in range(start + 1, end + 1):
                        _remove(spoken[k][0], spoken[k][1])
                        claimed.add(k)
                    claimed.add(start)
                    matched[a_index] = first
                    restored += 1
                    break
                if not target.startswith(acc):
                    break
    # (3) paraphrases
    for a_index, target in enumerate(targets):
        if a_index in matched:
            continue
        best_index, best_ratio = -1, 0.0
        for s_index, norm in enumerate(norms):
            if s_index in claimed or not owned[s_index]:
                continue
            ratio = difflib.SequenceMatcher(None, target, norm).ratio()
            if ratio > best_ratio:
                best_index, best_ratio = s_index, ratio
        if best_index >= 0 and best_ratio >= 0.5:
            line = spoken[best_index][1]
            line["text"] = narration[a_index]
            restored += 1
            claimed.add(best_index)
            matched[a_index] = line
    # (4) dropped lines
    inserted = 0
    for a_index, authored_line in enumerate(narration):
        if a_index in matched:
            continue
        new_line = {"kind": "vo", "text": authored_line, "speaker": "", "shot": ""}
        anchor = next((matched[j] for j in range(a_index - 1, -1, -1) if j in matched), None)
        placed = False
        if anchor is not None:
            for scene in scenes:
                lines = scene.get("lines") or []
                if any(candidate is anchor for candidate in lines):
                    index = next(i for i, candidate in enumerate(lines) if candidate is anchor)
                    lines.insert(index + 1, new_line)
                    placed = True
                    break
        if not placed:
            follower = next(
                (matched[j] for j in range(a_index + 1, len(narration)) if j in matched), None
            )
            for scene in scenes:
                lines = scene.get("lines") or []
                if follower is not None and any(candidate is follower for candidate in lines):
                    index = next(i for i, candidate in enumerate(lines) if candidate is follower)
                    lines.insert(index, new_line)
                    placed = True
                    break
        if not placed and scenes:
            # nothing matched anywhere: distribute in order across the scenes
            scene = scenes[min(a_index * len(scenes) // max(1, len(narration)), len(scenes) - 1)]
            scene.setdefault("lines", []).append(new_line)
            placed = True
        if placed:
            matched[a_index] = new_line
            inserted += 1
    # (5) leftovers: fragments and paraphrases of lines that now ship verbatim
    dropped = 0
    for s_index, (scene, line) in enumerate(spoken):
        if s_index in claimed or str(line.get("kind")) != "vo":
            # only vo is ever dropped — a character's dialogue that happens to
            # echo the narration is the writer's choice, never a fragment
            continue
        norm = norms[s_index]
        if not norm:
            continue
        duplicate = any(norm in target for target in targets) or any(
            difflib.SequenceMatcher(None, target, norm).ratio() >= 0.5 for target in targets
        )
        if duplicate:
            _remove(scene, line)
            dropped += 1
    return restored, inserted, dropped


_BRAIN_INSTRUCTIONS = (
    "You are the Director's head of story, cinematography, costume, and sound, "
    "writing a shooting script for AI video generation. Every clip is generated "
    "from your words alone — ONLY what you write exists on screen, so vague "
    "writing becomes vague pictures. Return ONLY a JSON object with keys: "
    "logline (str), beats (list of {beat, story_time}), settings (list of "
    "{setting_id, name, kind: INT|EXT, room_tone, time_weather, grade_language, "
    "camera_kit}), characters (list of {char_id, name, role?, age_body_notes, "
    "wardrobe, identity: {anchor, features, negatives}}), scenes (list of "
    "{heading, setting_ref, story_time, cast, emotional_job, lines: list of "
    "{kind: action|dialogue|vo|parenthetical|sound, text, speaker?, shot?, "
    "off_screen?}}).\n\n"
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
    "denim'). identity is the character's IDENTITY CARD, the text that will "
    "ride every shot they appear in verbatim: anchor = one clause of age, "
    "build, and face geometry ('34, lean, oval face, strong brow'); features "
    "= 3 to 5 non-negotiables a viewer would notice missing (hair colour and "
    "length, eye colour, skin tone, one mark — scar, freckles, grey streak); "
    "negatives = what must never change. Concrete nouns, no mood words.\n\n"
    "OFF-SCREEN LINES: a character who is in the scene but out of frame "
    "may speak — set off_screen: true on that dialogue line (screenplay "
    "'(O.S.)'). Use it when the shot should hold on the listener's reaction, "
    "or when the speaker is behind a door, in the next room, or behind the "
    "camera. The line's shot then frames who HEARS it, never the speaker. "
    "Keep it rare and deliberate.\n\n"
    "SETTINGS: time_weather names the light ('Night 1, sodium streetlight, "
    "light rain'); grade_language the palette and contrast ('warm practicals "
    "against teal shadow'); camera_kit the glass and movement grammar "
    "('32/50/85mm primes, chest height, slow arcs and push-ins').\n\n"
    "LOOK: the brief carries the film's LOOK BIBLE — its one medium, palette, "
    "and rendering language, which the system appends verbatim to every shot. "
    "Every setting's grade_language and camera_kit live INSIDE that look: a "
    "variation of the same palette and medium, never a new style, never a "
    "different studio's hand. Never write a setting that reads as another "
    "medium (no 'photorealistic' room in an animated film, no 'illustrated' "
    "room in a live-action one).\n\n"
    "STRUCTURE: every scene changes something irreversible; stakes escalate "
    "scene to scene; scene shapes VARY — never the same establishing/line/"
    "reaction template twice; the film ends on an image, not a summary. "
    "Headings follow screenplay form 'INT. NAME - TIME'.\n\n"
    + _likeness.BRAIN_LAW
    + " A character's name is an invented name or a role label ('THE DRIVER'), "
    "never a real person's; also return real_people at the top level of the JSON."
)

# Deterministic craft lint: the notes a script supervisor would write before
# anyone spends a cent on pixels. Feeds the brain's revision round verbatim.
_ABSTRACTION_TELLS = (
    "tension fills",
    "a sense of",
    "palpable",
    "we see",
    "we hear",
    "atmosphere of",
    "feeling of",
    "an air of",
    "emotions run",
)


def _script_lint(body: dict, *, verbatim_speech: frozenset[str] = frozenset()) -> list[str]:
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
                notes.append(
                    f"scene {index}: a {kind} line has no authored shot — the frame is unwritten"
                )
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
            if (
                kind == "dialogue"
                and len(text.split()) > 18
                and _norm_speech(text) not in verbatim_speech
            ):
                # authored narration is the user's finished words — never trimmed
                notes.append(
                    f"scene {index}: dialogue runs {len(text.split())} words — trim under 18"
                )
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
    lighting = ", ".join(
        x for x in (light.get("source"), light.get("quality"), light.get("shadows")) if x
    )
    parts = [
        "MASTER PROMPT — the pre-split finalized artifact this screenplay REALIZES. "
        "Its subject, setting, camera philosophy, lighting system, continuity "
        "anchors, constraints, and ending are already established: preserve them, "
        "do not reinterpret them. Scenes follow the beats in order; the final scene "
        "lands the ending exactly.",
        f"Generator-ready prompt: {master.prompt}",
    ]
    if master.look is not None and master.look.block():
        parts.append(
            "LOOK BIBLE (film-wide, appended verbatim to every shot — every setting's "
            "grade lives inside it): " + master.look.block()
        )
    if master.visual_style:
        parts.append(f"Visual style: {master.visual_style}.")
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


def take_brief(take_units: dict[str, int] | None) -> str:
    """Blueprint 33: tell the screenwriter how long a take runs on the route,
    so it writes beats that flow as continuous motion instead of fragments
    the splitter would have to pack four at a time."""
    if not take_units:
        return ""
    from diony_director.splitter import take_units_phrase

    return (
        f"TAKES: the engine shoots continuous takes of {take_units_phrase(take_units)}, "
        "and consecutive beats are packed into takes that long — a 16-20 second "
        "scene plays as one or two takes, not five clips. Write each beat as "
        "continuous physical motion inside ONE camera setup that can hold for "
        "several seconds; change the setup only when the story needs a new angle; "
        "never write a string of one-second fragments. A spoken line and the "
        "action around it share a take, so the beat before a line sets it up "
        "and the beat after it is the reaction, in the same frame."
    )


def _from_brain(
    idea: str,
    runtime_target_s: int,
    reference_images: list[str] | None = None,
    master: MasterPrompt | None = None,
    take_units: dict[str, int] | None = None,
) -> ScreenplayDraft:
    scene_target = max(2, round((runtime_target_s - 18) / 16))
    authored = authored_breakdown(idea)
    if authored["is_authored"]:
        # The user wrote their film. The screenplay REALIZES it: no scene
        # merged away, no narration paraphrased, no re-derived structure.
        scene_budget = ""
        if authored["scene_count"]:
            per_scene = max(2, round(runtime_target_s / authored["scene_count"]))
            scene_budget = (
                f" Write one scene per authored scene — {authored['scene_count']} scenes "
                f"of roughly {per_scene} seconds each; merge only adjacent scenes that "
                "share a location, and never below the count the imagery needs."
            )
        user_text = (
            f"Idea: {idea}\nRuntime target: {runtime_target_s}s.\n"
            "The idea above contains the user's own AUTHORED scene breakdown"
            + (f" ({authored['scene_count']} scenes)" if authored["scene_count"] else "")
            + ". REALIZE it — do not re-derive, merge away, or paraphrase it. "
            "Cover every authored scene's imagery, in the authored order; "
            "adjacent scenes in one location may share a heading, but no "
            "scene's visual content may be dropped." + scene_budget + " Carry EVERY authored "
            "narration line into the script as a vo line, VERBATIM and in "
            "order — the narration is the user's finished voiceover, exempt "
            "from the dialogue word cap. Author every shot."
        )
    else:
        user_text = (
            f"Idea: {idea}\nRuntime target: {runtime_target_s}s.\n"
            f"Write approximately {scene_target} scenes; each plays roughly 16-20 "
            "seconds on screen, built from 2-4 beats whose construction VARIES "
            "scene to scene — open some on a detail, some on movement, some mid-"
            "action; not every scene needs dialogue. Vary settings, escalate "
            "stakes, and author every shot."
        )
    takes = take_brief(take_units)
    if takes:
        user_text += "\n\n" + takes
    brief = master_brief(master)
    if brief:
        user_text += "\n\n" + brief
        if authored["is_authored"]:
            user_text += (
                "\nWhere the master prompt compresses the authored breakdown, "
                "the AUTHORED scenes and narration are senior for structure and "
                "words; the master stays law for style, camera, lighting, "
                "continuity, and the final image."
            )
    if authored["narration"]:
        user_text += (
            "\n\nAUTHORED NARRATION — every line below appears in the script "
            "as a vo line, verbatim, in this order:\n"
            + "\n".join(f"{i}. {line}" for i, line in enumerate(authored["narration"], start=1))
        )
    refs = [r for r in (reference_images or []) if r]
    if refs:
        # The brain SEES the user's reference stills — wardrobe, faces, and
        # places are described from the actual pixels, never from filenames.
        user_content: str | list = [
            {
                "type": "text",
                "text": user_text + "\nThe attached reference images show the intended people, "
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
        chat_completions_url(),
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
        raise brain_rejection(response)
    payload = response.json()
    body = _json_object(payload["choices"][0]["message"]["content"])
    usage = dict(payload.get("usage") or {})

    warnings: list[str] = []

    # The iterative craft pass: a supervisor's lint of the draft, and one
    # script-doctor round with the brain to resolve it. Script tokens are
    # pennies against the video seconds they steer — a flat draft that ships
    # unrevised is the expensive path.
    verbatim = frozenset(_norm_speech(line) for line in authored["narration"])
    # Missing narration leads the notes: those lines are the user's film, and
    # the refine round truncates its note list at 24.
    notes = [
        f"authored narration line {authored['narration'].index(gap) + 1} is missing or "
        f"paraphrased — restore it verbatim as a vo line: “{gap}”"
        for gap in _narration_gaps(body, authored["narration"])
    ] + _script_lint(body, verbatim_speech=verbatim)
    if notes and brain_refine_enabled():
        try:
            refine_response = provider_base.post_with_retries(
                chat_completions_url(),
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
                raise brain_rejection(refine_response, "refine")
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
        except Exception as err:
            warnings.append(
                f"screenwriter: script-doctor pass unavailable ({type(err).__name__}) — "
                f"first draft shipped with {len(notes)} open note(s)"
            )
    elif notes:
        warnings.append(f"screenwriter: {len(notes)} supervisor note(s) open (refine disabled)")

    if authored["narration"] and _narration_gaps(body, authored["narration"]):
        # the FINAL body still paraphrases or drops the user's words — the
        # deterministic restore pass puts them back; the brain's craft stays,
        # the user's narration ships
        restored, inserted, dropped = _restore_narration(body, authored["narration"])
        if restored:
            warnings.append(
                f"screenwriter: restored {restored} authored narration line(s) verbatim "
                "(the draft had paraphrased them)"
            )
        if inserted:
            warnings.append(
                f"screenwriter: inserted {inserted} authored narration line(s) the draft dropped"
            )
        if dropped:
            warnings.append(
                f"screenwriter: dropped {dropped} duplicate narration fragment(s) the draft "
                "left beside the restored line(s)"
            )
        residue = _narration_gaps(body, authored["narration"])
        if residue:
            warnings.append(
                f"screenwriter: {len(residue)} authored narration line(s) still not carried "
                f"verbatim — first: “{residue[0][:80]}”"
            )

    # The likeness guard (Blueprint 27 §3a): whoever the master derivation
    # recognized, plus whoever this brain confessed, plus whoever a text-only
    # audit of the FINAL draft still finds — described, never named; a real
    # person cast as a character is renamed to a label, speakers with them.
    people = _likeness.merge(
        getattr(master, "real_people", None) if master is not None else None,
        _likeness.normalize_people(body.get("real_people"), has_reference=bool(refs)),
    )
    if _likeness.audit_enabled():
        phrases = _likeness.candidates(_likeness.script_texts(body), people)
        if phrases:
            try:
                found = _likeness.audit(
                    " ".join(_likeness.script_texts(body)), phrases, has_reference=bool(refs)
                )
            except Exception as err:
                warnings.append(
                    f"screenwriter: likeness audit unavailable ({type(err).__name__}) — "
                    "the brains' own real_people lists were enforced; review names by eye"
                )
                found = []
            if found:
                people = _likeness.merge(people, found)
    if people:
        hits = _likeness.enforce_script_body(body, people)
        warnings.append(
            _likeness.note(
                hits,
                people,
                owner="screenwriter",
                where="reference image" if refs else "idea",
            )
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
    # Blueprint 32: the brain's identity card, when it wrote one; the
    # deterministic derivation fills the rest at lock under Locked
    from diony_director import cast as _cast

    for char, raw_char in zip(characters, body["characters"]):
        card = _cast.parse_brain_identity(raw_char.get("identity"), char)
        if card is not None:
            char.identity = card

    lines: list[ScriptLine] = []
    scenes: list[Scene] = []
    counter = 0

    def _add(
        kind: str,
        text: str,
        scene_id: str,
        speaker: str = "",
        shot: str = "",
        *,
        off_screen: bool = False,
    ) -> str:
        nonlocal counter
        counter += 1
        line = ScriptLine(
            line_id=f"ln-{counter}",
            kind=kind,
            text=text,
            speaker=speaker,
            scene_id=scene_id,
            shot=shot,
            off_screen=off_screen,
        )
        lines.append(line)
        return line.line_id

    for index, scene_body in enumerate(body["scenes"], start=1):
        scene_id = f"sc-{index}"
        line_ids = [_add("heading", str(scene_body["heading"]), scene_id)]
        for raw in scene_body["lines"]:
            kind = str(raw["kind"])
            # Blueprint 32: a screenplay cue tag on the speaker — "MARA (O.S.)"
            # — or the brain's off_screen flag marks the line as heard, not
            # seen; "(V.O.)" on a dialogue line is a voice-over line.
            speaker, tag = _cast.parse_speaker(str(raw.get("speaker", "")))
            if kind == "dialogue" and tag == "vo":
                kind = "vo"
            off_screen = kind == "dialogue" and (tag == "os" or bool(raw.get("off_screen")))
            shot = str(raw.get("shot", "") or "").strip()
            if kind == "dialogue" and speaker:
                line_ids.append(_add("cue", speaker, scene_id, speaker))
            line_ids.append(
                _add(kind, str(raw["text"]), scene_id, speaker, shot, off_screen=off_screen)
            )
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
    look = master.look if master is not None else None
    if look is not None and look.palette:
        # the film's one palette, not a per-setting guess
        grade = look.palette + (f", {grade}" if grade else "")
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
            grade_language=(
                f"{look.palette}, headlights and horizon glow"
                if look is not None and look.palette
                else "headlights and horizon glow"
            ),
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
        (
            "LEAD stops moving. A decision lands.",
            "low, deciding",
            "We finish this tonight. Then we are gone.",
            "SECOND holds the look, then nods once.",
            "Medium close-up, slow push-in as the stillness sets, face half in shadow",
            "Reverse single at eye level, holding on the nod, background falling soft",
        ),
        (
            "SECOND spreads the map across the table.",
            "quick, certain",
            "Two ways in. Only one way back out.",
            "LEAD traces the route without touching it.",
            "Overhead top-shot of the map unrolling, hands entering from both sides",
            "Tight insert on a finger hovering over the route, lamplight raking the paper",
        ),
        (
            "Headlights sweep the window and pass.",
            "barely above the hum",
            "They are already looking for us.",
            "Neither of them moves until the light dies.",
            "Wide from the back wall, the beam wiping across both figures",
            "Two-shot in profile, frozen, the last of the light sliding off their faces",
        ),
        (
            "LEAD counts what is left. It is not much.",
            "flat, honest",
            "After this, we start clean. All of it.",
            "SECOND laughs once, without any joy in it.",
            "Close on hands counting worn bills, shallow focus, tabletop grain sharp",
            "Medium single, a laugh that dies early, eyes elsewhere",
        ),
        (
            "A phone buzzes face-down. Nobody reaches for it.",
            "measured",
            "Whoever that is, they can wait until morning.",
            "The buzzing gives up. The room settles.",
            "Insert on the phone vibrating against wood, both figures blurred beyond",
            "Slow drift back from the table as the sound stops",
        ),
        (
            "SECOND checks the clock over the pass.",
            "urgent, hushed",
            "Nineteen minutes. Then the shift changes.",
            "LEAD stands and rolls a shoulder loose.",
            "Low angle up at the clock, second hand crossing twelve",
            "Full-body single rising out of frame, chair scraping back",
        ),
        (
            "Rain starts against the glass, soft then certain.",
            "quiet",
            "Weather like this hides a multitude of sins.",
            "SECOND pulls a coat from the booth.",
            "Macro on the first drops hitting the window, neon smearing through",
            "Medium two-shot, one figure shrugging into a coat, the other watching the rain",
        ),
        (
            "LEAD sets the keys on the table between them.",
            "even, final",
            "Last chance to walk away from this one.",
            "SECOND slides the keys straight back.",
            "Top-down insert: keys landing between two pairs of hands",
            "Matching insert: the keys sliding back, fingertips releasing at frame edge",
        ),
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
            _add(
                "action",
                before.replace("LEAD", characters[0].name).replace("SECOND", speakers[-1].name),
                scene_id,
                shot=before_shot,
            ),
            _add("cue", speaker, scene_id, speaker),
            _add("parenthetical", direction, scene_id, speaker),
            _add("dialogue", line, scene_id, speaker),
            _add(
                "action",
                after.replace("LEAD", characters[0].name).replace("SECOND", speakers[-1].name),
                scene_id,
                shot=after_shot,
            ),
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
                "action",
                "Headlights carve the dark. The town shrinks behind.",
                close_id,
                shot="Long lens from the roadside as the car passes; taillights pull away "
                "into black, town glow dying on the horizon",
            ),
            _add(
                "vo",
                "Some towns you leave. Some you escape.",
                close_id,
                characters[0].name,
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


def _template_authored(
    idea: str,
    runtime_target_s: int,
    master: MasterPrompt | None,
    blocks: list[dict],
    narration: list[str],
) -> ScreenplayDraft:
    """Brain off, authored breakdown in the idea: the deterministic path
    realizes the user's own scenes — each block's Visual becomes the action
    line, its Narration ships verbatim as vo. Nothing is invented beyond
    framing; the canned getaway template never overwrites a written film."""
    light = (master.lighting if master is not None else {}) or {}
    grade = ", ".join(x for x in (light.get("quality"), light.get("shadows")) if x)
    look = master.look if master is not None else None
    if look is not None and look.palette:
        grade = look.palette + (f", {grade}" if grade else "")
    camera_shot = (
        f"The camera {master.camera[:1].lower()}{master.camera[1:].rstrip('.')}"
        if master is not None and master.camera
        else "Locked wide frame at chest height, one motivated light source"
    )
    narrator = CharacterProfile(
        char_id="chr-narrator",
        name="NARRATOR",
        role="voiceover",
        age_body_notes="voice only — never on camera",
        wardrobe={"default": "n/a (voice only)"},
        voice=_voice("chr-narrator", 0),
    )
    # blocks with no narration of their own get the global lines spread over
    # them in order, so a paste whose labels didn't parse per-scene still ships
    per_block = [list(b["narration"]) for b in blocks]
    if narration and not any(per_block):
        for index, line in enumerate(narration):
            per_block[min(index * len(blocks) // len(narration), len(blocks) - 1)].append(line)

    lines: list[ScriptLine] = []
    scenes: list[Scene] = []
    settings: list[SettingProfile] = []
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

    for number, block in enumerate(blocks, start=1):
        pool = f"{block['title']} {block['visual']}".lower()
        kind, tone = "EXT", "quiet ambient air"
        for keyword, mapped in _SETTING_KEYWORDS.items():
            if keyword in pool:
                kind, _, tone = mapped
                break
        time_of_day = "NIGHT" if "night" in pool or "dark" in pool else "DAY"
        title = block["title"] or f"Scene {number}"
        setting = SettingProfile(
            setting_id=f"set-a{number}",
            name=title,
            room_tone_id=f"set-a{number}/tone",
            architecture_notes=tone,
            time_weather=f"{time_of_day.title()}, {light.get('source') or 'available light'}",
            grade_language=grade or "restrained palette, motivated contrast",
            camera_kit="35-50mm, chest height, deliberate slow moves",
        )
        settings.append(setting)
        scene_id = f"sc-{number}"
        heading = f"{kind}. {title.upper()} - {time_of_day}"
        line_ids = [_add("heading", heading, scene_id)]
        if block["visual"]:
            line_ids.append(_add("action", block["visual"], scene_id, shot=camera_shot))
        for spoken in per_block[number - 1]:
            line_ids.append(_add("vo", spoken, scene_id, narrator.name))
        line_ids.append(_add("sound", f"Room tone: {tone}.", scene_id))
        scenes.append(
            Scene(
                scene_id=scene_id,
                heading=heading,
                setting_ref=setting.setting_id,
                cast=[narrator.char_id],
                story_time=f"Scene {number}",
                line_ids=line_ids,
                emotional_job=title,
            )
        )

    if master is not None and master.ending and scenes:
        last = scenes[-1]
        last.line_ids.append(
            _add(
                "action",
                f"{master.ending[:1].upper()}{master.ending[1:].rstrip('.')}.",
                last.scene_id,
                shot=camera_shot,
            )
        )

    script = Script(
        id=f"scr-{uuid.uuid4().hex[:8]}",
        logline=_snip(idea, 160),
        runtime_target_s=runtime_target_s,
        beats=[
            {"beat": b["title"] or f"Scene {i}", "story_time": f"Scene {i}"}
            for i, b in enumerate(blocks, start=1)
        ],
        scenes=scenes,
        lines=lines,
    )
    return ScreenplayDraft(
        script=script,
        characters=[narrator],
        settings=settings,
        warnings=[
            "screenwriter: deterministic template (Director brain not configured) — "
            f"realized the authored breakdown: {len(blocks)} scenes, "
            f"{sum(len(p) for p in per_block)} narration lines verbatim"
        ],
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
    take_units: dict[str, int] | None = None,
) -> ScreenplayDraft:
    """`master` is the pre-split finalized artifact (Blueprint 27): when given,
    the screenplay realizes it — brain or template — and carries it forward.
    `take_units` (Blueprint 33) is the route's take length, {"picture": s,
    "spoken": s}, so the brain writes beats that fill long takes."""
    if brain_url():
        last_error: Exception | None = None
        for _attempt in range(BRAIN_ATTEMPTS):
            try:
                return _from_brain(idea, runtime_target_s, reference_images, master, take_units)
            except Exception as err:
                last_error = err
        raise BrainUnavailable(
            f"{brain_model()} could not deliver after {BRAIN_ATTEMPTS} attempts: {last_error}"
        )
    authored = authored_breakdown(idea)
    if authored["scenes"]:
        return _template_authored(
            idea, runtime_target_s, master, authored["scenes"], authored["narration"]
        )
    return _template(idea, runtime_target_s, cast_size, master)
