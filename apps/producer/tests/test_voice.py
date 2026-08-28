from __future__ import annotations

import math
import shutil
import struct
import subprocess
import wave
from pathlib import Path

import pytest

from diony_contracts.enums import AssetKind, Engine, VoiceCastingSource, VoiceRenderState
from diony_contracts.fixture import demo_project
from diony_contracts.models import Asset
from diony_contracts.wan_prompt import WAN_NATIVE_TTS_AUDIO_STRATEGY, is_spoken_plan
from diony_producer.voice import (
    HOST_SPEECH_ENV_KEYS,
    MAX_WORDS_PER_SECOND,
    VoiceCastingRegistry,
    VoicePipelineError,
    host_speech_env,
    prepare_speech_for_take,
    resolve_prompt_clip,
    speech_failure_message,
    speech_pack_missing,
    spoken_line,
    verify_speech_render,
    wan_speech_inputs,
)

FFMPEG = shutil.which("ffmpeg")


def _spoken_take():
    project = demo_project()
    shot = next(item for item in project.shots if item.dialogue_turns or item.audio_line)
    take = project.take(shot.selected_take_id)
    take.artifacts = []
    take.asset_720p_id = None
    take.asset_1080p_id = None
    take.qc = None
    take.voice_package = None
    return project, shot, take


