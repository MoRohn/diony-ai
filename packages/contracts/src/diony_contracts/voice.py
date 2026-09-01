"""Voice contracts for the Wan2.2-native spoken lane.

Diony no longer synthesizes dialogue itself. A spoken Shot hands Wan2.2 S2V the
authored line and a per-character prompt clip (``--enable_tts``,
``--tts_prompt_audio``, ``--tts_prompt_text``, ``--tts_text``); Wan performs the
line and animates the face in one pass and muxes its own speech into the
negative. Producer records what it asked for (:class:`WanSpeechRequest`),
measures the audio Wan delivered (:class:`SpeechQCReceipt`), and binds both to
the rendered negative (:class:`SpeechClipPackage`) so Mix can assemble verified
per-clip dialogue in timeline order.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from diony_contracts.enums import VoiceCastingSource, VoiceRenderState

SPEECH_BACKEND = "wan2.2-s2v-native-tts"
SPEECH_REQUEST_VERSION = "wan22-s2v-native-tts-v1"
# Upstream generate.py: the prompt clip "must be greater than 16khz, and between 5s to 15s".
PROMPT_CLIP_MIN_SECONDS = 5.0
PROMPT_CLIP_MAX_SECONDS = 15.0
PROMPT_CLIP_MIN_SAMPLE_RATE = 16_000
# One Wan S2V frame at its native 16 fps. Speech that is still active inside
# the last frame of the window was cut by the picture, not finished.
SPEECH_TAIL_MIN_MS = 62.5
# Voiced-speech presence is the voiced fraction of the whole picture window,
# so its floor must follow the line: a one-word "QUACK!" in a 3.5 s window can
# voice at most ~0.14 of it (B200 2026-08-28: measured 0.108 on four identical
# renders against a fixed 0.20 floor, 57 minutes of futile retakes). Expected
# speech is ~350 ms per word plus 250 ms of attack; the floor is half of that
# share of the window, never below 0.04 (some speech must exist) and never
# above the long-line floor of 0.20.
SPEECH_PRESENCE_FLOOR_MAX = 0.20
SPEECH_PRESENCE_FLOOR_MIN = 0.04
SPEECH_MS_PER_WORD = 350.0
SPEECH_ATTACK_MS = 250.0


def expected_speech_ms(word_count: int) -> float:
    return max(0, int(word_count)) * SPEECH_MS_PER_WORD + SPEECH_ATTACK_MS


def presence_floor_for(word_count: int, window_ms: float) -> float:
    """The voiced-presence floor for a line of ``word_count`` words in ``window_ms`` of picture."""
    if window_ms <= 0:
        return SPEECH_PRESENCE_FLOOR_MAX
    share = expected_speech_ms(word_count) / float(window_ms)
    return round(min(SPEECH_PRESENCE_FLOOR_MAX, max(SPEECH_PRESENCE_FLOOR_MIN, 0.5 * share)), 4)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _content_sha256(model: BaseModel, *, exclude: set[str]) -> str:
    payload = model.model_dump(mode="json", exclude=exclude)
    blob = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()


class DialogueTurn(BaseModel):
    """One authored line for one character inside a Shot window."""

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


class VoiceCasting(BaseModel):
    """Which prompt clip Wan2.2 clones for one character. Project-scoped, immutable once locked."""

    model_config = ConfigDict(frozen=True)

    id: str
    project_id: str
    character_id: str
    source: VoiceCastingSource
    # Library casting: slot in the site's casting library (Bible roster order).
    cast_index: int = Field(default=0, ge=0)
    # Reference casting: a local audio Asset on this Project and its exact transcript.
    prompt_audio_asset_id: str = ""
    prompt_audio_sha256: str = Field(default="", pattern=r"^(?:|[0-9a-f]{64})$")
    prompt_duration_seconds: float | None = Field(default=None, gt=0)
    # Exact transcript of the prompt clip (zero-shot cloning). Empty means Wan
    # falls back to cross-lingual cloning, which needs no transcript.
    prompt_text: str = ""
    descriptors: dict[str, str] = Field(default_factory=dict)
    locked: bool = False
    locked_at: datetime | None = None
    content_sha256: str = ""
    created_at: datetime = Field(default_factory=_now)

    @model_validator(mode="after")
    def validate_source_and_lock(self) -> VoiceCasting:
        if not self.project_id.strip() or not self.character_id.strip():
            raise ValueError("voice casting must be project and character scoped")
        if self.source == VoiceCastingSource.REFERENCE:
            if not self.prompt_audio_asset_id.strip() or not self.prompt_audio_sha256:
                raise ValueError("reference casting requires a local prompt audio asset and its sha256")
            if self.prompt_duration_seconds is None or not (
                PROMPT_CLIP_MIN_SECONDS <= self.prompt_duration_seconds <= PROMPT_CLIP_MAX_SECONDS
            ):
                raise ValueError(
                    f"Wan2.2 S2V prompt clips must run {PROMPT_CLIP_MIN_SECONDS:g}–{PROMPT_CLIP_MAX_SECONDS:g} seconds"
                )
        elif self.prompt_audio_asset_id:
            raise ValueError("library casting does not carry a reference asset")
        if self.locked and self.locked_at is None:
            raise ValueError("locked voice casting requires locked_at")
        expected = _content_sha256(self, exclude={"content_sha256", "created_at"})
        if self.content_sha256 and self.content_sha256 != expected:
            raise ValueError("voice casting content hash does not match its locked fields")
        if not self.content_sha256:
            object.__setattr__(self, "content_sha256", expected)
        return self


class WanSpeechRequest(BaseModel):
    """Exactly what Producer hands Wan2.2 S2V's native TTS lane for one Take."""

    model_config = ConfigDict(frozen=True)

    version: str = SPEECH_REQUEST_VERSION
    character_id: str
    casting_id: str
    tts_text: str
    # Resolved on the GPU host at dispatch; empty until then.
    tts_prompt_audio_sha256: str = Field(default="", pattern=r"^(?:|[0-9a-f]{64})$")
    tts_prompt_text: str = ""
    prompt_source: str = ""
    word_count: int = Field(default=0, ge=0)
    window_ms: int = Field(gt=0)
    performance_direction: str = ""
    content_sha256: str = ""

    @field_validator("tts_text")
    @classmethod
    def line_has_words(cls, value: str) -> str:
        clean = " ".join(value.split())
        if not clean:
            raise ValueError("a Wan speech request needs spoken text")
        return clean

    @model_validator(mode="after")
    def address_request(self) -> WanSpeechRequest:
        if not self.character_id.strip() or not self.casting_id.strip():
            raise ValueError("a Wan speech request names its character and casting")
        if self.word_count == 0:
            object.__setattr__(self, "word_count", len(self.tts_text.split()))
        expected = _content_sha256(self, exclude={"content_sha256"})
        if self.content_sha256 and self.content_sha256 != expected:
            raise ValueError("speech request content hash does not match")
        if not self.content_sha256:
            object.__setattr__(self, "content_sha256", expected)
        return self

    @property
    def words_per_second(self) -> float:
        return round(self.word_count / max(0.001, self.window_ms / 1000.0), 3)


