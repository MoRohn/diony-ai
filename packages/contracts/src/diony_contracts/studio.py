"""Idea-to-Studio brain.

Day 2: local, deterministic, cinematic. Week 2+ this is DeepSeek-V4-Flash-0731
on the cluster (Blueprint 10). Never Dashscope.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass

from diony_contracts.enums import (
    AssetKind,
    CameraMove,
    CustomerKind,
    Engine,
    FinishIntent,
    GateName,
    Grain,
    LoudnessTarget,
    ProjectStatus,
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
from diony_contracts.stitch import stitch_events

NEGATIVE = (
    "morphing face, melting hands, extra fingers, identity drift, "
    "modern UI chrome, illegible signage, cartoon, watermark, "
    "720p as finished look, crushed blacks illegal for broadcast"
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


def _shot_count(minutes: float) -> int:
    """Coverage size for a length goal. Not TRT. Cap 12 — do not fake an hour."""
    if minutes <= 0.5:
        return 4
    if minutes <= 1:
        return 6
    if minutes <= 3:
        return 8
    if minutes <= 5:
        return 10
    return 12


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


def blank_studio(compute: ClusterSite | None = None) -> Project:
    """Empty Idea page after Wizard Sign. Sample still loads Night Press."""
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
    seed = _seed(idea + brief.customer_kind.value)
    n = _shot_count(brief.target_minutes)
    recipes = _recipes(brief.customer_kind)[:n]
    image_ids = [a.id for a in held if a.kind == AssetKind.IMAGE]
    video_ids = [a.id for a in held if a.kind == AssetKind.VIDEO]
    char_id = "char-lead"
    stills = image_ids[:2] or ["still-lead-wide", "still-lead-close"]
    character = Character(
        id=char_id,
        name=book.lead_name,
        still_asset_ids=stills,
        wardrobe=book.lead_wardrobe,
        notes="Lock stills before FARM. Identity drift is a QC fail.",
    )
    bible = Bible(
        title=title,
        logline=idea[:240],
        fps=23.976,
        grain=book.grain,
        loudness=book.loudness,
        visual_grammar=book.grammar,
        world=f"{book.world_prefix}: {idea}",
        characters=[character],
        locked=False,
    )
    shots: list[Shot] = []
    for i, rec in enumerate(recipes):
        frames = max(24, int(round(rec["seconds"] * 23.976)))
        sid = f"sh-{(i + 1) * 10:03d}"
        engine: Engine = rec["engine"]
        refs = [char_id] if engine in (Engine.I2V_A14B, Engine.S2V_14B, Engine.ANIMATE_14B) else []
        keys = list(image_ids[:2]) if refs else []
        if engine == Engine.ANIMATE_14B and video_ids:
            keys = video_ids[:1] + keys
        audio = None
        if rec.get("audio"):
            audio = idea[:120]
        prompt = (
            f"{rec['slug']}. {idea} "
            f"{rec['focal_mm']}mm T{rec['t_stop']}, {rec['lighting_ratio']}, "
            f"{rec['camera_move'].value.replace('_', ' ')}. "
            f"Continuity with bible '{title}'. Cinematic, 1080 finish-intent. "
            f"{book.grammar}"
        )
        shots.append(
            Shot(
                id=sid,
                scene=str(rec["scene"]),
                slug=rec["slug"],
                duration_frames=frames,
                fps=23.976,
                focal_mm=rec["focal_mm"],
                t_stop=rec["t_stop"],
                lighting_ratio=rec["lighting_ratio"],
                camera_move=rec["camera_move"],
                prompt=prompt,
                negative_prompt=NEGATIVE,
                seed=seed + i * 17,
                character_ref_ids=refs,
                keyframe_asset_ids=keys,
                engine=engine,
                finish=FinishIntent.DELIVERABLE,
                audio_line=audio,
            )
        )
    pid = f"studio-{seed}"
    notes = []
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
        mix=MixLock(
            id=f"mix-{seed}",
            loudness=book.loudness,
            picture_lock_timeline_id=f"tl-{seed}",
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
        id=f"tl-{seed}",
        events=events,
        total_frames=total,
        fps=bible.fps,
    )
    return draft


def expand_shot_prompt(shot: Shot, bible: Bible) -> str:
    """Enrich a one-line shot into mandate-grade camera language."""
    if "mm T" in shot.prompt and "Continuity" in shot.prompt:
        return shot.prompt
    return (
        f"{shot.slug}. {shot.prompt.strip()} "
        f"{shot.focal_mm}mm T{shot.t_stop}, {shot.lighting_ratio}, "
        f"{shot.camera_move.value.replace('_', ' ')}. "
        f"World: {bible.world[:160]}. Grammar: {bible.visual_grammar[:160]}. "
        "1080 finish-intent. No morph, no identity drift."
    )
