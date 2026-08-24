from __future__ import annotations

from datetime import datetime, timezone
from pydantic import BaseModel, Field, field_validator, model_validator

from diony_contracts.enums import (
    ArtifactKind,
    AssetKind,
    CameraMove,
    ContinuityMode,
    ComputePath,
    CustomerKind,
    DeliverableKind,
    Engine,
    FinishIntent,
    GateName,
    GalleryCompositionStatus,
    GalleryTransition,
    Grain,
    LoudnessTarget,
    MixLockState,
    NoteAuthor,
    PictureLockState,
    ProductionKind,
    ProductionStopDisposition,
    ProjectStatus,
    QCVerdict,
    ReferenceLock,
    TakeOrigin,
)
from diony_contracts.voice import (
    DialogueTurn,
    EmotionPerformance,
    PerformanceScript,
    PerformanceScriptLine,
    ProsodySignature,
    VoiceClipPackage,
    VoiceProfile,
    VoiceQCReceipt,
    VoiceReferenceSample,
)


def _now() -> datetime:
    return datetime.now(timezone.utc)


class ClusterSite(BaseModel):
    """Where Diony compute lives. Local Quad-Spark is the SKU. Dual-Spark, B300, and B200 are signed lease exceptions."""

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
        if self.path == ComputePath.B200:
            if len(self.exception_by.strip()) < 2:
                raise ValueError("leased B200 requires a named human exception")
            if self.profile != "b200-v1":
                raise ValueError("B200 profile must be b200-v1")
            if not self.gpu_class.strip():
                self.gpu_class = "1×B200"
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
    production_kind: ProductionKind = ProductionKind.PROGRAM
    sample_seconds: int | None = None
    audience: str = ""
    title_hint: str = ""
    attachment_ids: list[str] = Field(default_factory=list)

    @field_validator("target_minutes")
    @classmethod
    def minutes_range(cls, v: float) -> float:
        if v < (5 / 60) or v > 60:
            raise ValueError("target_minutes must be between 5 seconds and 60 minutes")
        return v

    @model_validator(mode="after")
    def validate_intake(self) -> Brief:
        if len(self.idea.strip()) < 8 and not self.attachment_ids:
            raise ValueError("need an idea in words (8+ characters) or at least one reference file")
        if self.production_kind == ProductionKind.SAMPLE:
            seconds = self.sample_seconds if self.sample_seconds is not None else 5
            if seconds < 5 or seconds > 10:
                raise ValueError("sample_seconds must be between 5 and 10 seconds")
            self.sample_seconds = seconds
            self.target_minutes = seconds / 60
        elif self.target_minutes < 0.5:
            raise ValueError("program target_minutes must be between 0.5 and 60")
        elif self.sample_seconds is not None:
            raise ValueError("sample_seconds is only valid for a sample")
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
    storage_key: str = ""
    thumbnail_path: str = ""
    proxy_path: str = ""
    source_job_id: str | None = None
    source_asset_ids: list[str] = Field(default_factory=list)
    metadata: dict[str, str | int | float | bool | None] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=_now)
    deleted_at: datetime | None = None


class Artifact(BaseModel):
    id: str
    take_id: str
    kind: ArtifactKind
    path: str
    project_id: str = ""
    shot_id: str = ""
    sha256: str = ""
    source_sha256: str = ""
    prompt_sha256: str = ""
    reference_asset_ids: list[str] = Field(default_factory=list)
    reference_sha256: str = ""
    continuity_from_take_id: str | None = None
    continuity_sha256: str = ""
    generation_task: str = ""
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


class ClipJoinSource(BaseModel):
    """One immutable Gallery clip reference with frame-authoritative trims."""

    clip_id: str
    project_id: str
    title: str
    path: str
    duration_frames: int = Field(ge=2)
    trim_in_frame: int = Field(default=0, ge=0)
    trim_out_frame: int = Field(ge=1)
    fps: float = Field(gt=0)
    width: int = Field(gt=0)
    height: int = Field(gt=0)
    codec: str = "unknown"
    normalized: bool = False
    normalization_note: str = ""

    @property
    def retained_frames(self) -> int:
        return self.trim_out_frame - self.trim_in_frame

    @model_validator(mode="after")
    def trims_retain_picture(self) -> ClipJoinSource:
        if self.trim_out_frame > self.duration_frames:
            raise ValueError("Trim out cannot exceed the source duration")
        if self.retained_frames < 2:
            raise ValueError("A joined clip must retain at least two frames")
        return self


