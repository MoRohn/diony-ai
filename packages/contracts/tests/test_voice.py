from __future__ import annotations

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from diony_contracts.enums import VoiceProfileSource, VoiceRenderState
from diony_contracts.models import (
    DialogueTurn,
    EmotionPerformance,
    PerformanceScript,
    PerformanceScriptLine,
    ProsodySignature,
    Take,
    VoiceClipPackage,
    VoiceProfile,
    VoiceQCReceipt,
    VoiceReferenceSample,
)


def synthetic_profile() -> VoiceProfile:
    return VoiceProfile(
        id="voice-char-mara-v1",
        project_id="project-night-press",
        character_id="char-mara",
        version=1,
        source=VoiceProfileSource.SYNTHETIC,
        speaker_embedding_model="cam++-diony-v1",
        speaker_embedding=[1.0, 0.0, 0.0, 0.0],
        prosody=ProsodySignature(
            pitch_floor_hz=145,
            pitch_ceiling_hz=310,
            speaking_rate_syllables_per_second=4.1,
            breath_interval_seconds=5.5,
            vocal_fry=0.15,
        ),
        emotion_map=[
            EmotionPerformance(
                emotion="neutral",
                intensity_curve=[0.2, 0.35, 0.25],
                rate_multiplier=1.0,
                pitch_shift_semitones=0,
                timbre="restrained, warm chest resonance",
            )
        ],
        age_descriptor="early thirties",
        gender_descriptor="woman",
        accent_descriptor="inland American",
        texture_descriptor="warm, lightly textured alto",
        seed=10401,
        style_lock="mara-night-press-alto-v1",
        locked=True,
        locked_at=datetime.now(timezone.utc),
    )


def passing_voice_qc() -> VoiceQCReceipt:
    return VoiceQCReceipt(
        measurement_version="diony-voice-qc-v1",
        speaker_similarity=0.97,
        speaker_similarity_min=0.92,
        project_consistency=0.96,
        project_consistency_min=0.90,
        snr_db=43.0,
        snr_db_min=30.0,
        integrated_lufs=-16.1,
        loudness_target_lufs=-16.0,
        true_peak_dbtp=-1.2,
        true_peak_ceiling_dbtp=-1.0,
        harshness_score=0.08,
        sibilance_score=0.10,
        lip_sync_confidence=0.96,
        lip_sync_confidence_min=0.90,
        audiovisual_offset_ms_p95=18.0,
        audiovisual_offset_ms_max=40.0,
        performance_consistency=0.95,
        performance_consistency_min=0.90,
        audio_passed=True,
        animation_passed=True,
        passed=True,
    )


def test_locked_voice_profile_is_content_addressed_and_immutable() -> None:
    profile = synthetic_profile()

    assert len(profile.content_sha256) == 64
    assert profile.identity_lineage_id.startswith("voice-identity-")
    assert profile.project_id == "project-night-press"
    with pytest.raises(ValidationError, match="frozen"):
        profile.seed = 9
    with pytest.raises(TypeError):
        profile.speaker_embedding[0] = 0.0


def test_zero_shot_profile_requires_five_to_thirty_seconds_of_reference() -> None:
    with pytest.raises(ValidationError, match="5 to 30 seconds"):
        VoiceProfile(
            id="voice-char-mara-v1",
            project_id="project-night-press",
            character_id="char-mara",
            source=VoiceProfileSource.ZERO_SHOT,
            reference_samples=[
                VoiceReferenceSample(
                    asset_id="ast-mara-reference",
                    duration_seconds=3.5,
                    emotion="neutral",
                    transcript="The presses are still running.",
                    embedding=[1.0, 0.0, 0.0, 0.0],
                    embedding_model="cam++-diony-v1",
                    sha256="a" * 64,
                )
            ],
            speaker_embedding_model="cam++-diony-v1",
            speaker_embedding=[1.0, 0.0, 0.0, 0.0],
            prosody=ProsodySignature(),
            emotion_map=[EmotionPerformance(emotion="neutral")],
            seed=7,
            style_lock="mara-zero-shot-v1",
            locked=True,
            locked_at=datetime.now(timezone.utc),
        )


def test_passing_voice_qc_cannot_claim_metrics_below_locked_thresholds() -> None:
    with pytest.raises(ValidationError, match="speaker identity"):
        passing_voice_qc().model_copy(
            update={"speaker_similarity": 0.70},
        ).model_validate(
            passing_voice_qc().model_dump() | {"speaker_similarity": 0.70}
        )

    with pytest.raises(ValidationError, match="physical-performance consistency"):
        VoiceQCReceipt.model_validate(
            passing_voice_qc().model_dump()
            | {"performance_consistency": 0.70}
        )


