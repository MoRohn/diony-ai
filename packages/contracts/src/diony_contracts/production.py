"""Blueprint 26 production package contracts.

The scripted long-form data model: a locked shooting script, Character and
Setting Bibles, provider-legal ClipUnits with per-clip audio packets and
continuity locks, clip reviews, and the assembly map. Identity is an input:
these objects exist so no hero pixel is ever generated from text alone.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

from diony_contracts.director import (
    CloudGenerationMode,
    CloudProvider,
    CostEstimate,
    ProductionStyle,
    Voicing,
)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


ClipClass = Literal[
    "sync-dialogue", "vo-over", "silent-picture", "score-led", "ambience-led"
]

DEFAULT_FORBIDDEN_DRIFT = [
    "no added jewelry",
    "no hair restyle",
    "no teeth or eye-color change",
    "no weight shift",
    "no tattoos appearing or vanishing",
]


class VoiceProfile(BaseModel):
    voice_id: str
    engine: Literal["omni_preset", "flow_user_voice", "none"] = "omni_preset"
    engine_ref: str = ""
    sample_asset_id: str = ""
    accent: str = ""
    pace_wpm: int = Field(default=150, gt=0)
    pitch_band: str = ""
    mic_distance: str = "close, dry"
    approved: bool = False


def _as_noun(negative: str) -> str:
    """A stored "no hair restyle" reads back as the noun it forbids."""
    text = negative.strip().rstrip(".")
    lowered = text.lower()
    for lead in ("never ", "no ", "not "):
        if lowered.startswith(lead):
            return text[len(lead):].strip()
    return text


class IdentityCard(BaseModel):
    """Blueprint 32 — the character's identity contract, written once and
    carried byte-identical on every prompt, plate, sheet, and keyframe that
    shows the character. Prose re-derived per clip is how a face drifts; a
    locked card is how it holds. ``anchor`` is the one-line casting read
    (age, build, face geometry), ``features`` the non-negotiables a viewer
    would notice missing (hair, eyes, skin, one distinguishing mark),
    ``wardrobe_lock`` the outfit worn for the whole film, ``negatives`` what
    must never change. ``source`` says who wrote it: the brain, the
    deterministic derivation from the character's notes, or the user."""

    anchor: str = ""
    features: list[str] = Field(default_factory=list)
    wardrobe_lock: str = ""
    negatives: list[str] = Field(default_factory=list)
    source: Literal["brain", "derived", "user"] = "derived"

    def block(self, name: str) -> str:
        """The verbatim IDENTITY LOCK clause. Deterministic from the fields:
        two cards with the same fields render the same bytes."""
        parts: list[str] = []
        head = f"IDENTITY LOCK — {name}"
        if self.anchor:
            head += f": {self.anchor.strip().rstrip('.')}"
        parts.append(head + ".")
        features = [f.strip().rstrip(".") for f in self.features if f and f.strip()]
        if features:
            parts.append("Non-negotiable: " + "; ".join(features) + ".")
        if self.wardrobe_lock.strip():
            parts.append(f"Wardrobe, unchanged in every shot: {self.wardrobe_lock.strip().rstrip('.')}.")
        negatives = [_as_noun(n) for n in self.negatives if n and n.strip()]
        if negatives:
            parts.append("Never: " + "; ".join(negatives) + ".")
        parts.append(f"{name} is the same person in every frame — same face, same build, same hair.")
        return " ".join(parts)


class CharacterProfile(BaseModel):
    schema_version: int = 1
    char_id: str
    name: str
    role: str = ""
    sheet_version: int = Field(default=1, ge=1)
    hero_stills: dict[str, str] = Field(default_factory=dict)
    # Blueprint 32: the locked identity contract (None = the pre-32 prose
    # block), the measured self-consistency of the sheet (slot → cosine vs
    # the front still; empty = unmeasured), and how many times each slot
    # was redrawn after failing that measure.
    identity: IdentityCard | None = None
    sheet_receipt: dict[str, Any] = Field(default_factory=dict)
    sheet_retries: dict[str, int] = Field(default_factory=dict)
    wardrobe: dict[str, str] = Field(default_factory=dict)
    age_body_notes: str = ""
    forbidden_drift: list[str] = Field(
        default_factory=lambda: list(DEFAULT_FORBIDDEN_DRIFT)
    )
    grok_ref_pack: list[str] = Field(default_factory=list)
    flow_media_ids: dict[str, str] = Field(default_factory=dict)
    flow_character_ref: str = ""
    voice: VoiceProfile | None = None
    casting_card: str = ""
    face_approved: bool = False


