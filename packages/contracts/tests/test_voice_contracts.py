from __future__ import annotations

import pytest
from pydantic import ValidationError

from diony_contracts.enums import Engine, VoiceCastingSource, VoiceRenderState
from diony_contracts.fixture import demo_project
from diony_contracts.models import (
    SpeechClipPackage,
    SpeechQCReceipt,
    VoiceCasting,
    WanSpeechRequest,
)


def library_casting() -> VoiceCasting:
    return VoiceCasting(
        id="cast-char-mara-v1",
        project_id="project-night-press",
        character_id="char-mara",
        source=VoiceCastingSource.LIBRARY,
        cast_index=0,
        locked=True,
        locked_at=__import__("datetime").datetime.now(__import__("datetime").timezone.utc),
    )


def speech_request(**overrides) -> WanSpeechRequest:
    payload = {
        "character_id": "char-mara",
        "casting_id": "cast-char-mara-v1",
        "tts_text": "The press room is empty by midnight.",
        "tts_prompt_audio_sha256": "a" * 64,
        "tts_prompt_text": "希望你以后能够做的比我还好呦。",
        "prompt_source": "library:cast-01-zero_shot_prompt.wav",
        "window_ms": 5005,
    }
    payload.update(overrides)
    return WanSpeechRequest(**payload)


def passing_receipt(**overrides) -> SpeechQCReceipt:
    payload = {
        "measurement_version": "diony-speech-qc-v1",
        "audio_present": True,
        "duration_ms": 5005.0,
        "window_ms": 5005.0,
        "speech_presence_score": 0.42,
        "speech_tail_ms": 640.0,
        "integrated_lufs": -24.3,
        "loudness_target_lufs": -24.0,
        "true_peak_dbtp": -1.4,
        "true_peak_ceiling_dbtp": -1.0,
        "passed": True,
    }
    payload.update(overrides)
    return SpeechQCReceipt(**payload)


def test_library_casting_is_content_addressed_and_immutable() -> None:
    casting = library_casting()
    assert casting.content_sha256
    assert VoiceCasting.model_validate(casting.model_dump(mode="json")).content_sha256 == casting.content_sha256
    with pytest.raises(ValidationError):
        casting.cast_index = 3  # type: ignore[misc]
    tampered = casting.model_dump(mode="json") | {"cast_index": 3}
    with pytest.raises(ValidationError):
        VoiceCasting.model_validate(tampered)


def test_reference_casting_needs_a_five_to_fifteen_second_local_clip() -> None:
    base = library_casting().model_dump(mode="json") | {
        "source": "reference",
        "prompt_audio_asset_id": "ast-ref-mara",
        "prompt_audio_sha256": "b" * 64,
        "content_sha256": "",
    }
    with pytest.raises(ValidationError):
        VoiceCasting.model_validate(base | {"prompt_duration_seconds": 3.0})
    with pytest.raises(ValidationError):
        VoiceCasting.model_validate(base | {"prompt_duration_seconds": 22.0})
    casting = VoiceCasting.model_validate(base | {"prompt_duration_seconds": 9.5, "prompt_text": "Reference line."})
    assert casting.source == VoiceCastingSource.REFERENCE
    with pytest.raises(ValidationError):
        VoiceCasting.model_validate(library_casting().model_dump(mode="json") | {"prompt_audio_asset_id": "x", "content_sha256": ""})


def test_speech_request_is_hashed_and_counts_its_words() -> None:
    first = speech_request()
    second = speech_request()
    assert first.content_sha256 == second.content_sha256
    assert first.word_count == 7
    assert first.words_per_second == pytest.approx(7 / 5.005, rel=1e-3)
    assert speech_request(tts_text="Different line here.").content_sha256 != first.content_sha256
    with pytest.raises(ValidationError):
        speech_request(tts_text="   ")


def test_speech_qc_cannot_pass_a_missed_gate() -> None:
    assert passing_receipt().failures() == []
    for bad in (
        {"audio_present": False},
        {"speech_presence_score": 0.1},
        {"speech_tail_ms": 20.0},
        {"integrated_lufs": -20.0},
        {"true_peak_dbtp": -0.2},
    ):
        with pytest.raises(ValidationError):
            passing_receipt(**bad)
        failed = passing_receipt(passed=False, **bad)
        assert failed.failures(), bad


def test_verified_speech_package_binds_assets_and_the_exact_negative() -> None:
    package = SpeechClipPackage(
        id="speech-take-1",
        project_id="project-night-press",
        shot_id="sh-010",
        take_id="take-1",
        character_id="char-mara",
        casting_id="cast-char-mara-v1",
        request=speech_request(),
        dialogue_asset_id="ast-voice-dialogue-1",
        mix_stem_asset_id="ast-voice-mix-stem-1",
        video_sha256="c" * 64,
        state=VoiceRenderState.VERIFIED,
        qc=passing_receipt(),
    )
    assert package.verified is True
    assert package.backend == "wan2.2-s2v-native-tts"
    payload = package.model_dump(mode="json")
    with pytest.raises(ValidationError):
        SpeechClipPackage.model_validate(payload | {"video_sha256": ""})
    with pytest.raises(ValidationError):
        SpeechClipPackage.model_validate(payload | {"mix_stem_asset_id": ""})
    with pytest.raises(ValidationError):
        SpeechClipPackage.model_validate(payload | {"qc": passing_receipt(passed=False, speech_presence_score=0.05).model_dump(mode="json")})
    with pytest.raises(ValidationError):
        SpeechClipPackage.model_validate(payload | {"character_id": "char-other"})
    planned = SpeechClipPackage.model_validate(payload | {"state": "planned", "qc": None, "video_sha256": "", "dialogue_asset_id": "", "mix_stem_asset_id": ""})
    assert planned.verified is False


def test_an_authored_line_is_enough_for_an_s2v_shot() -> None:
    project = demo_project()
    shot = next(item for item in project.shots if item.dialogue_turns or item.audio_line)
    payload = shot.model_dump(mode="json") | {"engine": Engine.S2V_14B.value, "audio_asset_ids": []}
    from diony_contracts.models import Shot

    spoken = Shot.model_validate(payload)
    assert spoken.engine == Engine.S2V_14B
    with pytest.raises(ValidationError):
        Shot.model_validate(payload | {"audio_line": None, "dialogue_turns": []})