class ClipJoinPoint(BaseModel):
    left_clip_id: str
    right_clip_id: str
    transition: GalleryTransition = GalleryTransition.CUT
    duration_frames: int = Field(default=0, ge=0, le=120)

    @model_validator(mode="after")
    def transition_has_sensible_duration(self) -> ClipJoinPoint:
        if self.transition == GalleryTransition.CUT and self.duration_frames:
            raise ValueError("Clean cuts use zero transition frames")
        if self.transition != GalleryTransition.CUT and self.duration_frames < 2:
            raise ValueError("Transitions use at least two frames")
        return self


class ClipComposition(BaseModel):
    """Non-destructive Gallery composition and exportable source lineage."""

    id: str
    project_id: str
    version: int = Field(default=1, ge=1)
    parent_composition_id: str | None = None
    sources: list[ClipJoinSource] = Field(min_length=2, max_length=32)
    junctions: list[ClipJoinPoint] = Field(min_length=1, max_length=31)
    source_asset_ids: list[str] = Field(default_factory=list)
    asset_id: str = ""
    width: int = 1920
    height: int = 1080
    fps: float = 23.976
    color_space: str = "rec709"
    total_frames: int = 0
    profile_name: str = ""
    cost_center_ids: list[str] = Field(default_factory=list)
    status: GalleryCompositionStatus = GalleryCompositionStatus.DRAFT
    output_path: str = ""
    proxy_path: str = ""
    preview_path: str = ""
    manifest_path: str = ""
    error: str = ""
    created_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)

    @field_validator("width")
    @classmethod
    def composition_width_is_1080p(cls, value: int) -> int:
        if value != 1920:
            raise ValueError("Gallery compositions output at 1920×1080")
        return value

    @field_validator("height")
    @classmethod
    def composition_height_is_1080p(cls, value: int) -> int:
        if value != 1080:
            raise ValueError("Gallery compositions output at 1920×1080")
        return value

    @field_validator("fps")
    @classmethod
    def composition_fps_is_studio_rate(cls, value: float) -> float:
        if value not in (23.976, 24.0, 24.000):
            raise ValueError("Gallery compositions use 23.976 or 24.000 fps")
        return value

    @model_validator(mode="after")
    def junctions_follow_source_order(self) -> ClipComposition:
        if len(self.junctions) != len(self.sources) - 1:
            raise ValueError("A composition needs one junction between every pair of clips")
        for index, junction in enumerate(self.junctions):
            left = self.sources[index]
            right = self.sources[index + 1]
            if (junction.left_clip_id, junction.right_clip_id) != (
                left.clip_id,
                right.clip_id,
            ):
                raise ValueError("Join points must connect consecutive clips in sequence")
            if junction.duration_frames > min(left.retained_frames, right.retained_frames) - 2:
                raise ValueError("A transition cannot be longer than the available trimmed media")
        expected_total = sum(source.retained_frames for source in self.sources) - sum(
            junction.duration_frames for junction in self.junctions
        )
        if expected_total < 2:
            raise ValueError("The joined version must retain at least two frames")
        self.total_frames = expected_total
        if not self.source_asset_ids:
            self.source_asset_ids = [source.clip_id for source in self.sources]
        return self


class Character(BaseModel):
    id: str
    name: str
    still_asset_ids: list[str] = Field(default_factory=list)
    wardrobe: str = ""
    notes: str = ""
    voice_profile_id: str = ""


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