class SettingProfile(BaseModel):
    schema_version: int = 1
    setting_id: str
    name: str
    plate_asset_id: str = ""
    plate_version: int = Field(default=1, ge=1)
    architecture_notes: str = ""
    time_weather: str = ""
    grade_language: str = ""
    camera_kit: str = ""
    props: list[str] = Field(default_factory=list)
    geography: str = ""
    room_tone_id: str = ""


class ScriptLine(BaseModel):
    line_id: str
    kind: Literal["heading", "action", "cue", "dialogue", "parenthetical", "vo", "sound"]
    text: str
    speaker: str = ""
    scene_id: str = ""
    # The authored frame for this beat — size, angle, movement, and what
    # dominates the picture — written by the screenwriter, not synthesized by
    # a coverage rotation. Empty means the splitter falls back to its grammar.
    shot: str = ""
    # Blueprint 32: a dialogue line spoken by a character who is in the scene
    # but OUT OF FRAME (screenplay "(O.S.)") — the picture holds on who hears
    # it, the voice comes from beyond the frame edge, and no mouth on screen
    # forms the words. Meaningless on any kind but dialogue.
    off_screen: bool = False


class Scene(BaseModel):
    scene_id: str
    heading: str
    setting_ref: str
    cast: list[str] = Field(default_factory=list)
    story_time: str = ""
    line_ids: list[str] = Field(default_factory=list)
    emotional_job: str = ""


class Script(BaseModel):
    schema_version: int = 1
    id: str
    project_id: str = ""
    version: int = Field(default=1, ge=1)
    logline: str = ""
    runtime_target_s: int = Field(default=60, gt=0)
    beats: list[dict[str, str]] = Field(default_factory=list)
    scenes: list[Scene] = Field(default_factory=list)
    lines: list[ScriptLine] = Field(default_factory=list)
    locked: bool = False
    locked_at: datetime | None = None
    created_at: datetime = Field(default_factory=_utc_now)

    @model_validator(mode="after")
    def scene_lines_exist(self) -> "Script":
        known = {line.line_id for line in self.lines}
        for scene in self.scenes:
            missing = [i for i in scene.line_ids if i not in known]
            if missing:
                raise ValueError(
                    f"scene {scene.scene_id} references unknown lines: {missing}"
                )
        return self

    def lock(self) -> None:
        self.locked = True
        self.locked_at = _utc_now()

    def scene(self, scene_id: str) -> Scene:
        for scene in self.scenes:
            if scene.scene_id == scene_id:
                return scene
        raise KeyError(scene_id)

    def line(self, line_id: str) -> ScriptLine:
        for line in self.lines:
            if line.line_id == line_id:
                return line
        raise KeyError(line_id)


class AudioPacket(BaseModel):
    clip_class: ClipClass
    # {speaker, line, parenthetical} — plus, since Blueprint 32, off_screen
    # (bool) and listeners (list) when the line is heard from outside the frame
    script_excerpt: list[dict[str, Any]] = Field(default_factory=list)
    voice_ref: str = ""
    delivery: dict[str, str] = Field(default_factory=dict)
    on_camera: bool = True
    # Blueprint 31: where this clip's words come from. "post" — the narrator
    # records the excerpt against the cut and the location track carries no
    # speech (Blueprint 28). "generated" — the excerpt rides the prompt and
    # the character voices it in the clip; the track IS the voice.
    voicing: Voicing = "post"
    # Blueprint 33: where the spoken line begins inside its take, in seconds
    # from the clip's head — 0 when the take opens on the line. A take that
    # packs picture beats before the line puts the words where the body
    # speaks them, and the post narrator's cue lands there.
    line_at_s: float = Field(default=0.0, ge=0.0)
    room_tone_id: str = ""
    foley: list[dict[str, Any]] = Field(default_factory=list)
    score: Literal["in", "out", "none"] = "none"
    # The scripted piece itself ("a catchy funky instrumental groove…"), shared
    # verbatim by every clip in the scene so each generation describes the SAME
    # music instead of inventing a new jingle per clip.
    score_desc: str = ""
    handles_s: dict[str, float] = Field(
        default_factory=lambda: {"pre": 0.5, "tail": 1.0}
    )
    forbidden: list[str] = Field(
        default_factory=lambda: ["new accent", "new mic character", "unscripted music"]
    )


