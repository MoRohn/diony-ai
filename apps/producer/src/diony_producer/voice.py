"""Voice identity, performance scripting, studio audio, and animation coupling.

The production path fails closed unless local synthesis, embedding, alignment,
and audiovisual-QC backends are configured. ``DIONY_DRY_RUN`` selects the
deterministic test backend; its tones and metrics are never a deliverable path.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import shlex
import shutil
import struct
import subprocess
import wave
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol

from diony_contracts.enums import AssetKind, Engine, LoudnessTarget, VoiceProfileSource, VoiceRenderState
from diony_contracts.models import Asset, Character, Project, Shot, Take
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


VOICE_QC_VERSION = "diony-voice-qc-v1"
VOICE_PIPELINE_VERSION = "diony-voice-performance-v2"


class VoicePipelineError(RuntimeError):
    """A mandatory voice or performance gate could not produce evidence."""


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _media_duration_seconds(path: Path) -> float:
    """Measure a local reference clip without trusting caller-supplied duration."""
    try:
        with wave.open(str(path), "rb") as source:
            rate = source.getframerate()
            if rate > 0:
                return source.getnframes() / rate
    except (EOFError, OSError, wave.Error):
        pass

    ffprobe = os.environ.get("DIONY_FFPROBE", "").strip() or shutil.which("ffprobe")
    if not ffprobe:
        return 0.0
    result = subprocess.run(
        [
            ffprobe,
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(path),
        ],
        capture_output=True,
        check=False,
        text=True,
    )
    if result.returncode != 0:
        return 0.0
    try:
        return float(result.stdout.strip())
    except ValueError:
        return 0.0


def _stable_seed(*parts: str) -> int:
    digest = hashlib.sha256("\x1f".join(parts).encode("utf-8")).hexdigest()
    return int(digest[:12], 16) % 2_000_000_000


def _stable_embedding(*parts: str, dimensions: int = 192) -> list[float]:
    values: list[float] = []
    counter = 0
    while len(values) < dimensions:
        block = hashlib.sha256(("\x1f".join(parts) + f":{counter}").encode("utf-8")).digest()
        values.extend((byte - 127.5) / 127.5 for byte in block)
        counter += 1
    vector = values[:dimensions]
    norm = math.sqrt(sum(value * value for value in vector)) or 1.0
    return [round(value / norm, 8) for value in vector]


def _cosine(left: list[float], right: list[float]) -> float:
    if len(left) != len(right) or not left:
        return -1.0
    numerator = sum(a * b for a, b in zip(left, right, strict=True))
    left_norm = math.sqrt(sum(value * value for value in left))
    right_norm = math.sqrt(sum(value * value for value in right))
    if not left_norm or not right_norm:
        return -1.0
    return max(-1.0, min(1.0, numerator / (left_norm * right_norm)))


def _enabled(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in {"1", "true", "yes", "on"}


def _character(project: Project, character_id: str) -> Character:
    for character in project.bible.characters:
        if character.id == character_id:
            return character
    raise VoicePipelineError(f"Dialogue names unknown character {character_id}")


def _primary_character_id(project: Project, shot: Shot) -> str:
    known = {character.id for character in project.bible.characters}
    for character_id in shot.character_ref_ids:
        if character_id in known:
            return character_id
    if project.bible.characters:
        return project.bible.characters[0].id
    raise VoicePipelineError(f"{shot.id} has dialogue but no Bible character")


class VoiceProfileRegistry:
    """Project-scoped immutable profile revisions stored on the Project contract."""

    def __init__(self, project: Project) -> None:
        self.project = project

    def current(self, character_id: str) -> VoiceProfile | None:
        character = _character(self.project, character_id)
        if character.voice_profile_id:
            try:
                return self.project.voice_profile(character.voice_profile_id)
            except KeyError:
                pass
        versions = [
            profile
            for profile in self.project.voice_profiles
            if profile.character_id == character_id and profile.locked
        ]
        return max(versions, key=lambda profile: profile.version) if versions else None

    def ensure_synthetic(
        self,
        character_id: str,
        descriptors: dict[str, str] | None = None,
        *,
        speaker_embedding: list[float] | None = None,
        speaker_embedding_model: str | None = None,
    ) -> VoiceProfile:
        current = self.current(character_id)
        if current is not None:
            return current
        character = _character(self.project, character_id)
        details = descriptors or {}
        seed = _stable_seed(character.id, character.name, "voice-v1")
        descriptor = " ".join(
            (
                character.name,
                character.notes,
                character.wardrobe,
                details.get("age", ""),
                details.get("gender", ""),
                details.get("accent", ""),
                details.get("texture", ""),
            )
        ).strip()
        profile = VoiceProfile(
            id=f"voice-{character.id}-v1",
            project_id=self.project.id,
            character_id=character.id,
            version=1,
            source=VoiceProfileSource.SYNTHETIC,
            speaker_embedding_model=(
                speaker_embedding_model or "diony-synthetic-speaker-space-v1"
            ),
            speaker_embedding=(
                list(speaker_embedding)
                if speaker_embedding is not None
                else _stable_embedding(character.id, descriptor)
            ),
            prosody=ProsodySignature(
                pitch_floor_hz=105,
                pitch_ceiling_hz=310,
                speaking_rate_syllables_per_second=4.0,
                breath_interval_seconds=5.0,
                vocal_fry=0.08,
                pause_bias=0.5,
            ),
            emotion_map=[
                EmotionPerformance(emotion="neutral", timbre="locked natural character voice"),
                EmotionPerformance(
                    emotion="joy",
                    intensity_curve=[0.25, 0.55, 0.4],
                    rate_multiplier=1.06,
                    pitch_shift_semitones=0.8,
                    timbre="brighter resonance without identity change",
                ),
                EmotionPerformance(
                    emotion="grief",
                    intensity_curve=[0.2, 0.65, 0.35],
                    rate_multiplier=0.9,
                    pitch_shift_semitones=-0.5,
                    timbre="restrained breath and stable chest resonance",
                ),
                EmotionPerformance(
                    emotion="tension",
                    intensity_curve=[0.35, 0.8, 0.55],
                    rate_multiplier=1.03,
                    pitch_shift_semitones=0.3,
                    timbre="compressed breath with profile-locked texture",
                ),
            ],
            age_descriptor=details.get("age", "Bible locked"),
            gender_descriptor=details.get("gender", "Bible locked"),
            accent_descriptor=details.get("accent", "project neutral until directed"),
            texture_descriptor=details.get("texture", "natural studio character voice"),
            seed=seed,
            style_lock=(
                f"identity:{character.id}:synthetic-v1:"
                f"{hashlib.sha256(descriptor.encode('utf-8')).hexdigest()[:16]}"
            ),
            locked=True,
            locked_at=datetime.now(timezone.utc),
        )
        self.project.voice_profiles.append(profile)
        character.voice_profile_id = profile.id
        return profile

    def lock_from_references(
        self,
        character_id: str,
        samples: list[VoiceReferenceSample],
        *,
        speaker_embedding: list[float],
        speaker_embedding_model: str,
        descriptors: dict[str, str] | None = None,
    ) -> VoiceProfile:
        """Lock a zero/few-shot revision without mutating any earlier profile."""
        character = _character(self.project, character_id)
        prior = self.current(character_id)
        version = (prior.version + 1) if prior else 1
        details = descriptors or {}
        profile = VoiceProfile(
            id=f"voice-{character.id}-v{version}",
            project_id=self.project.id,
            character_id=character.id,
            version=version,
            parent_profile_id=prior.id if prior else None,
            source=(VoiceProfileSource.ZERO_SHOT if len(samples) == 1 else VoiceProfileSource.FEW_SHOT),
            reference_samples=samples,
            speaker_embedding_model=speaker_embedding_model,
            speaker_embedding=speaker_embedding,
            prosody=ProsodySignature(),
            emotion_map=[
                EmotionPerformance(emotion=emotion)
                for emotion in dict.fromkeys(sample.emotion for sample in samples)
            ],
            age_descriptor=details.get("age", "Bible locked"),
            gender_descriptor=details.get("gender", "Bible locked"),
            accent_descriptor=details.get("accent", "reference locked"),
            texture_descriptor=details.get("texture", "reference locked"),
            seed=_stable_seed(
                character.id,
                speaker_embedding_model,
                _sha256_bytes(json.dumps(speaker_embedding).encode("utf-8")),
                f"voice-v{version}",
            ),
            style_lock=(
                f"identity:{character.id}:reference:"
                f"{_sha256_bytes(json.dumps(speaker_embedding).encode('utf-8'))[:16]}"
            ),
            locked=True,
            locked_at=datetime.now(timezone.utc),
        )
        self.project.voice_profiles.append(profile)
        character.voice_profile_id = profile.id
        return profile

    def enroll_synthetic_render(
        self,
        profile: VoiceProfile,
        *,
        speaker_embedding: list[float],
        speaker_embedding_model: str,
    ) -> VoiceProfile:
        """Finalize an unreferenced synthetic identity from its first measured render."""
        current = self.current(profile.character_id)
        if current is None or current.id != profile.id:
            raise VoicePipelineError("synthetic voice enrollment profile is no longer current")
        if profile.source != VoiceProfileSource.SYNTHETIC or profile.reference_samples:
            raise VoicePipelineError("only an unreferenced synthetic profile can be enrolled")
        if len(speaker_embedding) != len(profile.speaker_embedding):
            raise VoicePipelineError("synthetic enrollment embedding dimensions do not match")
        payload = profile.model_dump(mode="python")
        payload.update(
            {
                "identity_lineage_id": "",
                "speaker_embedding_model": speaker_embedding_model,
                "speaker_embedding": speaker_embedding,
                "content_sha256": "",
                "locked_at": datetime.now(timezone.utc),
            }
        )
        enrolled = VoiceProfile.model_validate(payload)
        for index, candidate in enumerate(self.project.voice_profiles):
            if candidate.id == profile.id:
                self.project.voice_profiles[index] = enrolled
                break
        return enrolled


def _dialogue_turns(project: Project, shot: Shot) -> list[DialogueTurn]:
    if shot.dialogue_turns:
        return list(shot.dialogue_turns)
    line = " ".join(str(shot.audio_line or "").split()).strip()
    if not line:
        return []
    return [
        DialogueTurn(
            id=f"turn-{shot.id}-1",
            character_id=_primary_character_id(project, shot),
            text=line,
            emotion="neutral",
            intensity_curve=[0.25, 0.45, 0.3],
        )
    ]


def _emotion_curve(profile: VoiceProfile, emotion: str) -> list[float]:
    for mapping in profile.emotion_map:
        if mapping.emotion.casefold() == emotion.casefold():
            return list(mapping.intensity_curve)
    return [0.25, 0.45, 0.3]


def optimize_voice_script(
    project: Project,
    shot: Shot,
    profiles: list[VoiceProfile],
) -> PerformanceScript:
    """Compile dialogue into deterministic breath, stress, emotion, and timing decisions."""
    turns = _dialogue_turns(project, shot)
    if not turns:
        raise VoicePipelineError(f"{shot.id} has no dialogue to optimize")
    by_character = {profile.character_id: profile for profile in profiles}
    clip_ms = max(1, round(shot.duration_frames / shot.fps * 1000))
    default_line_ms = max(250, clip_ms // len(turns))
    lines: list[PerformanceScriptLine] = []
    cursor = 0
    for index, turn in enumerate(turns):
        profile = by_character.get(turn.character_id)
        if profile is None:
            raise VoicePipelineError(f"No locked voice profile for {turn.character_id}")
        words = turn.text.split()
        interval_words = max(
            2,
            round(
                profile.prosody.breath_interval_seconds
                * profile.prosody.speaking_rate_syllables_per_second
                / 1.45
            ),
        )
        breaths = [position for position in range(interval_words - 1, len(words) - 1, interval_words)]
        if not breaths and len(words) > 3:
            breaths = [max(1, len(words) // 2 - 1)]
        stress = sorted(
            range(len(words)),
            key=lambda position: (len(words[position].strip(".,!?;:")), -position),
            reverse=True,
        )[: min(3, len(words))]
        spoken_words = list(words)
        for position in reversed(breaths):
            spoken_words[position] = spoken_words[position].rstrip(",;:") + " |"
        target_ms = turn.target_duration_ms or default_line_ms
        start_ms = turn.start_ms if turn.start_ms else max(0, cursor - turn.overlap_ms)
        target_ms = min(target_ms, max(1, clip_ms - min(start_ms, clip_ms - 1)))
        intensity = list(turn.intensity_curve or _emotion_curve(profile, turn.emotion))
        lines.append(
            PerformanceScriptLine(
                id=turn.id or f"turn-{shot.id}-{index + 1}",
                shot_id=shot.id,
                character_id=turn.character_id,
                voice_profile_id=profile.id,
                text=turn.text,
                spoken_text=" ".join(spoken_words),
                emotion=turn.emotion,
                intensity_curve=intensity,
                phonetic_guidance=dict(turn.pronunciation),
                breath_after_word_indices=breaths,
                stress_word_indices=sorted(stress),
                micro_pauses_ms=[120 + round(profile.prosody.pause_bias * 80) for _ in breaths],
                start_ms=start_ms,
                target_duration_ms=target_ms,
                overlap_ms=turn.overlap_ms,
                interrupts_line_id=turn.interrupts_turn_id,
                subtext=turn.subtext,
            )
        )
        cursor = max(cursor, start_ms + target_ms)
    identity = _sha256_bytes(
        json.dumps(
            {
                "project": project.id,
                "shot": shot.id,
                "profiles": [profile.content_sha256 for profile in profiles],
                "turns": [turn.model_dump(mode="json") for turn in turns],
                "version": VOICE_PIPELINE_VERSION,
            },
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    )
    return PerformanceScript(
        id=f"vscript-{shot.id}-{identity[:12]}",
        project_id=project.id,
        shot_id=shot.id,
        version=1,
        narrative_context=(
            project.brief.idea.strip()
            if project.brief is not None and project.brief.idea.strip()
            else project.bible.logline
        ),
        camera_language=(
            f"{shot.camera_move.value}; {shot.focal_mm:g}mm at T{shot.t_stop:g}; "
            f"{shot.lighting_ratio}; {shot.duration_frames} frames at {shot.fps:g} fps"
        ),
        pacing_target_ms=clip_ms,
        lines=lines,
    )


@dataclass(frozen=True)
class AudioMeasurements:
    snr_db: float
    integrated_lufs: float
    true_peak_dbtp: float
    harshness_score: float
    sibilance_score: float


@dataclass(frozen=True)
class AnimationMeasurements:
    lip_sync_confidence: float
    audiovisual_offset_ms_p95: float
    performance_consistency: float
    diagnostics: tuple[str, ...] = ()


class Synthesizer(Protocol):
    name: str

    def synthesize(
        self,
        line: PerformanceScriptLine,
        profile: VoiceProfile,
        output: Path,
        *,
        seed: int,
        reference_paths: list[Path],
    ) -> None: ...


class SpeakerEmbedder(Protocol):
    name: str

    def speaker_embedding(self, audio: Path, profile: VoiceProfile) -> list[float]: ...


class PerformanceAligner(Protocol):
    name: str

    def align(self, audio: Path, line: PerformanceScriptLine) -> dict[str, Any]: ...


class StudioAudioProcessor(Protocol):
    def process_audio(
        self,
        lines: list[tuple[Path, PerformanceScriptLine]],
        clean_output: Path,
        final_output: Path,
        *,
        duration_ms: int,
        target_lufs: float,
        true_peak_dbtp: float,
        seed: int,
    ) -> AudioMeasurements: ...


class AudiovisualQC(Protocol):
    def verify_audiovisual(
        self,
        video: Path,
        audio: Path,
        performance_metadata: Path,
    ) -> AnimationMeasurements: ...


class DeterministicVoiceTestBackend:
    """Deterministic non-production double selected only by explicit dry-run state."""

    name = "diony-deterministic-test-voice-v1"

    def __init__(self) -> None:
        self.synthesis_calls = 0

    @staticmethod
    def _tone(path: Path, *, duration_ms: int, seed: int, frequency: float = 180.0) -> None:
        sample_rate = 48_000
        frames = max(1, round(duration_ms / 1000 * sample_rate))
        path.parent.mkdir(parents=True, exist_ok=True)
        phase = (seed % 360) * math.pi / 180
        with wave.open(str(path), "wb") as output:
            output.setnchannels(1)
            output.setsampwidth(2)
            output.setframerate(sample_rate)
            for index in range(frames):
                envelope = min(1.0, index / 480) * min(1.0, (frames - index) / 480)
                value = int(6000 * envelope * math.sin(2 * math.pi * frequency * index / sample_rate + phase))
                output.writeframesraw(struct.pack("<h", value))

    def synthesize(
        self,
        line: PerformanceScriptLine,
        profile: VoiceProfile,
        output: Path,
        *,
        seed: int,
        reference_paths: list[Path],
    ) -> None:
        del reference_paths
        self.synthesis_calls += 1
        frequency = 135 + (sum(ord(char) for char in profile.character_id) % 110)
        self._tone(output, duration_ms=line.target_duration_ms, seed=seed, frequency=frequency)

    def speaker_embedding(self, audio: Path, profile: VoiceProfile) -> list[float]:
        if not audio.is_file():
            raise VoicePipelineError("test voice audio is missing")
        return list(profile.speaker_embedding)

    def reference_embedding(self, audio: Path, model: str) -> list[float]:
        if not audio.is_file():
            raise VoicePipelineError("test voice reference is missing")
        return _stable_embedding(model, _sha256_file(audio))

    def align(self, audio: Path, line: PerformanceScriptLine) -> dict[str, Any]:
        del audio
        words = line.text.split()
        step = line.target_duration_ms / max(1, len(words))
        cues = []
        for index, word in enumerate(words):
            start = round(line.start_ms + index * step, 3)
            end = round(line.start_ms + (index + 1) * step, 3)
            cues.append(
                {
                    "word": word,
                    "phoneme": " ".join(char.casefold() for char in word if char.isalpha()),
                    "viseme": "open" if any(char in "aeiouAEIOU" for char in word) else "closed",
                    "start_ms": start,
                    "end_ms": end,
                    "jaw": round(0.35 + 0.4 * line.intensity_curve[min(index, len(line.intensity_curve) - 1)], 3),
                    "brow": round(0.15 + 0.5 * max(line.intensity_curve), 3),
                    "head_energy": round(0.2 + 0.55 * max(line.intensity_curve), 3),
                }
            )
        return {"line_id": line.id, "cues": cues, "source": self.name}

    def process_audio(
        self,
        lines: list[tuple[Path, PerformanceScriptLine]],
        clean_output: Path,
        final_output: Path,
        *,
        duration_ms: int,
        target_lufs: float,
        true_peak_dbtp: float,
        seed: int,
    ) -> AudioMeasurements:
        del lines
        self._tone(clean_output, duration_ms=duration_ms, seed=seed, frequency=195)
        self._tone(final_output, duration_ms=duration_ms, seed=seed, frequency=195)
        return AudioMeasurements(
            snr_db=48.0,
            integrated_lufs=target_lufs,
            true_peak_dbtp=true_peak_dbtp - 0.2,
            harshness_score=0.04,
            sibilance_score=0.05,
        )

    def verify_audiovisual(
        self,
        video: Path,
        audio: Path,
        performance_metadata: Path,
    ) -> AnimationMeasurements:
        if not video.is_file() or not audio.is_file() or not performance_metadata.is_file():
            raise VoicePipelineError("deterministic audiovisual evidence is missing")
        return AnimationMeasurements(
            lip_sync_confidence=0.98,
            audiovisual_offset_ms_p95=8.0,
            performance_consistency=0.97,
        )


class LocalVoiceCommandBackend:
    """Swappable local model adapter for synthesis, embeddings, alignment, and AV QC.

    The configured executable receives ``<action> --request JSON`` and writes
    the response path named by the request. This keeps model-specific Python
    environments outside Producer's packaged runtime.
    """

    name = "diony-local-voice-command-v1"

    def __init__(self, command: str) -> None:
        self.command = shlex.split(command)
        if not self.command:
            raise VoicePipelineError("DIONY_VOICE_BACKEND_COMMAND is empty")

    def _invoke(self, action: str, request: dict[str, Any], response_path: Path) -> dict[str, Any]:
        response_path.parent.mkdir(parents=True, exist_ok=True)
        request_path = response_path.with_suffix(response_path.suffix + f".{action}.request.json")
        request_path.write_text(json.dumps(request, indent=2, sort_keys=True) + "\n")
        command = [*self.command, action, "--request", str(request_path), "--response", str(response_path)]
        result = subprocess.run(command, capture_output=True, text=True, timeout=1800, check=False)
        if result.returncode:
            detail = (result.stderr or result.stdout or "").strip().splitlines()
            raise VoicePipelineError(
                f"local voice {action} failed: {(detail[-1] if detail else result.returncode)}"
            )
        if not response_path.is_file():
            raise VoicePipelineError(f"local voice {action} returned no response")
        if response_path.suffix.lower() == ".json":
            try:
                return json.loads(response_path.read_text())
            except (OSError, json.JSONDecodeError) as exc:
                raise VoicePipelineError(f"local voice {action} returned unreadable JSON") from exc
        return {}

    def synthesize(
        self,
        line: PerformanceScriptLine,
        profile: VoiceProfile,
        output: Path,
        *,
        seed: int,
        reference_paths: list[Path],
    ) -> None:
        self._invoke(
            "synthesize",
            {
                "line": line.model_dump(mode="json"),
                "profile": profile.model_dump(mode="json"),
                "seed": seed,
                "reference_paths": [str(path) for path in reference_paths],
                "output": str(output),
            },
            output,
        )
        if output.stat().st_size <= 44:
            raise VoicePipelineError("local voice synthesis returned empty audio")

    def enroll_synthetic(
        self,
        line: PerformanceScriptLine,
        profile: VoiceProfile,
        output: Path,
    ) -> list[float]:
        response = output.with_suffix(".enrollment.json")
        payload = self._invoke(
            "enroll-synthetic",
            {
                "line": line.model_dump(mode="json"),
                "profile": profile.model_dump(mode="json"),
                "seed": profile.seed,
                "output": str(output),
            },
            response,
        )
        embedding = payload.get("embedding")
        if not isinstance(embedding, list) or len(embedding) != len(profile.speaker_embedding):
            raise VoicePipelineError("synthetic enrollment embedding dimensions do not match")
        return [float(value) for value in embedding]

    def speaker_embedding(self, audio: Path, profile: VoiceProfile) -> list[float]:
        response = audio.with_suffix(".embedding.json")
        payload = self._invoke(
            "embed",
            {"audio": str(audio), "model": profile.speaker_embedding_model},
            response,
        )
        embedding = payload.get("embedding")
        if not isinstance(embedding, list) or len(embedding) != len(profile.speaker_embedding):
            raise VoicePipelineError("speaker embedding dimensions do not match the locked profile")
        return [float(value) for value in embedding]

    def reference_embedding(self, audio: Path, model: str) -> list[float]:
        response = audio.with_suffix(".reference-embedding.json")
        payload = self._invoke(
            "embed-reference",
            {"audio": str(audio), "model": model},
            response,
        )
        embedding = payload.get("embedding")
        if not isinstance(embedding, list) or len(embedding) < 2:
            raise VoicePipelineError("reference embedding backend returned no speaker vector")
        return [float(value) for value in embedding]

    def align(self, audio: Path, line: PerformanceScriptLine) -> dict[str, Any]:
        response = audio.with_suffix(".alignment.json")
        return self._invoke(
            "align",
            {"audio": str(audio), "line": line.model_dump(mode="json")},
            response,
        )

    def process_audio(
        self,
        lines: list[tuple[Path, PerformanceScriptLine]],
        clean_output: Path,
        final_output: Path,
        *,
        duration_ms: int,
        target_lufs: float,
        true_peak_dbtp: float,
        seed: int,
    ) -> AudioMeasurements:
        response = final_output.with_suffix(".audio-qc.json")
        payload = self._invoke(
            "studio-audio",
            {
                "lines": [
                    {"audio": str(audio), "line": line.model_dump(mode="json")}
                    for audio, line in lines
                ],
                "clean_output": str(clean_output),
                "final_output": str(final_output),
                "duration_ms": duration_ms,
                "target_lufs": target_lufs,
                "true_peak_dbtp": true_peak_dbtp,
                "seed": seed,
                "chain": [
                    "noise_gate",
                    "de_ess",
                    "breath_control",
                    "mouth_noise_reduction",
                    "character_eq",
                    "gentle_compression",
                    "harmonic_enhancement",
                    "room_match",
                    "loudness_normalization",
                    "dialogue_priority_ducking",
                ],
            },
            response,
        )
        if not clean_output.is_file() or not final_output.is_file():
            raise VoicePipelineError("studio audio backend did not write both required stems")
        return AudioMeasurements(
            snr_db=float(payload["snr_db"]),
            integrated_lufs=float(payload["integrated_lufs"]),
            true_peak_dbtp=float(payload["true_peak_dbtp"]),
            harshness_score=float(payload["harshness_score"]),
            sibilance_score=float(payload["sibilance_score"]),
        )

    def verify_audiovisual(
        self,
        video: Path,
        audio: Path,
        performance_metadata: Path,
    ) -> AnimationMeasurements:
        response = video.with_suffix(".voice-av-qc.json")
        payload = self._invoke(
            "audiovisual-qc",
            {
                "video": str(video),
                "audio": str(audio),
                "performance_metadata": str(performance_metadata),
                "max_offset_ms": 40,
            },
            response,
        )
        return AnimationMeasurements(
            lip_sync_confidence=float(payload["lip_sync_confidence"]),
            audiovisual_offset_ms_p95=float(payload["audiovisual_offset_ms_p95"]),
            performance_consistency=float(payload["performance_consistency"]),
            diagnostics=tuple(str(item) for item in payload.get("diagnostics", [])),
        )


class VoicePerformancePipeline:
    def __init__(
        self,
        *,
        vault: Path,
        synthesizer: Synthesizer,
        embedder: SpeakerEmbedder,
        aligner: PerformanceAligner,
        audio_processor: StudioAudioProcessor,
        audiovisual_qc: AudiovisualQC,
    ) -> None:
        self.vault = vault
        self.synthesizer = synthesizer
        self.embedder = embedder
        self.aligner = aligner
        self.audio_processor = audio_processor
        self.audiovisual_qc = audiovisual_qc

    def _asset_path(self, asset: Asset) -> Path:
        path = Path(asset.path)
        return path if path.is_absolute() else self.vault / path

    def _reference_paths(self, project: Project, profile: VoiceProfile) -> list[Path]:
        paths: list[Path] = []
        for sample in profile.reference_samples:
            try:
                asset = project.asset(sample.asset_id)
            except KeyError as exc:
                raise VoicePipelineError(f"Voice reference asset {sample.asset_id} is missing") from exc
            path = self._asset_path(asset)
            if not path.is_file():
                raise VoicePipelineError(f"Voice reference bytes are missing: {sample.asset_id}")
            paths.append(path)
        return paths

    def _profiles(self, project: Project, shot: Shot) -> list[VoiceProfile]:
        registry = VoiceProfileRegistry(project)
        ordered: list[VoiceProfile] = []
        for turn in _dialogue_turns(project, shot):
            profile = registry.current(turn.character_id)
            if profile is None:
                prompt_path = Path(os.environ.get("DIONY_COSYVOICE_PROMPT_AUDIO", ""))
                measured_embedding: list[float] | None = None
                measured_model: str | None = None
                reference_embedding = getattr(self.embedder, "reference_embedding", None)
                rendered_enrollment = getattr(self.synthesizer, "enroll_synthetic", None)
                if (
                    not callable(rendered_enrollment)
                    and prompt_path.is_file()
                    and callable(reference_embedding)
                ):
                    measured_model = os.environ.get(
                        "DIONY_SPEAKER_EMBEDDING_MODEL",
                        "cosyvoice-campplus-v1",
                    ).strip()
                    measured_embedding = list(reference_embedding(prompt_path, measured_model))
                profile = registry.ensure_synthetic(
                    turn.character_id,
                    speaker_embedding=measured_embedding,
                    speaker_embedding_model=measured_model,
                )
            if profile.id not in {item.id for item in ordered}:
                ordered.append(profile)
        return ordered

    def _enroll_synthetic_profiles(
        self,
        project: Project,
        script: PerformanceScript,
        profiles: list[VoiceProfile],
    ) -> list[VoiceProfile]:
        enroll = getattr(self.synthesizer, "enroll_synthetic", None)
        if not callable(enroll):
            return profiles
        registry = VoiceProfileRegistry(project)
        enrolled_profiles: list[VoiceProfile] = []
        marker = ":synthetic-enrollment-v1"
        for profile in profiles:
            if profile.source != VoiceProfileSource.SYNTHETIC or profile.speaker_embedding_model.endswith(marker):
                enrolled_profiles.append(profile)
                continue
            line = next(
                (item for item in script.lines if item.voice_profile_id == profile.id),
                None,
            )
            if line is None:
                raise VoicePipelineError(f"synthetic voice {profile.id} has no enrollment line")
            output = (
                self.vault
                / "projects"
                / project.id
                / "voice"
                / "profiles"
                / profile.id
                / "synthetic-enrollment.wav"
            )
            output.parent.mkdir(parents=True, exist_ok=True)
            embedding = [float(value) for value in enroll(line, profile, output)]
            if not output.is_file() or output.stat().st_size <= 44:
                raise VoicePipelineError("synthetic voice enrollment returned no audio evidence")
            model = os.environ.get(
                "DIONY_SPEAKER_EMBEDDING_MODEL",
                "cosyvoice-campplus-v1",
            ).strip()
            enrolled_profiles.append(
                registry.enroll_synthetic_render(
                    profile,
                    speaker_embedding=embedding,
                    speaker_embedding_model=f"{model}{marker}",
                )
            )
        return enrolled_profiles

    @staticmethod
    def _cache_key(script: PerformanceScript, profiles: list[VoiceProfile]) -> str:
        return _sha256_bytes(
            json.dumps(
                {
                    "pipeline": VOICE_PIPELINE_VERSION,
                    "script": script.content_sha256,
                    "profiles": [profile.content_sha256 for profile in profiles],
                },
                separators=(",", ":"),
                sort_keys=True,
            ).encode("utf-8")
        )

    def _package_assets_valid(self, project: Project, package: VoiceClipPackage) -> bool:
        for asset_id in (
            package.clean_dialogue_asset_id,
            package.final_mix_asset_id,
            package.performance_metadata_asset_id,
        ):
            try:
                asset = project.asset(asset_id)
            except KeyError:
                return False
            path = self._asset_path(asset)
            if not path.is_file() or path.stat().st_size <= 32 or _sha256_file(path) != asset.sha256:
                return False
        return True

    def _cached_package_valid(self, project: Project, take: Take, cache_key: str) -> bool:
        package = take.voice_package
        if package is None or package.cache_key != cache_key:
            return False
        return self._package_assets_valid(project, package)

    @staticmethod
    def _condition_shot(shot: Shot, take: Take, package: VoiceClipPackage) -> None:
        shot.audio_asset_ids = [package.clean_dialogue_asset_id]
        shot.engine = Engine.S2V_14B
        take.engine = Engine.S2V_14B
        if shot.prompt_plan is not None:
            shot.prompt_plan.generation_method = Engine.S2V_14B.value
            shot.prompt_plan.audio_strategy = "locked_voice_s2v"

    def _reuse_project_cache(
        self,
        project: Project,
        take: Take,
        shot: Shot,
        cache_key: str,
    ) -> VoiceClipPackage | None:
        for prior in reversed(project.takes):
            package = prior.voice_package
            if (
                prior.id == take.id
                or package is None
                or package.cache_key != cache_key
                or not package.qc.audio_passed
                or not self._package_assets_valid(project, package)
            ):
                continue
            qc_payload = package.qc.model_dump(mode="json") | {
                "lip_sync_confidence": None,
                "audiovisual_offset_ms_p95": None,
                "animation_passed": False,
                "passed": False,
                "diagnostics": [
                    *package.qc.diagnostics,
                    f"reused audio cache from {prior.id}; animation QC must run again",
                ],
            }
            reused = VoiceClipPackage.model_validate(
                package.model_dump(mode="json")
                | {
                    "id": f"voicepkg-{take.id}-{cache_key[:12]}",
                    "take_id": take.id,
                    "video_sha256": "",
                    "state": VoiceRenderState.AUDIO_LOCKED.value,
                    "qc": qc_payload,
                    "created_at": datetime.now(timezone.utc).isoformat(),
                }
            )
            take.voice_package = reused
            self._condition_shot(shot, take, reused)
            return reused
        return None

    def _append_asset(
        self,
        project: Project,
        path: Path,
        *,
        role: str,
        cache_key: str,
        kind: AssetKind,
        mime: str,
    ) -> Asset:
        digest = _sha256_file(path)
        relative = str(path.relative_to(self.vault)) if path.is_relative_to(self.vault) else str(path)
        asset = Asset(
            id=f"ast-{role.replace('_', '-')}-{digest[:16]}",
            kind=kind,
            filename=path.name,
            mime=mime,
            bytes=path.stat().st_size,
            path=relative,
            sha256=digest,
            role=role,
            metadata={
                "voice_pipeline_version": VOICE_PIPELINE_VERSION,
                "cache_key": cache_key,
            },
        )
        existing = next((item for item in project.assets if item.id == asset.id), None)
        if existing is None:
            project.assets.append(asset)
            return asset
        return existing

    def prepare_take(self, project: Project, take: Take) -> VoiceClipPackage:
        shot = project.shot(take.shot_id)
        turns = _dialogue_turns(project, shot)
        if not turns:
            raise VoicePipelineError(f"{shot.id} has no dialogue")
        profiles = self._profiles(project, shot)
        script = optimize_voice_script(project, shot, profiles)
        profiles = self._enroll_synthetic_profiles(project, script, profiles)
        script = optimize_voice_script(project, shot, profiles)
        cache_key = self._cache_key(script, profiles)
        if self._cached_package_valid(project, take, cache_key):
            return take.voice_package  # type: ignore[return-value]
        reused = self._reuse_project_cache(project, take, shot, cache_key)
        if reused is not None:
            return reused

        cache_root = self.vault / "projects" / project.id / "voice" / "cache" / cache_key
        cache_root.mkdir(parents=True, exist_ok=True)
        profile_by_id = {profile.id: profile for profile in profiles}
        line_audio: list[tuple[Path, PerformanceScriptLine]] = []
        alignments: list[dict[str, Any]] = []
        speaker_scores: list[float] = []
        generation_seeds: dict[str, int] = {profile.id: profile.seed for profile in profiles}
        audio_attempts: list[dict[str, Any]] = []
        try:
            retake_cap = min(5, max(1, int(os.environ.get("DIONY_VOICE_RETAKE_CAP", "3"))))
        except ValueError:
            retake_cap = 3

        def synthesize_line(
            indexed_line: tuple[int, PerformanceScriptLine],
        ) -> tuple[
            int,
            Path,
            PerformanceScriptLine,
            float,
            int,
            list[dict[str, Any]],
            dict[str, Any],
        ]:
            index, line = indexed_line
            profile = profile_by_id[line.voice_profile_id]
            raw: Path | None = None
            selected_score = -1.0
            selected_seed = profile.seed
            attempts: list[dict[str, Any]] = []
            reference_paths = self._reference_paths(project, profile)
            selected_alignment: dict[str, Any] | None = None
            last_failure = "identity"
            for attempt in range(1, retake_cap + 1):
                candidate = cache_root / f"line-{index + 1:02d}-{line.id}-a{attempt}.wav"
                seed = profile.seed + (attempt - 1) * 1009
                self.synthesizer.synthesize(
                    line,
                    profile,
                    candidate,
                    seed=seed,
                    reference_paths=reference_paths,
                )
                if not candidate.is_file() or candidate.stat().st_size <= 44:
                    attempts.append(
                        {
                            "line_id": line.id,
                            "attempt": attempt,
                            "seed": seed,
                            "diagnostic": "synthesis returned no audio bytes",
                        }
                    )
                    continue
                observed = self.embedder.speaker_embedding(candidate, profile)
                selected_score = _cosine(profile.speaker_embedding, observed)
                if selected_score < 0.92:
                    attempts.append(
                        {
                            "line_id": line.id,
                            "attempt": attempt,
                            "seed": seed,
                            "speaker_similarity": round(selected_score, 6),
                            "diagnostic": (
                                "speaker identity drift; regenerate with the exact locked profile "
                                "and stronger identity conditioning"
                            ),
                        }
                    )
                    last_failure = "identity"
                    continue
                alignment = self.aligner.align(candidate, line)
                measured_duration_ms = float(
                    alignment.get("measured_duration_ms")
                    or round(_media_duration_seconds(candidate) * 1000)
                )
                timing_tolerance_ms = max(250, round(line.target_duration_ms * 0.10))
                timing_limit_ms = line.target_duration_ms + timing_tolerance_ms
                if measured_duration_ms > timing_limit_ms:
                    attempts.append(
                        {
                            "line_id": line.id,
                            "attempt": attempt,
                            "seed": seed,
                            "speaker_similarity": round(selected_score, 6),
                            "measured_duration_ms": round(measured_duration_ms),
                            "target_duration_ms": line.target_duration_ms,
                            "timing_limit_ms": timing_limit_ms,
                            "diagnostic": (
                                "voice timing overflow; synthesized speech exceeds its authored "
                                "shot window and may not be trimmed"
                            ),
                        }
                    )
                    last_failure = "timing"
                    continue
                raw = candidate
                selected_seed = seed
                selected_alignment = alignment
                attempts.append(
                    {
                        "line_id": line.id,
                        "attempt": attempt,
                        "seed": seed,
                        "speaker_similarity": round(selected_score, 6),
                        "diagnostic": "speaker identity passed",
                    }
                )
                break
            if raw is None:
                raise VoicePipelineError(
                    f"Voice {last_failure} failed for {line.id} after "
                    f"{retake_cap} bounded attempts"
                )
            if selected_alignment is None:
                raise VoicePipelineError(f"Voice timing evidence is missing for {line.id}")
            return index, raw, line, selected_score, selected_seed, attempts, selected_alignment

        try:
            worker_cap = min(8, max(1, int(os.environ.get("DIONY_VOICE_WORKERS", "4"))))
        except ValueError:
            worker_cap = 4
        worker_count = min(worker_cap, len(script.lines))
        with ThreadPoolExecutor(max_workers=worker_count, thread_name_prefix="diony-voice") as pool:
            line_results = list(pool.map(synthesize_line, enumerate(script.lines)))
        for _, raw, line, selected_score, selected_seed, attempts, alignment in line_results:
            generation_seeds[line.voice_profile_id] = selected_seed
            audio_attempts.extend(attempts)
            speaker_scores.append(selected_score)
            alignments.append(alignment)
            line_audio.append((raw, line))

        clean_path = cache_root / "clean-dialogue.wav"
        final_path = cache_root / "final-clip-mix.wav"
        performance_path = cache_root / "performance.json"
        clip_ms = max(1, round(shot.duration_frames / shot.fps * 1000))
        target_lufs = -24.0 if project.bible.loudness == LoudnessTarget.BROADCAST else -16.0
        measurements: AudioMeasurements | None = None
        for attempt in range(1, retake_cap + 1):
            post_seed = _stable_seed(cache_key, "studio-audio", str(attempt))
            candidate = self.audio_processor.process_audio(
                line_audio,
                clean_path,
                final_path,
                duration_ms=clip_ms,
                target_lufs=target_lufs,
                true_peak_dbtp=project.bible.true_peak_dbtp,
                seed=post_seed,
            )
            post_passed = all(
                (
                    candidate.snr_db >= 30,
                    abs(candidate.integrated_lufs - target_lufs) <= 1.0,
                    candidate.true_peak_dbtp <= project.bible.true_peak_dbtp,
                    candidate.harshness_score <= 0.25,
                    candidate.sibilance_score <= 0.25,
                )
            )
            audio_attempts.append(
                {
                    "stage": "studio_audio",
                    "attempt": attempt,
                    "seed": post_seed,
                    "snr_db": candidate.snr_db,
                    "integrated_lufs": candidate.integrated_lufs,
                    "true_peak_dbtp": candidate.true_peak_dbtp,
                    "harshness_score": candidate.harshness_score,
                    "sibilance_score": candidate.sibilance_score,
                    "diagnostic": (
                        "studio audio QC passed"
                        if post_passed
                        else "studio audio QC failed; rerun gate, de-ess, dynamics, room match, and loudness chain"
                    ),
                }
            )
            measurements = candidate
            if post_passed:
                break
        if measurements is None:
            raise VoicePipelineError("Studio audio processor returned no measurements")
        performance_payload = {
            "schema_version": 1,
            "pipeline_version": VOICE_PIPELINE_VERSION,
            "project_id": project.id,
            "shot_id": shot.id,
            "take_id": take.id,
            "voice_profile_ids": [profile.id for profile in profiles],
            "voice_profile_sha256s": {
                profile.id: profile.content_sha256 for profile in profiles
            },
            "generation_seeds": generation_seeds,
            "audio_attempts": audio_attempts,
            "script": script.model_dump(mode="json"),
            "alignment": alignments,
            "drives": {
                "lips": "phoneme + viseme + co-articulation",
                "face": "brow + eye + cheek micro-expression",
                "body": "head + upper-body prosody energy",
                "visual_conditioning": "Wan2.2 S2V final locked audio",
            },
        }
        performance_path.write_text(json.dumps(performance_payload, indent=2, sort_keys=True) + "\n")

        clean_asset = self._append_asset(
            project,
            clean_path,
            role="voice_clean_dialogue",
            cache_key=cache_key,
            kind=AssetKind.AUDIO,
            mime="audio/wav",
        )
        final_asset = self._append_asset(
            project,
            final_path,
            role="voice_final_mix",
            cache_key=cache_key,
            kind=AssetKind.AUDIO,
            mime="audio/wav",
        )
        performance_asset = self._append_asset(
            project,
            performance_path,
            role="voice_performance_metadata",
            cache_key=cache_key,
            kind=AssetKind.DOCUMENT,
            mime="application/json",
        )
        speaker_similarity = min(speaker_scores) if speaker_scores else -1.0
        historical = [
            item.voice_package.qc.speaker_similarity
            for item in project.takes
            if item.id != take.id
            and item.voice_package is not None
            and set(item.voice_package.voice_profile_ids) & {profile.id for profile in profiles}
        ]
        project_consistency = min([speaker_similarity, *historical]) if historical else speaker_similarity
        audio_passed = all(
            (
                speaker_similarity >= 0.92,
                project_consistency >= 0.90,
                measurements.snr_db >= 30,
                abs(measurements.integrated_lufs - target_lufs) <= 1.0,
                measurements.true_peak_dbtp <= project.bible.true_peak_dbtp,
                measurements.harshness_score <= 0.25,
                measurements.sibilance_score <= 0.25,
            )
        )
        qc = VoiceQCReceipt(
            measurement_version=VOICE_QC_VERSION,
            speaker_similarity=speaker_similarity,
            project_consistency=project_consistency,
            snr_db=measurements.snr_db,
            integrated_lufs=measurements.integrated_lufs,
            loudness_target_lufs=target_lufs,
            true_peak_dbtp=measurements.true_peak_dbtp,
            true_peak_ceiling_dbtp=project.bible.true_peak_dbtp,
            harshness_score=measurements.harshness_score,
            sibilance_score=measurements.sibilance_score,
            audio_passed=audio_passed,
            animation_passed=False,
            passed=False,
            diagnostics=[] if audio_passed else ["voice audio failed one or more locked QC thresholds"],
        )
        package = VoiceClipPackage(
            id=f"voicepkg-{take.id}-{cache_key[:12]}",
            project_id=project.id,
            shot_id=shot.id,
            take_id=take.id,
            voice_profile_ids=[profile.id for profile in profiles],
            generation_seeds=generation_seeds,
            script=script,
            clean_dialogue_asset_id=clean_asset.id,
            final_mix_asset_id=final_asset.id,
            performance_metadata_asset_id=performance_asset.id,
            cache_key=cache_key,
            synthesis_backend=self.synthesizer.name,
            embedding_backend=self.embedder.name,
            alignment_backend=self.aligner.name,
            state=VoiceRenderState.AUDIO_LOCKED if audio_passed else VoiceRenderState.FAILED,
            qc=qc,
        )
        take.voice_package = package
        if not audio_passed:
            raise VoicePipelineError("Voice audio failed locked identity or studio-audio QC")
        self._condition_shot(shot, take, package)
        return package

    def verify_animation(self, project: Project, take: Take, video: Path) -> VoiceClipPackage:
        package = take.voice_package
        if package is None:
            raise VoicePipelineError(f"{take.id} has no audio-locked voice package")
        clean_asset = project.asset(package.clean_dialogue_asset_id)
        metadata_asset = project.asset(package.performance_metadata_asset_id)
        measurements = self.audiovisual_qc.verify_audiovisual(
            video,
            self._asset_path(clean_asset),
            self._asset_path(metadata_asset),
        )
        animation_passed = (
            measurements.lip_sync_confidence >= package.qc.lip_sync_confidence_min
            and measurements.audiovisual_offset_ms_p95 <= package.qc.audiovisual_offset_ms_max
            and measurements.performance_consistency >= package.qc.project_consistency_min
        )
        qc_payload = package.qc.model_dump(mode="json") | {
            "lip_sync_confidence": measurements.lip_sync_confidence,
            "audiovisual_offset_ms_p95": measurements.audiovisual_offset_ms_p95,
            "performance_consistency": measurements.performance_consistency,
            "animation_passed": animation_passed,
            "passed": bool(package.qc.audio_passed and animation_passed),
            "diagnostics": list(package.qc.diagnostics) + list(measurements.diagnostics),
        }
        qc = VoiceQCReceipt.model_validate(qc_payload)
        updated = VoiceClipPackage.model_validate(
            package.model_dump(mode="json")
            | {
                "state": (
                    VoiceRenderState.VERIFIED.value if qc.passed else VoiceRenderState.FAILED.value
                ),
                "video_sha256": _sha256_file(video),
                "qc": qc.model_dump(mode="json"),
            }
        )
        take.voice_package = updated
        return updated


def default_voice_pipeline() -> VoicePerformancePipeline:
    from diony_producer.vault import vault_root

    backend = default_voice_backend()
    return VoicePerformancePipeline(
        vault=vault_root(),
        synthesizer=backend,
        embedder=backend,
        aligner=backend,
        audio_processor=backend,
        audiovisual_qc=backend,
    )


def default_voice_backend() -> DeterministicVoiceTestBackend | LocalVoiceCommandBackend:
    if _enabled("DIONY_DRY_RUN"):
        return DeterministicVoiceTestBackend()
    command = os.environ.get("DIONY_VOICE_BACKEND_COMMAND", "").strip()
    if not command:
        raise VoicePipelineError(
            "Voice is required, but no local studio voice backend is configured. "
            "Set DIONY_VOICE_BACKEND_COMMAND; raw or post-picture TTS is forbidden."
        )
    return LocalVoiceCommandBackend(command)


def lock_reference_voice_profile(
    project: Project,
    character_id: str,
    reference_asset_ids: list[str],
    *,
    vault: Path,
    backend: Any,
    descriptors: dict[str, str] | None = None,
) -> VoiceProfile:
    """Extract and lock 5–30 seconds of local multi-sample speaker evidence."""
    if not reference_asset_ids:
        raise VoicePipelineError("Voice cloning requires one or more reference audio assets")
    model = os.environ.get("DIONY_SPEAKER_EMBEDDING_MODEL", "cam++-local-v1").strip()
    samples: list[VoiceReferenceSample] = []
    vectors: list[list[float]] = []
    for asset_id in reference_asset_ids:
        try:
            asset = project.asset(asset_id)
        except KeyError as exc:
            raise VoicePipelineError(f"Voice reference asset {asset_id} is not on this Project") from exc
        if asset.kind != AssetKind.AUDIO:
            raise VoicePipelineError(f"Voice reference asset {asset_id} is not audio")
        path = Path(asset.path)
        path = path if path.is_absolute() else vault / path
        if not path.is_file() or path.stat().st_size <= 44:
            raise VoicePipelineError(f"Voice reference bytes are missing: {asset_id}")
        try:
            duration = float(asset.metadata.get("duration_seconds") or 0)
        except (TypeError, ValueError):
            duration = 0
        if duration <= 0:
            duration = _media_duration_seconds(path)
        if duration <= 0:
            raise VoicePipelineError(f"Voice reference duration could not be measured: {asset_id}")
        vector = [float(value) for value in backend.reference_embedding(path, model)]
        if vectors and len(vector) != len(vectors[0]):
            raise VoicePipelineError("Voice reference embedding dimensions do not match")
        vectors.append(vector)
        samples.append(
            VoiceReferenceSample(
                asset_id=asset.id,
                duration_seconds=duration,
                emotion=str(asset.metadata.get("emotion") or "neutral"),
                transcript=str(asset.metadata.get("transcript") or asset.extracted_text or ""),
                embedding=vector,
                embedding_model=model,
                sha256=asset.sha256 or _sha256_file(path),
            )
        )
    combined = [
        sum(vector[index] for vector in vectors) / len(vectors)
        for index in range(len(vectors[0]))
    ]
    norm = math.sqrt(sum(value * value for value in combined)) or 1.0
    combined = [round(value / norm, 8) for value in combined]
    return VoiceProfileRegistry(project).lock_from_references(
        character_id,
        samples,
        speaker_embedding=combined,
        speaker_embedding_model=model,
        descriptors=descriptors,
    )


def prepare_voice_for_take(project: Project, take: Take) -> VoiceClipPackage | None:
    shot = project.shot(take.shot_id)
    if not shot.audio_line and not shot.dialogue_turns:
        return None
    return default_voice_pipeline().prepare_take(project, take)


def verify_voice_animation(project: Project, take: Take, video: Path) -> VoiceClipPackage | None:
    if take.voice_package is None:
        return None
    return default_voice_pipeline().verify_animation(project, take, video)
