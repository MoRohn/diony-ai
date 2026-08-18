from __future__ import annotations

from datetime import datetime, timezone
from pydantic import BaseModel, Field, field_validator, model_validator

from diony_contracts.enums import (
    ArtifactKind,
    AssetKind,
    CameraMove,
    ComputePath,
    CustomerKind,
    DeliverableKind,
    Engine,
    FinishIntent,
    GateName,
    Grain,
    LoudnessTarget,
    MixLockState,
    NoteAuthor,
    PictureLockState,
    ProjectStatus,
    QCVerdict,
    TakeOrigin,
)


def _now() -> datetime:
    return datetime.now(timezone.utc)


class ClusterSite(BaseModel):
    """Where Diony compute lives. Local Quad-Spark is the SKU. Dual-Spark and B300 are signed lease exceptions."""

    path: ComputePath = ComputePath.LOCAL
    profile: str = "spark-4x-v1"
    provider: str = ""
    gpu_class: str = ""
    exception_by: str = ""
    exception_at: datetime | None = None

    @model_validator(mode="after")
    def site_matches_profile(self) -> ClusterSite:
        if self.path == ComputePath.LEASE:
            if len(self.exception_by.strip()) < 2:
                raise ValueError("leased GPU requires a named human exception")
            if self.profile != "lease-gpu-v1":
                raise ValueError("lease profile must be lease-gpu-v1")
        if self.path == ComputePath.DUAL:
            if len(self.exception_by.strip()) < 2:
                raise ValueError("leased dual-spark requires a named human exception")
            if self.profile != "spark-2x-v1":
                raise ValueError("dual-spark profile must be spark-2x-v1")
            if not self.gpu_class.strip():
                self.gpu_class = "2×GB10"
        if self.path == ComputePath.B300:
            if len(self.exception_by.strip()) < 2:
                raise ValueError("leased B300 requires a named human exception")
            if self.profile != "b300-v1":
                raise ValueError("B300 profile must be b300-v1")
            if not self.gpu_class.strip():
                self.gpu_class = "1×B300"
        if self.path == ComputePath.LOCAL and self.profile != "spark-4x-v1":
            raise ValueError("local crate uses spark-4x-v1")
        return self


class Note(BaseModel):
    id: str
    author: NoteAuthor
    body: str
    created_at: datetime = Field(default_factory=_now)
    shot_id: str | None = None
    take_id: str | None = None

    @model_validator(mode="after")
    def attach_to_shot_or_take(self) -> Note:
        if not self.shot_id and not self.take_id:
            raise ValueError("notes must attach to shot_id or take_id")
        return self


class Brief(BaseModel):
    idea: str = ""
    customer_kind: CustomerKind
    target_minutes: float = 5.0
    audience: str = ""
    title_hint: str = ""
    attachment_ids: list[str] = Field(default_factory=list)

    @field_validator("target_minutes")
    @classmethod
    def minutes_range(cls, v: float) -> float:
        if v < 0.5 or v > 60:
            raise ValueError("target_minutes must be between 0.5 and 60")
        return v

    @model_validator(mode="after")
    def idea_or_attachments(self) -> Brief:
        if len(self.idea.strip()) < 8 and not self.attachment_ids:
            raise ValueError("need an idea in words (8+ characters) or at least one reference file")
        return self


class Asset(BaseModel):
    id: str
    kind: AssetKind
    filename: str
    mime: str
    bytes: int
    path: str
    sha256: str = ""
    extracted_text: str = ""
    role: str = "idea_ref"


class Artifact(BaseModel):
    id: str
    take_id: str
    kind: ArtifactKind
    path: str
    sha256: str = ""
    bytes: int = 0
    width: int = 1280
    height: int = 720
    fps: float = 23.976
    duration_frames: int = 0
    created_at: datetime = Field(default_factory=_now)


class ClipEdit(BaseModel):
    id: str
    shot_id: str
    source_take_id: str
    result_take_id: str
    instruction: str
    vace_prompt: str
    preserve: list[str] = Field(default_factory=list)
    t_in_frames: int | None = None
    t_out_frames: int | None = None
    attachment_ids: list[str] = Field(default_factory=list)
    engine: Engine = Engine.VACE_FUN_A14B
    created_at: datetime = Field(default_factory=_now)


class Character(BaseModel):
    id: str
    name: str
    still_asset_ids: list[str] = Field(default_factory=list)
    wardrobe: str = ""
    notes: str = ""


class Bible(BaseModel):
    title: str
    logline: str
    fps: float = 23.976
    width: int = 1920
    height: int = 1080
    color_space: str = "rec709"
    grain: Grain = Grain.FINE
    loudness: LoudnessTarget = LoudnessTarget.BROADCAST
    true_peak_dbtp: float = -1.0
    visual_grammar: str
    world: str
    characters: list[Character] = Field(default_factory=list)
    lut_asset_id: str | None = None
    locked: bool = False
    locked_at: datetime | None = None

    @field_validator("fps")
    @classmethod
    def fps_allowed(cls, v: float) -> float:
        if v not in (23.976, 24.0, 24.000):
            raise ValueError("fps must be 23.976 or 24.000")
        return v

    @field_validator("width")
    @classmethod
    def width_1080p(cls, v: int) -> int:
        if v != 1920:
            raise ValueError("bible width is 1920 for Diony deliverables")
        return v

    @field_validator("height")
    @classmethod
    def height_1080p(cls, v: int) -> int:
        if v != 1080:
            raise ValueError("bible height is 1080 for Diony deliverables")
        return v