def _write_tone(path: Path, *, seconds: float, active_seconds: float, rate: int = 16_000, hz: float = 150.0, lead_silence: float = 0.0) -> None:
    frames = int(seconds * rate)
    active = int(active_seconds * rate)
    lead = int(lead_silence * rate)
    with wave.open(str(path), "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(rate)
        samples = bytearray()
        for index in range(frames):
            if lead <= index < active:
                # Two harmonics with a slow amplitude swell: periodic like voiced speech.
                value = 0.45 * math.sin(2 * math.pi * hz * index / rate) + 0.2 * math.sin(2 * math.pi * hz * 2 * index / rate)
                value *= 0.6 + 0.4 * math.sin(2 * math.pi * 3 * index / rate)
            else:
                value = 0.0
            samples += struct.pack("<h", int(max(-1.0, min(1.0, value)) * 32767))
        out.writeframes(bytes(samples))


def _library(tmp_path: Path, monkeypatch, names=("cast-00-a.wav", "cast-01-b.wav")) -> Path:
    library = tmp_path / "prompt-library"
    library.mkdir()
    for name in names:
        _write_tone(library / name, seconds=6.0, active_seconds=5.0)
    (library / "cast-01-b.txt").write_text("希望你以后能够做的比我还好呦。\n", encoding="utf-8")
    monkeypatch.setenv("DIONY_WAN_TTS_PROMPT_LIBRARY", str(library))
    return library


def test_registry_casts_characters_on_the_library_by_roster_order() -> None:
    project, _, _ = _spoken_take()
    registry = VoiceCastingRegistry(project)
    lead = project.bible.characters[0].id

    first = registry.ensure_library(lead)
    again = registry.ensure_library(lead)

    assert first is again
    assert first.source == VoiceCastingSource.LIBRARY
    assert first.cast_index == 0
    assert first.locked is True
    assert project.bible.characters[0].voice_casting_id == first.id
    assert project.voice_castings == [first]


def test_prompt_clip_resolves_library_slot_then_default_then_holds(tmp_path, monkeypatch) -> None:
    project, _, _ = _spoken_take()
    lead = project.bible.characters[0].id
    casting = VoiceCastingRegistry(project).ensure_library(lead)
    monkeypatch.delenv("DIONY_WAN_TTS_PROMPT_LIBRARY", raising=False)
    monkeypatch.delenv("DIONY_WAN_TTS_PROMPT_AUDIO", raising=False)
    with pytest.raises(VoicePipelineError, match="Voice casting failed"):
        resolve_prompt_clip(project, casting, vault=tmp_path)

    default = tmp_path / "zero_shot_prompt.wav"
    _write_tone(default, seconds=6.0, active_seconds=5.0)
    monkeypatch.setenv("DIONY_WAN_TTS_PROMPT_AUDIO", str(default))
    monkeypatch.setenv("DIONY_WAN_TTS_PROMPT_TEXT", "希望你以后能够做的比我还好呦。")
    clip = resolve_prompt_clip(project, casting, vault=tmp_path)
    assert clip.source == "default" and clip.path == default and clip.text.startswith("希望")

    library = _library(tmp_path, monkeypatch)
    second = casting.model_copy(update={"cast_index": 1})
    clip = resolve_prompt_clip(project, second, vault=tmp_path)
    assert clip.path == library / "cast-01-b.wav"
    assert clip.text == "希望你以后能够做的比我还好呦。"
    assert clip.source == "library:cast-01-b.wav"
    third = casting.model_copy(update={"cast_index": 2})
    assert resolve_prompt_clip(project, third, vault=tmp_path).path == library / "cast-00-a.wav"


def test_reference_casting_measures_the_clip_and_locks_once(tmp_path, monkeypatch) -> None:
    project, _, _ = _spoken_take()
    lead = project.bible.characters[0].id
    clip = tmp_path / "references" / "mara-ref.wav"
    clip.parent.mkdir(parents=True)
    _write_tone(clip, seconds=8.0, active_seconds=7.0)
    project.assets.append(
        Asset(id="ast-ref-mara", kind=AssetKind.AUDIO, filename=clip.name, mime="audio/wav", bytes=clip.stat().st_size, path=str(clip.relative_to(tmp_path)), role="voice_reference")
    )
    registry = VoiceCastingRegistry(project)
    casting = registry.cast_from_reference(lead, "ast-ref-mara", prompt_text="A reference line.", vault=tmp_path)
    assert casting.source == VoiceCastingSource.REFERENCE
    assert casting.prompt_duration_seconds == pytest.approx(8.0, abs=0.01)
    assert casting.prompt_text == "A reference line."
    assert project.bible.characters[0].voice_casting_id == casting.id
    resolved = resolve_prompt_clip(project, casting, vault=tmp_path)
    assert resolved.path == clip and resolved.source == "reference"
    with pytest.raises(VoicePipelineError, match="already has a locked reference voice"):
        registry.cast_from_reference(lead, "ast-ref-mara", vault=tmp_path)

    short = tmp_path / "references" / "short.wav"
    _write_tone(short, seconds=2.0, active_seconds=1.0)
    project.assets.append(
        Asset(id="ast-ref-short", kind=AssetKind.AUDIO, filename=short.name, mime="audio/wav", bytes=short.stat().st_size, path=str(short.relative_to(tmp_path)), role="voice_reference")
    )
    other = project.bible.characters[1].id if len(project.bible.characters) > 1 else lead
    if other != lead:
        with pytest.raises(VoicePipelineError, match="5–15 second"):
            registry.cast_from_reference(other, "ast-ref-short", vault=tmp_path)


def _install_plan(project, shot) -> None:
    from diony_contracts.studio import _install_wan_prompt_plan

    _install_wan_prompt_plan(
        shot,
        idea=project.bible.logline,
        bible=project.bible,
        wardrobe=project.bible.characters[0].wardrobe if project.bible.characters else "",
        shot_index=0,
        sample_seconds=None,
    )


def test_prepare_locks_a_wan_speech_request_and_conditions_the_shot(tmp_path, monkeypatch) -> None:
    project, shot, take = _spoken_take()
    _library(tmp_path, monkeypatch)
    _install_plan(project, shot)
    shot.audio_asset_ids = ["ast-stale-audio"]
    character_id, text = spoken_line(project, shot)

    package = prepare_speech_for_take(project, take, vault=tmp_path)

    assert package is not None and take.voice_package is package
    assert package.state == VoiceRenderState.PLANNED and package.qc is None
    assert package.request.tts_text == text
    assert package.request.character_id == character_id
    assert package.request.tts_prompt_audio_sha256
    assert package.request.prompt_source.startswith("library:")
    assert package.request.window_ms == round(shot.duration_frames / shot.fps * 1000)
    assert shot.engine == Engine.S2V_14B and take.engine == Engine.S2V_14B
    assert shot.audio_asset_ids == []
    assert shot.prompt_plan is not None
    assert shot.prompt_plan.audio_strategy == WAN_NATIVE_TTS_AUDIO_STRATEGY
    assert is_spoken_plan(shot.prompt_plan)
    assert "speaks the line straight toward camera" in shot.prompt
    assert text not in shot.prompt  # the words ride --tts_text, never the picture prompt
    assert "subtitles" in shot.negative_prompt

    inputs = wan_speech_inputs(project, shot, vault=tmp_path)
    assert inputs == {
        "tts_text": text,
        "tts_prompt_audio": inputs["tts_prompt_audio"],
        "tts_prompt_text": inputs["tts_prompt_text"],
    }
    assert Path(inputs["tts_prompt_audio"]).is_file()

    again = prepare_speech_for_take(project, take, vault=tmp_path)
    assert again.request.content_sha256 == package.request.content_sha256


def test_a_line_that_cannot_finish_inside_the_window_holds_before_camera(tmp_path, monkeypatch) -> None:
    project, shot, take = _spoken_take()
    _library(tmp_path, monkeypatch)
    seconds = shot.duration_frames / shot.fps
    words = " ".join(["word"] * (math.ceil(seconds * MAX_WORDS_PER_SECOND) + 4))
    if shot.dialogue_turns:
        shot.dialogue_turns = [shot.dialogue_turns[0].model_copy(update={"text": words})]
    else:
        shot.audio_line = words
    with pytest.raises(VoicePipelineError, match="Voice timing failed"):
        prepare_speech_for_take(project, take, vault=tmp_path)
    assert take.voice_package is None


def test_two_speakers_in_one_window_is_a_casting_hold(tmp_path, monkeypatch) -> None:
    project, shot, take = _spoken_take()
    _library(tmp_path, monkeypatch)
    turns = list(shot.dialogue_turns)
    if not turns:
        pytest.skip("fixture shot has no dialogue turns")
    other = turns[0].model_copy(update={"id": "turn-other", "character_id": "char-someone-else", "text": "And me."})
    shot.dialogue_turns = [turns[0], other]
    with pytest.raises(VoicePipelineError, match="one speaker per window"):
        prepare_speech_for_take(project, take, vault=tmp_path)


def _negative(tmp_path: Path, *, audio: Path | None, seconds: float = 2.0) -> Path:
    video = tmp_path / "projects" / "demo" / "takes" / "t" / "negative_720.mp4"
    video.parent.mkdir(parents=True, exist_ok=True)
    command = [FFMPEG, "-nostdin", "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i", f"color=c=gray:s=64x64:r=16:d={seconds}"]
    if audio is not None:
        command += ["-i", str(audio), "-c:a", "aac", "-shortest"]
    command += ["-c:v", "libx264", "-pix_fmt", "yuv420p", str(video)]
    subprocess.run(command, check=True, capture_output=True)
    return video


@pytest.mark.skipif(FFMPEG is None, reason="ffmpeg is required to measure delivered speech")
def test_verify_measures_the_speech_wan_delivered_and_binds_the_negative(tmp_path, monkeypatch) -> None:
    project, shot, take = _spoken_take()
    _library(tmp_path, monkeypatch)
    shot.duration_frames = 48  # 2.0 s at 23.976
    prepare_speech_for_take(project, take, vault=tmp_path)
    tone = tmp_path / "line.wav"
    _write_tone(tone, seconds=2.0, active_seconds=1.2)
    video = _negative(tmp_path, audio=tone)

    package = verify_speech_render(project, take, video, vault=tmp_path)

    assert package.state == VoiceRenderState.VERIFIED, package.qc.diagnostics
    assert package.verified is True
    qc = package.qc
    assert qc.audio_present is True
    assert qc.speech_presence_score >= qc.speech_presence_min
    assert qc.speech_tail_ms >= qc.speech_tail_min_ms
    assert abs(qc.integrated_lufs - qc.loudness_target_lufs) <= qc.loudness_tolerance_lu
    assert qc.true_peak_dbtp <= qc.true_peak_ceiling_dbtp
    assert package.video_sha256
    roles = {project.asset(package.dialogue_asset_id).role, project.asset(package.mix_stem_asset_id).role}
    assert roles == {"voice_dialogue", "voice_mix_stem"}
    assert all((tmp_path / project.asset(asset_id).path).is_file() for asset_id in (package.dialogue_asset_id, package.mix_stem_asset_id))
    assert take.voice_package is package


@pytest.mark.skipif(FFMPEG is None, reason="ffmpeg is required to measure delivered speech")
def test_verify_fails_a_silent_negative_and_a_line_cut_by_the_picture(tmp_path, monkeypatch) -> None:
    project, shot, take = _spoken_take()
    _library(tmp_path, monkeypatch)
    shot.duration_frames = 48
    prepare_speech_for_take(project, take, vault=tmp_path)

    silent = _negative(tmp_path, audio=None)
    package = verify_speech_render(project, take, silent, vault=tmp_path)
    assert package.state == VoiceRenderState.FAILED
    assert package.qc.audio_present is False
    assert "no audio track" in speech_failure_message(package)

    cut = tmp_path / "cut.wav"
    _write_tone(cut, seconds=2.0, active_seconds=2.0, lead_silence=0.5)
    package = verify_speech_render(project, take, _negative(tmp_path, audio=cut), vault=tmp_path)
    assert package.state == VoiceRenderState.FAILED
    assert package.qc.speech_tail_ms is not None and package.qc.speech_tail_ms < package.qc.speech_tail_min_ms
    assert "cut by the picture" in speech_failure_message(package)


def test_host_speech_env_names_what_the_wan_pack_still_lacks(tmp_path) -> None:
    repo = tmp_path / "wan22"
    repo.mkdir()
    env, missing = host_speech_env(vault=tmp_path, wan_repo=repo)
    assert env == {}
    assert any("Wan TTS checkout" in item for item in missing)
    assert any("CosyVoice2-0.5B weights" in item for item in missing)

    (repo / "CosyVoice" / "cosyvoice" / "cli").mkdir(parents=True)
    (repo / "CosyVoice" / "cosyvoice" / "cli" / "cosyvoice.py").write_text("")
    (repo / "CosyVoice" / "third_party" / "Matcha-TTS").mkdir(parents=True)
    model = repo / "CosyVoice2-0.5B"
    model.mkdir()
    for name in ("cosyvoice2.yaml", "llm.pt", "flow.pt", "hift.pt", "speech_tokenizer_v2.onnx"):
        (model / name).write_bytes(b"x")
    assert speech_pack_missing(repo) == []
    (repo / "examples").mkdir()
    (repo / "examples" / "zero_shot_prompt.wav").write_bytes(b"RIFF")
    library = tmp_path / "runtime" / "voice" / "prompt-library"
    library.mkdir(parents=True)
    (library / "cast-00.wav").write_bytes(b"RIFF")
    env, missing = host_speech_env(vault=tmp_path, wan_repo=repo)
    assert missing == []
    assert set(env) == set(HOST_SPEECH_ENV_KEYS)
    assert env["DIONY_WAN_TTS_PROMPT_LIBRARY"] == str(library)
