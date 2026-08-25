"""Idea-to-Studio brain.

Day 2: local, deterministic, cinematic. Week 2+ this is DeepSeek-V4-Flash-0731
on the cluster (Blueprint 10). Never Dashscope.
"""

from __future__ import annotations

import hashlib
import math
import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

from diony_contracts.enums import (
    AssetKind,
    CameraMove,
    ContinuityMode,
    CustomerKind,
    Engine,
    FinishIntent,
    GateName,
    Grain,
    LoudnessTarget,
    ProductionKind,
    ProjectStatus,
    ReferenceLock,
    VoiceProfileSource,
)
from diony_contracts.models import (
    Asset,
    Bible,
    Brief,
    Character,
    ClusterSite,
    Gate,
    MixLock,
    Note,
    Project,
    Shot,
    Timeline,
)
from diony_contracts.voice import (
    DialogueTurn,
    EmotionPerformance,
    ProsodySignature,
    VoiceProfile,
)
from diony_contracts.stitch import stitch_events
from diony_contracts.wan_prompt import (
    WAN22_PROMPT_STRATEGY_VERSION,
    compile_wan_prompt_plan,
    render_negative_prompt,
    render_wan_prompt,
    validate_wan_prompt_plan,
)

@dataclass(frozen=True)
class Playbook:
    label: str
    hired_as: str
    grammar: str
    grain: Grain
    loudness: LoudnessTarget
    lead_name: str
    lead_wardrobe: str
    world_prefix: str


@dataclass(frozen=True)
class ScriptScene:
    number: int
    title: str
    visual: str
    narration: str
    weight: int


@dataclass(frozen=True)
class ScriptDialogue:
    speaker: str
    text: str


PLAYBOOKS: dict[CustomerKind, Playbook] = {
    CustomerKind.FEATURE: Playbook(
        "Feature / short",
        "the director",
        "35mm spherical, motivated practicals, coverage that can cut a scene, held masters, restrained camera until earned.",
        Grain.GRAIN_35MM,
        LoudnessTarget.BROADCAST,
        "Lead",
        "Wardrobe locked to one look for the reel",
        "A theatrical short built from the idea",
    ),
    CustomerKind.SERIES: Playbook(
        "Series / episode",
        "the showrunner",
        "Television coverage: masters, overs, inserts. Character continuity across shots. Tungsten interiors unless the bible says otherwise.",
        Grain.FINE,
        LoudnessTarget.BROADCAST,
        "Series regular",
        "Show wardrobe, one silhouette",
        "An episode cold open built from the idea",
    ),
    CustomerKind.COMMERCIAL: Playbook(
        "Brand / commercial",
        "the agency",
        "Hero product lock, three-second holds, clean negative space, legal-safe lighting, no identity drift on talent or pack.",
        Grain.NONE,
        LoudnessTarget.BROADCAST,
        "On-camera talent",
        "Brand-safe wardrobe, no logos except the product",
        "A commercial built from the idea",
    ),
    CustomerKind.MUSIC_VIDEO: Playbook(
        "Music video",
        "the artist",
        "Rhythm in the cut, stylized light, lipsync on S2V for singing shots, wardrobe as costume, camera that can dance.",
        Grain.FINE,
        LoudnessTarget.STREAMING,
        "Performer",
        "Costume locked to the still",
        "A picture-to-record piece built from the idea",
    ),
    CustomerKind.EDUCATION: Playbook(
        "Education / explainer",
        "the instructor",
        "Locked-off clarity, inserts of the object of study, VO-first, no visual noise, hold long enough to understand.",
        Grain.NONE,
        LoudnessTarget.STREAMING,
        "Presenter",
        "Simple, readable wardrobe",
        "A lesson built from the idea",
    ),
    CustomerKind.SOCIAL: Playbook(
        "Social / short hero",
        "the creator",
        "1080p 16:9 hero (crop later). Punchy duration, direct address allowed, still Rec.709 legal, still cinematic grammar — not a vertical novelty reel.",
        Grain.FINE,
        LoudnessTarget.STREAMING,
        "Creator",
        "One recognizable look",
        "A short hero built from the idea",
    ),
}

CUSTOMER_CATALOG = [
    {
        "id": kind.value,
        "label": book.label,
        "hired_as": book.hired_as,
        "promise": book.grammar.split(".")[0] + ".",
    }
    for kind, book in PLAYBOOKS.items()
]

# Idea-floor length goals. Not a stopwatch. >15 min is first reel (Blueprint 12).
TARGET_ALLOTMENTS: list[dict] = [
    {"id": "30s", "label": "30s", "minutes": 0.5},
    {"id": "1m", "label": "1m", "minutes": 1.0},
    {"id": "3m", "label": "3m", "minutes": 3.0},
    {"id": "5m", "label": "5m", "minutes": 5.0},
    {"id": "10m", "label": "10m", "minutes": 10.0},
    {"id": "15m", "label": "15m", "minutes": 15.0},
    {"id": "20m", "label": "20m", "minutes": 20.0},
    {"id": "30m", "label": "30m", "minutes": 30.0},
    {"id": "40m", "label": "40m", "minutes": 40.0},
    {"id": "1hr", "label": "1hr", "minutes": 60.0},
]


def allotment_label(minutes: float) -> str:
    for item in TARGET_ALLOTMENTS:
        if abs(float(item["minutes"]) - float(minutes)) < 0.05:
            return str(item["label"])
    if minutes < 1:
        return f"{int(round(minutes * 60))}s"
    if minutes >= 59.5:
        return "1hr"
    if abs(minutes - round(minutes)) < 0.05:
        return f"{int(round(minutes))}m"
    return f"{minutes:g}m"


def _seed(text: str) -> int:
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    return int(digest[:8], 16) % 90_000 + 10_000


def _title(idea: str, hint: str) -> str:
    if hint.strip():
        return hint.strip()[:80]
    words = re.findall(r"[A-Za-z0-9']+", idea)
    core = " ".join(words[:6]) or "Untitled"
    return core[:1].upper() + core[1:]


# Wan2.2 A14B's native quality lane is a bounded 81-frame (4n+1, ~3.4s) 720p
# take (Blueprint 07, 10, 23). The timeline books exactly what the camera can
# deliver — a booked frame may be trimmed at conform, never stretched. Adjacent
# shots share an 8-frame temporal blend, so every chained shot contributes
# net lane frames of TRT. >15m stays first reel (Blueprint 12).
TIMELINE_FPS = 23.976
WAN_LANE_FRAMES = 81
STITCH_OVERLAP_FRAMES = 8
WAN_LANE_NET_FRAMES = WAN_LANE_FRAMES - STITCH_OVERLAP_FRAMES
WAN_MIN_SHOT_FRAMES = 17
WAN_QUALITY_SECONDS = WAN_LANE_FRAMES / TIMELINE_FPS
MAX_QUALITY_SHOTS = 120
FIRST_REEL_SHOTS = 12