def test_voice_qc_v2_cannot_claim_pass_without_measured_speech_content() -> None:
    with pytest.raises(ValidationError, match="speech presence"):
        VoiceQCReceipt.model_validate(
            passing_voice_qc().model_dump()
            | {
                "measurement_version": "diony-voice-qc-v2",
                "speech_presence_score": 0.05,
            }
        )


def test_clip_package_roundtrip_keeps_voice_script_performance_and_qc() -> None:
    profile = synthetic_profile()
    turn = DialogueTurn(
        id="turn-mara-1",
        character_id="char-mara",
        text="They're running tomorrow's obit tonight.",
        emotion="dread",
        intensity_curve=[0.25, 0.45, 0.72],
        subtext="She already knows whose name is on the page.",
        target_duration_ms=3600,
    )
    line = PerformanceScriptLine(
        id=turn.id,
        shot_id="sh-010",
        character_id=turn.character_id,
        voice_profile_id=profile.id,
        text=turn.text,
        spoken_text="They're running tomorrow's obit | tonight.",
        emotion=turn.emotion,
        intensity_curve=turn.intensity_curve,
        breath_after_word_indices=[3],
        stress_word_indices=[1, 5],
        target_duration_ms=turn.target_duration_ms,
        micro_pauses_ms=[120],
        subtext=turn.subtext,
    )
    script = PerformanceScript(
        id="vscript-sh-010-v1",
        project_id=profile.project_id,
        shot_id="sh-010",
        version=1,
        lines=[line],
    )
    package = VoiceClipPackage(
        id="voicepkg-sh-010-t3-v1",
        project_id=profile.project_id,
        shot_id="sh-010",
        take_id="sh-010-t3",
        voice_profile_ids=[profile.id],
        generation_seeds={profile.id: profile.seed},
        script=script,
        clean_dialogue_asset_id="ast-dialogue-clean",
        final_mix_asset_id="ast-dialogue-mix",
        performance_metadata_asset_id="ast-performance-json",
        cache_key="b" * 64,
        synthesis_backend="cosyvoice2-local",
        embedding_backend="cam++-diony-v1",
        alignment_backend="forced-align-local-v1",
        video_sha256="c" * 64,
        state=VoiceRenderState.VERIFIED,
        qc=passing_voice_qc(),
    )
    take = Take(
        id="sh-010-t3",
        shot_id="sh-010",
        attempt=3,
        engine="s2v-14B",
        seed=10421,
        voice_package=package,
    )

    restored = Take.model_validate(take.model_dump(mode="json"))

    assert restored.voice_package is not None
    assert restored.voice_package.script.lines[0].subtext.startswith("She already")
    assert restored.voice_package.qc.passed is True
    assert restored.voice_package.voice_profile_ids == [profile.id]
    assert restored.voice_package.video_sha256 == "c" * 64


def test_verified_clip_package_requires_exact_video_lineage() -> None:
    profile = synthetic_profile()
    turn = DialogueTurn(
        id="turn-mara",
        character_id=profile.character_id,
        text="Hold the presses.",
    )
    line = PerformanceScriptLine(
        id=turn.id,
        shot_id="sh-010",
        character_id=turn.character_id,
        voice_profile_id=profile.id,
        text=turn.text,
        spoken_text=turn.text,
        target_duration_ms=1200,
    )
    script = PerformanceScript(
        id="vscript-sh-010-v1",
        project_id=profile.project_id,
        shot_id="sh-010",
        lines=[line],
    )

    with pytest.raises(ValidationError, match="exact rendered video hash"):
        VoiceClipPackage(
            id="voicepkg-sh-010-t1-v1",
            project_id=profile.project_id,
            shot_id="sh-010",
            take_id="sh-010-t1",
            voice_profile_ids=[profile.id],
            generation_seeds={profile.id: profile.seed},
            script=script,
            clean_dialogue_asset_id="ast-dialogue-clean",
            final_mix_asset_id="ast-dialogue-mix",
            performance_metadata_asset_id="ast-performance-json",
            cache_key="b" * 64,
            synthesis_backend="voice-local",
            embedding_backend="speaker-local",
            alignment_backend="align-local",
            state=VoiceRenderState.VERIFIED,
            qc=passing_voice_qc(),
        )