class WanPromptPlan(BaseModel):
    """Typed, inspectable Wan2.2 prompt layers compiled from the Idea."""

    strategy_version: str = "wan22-five-layer-v3"
    source_idea_sha256: str = Field(min_length=64, max_length=64)
    subject: str
    identity_anchors: list[str] = Field(default_factory=list)
    motion: str
    camera: str
    scene: str
    environment_dynamics: str = ""
    high_noise_plan: str = ""
    low_noise_plan: str = ""
    generation_method: str = ""
    audio_strategy: str = "silent_plate"
    quality_lora_ids: list[str] = Field(default_factory=list)
    continuity_risks: list[str] = Field(default_factory=list)
    negative_terms: list[str] = Field(default_factory=list)
    word_count: int = Field(default=0, ge=0)
    diagnostics: list[str] = Field(default_factory=list)


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
    prompt_plan: WanPromptPlan | None = None
    seed: int
    character_ref_ids: list[str] = Field(default_factory=list)
    keyframe_asset_ids: list[str] = Field(default_factory=list)
    audio_asset_ids: list[str] = Field(default_factory=list)
    motion_reference_asset_ids: list[str] = Field(default_factory=list)
    reference_lock: ReferenceLock = ReferenceLock.NONE
    continuity_mode: ContinuityMode = ContinuityMode.INDEPENDENT
    engine: Engine
    finish: FinishIntent
    audio_line: str | None = None
    dialogue_turns: list[DialogueTurn] = Field(default_factory=list, max_length=32)
    overlap_frames: int = 8
    max_takes: int = 4
    selected_take_id: str | None = None

    @field_validator("duration_frames")
    @classmethod
    def duration_positive(cls, v: int) -> int:
        if v < 1:
            raise ValueError("duration_frames must be >= 1")
        return v

    @model_validator(mode="after")
    def locked_reference_is_conditioned(self) -> Shot:
        if self.reference_lock != ReferenceLock.NONE:
            if not self.character_ref_ids or not self.keyframe_asset_ids:
                raise ValueError("reference lock requires character and keyframe references")
            if self.engine not in {
                Engine.I2V_A14B,
                Engine.S2V_14B,
                Engine.ANIMATE_14B,
                Engine.VACE_FUN_A14B,
            }:
                raise ValueError("reference-locked shots cannot use unconditioned T2V")
        if self.continuity_mode == ContinuityMode.PREVIOUS_FRAME and self.overlap_frames < 1:
            raise ValueError("previous-frame continuity requires an overlap window")
        if self.engine == Engine.S2V_14B:
            if (
                not self.keyframe_asset_ids
                and self.continuity_mode != ContinuityMode.PREVIOUS_FRAME
            ) or not self.audio_asset_ids:
                raise ValueError("S2V requires a reference image or prior frame, plus locked audio")
        if self.engine == Engine.ANIMATE_14B:
            if not self.keyframe_asset_ids or not self.motion_reference_asset_ids:
                raise ValueError("Animate requires a character image and driving video")
        return self


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
    motion_ok: bool | None = None
    camera_ok: bool | None = None
    lighting_ok: bool | None = None
    production_clean_ok: bool | None = None
    audio_ready_ok: bool | None = None
    voice_ready_ok: bool | None = None
    lipsync_ok: bool | None = None
    loudness_ok: bool | None = None
    voice_measurement_version: str = ""
    speaker_similarity: float | None = Field(default=None, ge=-1, le=1)
    voice_project_consistency: float | None = Field(default=None, ge=-1, le=1)
    voice_performance_consistency: float | None = Field(default=None, ge=0, le=1)
    lip_sync_confidence: float | None = Field(default=None, ge=0, le=1)
    audiovisual_offset_ms_p95: float | None = Field(default=None, ge=0)
    reference_asset_ids: list[str] = Field(default_factory=list)
    reference_similarity: float | None = None
    reference_similarities: dict[str, float] = Field(default_factory=dict)
    reference_verified: bool | None = None
    continuity_from_take_id: str | None = None
    continuity_similarity: float | None = None
    continuity_verified: bool | None = None
    measurement_version: str = ""
    temporal_delta_p95: float | None = Field(default=None, ge=0)
    temporal_residual_p95: float | None = Field(default=None, ge=0)
    flicker_luma_p95: float | None = Field(default=None, ge=0)
    measured_frame_count: int = Field(default=0, ge=0)
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
        hard.extend(
            value
            for value in (
                self.motion_ok,
                self.camera_ok,
                self.lighting_ok,
                self.production_clean_ok,
                self.audio_ready_ok,
                self.voice_ready_ok,
            )
            if value is not None
        )
        if self.lipsync_ok is False or self.loudness_ok is False:
            if self.verdict == QCVerdict.PASS:
                raise ValueError("lipsync/loudness fail cannot be pass")
        if self.reference_asset_ids and self.reference_verified is not True:
            if self.verdict == QCVerdict.PASS:
                raise ValueError("unverified reference identity cannot be pass")
        if self.continuity_from_take_id and self.continuity_verified is not True:
            if self.verdict == QCVerdict.PASS:
                raise ValueError("unverified temporal continuity cannot be pass")
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
    voice_package: VoiceClipPackage | None = None

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
    picture_sha256: str = ""
    program_sha256: str = ""
    source_artifact_sha256s: list[str] = Field(default_factory=list)
    stitched: bool = False
    gallery_removed_at: datetime | None = None


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