class ContinuityLock(BaseModel):
    lock_recipe: Literal[
        "still", "character_asset", "edit", "ref", "bridge", "hybrid", "none"
    ] = "none"
    character_packs: list[dict[str, Any]] = Field(default_factory=list)
    setting_plate: dict[str, Any] = Field(default_factory=dict)
    prev_frame_ptr: dict[str, Any] = Field(default_factory=dict)


MASTER_BEAT_STAGES = (
    "initial_state",
    "first_change",
    "development",
    "escalation",
    "payoff",
    "final_state",
)



# Words that place a look in photographic reality; any of the animation /
# illustration tells below overrides them (an "animated documentary" is
# animation). The Grok REALISM ground truth and the Flow baseline cinema
# clause ride only a photoreal look — under any other medium they are the
# contradiction that pulls every shot back toward live-action gloss.
LOOK_PHOTOREAL_TELLS = (
    "photoreal", "photo-real", "live action", "live-action", "realism", "realistic",
    "documentary", "filmed", "footage", "cinema camera", "35mm", "16mm", "digital cinema",
)
LOOK_STYLIZED_TELLS = (
    "animat", "anime", "cartoon", "illustrat", "2.5d", "2d ", "2-d", "3d render", "cgi",
    "claymation", "clay", "stop-motion", "stop motion", "pixel", "painterly", "painted",
    "watercolor", "watercolour", "comic", "cel ", "cel-", "isometric", "low-poly",
    "low poly", "vector", "line art", "ink", "sketch", "paper cut", "papercut",
    "collage", "motion graphic", "explainer", "toon", "render", "stylized", "stylised",
    "graphic novel", "storybook", "puppet",
)


def look_is_photoreal(*texts: str) -> bool:
    """Whether a described look lives in photographic reality. Stylized tells
    win over realism tells; no tells at all reads as photoreal (the house
    default every prompt carried before the Look Bible existed)."""
    lowered = " ".join(t or "" for t in texts).lower()
    if any(tell in lowered for tell in LOOK_STYLIZED_TELLS):
        return False
    return True


_NEGATIVE_LEADS = ("never ", "no ", "avoid ", "not ", "without ")


def _negative_noun(text: str) -> str:
    """'no photoreal footage' → 'photoreal footage', so the block's
    'Never: …' list never reads as a double negative."""
    cleaned = " ".join(text.split()).rstrip(".")
    lowered = cleaned.lower()
    for lead in _NEGATIVE_LEADS:
        if lowered.startswith(lead):
            return cleaned[len(lead):].strip()
    return cleaned


class LookBible(BaseModel):
    """The film-wide look — one medium, one hand, for every shot.

    Derived once by the master derivation (or set by the user) and carried
    VERBATIM on every clip prompt, every setting plate, every character sheet,
    and every style frame of the production. It is the answer to a film in
    which every shot looked drawn by a different studio: text alone re-derived
    the medium per clip, and nothing byte-identical tied the shots together.
    """

    schema_version: int = 1
    # "2.5D animated explainer, flat shaded with soft paper texture" /
    # "photoreal live action" / "hand-inked animation on watercolor washes"
    medium: str = ""
    # how surfaces, edges, and light are drawn: shading model, outline, grain
    render_language: str = ""
    # the palette and its discipline: "muted navy and warm amber, low saturation"
    palette: str = ""
    # lens, depth of field, contrast and grade the whole film shares
    lens_grade: str = ""
    # the lighting PHILOSOPHY (one motivated key, cool fill…), not a scene's sources
    lighting_system: str = ""
    # what the look must never become: "no photoreal footage", "no 3D gloss"
    negatives: list[str] = Field(default_factory=list)
    source: Literal["brain", "template", "user"] = "template"
    # the look's reference images: the user's stills and, once a chain or a
    # plate has printed, the frames the film established its look with
    anchor_assets: list[str] = Field(default_factory=list)

    @property
    def photoreal(self) -> bool:
        return look_is_photoreal(self.medium, self.render_language)

    def block(self) -> str:
        """The verbatim LOOK BIBLE clause — deterministic from the fields, so
        every prompt of the film carries byte-identical words."""
        bits = [
            f"medium: {self.medium.rstrip('.')}" if self.medium else "",
            f"rendering: {self.render_language.rstrip('.')}" if self.render_language else "",
            f"palette: {self.palette.rstrip('.')}" if self.palette else "",
            f"lens and grade: {self.lens_grade.rstrip('.')}" if self.lens_grade else "",
            f"lighting system: {self.lighting_system.rstrip('.')}" if self.lighting_system else "",
        ]
        described = "; ".join(b for b in bits if b)
        if not described:
            return ""
        never = [_negative_noun(n) for n in self.negatives if n and n.strip()]
        never = [n for n in never if n]
        never_clause = (
            " Never: " + "; ".join(never) + "." if never else ""
        )
        return (
            f"LOOK BIBLE (holds for every shot of this film) — {described}. "
            "Every shot is rendered by the same hand in the same medium, palette, "
            "line, and grain — one studio, one film; never a change of medium, "
            "never a new palette, never a different studio's hand."
            + never_clause
        )