class SpeechQCReceipt(BaseModel):
    """What the audio Wan2.2 delivered inside the negative measured."""

    measurement_version: str
    audio_present: bool = False
    duration_ms: float = Field(default=0.0, ge=0)
    window_ms: float = Field(default=0.0, ge=0)
    speech_presence_score: float | None = Field(default=None, ge=0, le=1)
    speech_presence_min: float = Field(default=SPEECH_PRESENCE_FLOOR_MAX, ge=0, le=1)
    # The line the floor was set for (see ``presence_floor_for``).
    line_word_count: int = Field(default=0, ge=0)
    expected_speech_ms: float = Field(default=0.0, ge=0)
    # Fingerprints of what was measured: the delivered dialogue audio and the
    # speech request that produced it. Wan's native TTS is deterministic for
    # one request, so a retake that carries the same request measures the same
    # audio; the farm settles instead of re-rolling the picture.
    dialogue_sha256: str = Field(default="", pattern=r"^(?:|[0-9a-f]{64})$")
    request_sha256: str = ""
    # Milliseconds between the last speech-active moment and the end of the
    # picture window. Below one S2V frame the line was cut, not finished.
    speech_tail_ms: float | None = Field(default=None, ge=0)
    speech_tail_min_ms: float = Field(default=SPEECH_TAIL_MIN_MS, ge=0)
    integrated_lufs: float | None = None
    loudness_target_lufs: float = -24.0
    loudness_tolerance_lu: float = Field(default=1.0, ge=0, le=3)
    true_peak_dbtp: float | None = None
    true_peak_ceiling_dbtp: float = -1.0
    # Optional local transcript of the delivered speech (faster-whisper when a
    # model is installed on the host). Word accuracy is the fraction of the
    # authored line's words heard back; unmeasured stays None and gates nothing.
    transcript: str = ""
    transcript_word_accuracy: float | None = Field(default=None, ge=0, le=1)
    transcript_last_word_ok: bool | None = None
    transcript_min_accuracy: float = Field(default=0.6, ge=0, le=1)
    # Word accuracy is a gate only on lines long enough to measure it: short
    # or onomatopoeic lines ("QUACK!") are transcribed too loosely to reject a
    # 15-minute render on. Below this word count the transcript is evidence.
    transcript_word_count: int = Field(default=0, ge=0)
    transcript_gate_min_words: int = Field(default=6, ge=1)
    passed: bool = False
    diagnostics: list[str] = Field(default_factory=list)

    def failures(self) -> list[str]:
        """Every gate this receipt misses, in the order Mix would care."""
        failed: list[str] = []
        if not self.audio_present:
            failed.append("the negative carries no audio track")
        if self.speech_presence_score is None or self.speech_presence_score < self.speech_presence_min:
            measured = "unmeasured" if self.speech_presence_score is None else f"{self.speech_presence_score:.2f}"
            failed.append(f"voiced-speech presence {measured} is below {self.speech_presence_min:.2f}")
        if self.speech_tail_ms is not None and self.speech_tail_ms < self.speech_tail_min_ms:
            failed.append(
                f"speech is still active {self.speech_tail_ms:.0f} ms before the window ends "
                f"(needs {self.speech_tail_min_ms:g} ms); the line was cut by the picture"
            )
        if self.integrated_lufs is None or abs(self.integrated_lufs - self.loudness_target_lufs) > self.loudness_tolerance_lu:
            measured = "unmeasured" if self.integrated_lufs is None else f"{self.integrated_lufs:.1f} LUFS"
            failed.append(f"dialogue stem loudness {measured} is outside {self.loudness_target_lufs:g} ±{self.loudness_tolerance_lu:g} LU")
        if self.true_peak_dbtp is None or self.true_peak_dbtp > self.true_peak_ceiling_dbtp:
            measured = "unmeasured" if self.true_peak_dbtp is None else f"{self.true_peak_dbtp:.1f} dBTP"
            failed.append(f"dialogue stem true peak {measured} exceeds {self.true_peak_ceiling_dbtp:g} dBTP")
        if (
            self.transcript_word_accuracy is not None
            and self.transcript_word_count >= self.transcript_gate_min_words
            and self.transcript_word_accuracy < self.transcript_min_accuracy
        ):
            failed.append(
                f"the delivered speech matches only {self.transcript_word_accuracy:.0%} of the authored line "
                f"(needs {self.transcript_min_accuracy:.0%})"
            )
        return failed

    @model_validator(mode="after")
    def passing_receipt_meets_every_gate(self) -> SpeechQCReceipt:
        if self.passed:
            failed = self.failures()
            if failed:
                raise ValueError(f"speech QC cannot pass: {failed[0]}")
        return self