def _lane_durations(target_frames: int) -> list[int]:
    """Split a TRT goal into lane-true bookings the camera can actually deliver.

    Every booking is within [WAN_MIN_SHOT_FRAMES, WAN_LANE_FRAMES]; adjacent
    shots share STITCH_OVERLAP_FRAMES in the timeline, so the stitched TRT of
    the returned bookings equals target_frames exactly.
    """
    target = max(WAN_MIN_SHOT_FRAMES, int(target_frames))
    if target <= WAN_LANE_FRAMES:
        return [target]
    n = 1 + -(-(target - WAN_LANE_FRAMES) // WAN_LANE_NET_FRAMES)
    durations = [WAN_LANE_FRAMES] * (n - 1)
    last = target - WAN_LANE_NET_FRAMES * (n - 1)
    if last < WAN_MIN_SHOT_FRAMES:
        durations[-1] -= WAN_MIN_SHOT_FRAMES - last
        last = WAN_MIN_SHOT_FRAMES
    durations.append(last)
    return durations


def _program_durations(minutes: float) -> list[int]:
    """Lane-true bookings whose stitched TRT lands on the length goal."""
    if minutes > 15:
        # First reel only (Blueprint 12): a bounded honest opening pass.
        return [WAN_LANE_FRAMES] * FIRST_REEL_SHOTS
    durations = _lane_durations(max(WAN_MIN_SHOT_FRAMES, int(round(minutes * 60.0 * TIMELINE_FPS))))
    if len(durations) > MAX_QUALITY_SHOTS:
        durations = [WAN_LANE_FRAMES] * MAX_QUALITY_SHOTS
    while (
        len(durations) < 4
        and max(durations) >= 2 * WAN_MIN_SHOT_FRAMES + STITCH_OVERLAP_FRAMES
    ):
        # Coverage floor: split the longest booking into two chained shots.
        # The split adds one shared handle, so stitched TRT is preserved.
        i = durations.index(max(durations))
        widest = durations.pop(i)
        first = (widest + STITCH_OVERLAP_FRAMES) // 2
        durations[i:i] = [first, widest + STITCH_OVERLAP_FRAMES - first]
    return durations


def _coverage_recipes(kind: CustomerKind, durations: list[int]) -> list[dict]:
    """Cycle cinematic patterns. Every take books what the Wan lane delivers."""
    pool = _recipes(kind)
    out: list[dict] = []
    for i, frames in enumerate(durations):
        rec = dict(pool[i % len(pool)])
        cycle = i // len(pool)
        if cycle:
            rec["slug"] = f"{rec['slug']} · {cycle + 1}"
        rec["duration_frames"] = int(frames)
        rec["seconds"] = frames / TIMELINE_FPS
        out.append(rec)
    return out


def _sample_recipes(kind: CustomerKind, seconds: int) -> list[dict]:
    """A Sample is an exact 5–10s cut made from bounded lane-true Wan takes."""
    durations = _lane_durations(int(round(seconds * TIMELINE_FPS)))
    pool = _recipes(kind)
    recipes: list[dict] = []
    for index, frames in enumerate(durations):
        rec = dict(pool[min(index, 1)])
        rec["hero"] = False
        if index == 0:
            rec["slug"] = "SAMPLE — DEFINING IMAGE"
        elif index == len(durations) - 1:
            rec["slug"] = "SAMPLE — PAYOFF"
        else:
            rec["slug"] = f"SAMPLE — CONTINUITY {index}"
        rec["duration_frames"] = int(frames)
        rec["seconds"] = frames / TIMELINE_FPS
        recipes.append(rec)
    return recipes


def _explicit_sample_camera(idea: str) -> dict:
    """Honor camera facts the operator typed instead of replacing them with coverage defaults."""
    overrides: dict = {}
    focal = re.search(r"(?<![\d.])(\d{1,3}(?:\.\d+)?)\s*mm\b", idea, re.I)
    if focal:
        value = float(focal.group(1))
        if 8 <= value <= 300:
            overrides["focal_mm"] = value
    stop = re.search(r"\bT\s*/?\s*(\d+(?:\.\d+)?)\b", idea, re.I)
    if stop:
        value = float(stop.group(1))
        if 0.7 <= value <= 32:
            overrides["t_stop"] = value
    lowered = idea.casefold()
    moves = (
        (CameraMove.DOLLY_IN, r"\b(?:dolly|push)[\s-]*in\b"),
        (CameraMove.DOLLY_OUT, r"\b(?:dolly|pull)[\s-]*(?:out|back)\b"),
        (CameraMove.STEADICAM, r"\bsteadicam\b"),
        (CameraMove.HANDHELD, r"\bhandheld\b"),
        (CameraMove.TRACK, r"\btrack(?:ing)?\b"),
        (CameraMove.CRANE, r"\bcrane\b"),
        (CameraMove.PAN, r"\bpan(?:ning)?\b"),
        (CameraMove.TILT, r"\btilt(?:ing)?\b"),
        (CameraMove.ZOOM, r"\bzoom(?:ing)?\b"),
        (CameraMove.STATIC, r"\b(?:static|locked[\s-]*off)\b|\bno camera movement\b"),
    )
    for move, pattern in moves:
        if re.search(pattern, lowered):
            overrides["camera_move"] = move
            break
    lighting_cues = (
        "cool key",
        "warm key",
        "soft key",
        "hard key",
        "cool rim",
        "warm rim",
        "soft fill",
        "hard fill",
    )
    found = sorted(
        (lowered.index(cue), cue) for cue in lighting_cues if cue in lowered
    )
    if found:
        overrides["lighting_ratio"] = ", ".join(cue for _, cue in found)
    return overrides


def _recipes(kind: CustomerKind) -> list[dict]:
    """Ordered coverage patterns. Extra slots loop from the middle."""
    base: list[dict] = [
        {
            "slug": "ESTABLISHING — THE WORLD OF THE IDEA",
            "scene": "1",
            "seconds": 5,
            "focal_mm": 24,
            "t_stop": 4.0,
            "lighting_ratio": "open sky or wide practical, even",
            "camera_move": CameraMove.STATIC,
            "engine": Engine.T2V_A14B,
            "audio": None,
        },
        {
            "slug": "CHARACTER LOCK — LEAD IN FRAME",
            "scene": "1",
            "seconds": 5,
            "focal_mm": 40,
            "t_stop": 2.8,
            "lighting_ratio": "4:1 key/fill, motivated",
            "camera_move": CameraMove.DOLLY_IN,
            "engine": Engine.I2V_A14B,
            "audio": None,
            "hero": True,
        },
        {
            "slug": "INSERT — THE OBJECT THAT MATTERS",
            "scene": "1",
            "seconds": 3,
            "focal_mm": 90,
            "t_stop": 4.0,
            "lighting_ratio": "hard top, texture held",
            "camera_move": CameraMove.STATIC,
            "engine": Engine.T2V_A14B,
            "audio": None,
        },
        {
            "slug": "COVERAGE — THE TURN",
            "scene": "2",
            "seconds": 6,
            "focal_mm": 35,
            "t_stop": 2.8,
            "lighting_ratio": "3:1, edge light",
            "camera_move": CameraMove.STEADICAM,
            "engine": Engine.I2V_A14B,
            "audio": None,
        },
        {
            "slug": "DIALOGUE / VO — STORY BEAT",
            "scene": "2",
            "seconds": 6,
            "focal_mm": 50,
            "t_stop": 2.2,
            "lighting_ratio": "beauty key, soft fill",
            "camera_move": CameraMove.STATIC,
            "engine": Engine.S2V_14B,
            "audio": True,
            "hero": True,
        },
        {
            "slug": "BUTTON — LAST IMAGE",
            "scene": "3",
            "seconds": 4,
            "focal_mm": 28,
            "t_stop": 3.2,
            "lighting_ratio": "silhouette vs practical",
            "camera_move": CameraMove.DOLLY_OUT,
            "engine": Engine.T2V_A14B,
            "audio": None,
        },
    ]
    if kind == CustomerKind.COMMERCIAL:
        base[2]["slug"] = "HERO — PRODUCT LOCK"
        base[2]["engine"] = Engine.I2V_A14B
        base[2]["hero"] = True
        base[4]["slug"] = "TALENT TO CAMERA — CLAIM"
    if kind == CustomerKind.MUSIC_VIDEO:
        base[4]["slug"] = "PERFORMANCE — LIPSYNC CHORUS"
        base[3]["camera_move"] = CameraMove.HANDHELD
    if kind == CustomerKind.EDUCATION:
        base[3]["slug"] = "DEMO — THE STEP YOU MUST SEE"
        base[3]["camera_move"] = CameraMove.STATIC
        base[4]["slug"] = "PRESENTER — VO THE RULE"
    extras = [
        {
            "slug": "BRIDGE — GEOGRAPHY",
            "scene": "2",
            "seconds": 4,
            "focal_mm": 32,
            "t_stop": 4.0,
            "lighting_ratio": "available plus bounce",
            "camera_move": CameraMove.TRACK,
            "engine": Engine.T2V_A14B,
            "audio": None,
        },
        {
            "slug": "REACTION — COST OF THE BEAT",
            "scene": "2",
            "seconds": 4,
            "focal_mm": 85,
            "t_stop": 2.0,
            "lighting_ratio": "2:1, eyes lit",
            "camera_move": CameraMove.STATIC,
            "engine": Engine.I2V_A14B,
            "audio": None,
        },
        {
            "slug": "WIDE — AIR AFTER THE LINE",
            "scene": "3",
            "seconds": 5,
            "focal_mm": 20,
            "t_stop": 5.6,
            "lighting_ratio": "environmental",
            "camera_move": CameraMove.PAN,
            "engine": Engine.T2V_A14B,
            "audio": None,
        },
        {
            "slug": "DETAIL — HANDS / TOOL / TEXTURE",
            "scene": "1",
            "seconds": 3,
            "focal_mm": 100,
            "t_stop": 2.8,
            "lighting_ratio": "raking side",
            "camera_move": CameraMove.STATIC,
            "engine": Engine.T2V_A14B,
            "audio": None,
        },
        {
            "slug": "ENTRY — WALK INTO THE WORLD",
            "scene": "1",
            "seconds": 5,
            "focal_mm": 35,
            "t_stop": 2.8,
            "lighting_ratio": "inside vs outside mismatch",
            "camera_move": CameraMove.STEADICAM,
            "engine": Engine.I2V_A14B,
            "audio": None,
        },
        {
            "slug": "HOLD — LET THE FRAME BREATHE",
            "scene": "3",
            "seconds": 5,
            "focal_mm": 40,
            "t_stop": 4.0,
            "lighting_ratio": "unchanged from prior",
            "camera_move": CameraMove.STATIC,
            "engine": Engine.I2V_A14B,
            "audio": None,
        },
    ]
    return base + extras


def _idea_body(brief: Brief, assets: list[Asset]) -> str:
    text = brief.idea.strip()
    extras: list[str] = []
    for asset in assets:
        if asset.id not in brief.attachment_ids:
            continue
        if asset.extracted_text.strip():
            extras.append(asset.extracted_text.strip()[:800])
        elif asset.kind == AssetKind.IMAGE:
            extras.append(f"still: {asset.filename}")
        elif asset.kind == AssetKind.VIDEO:
            extras.append(f"motion ref: {asset.filename}")
        elif asset.kind == AssetKind.DOCUMENT:
            extras.append(f"document: {asset.filename}")
    if len(text) < 8:
        text = " ".join(extras) or "Untitled from picture and file references."
    elif extras:
        text = f"{text} References: {'; '.join(extras[:4])}"
    return text


def _install_wan_prompt_plan(
    shot: Shot,
    *,
    idea: str,
    bible: Bible,
    wardrobe: str,
    shot_index: int,
    sample_seconds: int | None,
) -> None:
    """Compile and atomically install the dispatchable five-layer prompt."""
    visual_idea = _visual_only_text(idea) or "A Bible-locked performance in the authored setting."
    plan = compile_wan_prompt_plan(
        idea=visual_idea,
        world=_visual_only_text(bible.world),
        wardrobe=wardrobe,
        shot_index=shot_index,
        focal_mm=shot.focal_mm,
        t_stop=shot.t_stop,
        lighting_ratio=shot.lighting_ratio,
        camera_move=shot.camera_move,
        reference_lock=shot.reference_lock,
        continuity_mode=shot.continuity_mode,
        generation_method=shot.engine.value,
        audio_strategy=(
            "native_s2v"
            if shot.engine == Engine.S2V_14B
            else "post_mix"
            if shot.audio_line
            else "silent_plate"
        ),
        sample_seconds=sample_seconds,
    )
    shot.prompt_plan = plan
    shot.prompt = render_wan_prompt(plan)
    shot.negative_prompt = render_negative_prompt(plan)


def _asks_for_sound(idea: str) -> bool:
    """Do not invent dialogue merely because the coverage template has a VO slot."""
    lowered = idea.casefold()
    cues = (
        "dialogue",
        "voice-over",
        "voiceover",
        "narration",
        "narrator",
        "speaks",
        "says ",
        "singing",
        "song",
        "lipsync",
        "lip sync",
        "music",
        "score",
        "sound",
        "audio",
        "welcomes",
        "explains",
        "introduces",
        "motto",
    )
    return any(cue in lowered for cue in cues)


def _spoken_direction_script(idea: str) -> str:
    """Turn explicit spoken direction into concise VO without reading stage directions."""
    lines: list[str] = []
    welcome = re.search(
        r"\bwelcomes?\s+(?:people|viewers?|the audience|us)\s+to\s+([^.;,!?'\"]+)",
        idea,
        re.IGNORECASE,
    )
    if welcome:
        destination = re.sub(r"\s+", " ", welcome.group(1)).strip()
        if destination:
            lines.append(f"Welcome to {destination}.")
    motto = re.search(
        r"\bmotto\s*[,;:]?\s*['\"]([^'\"]+)['\"]",
        idea,
        re.IGNORECASE,
    )
    if motto:
        text = re.sub(r"\s+", " ", motto.group(1)).strip()
        if text:
            lines.append(text.rstrip(".!?") + ".")
    return " ".join(dict.fromkeys(lines))


def _asks_for_synced_performance(idea: str) -> bool:
    """A recorded performance is S2V; narration and score remain post stems."""
    lowered = idea.casefold()
    cues = (
        "dialogue",
        "lip sync",
        "lipsync",
        "sings",
        "singing",
        "speaks to camera",
        "says to camera",
        "performs to camera",
        "recorded chorus",
        "recorded performance",
    )
    return any(cue in lowered for cue in cues)


_SCENE_HEADING = re.compile(
    r"^[ \t]*Scene[ \t]+(?P<number>\d+)[ \t]*"
    r"(?:[—–:-][ \t]*(?P<title>[^\n(]*?))?[ \t]*"
    r"(?:\((?P<start>\d+)[ \t]*[—–-][ \t]*(?P<end>\d+)[ \t]*s\+?\))?[ \t]*$",
    re.IGNORECASE | re.MULTILINE,
)


def _clean_narration(value: str) -> str:
    lines = [line.strip().strip('"“”') for line in value.splitlines()]
    return " ".join(line for line in lines if line).strip()


_SCREENPLAY_DIALOGUE = re.compile(
    r"^[ \t]*(?P<speaker>[A-Z][A-Za-z0-9.'\N{RIGHT SINGLE QUOTATION MARK}-]*"
    r"(?:[ \t]+[A-Z][A-Za-z0-9.'\N{RIGHT SINGLE QUOTATION MARK}-]*){0,3})"
    r"(?:[ \t]*\([^\n)]*\))?[ \t]*:[ \t]*(?P<text>[^\n]+?)[ \t]*$",
    re.MULTILINE,
)
_NON_CHARACTER_CUES = {
    "AUDIO",
    "ACTION",
    "CAMERA",
    "CUT TO",
    "DIALOGUE",
    "EXT",
    "FADE IN",
    "FADE OUT",
    "FINAL HOLD",
    "INT",
    "MUSIC",
    "NARRATION",
    "NARRATOR",
    "SCENE",
    "SCORE",
    "SFX",
    "SOUND",
    "TITLE",
    "VOICEOVER",
    "VOICE OVER",
}


def _visual_only_text(value: str) -> str:
    """Remove authored voice lines from every source used by the picture prompt."""
    cleaned = _SCREENPLAY_DIALOGUE.sub(" ", value)
    for _speaker, _text, start, end in reversed(_prose_quoted_speech(cleaned)):
        cleaned = cleaned[:start] + " " + cleaned[end:]
    return re.sub(r"\s+", " ", cleaned).strip()


_QUOTED_SPEECH = re.compile(r'["“](?P<text>[^"“”\n]+)["”]')
_SPEECH_VERB = re.compile(
    r"\b(?:asks?|calls?|exclaims?|replies|responds?|says?|speaks?|tells?|whispers?|yells?)\b",
    re.IGNORECASE,
)
_SPEAKER_TOKEN = re.compile(
    r"\b(?:[A-Z][A-Za-z0-9.'\N{RIGHT SINGLE QUOTATION MARK}-]*|"
    r"[Cc]haracter|[Hh]e|[Ss]he|[Tt]hey)\b",
)
_GENERIC_SPEAKERS = {"character", "he", "she", "they"}


def _prose_quoted_speech(idea: str) -> list[tuple[str | None, str, int, int]]:
    """Find authored quotes attached to prose speech verbs without inventing words."""
    found: list[tuple[str | None, str, int, int]] = []
    for quote in _QUOTED_SPEECH.finditer(idea):
        prefix = idea[max(0, quote.start() - 240) : quote.start()]
        boundary = max(prefix.rfind("\n"), prefix.rfind("."), prefix.rfind("!"), prefix.rfind("?"))
        clause = prefix[boundary + 1 :]
        verbs = list(_SPEECH_VERB.finditer(clause))
        if not verbs:
            continue
        verb = verbs[-1]
        if len(clause) - verb.end() > 140:
            continue
        tokens = list(_SPEAKER_TOKEN.finditer(clause[: verb.start()]))
        speaker = tokens[-1].group(0).strip() if tokens else None
        text = re.sub(r"\s+", " ", quote.group("text")).strip()
        if text:
            found.append((speaker, text, quote.start(), quote.end()))
    return found


def _script_dialogue(idea: str, *, default_speaker: str = "Character") -> list[ScriptDialogue]:
    """Preserve explicitly authored screenplay lines without inventing speech."""
    addressed: list[tuple[int, ScriptDialogue]] = []
    for match in _SCREENPLAY_DIALOGUE.finditer(idea):
        raw_speaker = re.sub(r"\s+", " ", match.group("speaker")).strip(" .-")
        cue = raw_speaker.upper()
        if cue in _NON_CHARACTER_CUES or cue.startswith("SCENE "):
            continue
        text = re.sub(r"\s+", " ", match.group("text")).strip()
        text = text.strip('"“”')
        if not text:
            continue
        speaker = raw_speaker.title() if raw_speaker.isupper() else raw_speaker
        addressed.append((match.start(), ScriptDialogue(speaker=speaker, text=text)))
    for raw_speaker, text, start, _end in _prose_quoted_speech(idea):
        speaker = str(raw_speaker or "").strip()
        if not speaker or speaker.casefold() in _GENERIC_SPEAKERS:
            speaker = default_speaker
        elif speaker.isupper():
            speaker = speaker.title()
        addressed.append((start, ScriptDialogue(speaker=speaker, text=text)))
    addressed.sort(key=lambda row: row[0])
    lines: list[ScriptDialogue] = []
    seen: set[tuple[str, str]] = set()
    for _start, line in addressed:
        key = (line.speaker.casefold(), line.text.casefold())
        if key in seen:
            continue
        seen.add(key)
        lines.append(line)
    return lines


def _fit_dialogue_to_shots(
    lines: list[ScriptDialogue],
    *,
    shot_count: int,
    seconds_per_shot: float,
) -> list[list[ScriptDialogue]]:
    """Split authored speech into natural-rate shot windows without rewriting it."""
    if not lines:
        return []
    max_words = max(1, int(seconds_per_shot * 2.0))
    fitted: list[ScriptDialogue] = []
    tokens: list[tuple[int, str, str]] = []
    preferred_boundaries: set[int] = set()
    for line_index, line in enumerate(lines):
        words = line.text.split()
        line_start = len(tokens)
        tokens.extend((line_index, line.speaker, word) for word in words)
        for start in range(0, len(words), max_words):
            fitted.append(
                ScriptDialogue(
                    speaker=line.speaker,
                    text=" ".join(words[start : start + max_words]),
                )
            )
            preferred_boundaries.add(
                min(len(tokens), line_start + start + max_words)
            )
    preferred_boundaries.discard(len(tokens))
    if len(fitted) <= shot_count:
        return [[line] for line in fitted]

    minimum_windows = math.ceil(len(tokens) / max_words)
    if minimum_windows > shot_count:
        raise ValueError(
            "Authored dialogue needs "
            f"{minimum_windows} natural-rate shot windows but this rundown has {shot_count}. "
            "Increase the program duration or shorten the authored dialogue before Production."
        )

    # Short authored turns should not each consume a whole 5s take when their
    # combined natural speaking time fits the rundown. Prefer existing turn and
    # ten-word boundaries, then split the minimum number of turns necessary.
    cache: dict[tuple[int, int], tuple[int, tuple[int, ...]] | None] = {}

    def partition(start: int, windows_left: int) -> tuple[int, tuple[int, ...]] | None:
        key = (start, windows_left)
        if key in cache:
            return cache[key]
        remaining = len(tokens) - start
        if remaining < windows_left or remaining > windows_left * max_words:
            cache[key] = None
            return None
        if windows_left == 1:
            result = (0, (len(tokens),))
            cache[key] = result
            return result

        best: tuple[int, tuple[int, ...]] | None = None
        minimum_size = max(1, remaining - (windows_left - 1) * max_words)
        maximum_size = min(max_words, remaining - (windows_left - 1))
        for size in range(minimum_size, maximum_size + 1):
            end = start + size
            tail = partition(end, windows_left - 1)
            if tail is None:
                continue
            cost = (0 if end in preferred_boundaries else 1) + tail[0]
            candidate = (cost, (end, *tail[1]))
            if best is None or candidate[0] < best[0]:
                best = candidate
        cache[key] = best
        return best

    packed_partition = partition(0, shot_count)
    if packed_partition is None:  # Defensive: minimum_windows already proves capacity.
        raise ValueError("Authored dialogue could not be assigned to this rundown.")

    windows: list[list[ScriptDialogue]] = []
    start = 0
    for end in packed_partition[1]:
        fragments: list[tuple[int, str, list[str]]] = []
        for line_index, speaker, word in tokens[start:end]:
            if fragments and fragments[-1][0] == line_index:
                fragments[-1][2].append(word)
            else:
                fragments.append((line_index, speaker, [word]))
        windows.append(
            [
                ScriptDialogue(speaker=speaker, text=" ".join(words))
                for _, speaker, words in fragments
            ]
        )
        start = end
    return windows


def _character_id(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.casefold()).strip("-")
    return f"char-{slug or hashlib.sha256(name.encode('utf-8')).hexdigest()[:12]}"


def _stable_voice_embedding(*parts: str, dimensions: int = 192) -> tuple[float, ...]:
    values: list[float] = []
    counter = 0
    while len(values) < dimensions:
        block = hashlib.sha256(("\x1f".join(parts) + f":{counter}").encode("utf-8")).digest()
        values.extend((byte - 127.5) / 127.5 for byte in block)
        counter += 1
    vector = values[:dimensions]
    norm = math.sqrt(sum(value * value for value in vector)) or 1.0
    return tuple(round(value / norm, 8) for value in vector)


def _synthetic_voice_profile(project_id: str, character: Character) -> VoiceProfile:
    """Lock a deterministic project-local identity before picture generation."""
    descriptor = " ".join(
        (character.name, character.notes, character.wardrobe)
    ).strip()
    seed_blob = "\x1f".join((character.id, character.name, "voice-v1"))
    seed = (
        int(hashlib.sha256(seed_blob.encode("utf-8")).hexdigest()[:12], 16)
        % 2_000_000_000
    )
    return VoiceProfile(
        id=f"voice-{character.id}-v1",
        project_id=project_id,
        character_id=character.id,
        source=VoiceProfileSource.SYNTHETIC,
        speaker_embedding_model="diony-synthetic-speaker-space-v1",
        speaker_embedding=_stable_voice_embedding(character.id, descriptor),
        prosody=ProsodySignature(
            pitch_floor_hz=105,
            pitch_ceiling_hz=310,
            speaking_rate_syllables_per_second=4.0,
            breath_interval_seconds=5.0,
            vocal_fry=0.08,
            pause_bias=0.5,
        ),
        emotion_map=(
            EmotionPerformance(emotion="neutral", timbre="locked natural character voice"),
            EmotionPerformance(
                emotion="joy",
                intensity_curve=(0.25, 0.55, 0.4),
                rate_multiplier=1.06,
                pitch_shift_semitones=0.8,
                timbre="brighter resonance without identity change",
            ),
            EmotionPerformance(
                emotion="grief",
                intensity_curve=(0.2, 0.65, 0.35),
                rate_multiplier=0.9,
                pitch_shift_semitones=-0.5,
                timbre="restrained breath and stable chest resonance",
            ),
            EmotionPerformance(
                emotion="tension",
                intensity_curve=(0.35, 0.8, 0.55),
                rate_multiplier=1.03,
                pitch_shift_semitones=0.3,
                timbre="compressed breath with profile-locked texture",
            ),
        ),
        age_descriptor="Bible locked",
        gender_descriptor="Bible locked",
        accent_descriptor="project neutral until directed",
        texture_descriptor="natural studio character voice",
        seed=seed,
        style_lock=(
            f"identity:{character.id}:synthetic-v1:"
            f"{hashlib.sha256(descriptor.encode('utf-8')).hexdigest()[:16]}"
        ),
        locked=True,
        locked_at=datetime.now(timezone.utc),
    )


def _script_scenes(idea: str) -> list[ScriptScene]:
    """Parse an explicitly authored Scene plan without asking a model to rewrite it."""
    matches = list(_SCENE_HEADING.finditer(idea))
    scenes: list[ScriptScene] = []
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(idea)
        body = idea[match.end() : end].strip()
        narration_match = re.search(r"\bNarration\s*:\s*", body, re.IGNORECASE)
        if narration_match:
            visual = body[: narration_match.start()].strip()
            narration = _clean_narration(body[narration_match.end() :])
        else:
            visual = body
            narration = ""
        start_s = int(match.group("start") or 0)
        end_s = int(match.group("end") or 0)
        weight = max(1, end_s - start_s) if end_s > start_s else 1
        scenes.append(
            ScriptScene(
                number=int(match.group("number")),
                title=(match.group("title") or f"Scene {match.group('number')}").strip(),
                visual=re.sub(r"\s+", " ", visual).strip(),
                narration=narration,
                weight=weight,
            )
        )
    return scenes


def _scene_schedule(scenes: list[ScriptScene], count: int) -> list[ScriptScene | None]:
    if not scenes or count < 1:
        return [None] * max(0, count)
    if count <= len(scenes):
        return list(scenes[:count])
    allocation = [1] * len(scenes)
    remaining = count - len(scenes)
    total_weight = sum(scene.weight for scene in scenes) or len(scenes)
    raw = [remaining * scene.weight / total_weight for scene in scenes]
    for index, value in enumerate(raw):
        allocation[index] += int(value)
    leftover = count - sum(allocation)
    order = sorted(
        range(len(scenes)),
        key=lambda index: (raw[index] - int(raw[index]), scenes[index].weight, -index),
        reverse=True,
    )
    for index in order[:leftover]:
        allocation[index] += 1
    scheduled: list[ScriptScene | None] = []
    for scene, slots in zip(scenes, allocation):
        scheduled.extend([scene] * slots)
    return scheduled[:count]


def _image_defines_subject(idea: str) -> bool:
    """True when an attached still is the subject, not merely a loose mood reference."""
    lowered = idea.casefold()
    cues = (
        "this character",
        "attached character",
        "attached person",
        "attached figure",
        "attached subject",
        "attached product",
        "attached object",
        "attached logo",
        "character from image",
        "character from the image",
        "subject from image",
        "subject from the image",
        "person from image",
        "person from the image",
        "animate this image",
        "animate the image",
        "animate this picture",
        "animate the picture",
        "illustrate this image",
        "illustrate the image",
        "exact illustration provided",
        "exact image provided",
        "exact picture provided",
    )
    if any(cue in lowered for cue in cues):
        return True
    if re.search(r"\b(?:last|first|second|third|fourth|fifth)\s+attached\s+"
                 r"(?:image|picture|photo|still|illustration)\b", lowered):
        return bool(re.search(r"\b(character|figure|person|subject|product|object|logo)\b", lowered))
    subject = re.search(
        r"\b(characters?|persons?|people|subjects?|figures?|creatures?|products?|objects?|logos?)\b",
        lowered,
    )
    reference = re.search(r"\b(attached|reference|image|picture|photo|still|same|this)\b", lowered)
    direction = re.search(r"\b(use|using|animate|illustrate|preserve|keep|match|same)\b", lowered)
    return bool(subject and reference and direction)


def _subject_image_ids(idea: str, image_ids: list[str]) -> list[str]:
    """Resolve an explicitly ordinal subject image before generic attachments."""
    if not image_ids:
        return []
    match = re.search(
        r"\b(first|second|third|fourth|fifth|last)\s+attached\s+"
        r"(?:image|picture|photo|still|illustration)\b",
        idea.casefold(),
    )
    if match:
        ordinal = match.group(1)
        if ordinal == "last":
            return [image_ids[-1]]
        index = {"first": 0, "second": 1, "third": 2, "fourth": 3, "fifth": 4}[ordinal]
        if index < len(image_ids):
            return [image_ids[index]]
    return [image_ids[0]]


def _reference_lock(idea: str, *, image_is_subject: bool) -> ReferenceLock:
    if not image_is_subject:
        return ReferenceLock.NONE
    lowered = idea.casefold()
    if re.search(r"\b(same|preserve|keep|match)\b.{0,32}\b(background|world|setting|canvas|scene)\b", lowered):
        return ReferenceLock.CHARACTER_AND_BACKGROUND
    return ReferenceLock.CHARACTER


def _upgrade_binding_reference_contract(project: Project) -> None:
    """Promote persisted pre-detector projects to today's binding reference contract."""
    brief = project.brief
    if brief is None or not _image_defines_subject(brief.idea):
        return
    attached = set(brief.attachment_ids)
    image_ids = [
        asset.id
        for asset in project.assets
        if asset.kind == AssetKind.IMAGE and (not attached or asset.id in attached)
    ]
    if not image_ids:
        return
    subject_image_ids = _subject_image_ids(brief.idea, image_ids)
    lock = _reference_lock(brief.idea, image_is_subject=True)
    if lock == ReferenceLock.NONE:
        return
    char_id = project.bible.characters[0].id if project.bible.characters else "char-lead"
    if project.bible.characters:
        project.bible.characters[0].still_asset_ids = list(subject_image_ids)
    scene_schedule = _scene_schedule(_script_scenes(brief.idea), len(project.shots))
    narrated_scenes: set[int] = set()
    spoken_direction = _spoken_direction_script(brief.idea)
    spoken_direction_assigned = False
    for index, item in enumerate(project.shots):
        scene_beat = scene_schedule[index]
        item.engine = Engine.I2V_A14B
        item.character_ref_ids = [char_id]
        item.keyframe_asset_ids = list(subject_image_ids)
        item.reference_lock = lock
        item.continuity_mode = (
            ContinuityMode.REFERENCE if index == 0 else ContinuityMode.PREVIOUS_FRAME
        )
        item.overlap_frames = 8
        narration = None
        if scene_beat is not None:
            item.scene = str(scene_beat.number)
            item.slug = f"SCENE {scene_beat.number} — {scene_beat.title}"
            narration = (
                scene_beat.narration
                if scene_beat.narration and scene_beat.number not in narrated_scenes
                else None
            )
            narrated_scenes.add(scene_beat.number)
        if narration:
            item.audio_line = narration
        elif spoken_direction and not spoken_direction_assigned:
            item.audio_line = spoken_direction
            spoken_direction_assigned = True
        else:
            item.audio_line = None


def refresh_reference_shot(project: Project, shot: Shot) -> Shot:
    """Reapply the current binding reference contract before a retake.

    Projects remain recoverable across product upgrades. A retake of an older
    Project therefore receives today's strict prompt, negative conditioning,
    and same-world camera lock instead of replaying stale creative direction.
    """
    def _plan_inputs() -> tuple:
        return (
            shot.reference_lock,
            shot.engine,
            tuple(shot.keyframe_asset_ids),
            shot.continuity_mode,
            shot.overlap_frames,
            shot.focal_mm,
            shot.t_stop,
            shot.lighting_ratio,
            shot.camera_move,
        )

    before_contract = _plan_inputs()
    _upgrade_binding_reference_contract(project)
    sample_camera = (
        _explicit_sample_camera(project.brief.idea)
        if project.brief is not None
        and project.brief.production_kind == ProductionKind.SAMPLE
        else {}
    )
    if shot.reference_lock == ReferenceLock.NONE and not sample_camera:
        return shot
    try:
        index = next(i for i, item in enumerate(project.shots) if item.id == shot.id)
    except StopIteration:
        index = 0
    if shot.reference_lock == ReferenceLock.CHARACTER_AND_BACKGROUND:
        anchor = project.shots[0] if project.shots else shot
        shot.focal_mm = anchor.focal_mm
        shot.t_stop = anchor.t_stop
        shot.lighting_ratio = anchor.lighting_ratio
        shot.camera_move = CameraMove.STATIC
    shot.focal_mm = sample_camera.get("focal_mm", shot.focal_mm)
    shot.t_stop = sample_camera.get("t_stop", shot.t_stop)
    shot.lighting_ratio = sample_camera.get("lighting_ratio", shot.lighting_ratio)
    shot.camera_move = sample_camera.get("camera_move", shot.camera_move)
    idea = (
        _idea_body(project.brief, project.assets)
        if project.brief is not None
        else project.bible.logline
    )
    if project.brief is not None:
        scene_schedule = _scene_schedule(
            _script_scenes(project.brief.idea),
            len(project.shots),
        )
        scene_beat = scene_schedule[index] if index < len(scene_schedule) else None
        if scene_beat is not None and scene_beat.visual:
            idea = scene_beat.visual
    if (
        before_contract == _plan_inputs()
        and shot.prompt_plan is not None
        and shot.prompt_plan.strategy_version == WAN22_PROMPT_STRATEGY_VERSION
        and not validate_wan_prompt_plan(shot.prompt_plan)
        and shot.prompt == render_wan_prompt(shot.prompt_plan)
        and shot.negative_prompt == render_negative_prompt(shot.prompt_plan)
    ):
        # The strict contract is already applied and the plan is dispatch-valid.
        # Recompiling here would erase a QC repair or retake directive, replaying
        # the exact creative direction that just failed.
        return shot
    sample_seconds = project.brief.sample_seconds if project.brief is not None else None
    wardrobe = (
        project.bible.characters[0].wardrobe
        if project.bible.characters
        else "Bible-locked wardrobe"
    )
    _install_wan_prompt_plan(
        shot,
        idea=idea,
        bible=project.bible,
        wardrobe=wardrobe,
        shot_index=index,
        sample_seconds=sample_seconds,
    )
    return shot


def blank_studio(compute: ClusterSite | None = None) -> Project:
    """Empty Idea page after Studio Sign."""
    return Project(
        id="studio-blank",
        status=ProjectStatus.DRAFT,
        bible=Bible(
            title="Untitled",
            logline="",
            visual_grammar="",
            world="",
        ),
        fleet_mode="SPLIT",
        compute=compute or ClusterSite(),
    )


def expand_idea(
    brief: Brief,
    assets: list[Asset] | None = None,
    compute: ClusterSite | None = None,
) -> Project:
    """Cast a studio from a customer idea. 1080 finish-intent, grammar on every shot."""
    held = list(assets or [])
    idea = _idea_body(brief, held)
    book = PLAYBOOKS[brief.customer_kind]
    title = _title(idea, brief.title_hint)
    is_sample = brief.production_kind == ProductionKind.SAMPLE
    sample_seconds = brief.sample_seconds or 5
    seed = _seed(
        idea
        + brief.customer_kind.value
        + brief.production_kind.value
        + (str(sample_seconds) if is_sample else "")
    )
    run_id = f"{seed}-{uuid.uuid4().hex}"
    pid = f"studio-{run_id}"
    if is_sample:
        recipes = _sample_recipes(brief.customer_kind, sample_seconds)
    else:
        recipes = _coverage_recipes(brief.customer_kind, _program_durations(brief.target_minutes))
    n = len(recipes)
    image_ids = [a.id for a in held if a.kind == AssetKind.IMAGE]
    video_ids = [a.id for a in held if a.kind == AssetKind.VIDEO]
    audio_ids = [a.id for a in held if a.kind == AssetKind.AUDIO]
    dialogue_windows = _fit_dialogue_to_shots(
        _script_dialogue(brief.idea, default_speaker=book.lead_name),
        shot_count=n,
        seconds_per_shot=sum(float(recipe["seconds"]) for recipe in recipes) / len(recipes),
    )
    sound_requested = bool(dialogue_windows) or _asks_for_sound(idea)
    spoken_direction = _spoken_direction_script(brief.idea)
    spoken_direction_assigned = False
    synced_performance = bool(audio_ids) and _asks_for_synced_performance(idea)
    image_is_subject = bool(image_ids) and _image_defines_subject(brief.idea)
    reference_lock = _reference_lock(brief.idea, image_is_subject=image_is_subject)
    subject_image_ids = _subject_image_ids(brief.idea, image_ids) if image_is_subject else []
    scenes = _script_scenes(brief.idea)
    scene_schedule = _scene_schedule(scenes, n)
    sample_camera = _explicit_sample_camera(brief.idea) if is_sample else {}
    stills = subject_image_ids or image_ids[:2] or ["still-lead-wide", "still-lead-close"]
    character_names = list(
        dict.fromkeys(
            line.speaker for window in dialogue_windows for line in window
        )
    )
    if not character_names:
        character_names = [book.lead_name]
    characters = [
        Character(
            id=_character_id(name) if dialogue_windows else "char-lead",
            name=name,
            still_asset_ids=stills,
            wardrobe=book.lead_wardrobe,
            notes="Lock stills before FARM. Identity drift is a QC fail.",
        )
        for name in character_names
    ]
    character_by_name = {character.name: character for character in characters}
    voice_profiles = [
        _synthetic_voice_profile(pid, character)
        for character in characters
        if dialogue_windows
    ]
    for character, profile in zip(characters, voice_profiles):
        character.voice_profile_id = profile.id
    dialogue_by_shot: dict[int, list[ScriptDialogue]] = {}
    for index, window in enumerate(dialogue_windows):
        shot_index = min(n - 1, (index * n) // len(dialogue_windows))
        dialogue_by_shot.setdefault(shot_index, []).extend(window)
    visual_idea = (
        _visual_only_text(idea)
        or "A Bible-locked performance in the authored setting."
    )
    bible = Bible(
        title=title,
        logline=idea[:240],
        fps=23.976,
        grain=book.grain,
        loudness=book.loudness,
        visual_grammar=book.grammar,
        world=f"{book.world_prefix}: {visual_idea}",
        characters=characters,
        locked=False,
    )
    shots: list[Shot] = []
    narrated_scenes: set[int] = set()
    for i, rec in enumerate(recipes):
        scene_beat = scene_schedule[i]
        spoken_lines = dialogue_by_shot.get(i, [])
        frames = max(
            WAN_MIN_SHOT_FRAMES,
            int(rec.get("duration_frames") or round(rec["seconds"] * TIMELINE_FPS)),
        )
        sid = f"sh-{(i + 1) * 10:03d}"
        engine = Engine.I2V_A14B if image_ids else Engine.T2V_A14B
        if synced_performance and rec.get("audio") and image_ids:
            engine = Engine.S2V_14B
        elif image_ids and video_ids:
            engine = Engine.ANIMATE_14B
        elif image_is_subject:
            engine = Engine.I2V_A14B
        elif i > 0:
            # Once a first print exists, its final frame is a stronger identity
            # and world anchor than a fresh unconditioned text generation.
            engine = Engine.I2V_A14B
        refs = list(
            dict.fromkeys(
                character_by_name[line.speaker].id for line in spoken_lines
            )
        ) or [characters[0].id]
        keys = list(subject_image_ids) if image_is_subject else []
        if spoken_lines and image_ids:
            keys = list(subject_image_ids) or image_ids[:1]
        if i == 0 and image_ids and not keys:
            keys = image_ids[:1]
        if engine in (Engine.S2V_14B, Engine.ANIMATE_14B) and not keys:
            keys = image_ids[:1]
        audio_refs = list(audio_ids) if engine == Engine.S2V_14B else []
        motion_refs = list(video_ids) if engine == Engine.ANIMATE_14B else []
        audio = None
        if rec.get("audio") and sound_requested and spoken_direction and not spoken_direction_assigned:
            audio = spoken_direction
            spoken_direction_assigned = True
        if scene_beat and scene_beat.narration and scene_beat.number not in narrated_scenes:
            audio = scene_beat.narration
            narrated_scenes.add(scene_beat.number)
        focal_mm = rec["focal_mm"]
        t_stop = rec["t_stop"]
        lighting_ratio = rec["lighting_ratio"]
        camera_move = rec["camera_move"]
        if reference_lock == ReferenceLock.CHARACTER_AND_BACKGROUND:
            anchor = recipes[0]
            focal_mm = anchor["focal_mm"]
            t_stop = anchor["t_stop"]
            lighting_ratio = anchor["lighting_ratio"]
            camera_move = CameraMove.STATIC
        focal_mm = sample_camera.get("focal_mm", focal_mm)
        t_stop = sample_camera.get("t_stop", t_stop)
        lighting_ratio = sample_camera.get("lighting_ratio", lighting_ratio)
        camera_move = sample_camera.get("camera_move", camera_move)
        continuity_mode = ContinuityMode.REFERENCE if i == 0 and keys else ContinuityMode.INDEPENDENT
        overlap_frames = 0
        if reference_lock != ReferenceLock.NONE:
            continuity_mode = ContinuityMode.REFERENCE if i == 0 else ContinuityMode.PREVIOUS_FRAME
            overlap_frames = 8
        elif i > 0 and engine in (Engine.I2V_A14B, Engine.S2V_14B):
            continuity_mode = ContinuityMode.PREVIOUS_FRAME
            overlap_frames = 8
        planned_shot = Shot(
                id=sid,
                scene=str(scene_beat.number if scene_beat else rec["scene"]),
                slug=(
                    f"SCENE {scene_beat.number} — {scene_beat.title}"
                    if scene_beat
                    else rec["slug"]
                ),
                duration_frames=frames,
                fps=23.976,
                focal_mm=focal_mm,
                t_stop=t_stop,
                lighting_ratio=lighting_ratio,
                camera_move=camera_move,
                prompt="pending Wan2.2 prompt compilation",
                negative_prompt="",
                seed=seed + i * 17,
                character_ref_ids=refs,
                keyframe_asset_ids=keys,
                audio_asset_ids=audio_refs,
                motion_reference_asset_ids=motion_refs,
                reference_lock=reference_lock,
                continuity_mode=continuity_mode,
                engine=engine,
                finish=FinishIntent.DELIVERABLE,
                audio_line=audio,
                dialogue_turns=[
                    DialogueTurn(
                        id=f"turn-{sid}-{turn_index + 1}",
                        character_id=character_by_name[line.speaker].id,
                        text=line.text,
                    )
                    for turn_index, line in enumerate(spoken_lines)
                ],
                overlap_frames=overlap_frames,
                hero=bool(rec.get("hero")),
            )
        _install_wan_prompt_plan(
            planned_shot,
            idea=(
                scene_beat.visual
                if scene_beat and scene_beat.visual
                else visual_idea
            ),
            bible=bible,
            wardrobe=(
                character_by_name[spoken_lines[0].speaker].wardrobe
                if spoken_lines
                else characters[0].wardrobe
            ),
            shot_index=i,
            sample_seconds=sample_seconds if is_sample else None,
        )
        shots.append(planned_shot)
    notes = []
    coverage_frames = sum(shot.duration_frames for shot in shots) - STITCH_OVERLAP_FRAMES * max(
        0, len(shots) - 1
    )
    coverage_sec = coverage_frames / TIMELINE_FPS
    if brief.target_minutes > 15:
        notes.append(
            Note(
                id="n-reel-cap",
                author="conductor",
                body=(
                    "First reel only. A full hour rundown is BRAIN-mode work "
                    "(Blueprint 07: days, not minutes)."
                ),
                shot_id=shots[0].id,
            )
        )
    elif coverage_sec + 5 < brief.target_minutes * 60:
        notes.append(
            Note(
                id="n-pass-cap",
                author="conductor",
                body=(
                    f"This pass is {int(coverage_sec)}s of bounded Wan quality-lane coverage "
                    f"toward the {allotment_label(brief.target_minutes)} goal. "
                    "Do not lengthen a take past the 81-frame lane to fake TRT."
                ),
                shot_id=shots[0].id,
            )
        )
    draft = Project(
        id=pid,
        status=ProjectStatus.BIBLE_PENDING,
        customer_kind=brief.customer_kind,
        brief=brief,
        bible=bible,
        shots=shots,
        takes=[],
        notes=notes,
        assets=held,
        voice_profiles=voice_profiles,
        mix=MixLock(
            id=f"mix-{run_id}",
            loudness=book.loudness,
            picture_lock_timeline_id=f"tl-{run_id}",
        ),
        gates=[
            Gate(name=GateName.BIBLE, open=True),
            Gate(name=GateName.PICTURE, open=True),
            Gate(name=GateName.MIX, open=True),
            Gate(name=GateName.DISTRIBUTION, open=True),
        ],
        fleet_mode="SPLIT",
        compute=compute or ClusterSite(),
    )
    events, total = stitch_events(draft)
    draft.timeline = Timeline(
        id=f"tl-{run_id}",
        events=events,
        total_frames=total,
        fps=bible.fps,
    )
    return draft


def expand_shot_prompt(shot: Shot, bible: Bible) -> str:
    """Enrich a one-line shot into mandate-grade camera language."""
    if shot.prompt_plan is not None:
        return render_wan_prompt(shot.prompt_plan)
    if "mm T" in shot.prompt and "Continuity" in shot.prompt:
        return shot.prompt
    return (
        f"{shot.slug}. {shot.prompt.strip()} "
        f"{shot.focal_mm}mm T{shot.t_stop}, {shot.lighting_ratio}, "
        f"{shot.camera_move.value.replace('_', ' ')}. "
        f"World: {bible.world[:160]}. Grammar: {bible.visual_grammar[:160]}. "
        "1080 finish-intent. No morph, no identity drift."
    )