class MasterPrompt(BaseModel):
    """Blueprint 27: the pre-split finalized artifact.

    The Idea-to-Prompt derivation of one creative brief — WHO, WHAT, WHERE,
    how the scene is layered, how the action develops, how the subject
    performs, how the environment responds, how the camera observes, how the
    scene is lit, what must stay consistent, what must be prevented, and
    exactly how the clip ends — plus the generator-ready ``prompt`` those
    derivations assemble into. A one-shot ships it as the prompt body; a
    scripted production writes its screenplay FROM it and every split clip
    inherits its lighting, physics, anchors, constraints, and ending.
    """

    schema_version: int = 1
    idea: str
    # The user's duration input drives the derivation: beats scale to it,
    # the final prompt paces to it, and the estimate books it. The
    # derivation reports back how long the action it wrote actually plays.
    target_duration_s: int = Field(gt=0)
    estimated_duration_s: int = Field(default=0, ge=0)
    reference_image: bool = False
    visual_style: str = ""
    primary_subject: str = ""
    primary_action: str = ""
    setting: str = ""
    beats: list[dict[str, str]] = Field(default_factory=list)  # {stage, text}
    scene_depth: dict[str, str] = Field(default_factory=dict)  # background/middle_ground/foreground
    spatial: str = ""
    camera: str = ""
    performance: str = ""
    micro_expressions: str = ""
    physics: str = ""
    body_mechanics: str = ""
    lighting: dict[str, str] = Field(default_factory=dict)  # source/quality/shadows/secondary
    mood: str = ""
    continuity_anchors: list[str] = Field(default_factory=list)
    ending: str = ""
    constraints: list[str] = Field(default_factory=list)
    # The film-wide look every shot shares — appended verbatim to every
    # clip prompt, plate, sheet, and style frame (see LookBible).
    look: LookBible | None = None
    prompt: str
    # Chained one-shot delivery: when the booked runtime exceeds the route's
    # single-clip length, the derivation also splits the action into
    # sequential clips — each entry {prompt, duration_s, narration}, in play
    # order; clip k+1 opens from clip k's final frame. Empty for a
    # single-clip booking.
    clips: list[dict[str, Any]] = Field(default_factory=list)
    # The verbatim voice-over the finished video carries, when the idea
    # scripts one. Narration is a POST instrument: it is never written into
    # the generator prompt as spoken dialogue — the narrator records it
    # against the assembled cut (Blueprint 28).
    narration: str = ""
    # The likeness guard's record: every real, identifiable person the
    # derivation recognized (in the idea or a reference still) and the
    # descriptor written in place of the name — {name, aliases, descriptor,
    # label}. A name never reaches a generator; every later compile of this
    # artifact (clip prompts, revisions, screenplay) holds to the same list.
    real_people: list[dict[str, Any]] = Field(default_factory=list)
    # The named characters the derivation wrote — invented or the idea's own —
    # in the same shape. Names are fine in a prompt on their own; beside a
    # reference picture Google Flow refuses ANY named person (verified live
    # 2026-09-07), so the submit gate describes them there.
    characters: list[dict[str, Any]] = Field(default_factory=list)
    # Whether a text-only audit has read this artifact for names the brain did
    # not confess (at derivation, or once at the first pictured submit for an
    # artifact derived before the characters contract existed).
    characters_audited: bool = False
    word_count: int = Field(default=0, ge=0)
    source: Literal["brain", "template"] = "template"
    warnings: list[str] = Field(default_factory=list)
    brain_usage: dict[str, int] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=_utc_now)

    def beat(self, stage: str) -> str:
        for entry in self.beats:
            if entry.get("stage") == stage:
                return entry.get("text", "")
        return ""


class PromptVersion(BaseModel):
    version: str
    prompt: str
    note: str = ""
    created_at: datetime = Field(default_factory=_utc_now)