class SpeechClipPackage(BaseModel):
    """One spoken Take's Wan2.2-native speech evidence, bound to the rendered negative."""

    id: str
    project_id: str
    shot_id: str
    take_id: str
    character_id: str
    casting_id: str
    request: WanSpeechRequest
    backend: str = SPEECH_BACKEND
    # Audio extracted from the negative exactly as Wan muxed it (48 kHz WAV).
    dialogue_asset_id: str = ""
    # Loudness-conformed copy of that audio for the program mix rail.
    mix_stem_asset_id: str = ""
    video_sha256: str = Field(default="", pattern=r"^(?:|[0-9a-f]{64})$")
    state: VoiceRenderState = VoiceRenderState.PLANNED
    qc: SpeechQCReceipt | None = None
    created_at: datetime = Field(default_factory=_now)

    @property
    def verified(self) -> bool:
        return self.state == VoiceRenderState.VERIFIED and self.qc is not None and self.qc.passed

    @model_validator(mode="after")
    def verified_package_is_complete(self) -> SpeechClipPackage:
        if self.request.character_id != self.character_id or self.request.casting_id != self.casting_id:
            raise ValueError("speech package request lineage does not match the clip")
        if self.state == VoiceRenderState.VERIFIED:
            if self.qc is None or not self.qc.passed:
                raise ValueError("verified speech package requires a passing speech QC receipt")
            if not self.video_sha256:
                raise ValueError("verified speech package requires the exact rendered video hash")
            if not self.dialogue_asset_id or not self.mix_stem_asset_id:
                raise ValueError("verified speech package requires the dialogue and mix-stem assets")
        return self