class ProductionStopReceipt(BaseModel):
    """Durable user decision for a manually stopped Production run."""

    disposition: ProductionStopDisposition
    stopped_at: datetime = Field(default_factory=_now)
    completed_take_ids: list[str] = Field(default_factory=list)
    result_path: str = ""
    result_sha256: str = ""
    result_width: int = Field(default=0, ge=0)
    result_height: int = Field(default=0, ge=0)
    deleted_file_count: int = Field(default=0, ge=0)
    result_error: str = ""

    @model_validator(mode="after")
    def delete_has_no_retained_result(self) -> ProductionStopReceipt:
        if self.disposition == ProductionStopDisposition.DELETE and (
            self.result_path or self.result_sha256 or self.result_width or self.result_height
        ):
            raise ValueError("delete stop cannot retain an incomplete result")
        if self.result_path and (
            len(self.result_sha256) != 64 or self.result_width < 1 or self.result_height < 1
        ):
            raise ValueError("incomplete result requires checksum and raster")
        return self


class ProductionRestartReceipt(BaseModel):
    """Audit receipt for a new immutable take set on the same stopped Project."""

    restarted_at: datetime = Field(default_factory=_now)
    reason: str = "reference_and_performance_recalibration"
    previous_project_version: int = Field(ge=1)
    new_project_version: int = Field(ge=2)
    previous_stop: ProductionStopReceipt
    preserved_take_ids: list[str] = Field(default_factory=list)
    replacement_take_ids: list[str] = Field(default_factory=list)
    prompt_strategy_version: str
    reference_asset_ids: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def version_advances(self) -> ProductionRestartReceipt:
        if self.new_project_version <= self.previous_project_version:
            raise ValueError("production restart must advance the Project version")
        if not self.reason.strip():
            raise ValueError("production restart requires a reason")
        if not self.replacement_take_ids:
            raise ValueError("production restart requires replacement Takes")
        return self


class ProductionPauseReceipt(BaseModel):
    """Durable drain checkpoint that holds dispatch after one active Take."""

    requested_at: datetime = Field(default_factory=_now)
    reason: str
    after_take_id: str
    remaining_take_ids: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def pause_has_boundary(self) -> ProductionPauseReceipt:
        if not self.reason.strip():
            raise ValueError("production pause requires a reason")
        if not self.after_take_id.strip():
            raise ValueError("production pause requires an active Take boundary")
        if self.after_take_id in self.remaining_take_ids:
            raise ValueError("active Take cannot also be a remaining Take")
        return self


class ProductionInfrastructureRecovery(BaseModel):
    """Audit receipt for resuming the same Takes after a service interruption."""

    recovered_at: datetime = Field(default_factory=_now)
    reason: str
    interrupted_stop: ProductionStopReceipt
    resumed_take_ids: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def recovery_reuses_takes(self) -> ProductionInfrastructureRecovery:
        if not self.reason.strip():
            raise ValueError("infrastructure recovery requires a reason")
        if not self.resumed_take_ids:
            raise ValueError("infrastructure recovery requires existing Takes")
        return self


class Project(BaseModel):
    schema_version: int = 1
    id: str
    workspace_id: str = "local-workspace"
    owner_user_id: str = "local-user"
    version: int = 1
    status: ProjectStatus = ProjectStatus.DRAFT
    customer_kind: CustomerKind = CustomerKind.SERIES
    brief: Brief | None = None
    bible: Bible
    shots: list[Shot] = Field(default_factory=list)
    takes: list[Take] = Field(default_factory=list)
    notes: list[Note] = Field(default_factory=list)
    assets: list[Asset] = Field(default_factory=list)
    edits: list[ClipEdit] = Field(default_factory=list)
    compositions: list[ClipComposition] = Field(default_factory=list)
    voice_profiles: list[VoiceProfile] = Field(default_factory=list)
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
    updated_at: datetime = Field(default_factory=_now)
    deleted_at: datetime | None = None
    production_started_at: datetime | None = None
    production_ended_at: datetime | None = None
    production_stop: ProductionStopReceipt | None = None
    production_restarts: list[ProductionRestartReceipt] = Field(default_factory=list)
    production_pause: ProductionPauseReceipt | None = None
    production_recoveries: list[ProductionInfrastructureRecovery] = Field(default_factory=list)

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

    def voice_profile(self, profile_id: str) -> VoiceProfile:
        for profile in self.voice_profiles:
            if profile.id == profile_id:
                return profile
        raise KeyError(profile_id)

    def mark_production_start(self) -> None:
        """First Produce/roll. Later rolls and retakes keep the same start."""
        if self.production_started_at is None:
            self.production_started_at = _now()
        self.production_ended_at = None

    def mark_production_end(self) -> None:
        """Freeze the clock when mix locks, or when the farm stops failed."""
        if self.production_started_at is None:
            self.production_started_at = _now()
        if self.production_ended_at is None:
            self.production_ended_at = _now()