class ClipUnit(BaseModel):
    schema_version: int = 1
    clip_id: str
    scene_id: str
    order: int = Field(ge=0)
    script_line_ids: list[str] = Field(default_factory=list)
    clip_class: ClipClass
    duration_s: int = Field(gt=0)
    # Blueprint 33: the length the take is paced to play — its scene's even
    # share when the beats can meet it, else the beats' own length — before
    # the provider grid rounded it up. The assembly trims the round-up
    # beyond it on spoken takes; picture takes play what they booked.
    natural_s: float = Field(default=0.0, ge=0.0)
    camera_idea: str
    # How this clip meets the NEXT one: "bridge" (last-frame chain inside the
    # scene), "reaction_cut" (dialogue into its reaction), "extend" (a
    # continuation take), or "cut" (scene boundary / hard cut).
    join_next: Literal["bridge", "reaction_cut", "extend", "cut"] = "cut"
    provider: CloudProvider | None = None
    model: str = ""
    mode: CloudGenerationMode | None = None
    resolution: str = "720p"
    audio_packet: AudioPacket
    continuity: ContinuityLock = Field(default_factory=ContinuityLock)
    prompt_versions: list[PromptVersion] = Field(default_factory=list)
    estimate_usd_micros: int = Field(default=0, ge=0)

    def add_prompt(self, version: PromptVersion) -> None:
        if any(v.version == version.version for v in self.prompt_versions):
            raise ValueError(f"prompt version {version.version} already exists on {self.clip_id}")
        self.prompt_versions.append(version)

    def latest_prompt(self) -> PromptVersion:
        if not self.prompt_versions:
            raise ValueError(f"clip {self.clip_id} has no compiled prompt")
        return self.prompt_versions[-1]


class ClipReview(BaseModel):
    schema_version: int = 1
    id: str
    clip_id: str
    op_id: str = ""
    verdict: Literal["pass", "warn", "fail"]
    families: dict[str, str] = Field(default_factory=dict)
    measured: dict[str, float] = Field(default_factory=dict)
    issues: list[str] = Field(default_factory=list)
    recommended_op: str = ""
    rationale: str = ""
    incremental_cost_usd_micros: int = Field(default=0, ge=0)
    created_at: datetime = Field(default_factory=_utc_now)


class AssemblyEvent(BaseModel):
    clip_id: str
    op_id: str = ""
    in_s: float = Field(default=0, ge=0)
    out_s: float | None = None
    join: Literal["cut", "xfade"] = "cut"


class AssemblyMap(BaseModel):
    schema_version: int = 1
    id: str
    events: list[AssemblyEvent] = Field(default_factory=list)
    loudness_target: Literal["broadcast", "streaming"] = "streaming"
    # the applied look by name: "none", "diony-default", or a montage
    # LookProfile — the map records what the cut actually did
    look: str = "diony-default"
    title: str = ""


class ProductionPackage(BaseModel):
    schema_version: int = 1
    id: str
    session_id: str
    idea: str
    characters: list[CharacterProfile] = Field(default_factory=list)
    settings: list[SettingProfile] = Field(default_factory=list)
    script: Script
    clips: list[ClipUnit] = Field(default_factory=list)
    estimate: CostEstimate | None = None
    revision_allowance_bp: int = Field(default=4000, ge=0, le=10000)
    # The pre-split finalized artifact this package was written from (Blueprint 27).
    master_prompt: MasterPrompt | None = None
    # Blueprint 31: the production style the package was split under — the
    # record of why each clip carries the voicing it does.
    production_style: ProductionStyle = ProductionStyle.NARRATED
    # Blueprint 32: the cast-lock mode the package was split under —
    # "standard" (the pre-32 platform) or "locked" (identity cards on every
    # prompt, a measured sheet, cast keyframes, provider identity markers).
    cast_lock: Literal["standard", "locked"] = "standard"
    created_at: datetime = Field(default_factory=_utc_now)

    @model_validator(mode="after")
    def clips_belong_to_scenes(self) -> "ProductionPackage":
        known = {scene.scene_id for scene in self.script.scenes}
        for clip in self.clips:
            if clip.scene_id not in known:
                raise ValueError(
                    f"clip {clip.clip_id} references unknown scene {clip.scene_id}"
                )
        return self

    def character(self, char_id: str) -> CharacterProfile:
        for char in self.characters:
            if char.char_id == char_id:
                return char
        raise KeyError(char_id)

    def setting(self, setting_id: str) -> SettingProfile:
        for setting in self.settings:
            if setting.setting_id == setting_id:
                return setting
        raise KeyError(setting_id)

    def clip(self, clip_id: str) -> ClipUnit:
        for clip in self.clips:
            if clip.clip_id == clip_id:
                return clip
        raise KeyError(clip_id)