class Shot(BaseModel):
    id: str
    scene: str
    slug: str
    duration_frames: int
    fps: float
    size_native: str = "1280x720"
    focal_mm: float
    t_stop: float
    lighting_ratio: str
    camera_move: CameraMove
    prompt: str
    negative_prompt: str = ""
    seed: int
    character_ref_ids: list[str] = Field(default_factory=list)
    keyframe_asset_ids: list[str] = Field(default_factory=list)
    engine: Engine
    finish: FinishIntent
    audio_line: str | None = None
    overlap_frames: int = 8
    max_takes: int = 4
    selected_take_id: str | None = None

    @field_validator("duration_frames")
    @classmethod
    def duration_positive(cls, v: int) -> int:
        if v < 1:
            raise ValueError("duration_frames must be >= 1")
        return v


class QCReport(BaseModel):
    id: str
    take_id: str
    shot_id: str
    verdict: QCVerdict
    identity_ok: bool
    morph_ok: bool
    flicker_ok: bool
    duration_ok: bool
    legal_ok: bool
    lipsync_ok: bool | None = None
    loudness_ok: bool | None = None
    notes: str = ""
    created_at: datetime = Field(default_factory=_now)

    @model_validator(mode="after")
    def fail_means_fail(self) -> QCReport:
        hard = [
            self.identity_ok,
            self.morph_ok,
            self.flicker_ok,
            self.duration_ok,
            self.legal_ok,
        ]
        if self.lipsync_ok is False or self.loudness_ok is False:
            if self.verdict == QCVerdict.PASS:
                raise ValueError("lipsync/loudness fail cannot be pass")
        if not all(hard) and self.verdict == QCVerdict.PASS:
            raise ValueError("hard QC fail cannot be pass")
        return self


class Take(BaseModel):
    id: str
    shot_id: str
    attempt: int
    engine: Engine
    seed: int
    asset_720p_id: str | None = None
    asset_1080p_id: str | None = None
    qc: QCReport | None = None
    created_at: datetime = Field(default_factory=_now)
    parent_take_id: str | None = None
    version: int = 1
    lineage: list[str] = Field(default_factory=list)
    edit_id: str | None = None
    origin: TakeOrigin = TakeOrigin.GENERATION
    label: str = ""
    artifacts: list[Artifact] = Field(default_factory=list)

    @model_validator(mode="after")
    def deliverable_needs_1080(self) -> Take:
        if self.asset_1080p_id and not self.asset_720p_id:
            raise ValueError("1080 finish requires a 720 negative")
        return self


class TimelineEvent(BaseModel):
    shot_id: str
    start_frame: int
    handle_in: int = 0
    handle_out: int = 0
    take_id: str | None = None
    overlap_frames: int = 8
    keyframe_from_take_id: str | None = None


class Timeline(BaseModel):
    id: str
    events: list[TimelineEvent] = Field(default_factory=list)
    picture_lock: PictureLockState = PictureLockState.OPEN
    total_frames: int = 0
    fps: float = 23.976
    preview_path: str = ""
    stitched: bool = False


class MixLock(BaseModel):
    id: str
    state: MixLockState = MixLockState.OPEN
    loudness: LoudnessTarget = LoudnessTarget.BROADCAST
    measured_lufs: float | None = None
    true_peak_dbtp: float | None = None
    stem_asset_ids: list[str] = Field(default_factory=list)
    picture_lock_timeline_id: str | None = None


class Deliverable(BaseModel):
    id: str
    kind: DeliverableKind
    path: str
    sha256: str | None = None
    finish: FinishIntent = FinishIntent.DELIVERABLE

    @model_validator(mode="after")
    def no_previz_master(self) -> Deliverable:
        if self.kind in (DeliverableKind.PRORES_HQ, DeliverableKind.DNXHR_HQ):
            if self.finish != FinishIntent.DELIVERABLE:
                raise ValueError("masters must be finish=deliverable")
        return self


class Gate(BaseModel):
    name: GateName
    open: bool = True
    locked_by: str | None = None
    locked_at: datetime | None = None


class Project(BaseModel):
    id: str
    status: ProjectStatus = ProjectStatus.DRAFT
    customer_kind: CustomerKind = CustomerKind.SERIES
    brief: Brief | None = None
    bible: Bible
    shots: list[Shot] = Field(default_factory=list)
    takes: list[Take] = Field(default_factory=list)
    notes: list[Note] = Field(default_factory=list)
    assets: list[Asset] = Field(default_factory=list)
    edits: list[ClipEdit] = Field(default_factory=list)
    timeline: Timeline | None = None
    mix: MixLock | None = None
    deliverables: list[Deliverable] = Field(default_factory=list)
    gates: list[Gate] = Field(
        default_factory=lambda: [
            Gate(name=GateName.BIBLE),
            Gate(name=GateName.PICTURE),
            Gate(name=GateName.MIX),
            Gate(name=GateName.DISTRIBUTION),
        ]
    )
    fleet_mode: str = "SPLIT"
    compute: ClusterSite = Field(default_factory=ClusterSite)
    created_at: datetime = Field(default_factory=_now)

    def shot(self, shot_id: str) -> Shot:
        for s in self.shots:
            if s.id == shot_id:
                return s
        raise KeyError(shot_id)

    def takes_for(self, shot_id: str) -> list[Take]:
        return [t for t in self.takes if t.shot_id == shot_id]

    def take(self, take_id: str) -> Take:
        for t in self.takes:
            if t.id == take_id:
                return t
        raise KeyError(take_id)

    def asset(self, asset_id: str) -> Asset:
        for a in self.assets:
            if a.id == asset_id:
                return a
        raise KeyError(asset_id)
