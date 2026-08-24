"""First-class voice identity, performance script, and clip-package contracts."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from diony_contracts.enums import VoiceProfileSource, VoiceRenderState


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _content_sha256(model: BaseModel, *, exclude: set[str]) -> str:
    payload = model.model_dump(mode="json", exclude=exclude)
    blob = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()


class VoiceReferenceSample(BaseModel):
    model_config = ConfigDict(frozen=True)

    asset_id: str
    duration_seconds: float = Field(ge=0.1, le=30.0)
    emotion: str = "neutral"
    transcript: str = ""
    embedding: tuple[float, ...] = Field(min_length=2, max_length=4096)
    embedding_model: str
    sha256: str = Field(min_length=64, max_length=64)


class ProsodySignature(BaseModel):
    model_config = ConfigDict(frozen=True)

    pitch_floor_hz: float = Field(default=90.0, gt=20, lt=1000)
    pitch_ceiling_hz: float = Field(default=300.0, gt=20, lt=1500)
    speaking_rate_syllables_per_second: float = Field(default=4.0, gt=0.5, lt=12)
    breath_interval_seconds: float = Field(default=5.0, gt=0.5, lt=30)
    vocal_fry: float = Field(default=0.0, ge=0, le=1)
    pause_bias: float = Field(default=0.5, ge=0, le=1)
    breath_texture: str = "natural, controlled"

    @model_validator(mode="after")
    def pitch_range_is_ordered(self) -> ProsodySignature:
        if self.pitch_ceiling_hz <= self.pitch_floor_hz:
            raise ValueError("pitch ceiling must exceed pitch floor")
        return self


class EmotionPerformance(BaseModel):
    model_config = ConfigDict(frozen=True)

    emotion: str
    intensity_curve: tuple[float, ...] = Field(default_factory=lambda: (0.25, 0.35, 0.25))
    rate_multiplier: float = Field(default=1.0, ge=0.5, le=1.8)
    pitch_shift_semitones: float = Field(default=0.0, ge=-12, le=12)
    timbre: str = "profile-locked"

    @field_validator("intensity_curve")
    @classmethod
    def intensity_is_normalized(cls, value: tuple[float, ...]) -> tuple[float, ...]:
        if not value or any(point < 0 or point > 1 for point in value):
            raise ValueError("emotion intensity curve values must be between 0 and 1")
        return value


class VoiceProfile(BaseModel):
    """One immutable, project-scoped voice identity revision."""

    model_config = ConfigDict(frozen=True)

    id: str
    project_id: str
    character_id: str
    identity_lineage_id: str = ""
    version: int = Field(default=1, ge=1)
    parent_profile_id: str | None = None
    source: VoiceProfileSource
    reference_samples: tuple[VoiceReferenceSample, ...] = Field(default_factory=tuple, max_length=16)
    speaker_embedding_model: str
    speaker_embedding: tuple[float, ...] = Field(min_length=2, max_length=4096)
    prosody: ProsodySignature
    emotion_map: tuple[EmotionPerformance, ...] = Field(min_length=1, max_length=32)
    age_descriptor: str = ""
    gender_descriptor: str = ""
    accent_descriptor: str = ""
    texture_descriptor: str = ""
    seed: int
    style_lock: str
    locked: bool = False
    locked_at: datetime | None = None
    content_sha256: str = ""
    created_at: datetime = Field(default_factory=_now)

    @model_validator(mode="after")
    def validate_lock_and_identity(self) -> VoiceProfile:
        if not self.project_id.strip() or not self.character_id.strip():
            raise ValueError("voice profile must be project and character scoped")
        if not self.style_lock.strip():
            raise ValueError("voice profile requires a deterministic style lock")
        reference_seconds = sum(sample.duration_seconds for sample in self.reference_samples)
        if self.source in {VoiceProfileSource.ZERO_SHOT, VoiceProfileSource.FEW_SHOT}:
            if not 5.0 <= reference_seconds <= 30.0:
                raise ValueError("zero/few-shot voice reference must total 5 to 30 seconds")
        elif self.reference_samples:
            raise ValueError("synthetic voice profiles do not accept reference samples")
        if self.locked and self.locked_at is None:
            raise ValueError("locked voice profile requires locked_at")
        if not self.identity_lineage_id:
            identity_blob = json.dumps(
                {
                    "model": self.speaker_embedding_model,
                    "embedding": self.speaker_embedding,
                    "style_lock": self.style_lock.split(":")[-1],
                },
                separators=(",", ":"),
                sort_keys=True,
            ).encode("utf-8")
            object.__setattr__(
                self,
                "identity_lineage_id",
                f"voice-identity-{hashlib.sha256(identity_blob).hexdigest()[:20]}",
            )
        expected = _content_sha256(self, exclude={"content_sha256", "created_at"})
        if self.content_sha256 and self.content_sha256 != expected:
            raise ValueError("voice profile content hash does not match its locked fields")
        if not self.content_sha256:
            object.__setattr__(self, "content_sha256", expected)
        return self


class DialogueTurn(BaseModel):
    id: str
    character_id: str
    text: str
    emotion: str = "neutral"
    intensity_curve: list[float] = Field(default_factory=lambda: [0.25, 0.4, 0.3])
    subtext: str = ""
    start_ms: int = Field(default=0, ge=0)
    target_duration_ms: int = Field(default=0, ge=0)
    overlap_ms: int = Field(default=0, ge=0)
    interrupts_turn_id: str | None = None
    pronunciation: dict[str, str] = Field(default_factory=dict)

    @field_validator("text")
    @classmethod
    def spoken_text_is_present(cls, value: str) -> str:
        clean = " ".join(value.split())
        if not clean:
            raise ValueError("dialogue turn requires spoken text")
        return clean

    @field_validator("intensity_curve")
    @classmethod
    def turn_intensity_is_normalized(cls, value: list[float]) -> list[float]:
        if not value or any(point < 0 or point > 1 for point in value):
            raise ValueError("dialogue intensity curve values must be between 0 and 1")
        return value


class PerformanceScriptLine(BaseModel):
    id: str
    shot_id: str
    character_id: str
    voice_profile_id: str
    text: str
    spoken_text: str
    emotion: str = "neutral"
    intensity_curve: list[float] = Field(default_factory=lambda: [0.25, 0.4, 0.3])
    phonetic_guidance: dict[str, str] = Field(default_factory=dict)
    breath_after_word_indices: list[int] = Field(default_factory=list)
    stress_word_indices: list[int] = Field(default_factory=list)
    micro_pauses_ms: list[int] = Field(default_factory=list)
    start_ms: int = Field(default=0, ge=0)
    target_duration_ms: int = Field(gt=0)
    overlap_ms: int = Field(default=0, ge=0)
    interrupts_line_id: str | None = None
    subtext: str = ""


class PerformanceScript(BaseModel):
    id: str
    project_id: str
    shot_id: str
    version: int = Field(default=1, ge=1)
    narrative_context: str = ""
    camera_language: str = ""
    pacing_target_ms: int = Field(default=0, ge=0)
    lines: list[PerformanceScriptLine] = Field(min_length=1, max_length=32)
    content_sha256: str = ""
    created_at: datetime = Field(default_factory=_now)

    @model_validator(mode="after")
    def address_script_content(self) -> PerformanceScript:
        if any(line.shot_id != self.shot_id for line in self.lines):
            raise ValueError("performance script lines must belong to the same shot")
        expected = _content_sha256(self, exclude={"content_sha256", "created_at"})
        if self.content_sha256 and self.content_sha256 != expected:
            raise ValueError("performance script content hash does not match")
        self.content_sha256 = expected
        return self


class VoiceQCReceipt(BaseModel):
    measurement_version: str
    speaker_similarity: float = Field(ge=-1, le=1)
    speaker_similarity_min: float = Field(default=0.92, ge=-1, le=1)
    project_consistency: float = Field(ge=-1, le=1)
    project_consistency_min: float = Field(default=0.90, ge=-1, le=1)
    snr_db: float
    snr_db_min: float = 30.0
    integrated_lufs: float
    loudness_target_lufs: float
    loudness_tolerance_lu: float = Field(default=1.0, ge=0, le=3)
    true_peak_dbtp: float
    true_peak_ceiling_dbtp: float = -1.0
    harshness_score: float = Field(default=0.0, ge=0, le=1)
    harshness_max: float = Field(default=0.25, ge=0, le=1)
    sibilance_score: float = Field(default=0.0, ge=0, le=1)
    sibilance_max: float = Field(default=0.25, ge=0, le=1)
    lip_sync_confidence: float | None = Field(default=None, ge=0, le=1)
    lip_sync_confidence_min: float = Field(default=0.90, ge=0, le=1)
    audiovisual_offset_ms_p95: float | None = Field(default=None, ge=0)
    audiovisual_offset_ms_max: float = Field(default=40.0, gt=0, le=100)
    performance_consistency: float | None = Field(default=None, ge=0, le=1)
    performance_consistency_min: float = Field(default=0.90, ge=0, le=1)
    audio_passed: bool = False
    animation_passed: bool = False
    passed: bool = False
    diagnostics: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def passing_receipt_meets_every_threshold(self) -> VoiceQCReceipt:
        audio_checks = (
            (self.speaker_similarity >= self.speaker_similarity_min, "speaker identity"),
            (self.project_consistency >= self.project_consistency_min, "project consistency"),
            (self.snr_db >= self.snr_db_min, "signal-to-noise"),
            (
                abs(self.integrated_lufs - self.loudness_target_lufs)
                <= self.loudness_tolerance_lu,
                "loudness",
            ),
            (self.true_peak_dbtp <= self.true_peak_ceiling_dbtp, "true peak"),
            (self.harshness_score <= self.harshness_max, "harshness"),
            (self.sibilance_score <= self.sibilance_max, "sibilance"),
        )
        if self.audio_passed:
            failed = next((label for ok, label in audio_checks if not ok), None)
            if failed:
                raise ValueError(f"audio cannot pass failed {failed} threshold")
        if self.animation_passed:
            if self.lip_sync_confidence is None or (
                self.lip_sync_confidence < self.lip_sync_confidence_min
            ):
                raise ValueError("animation cannot pass failed lip-sync confidence threshold")
            if self.audiovisual_offset_ms_p95 is None or (
                self.audiovisual_offset_ms_p95 > self.audiovisual_offset_ms_max
            ):
                raise ValueError("animation cannot pass failed audiovisual offset threshold")
            if self.performance_consistency is None or (
                self.performance_consistency < self.performance_consistency_min
            ):
                raise ValueError(
                    "animation cannot pass failed physical-performance consistency threshold"
                )
        if self.passed and not (self.audio_passed and self.animation_passed):
            raise ValueError("voice QC pass requires both audio and animation evidence")
        return self


class VoiceClipPackage(BaseModel):
    id: str
    project_id: str
    shot_id: str
    take_id: str
    voice_profile_ids: list[str] = Field(min_length=1)
    generation_seeds: dict[str, int]
    script: PerformanceScript
    clean_dialogue_asset_id: str
    final_mix_asset_id: str
    performance_metadata_asset_id: str
    cache_key: str = Field(min_length=64, max_length=64)
    synthesis_backend: str
    embedding_backend: str
    alignment_backend: str
    video_sha256: str = Field(default="", pattern=r"^(?:|[0-9a-f]{64})$")
    state: VoiceRenderState = VoiceRenderState.PLANNED
    qc: VoiceQCReceipt
    created_at: datetime = Field(default_factory=_now)

    @model_validator(mode="after")
    def verified_package_has_passing_receipt(self) -> VoiceClipPackage:
        if self.project_id != self.script.project_id or self.shot_id != self.script.shot_id:
            raise ValueError("voice package script lineage does not match the clip")
        if set(self.voice_profile_ids) != set(self.generation_seeds):
            raise ValueError("every voice profile requires its locked generation seed")
        if self.state == VoiceRenderState.VERIFIED and not self.qc.passed:
            raise ValueError("verified voice package requires passing audiovisual QC")
        if self.state == VoiceRenderState.VERIFIED and not self.video_sha256:
            raise ValueError("verified voice package requires the exact rendered video hash")
        return self
