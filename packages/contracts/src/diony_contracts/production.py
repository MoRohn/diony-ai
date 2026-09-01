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


class CharacterProfile(BaseModel):
    schema_version: int = 1
    char_id: str
    name: str
    role: str = ""
    sheet_version: int = Field(default=1, ge=1)
    hero_stills: dict[str, str] = Field(default_factory=dict)
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
    script_excerpt: list[dict[str, str]] = Field(default_factory=list)
    voice_ref: str = ""
    delivery: dict[str, str] = Field(default_factory=dict)
    on_camera: bool = True
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
