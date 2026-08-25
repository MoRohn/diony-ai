import json
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pytest

from diony_producer.cosyvoice_backend import (
    BackendError,
    _av_qc,
    alignment_payload,
    cosine_similarity,
    prompt_audio_for,
    speech_presence_score,
    snr_db_from_frame_rms,
    studio_audio_filters,
)
from diony_producer.voice import VoicePipelineError, VoicePerformancePipeline
from diony_producer.cosyvoice_worker import embedding_values


def test_alignment_payload_covers_authored_words_and_clip_timing(tmp_path: Path) -> None:
    audio = tmp_path / "line.wav"
    audio.write_bytes(b"RIFF" + b"0" * 128)
    line = {
        "id": "line-1",
        "text": "Every frame begins with light.",
        "start_ms": 250,
        "target_duration_ms": 2000,
        "intensity_curve": [0.25, 0.45, 0.3],
    }

    payload = alignment_payload(audio, line, measured_duration_ms=1800)

    assert payload["line_id"] == "line-1"
    assert payload["source"] == "cosyvoice-duration-alignment-v1"
    assert [cue["word"] for cue in payload["cues"]] == [
        "Every",
        "frame",
        "begins",
        "with",
        "light.",
    ]
    assert payload["cues"][0]["start_ms"] == 250
    assert payload["cues"][-1]["end_ms"] == 2050


def test_studio_audio_filters_keep_broadcast_loudness_and_peak_contract() -> None:
    clean, final = studio_audio_filters(target_lufs=-24.0, true_peak_dbtp=-1.0)

    assert "amix" in clean
    assert "highpass" in final
    assert "deesser" in final
    assert "acompressor" in final
    assert "loudnorm=I=-24:TP=-1" in final


def test_snr_uses_silent_noise_floor_not_quiet_speech_as_noise() -> None:
    assert snr_db_from_frame_rms([0.0008, 0.001, 0.09, 0.1, 0.11]) >= 30.0
    assert snr_db_from_frame_rms([0.0] * 6 + [0.007, 0.016, 0.036, 0.075, 0.1]) >= 60.0


def test_speech_presence_rejects_noise_and_accepts_voiced_content() -> None:
    sample_rate = 16_000
    seconds = 3.0
    t = np.arange(round(sample_rate * seconds), dtype=np.float64) / sample_rate
    noise = np.random.default_rng(10401).normal(0.0, 0.08, t.shape)
    syllables = np.maximum(0.0, np.sin(2 * np.pi * 2.2 * t)) ** 1.5
    voiced = syllables * (
        0.7 * np.sin(2 * np.pi * 155 * t)
        + 0.2 * np.sin(2 * np.pi * 310 * t)
        + 0.1 * np.sin(2 * np.pi * 465 * t)
    )

    assert speech_presence_score(noise, sample_rate=sample_rate) < 0.20
    assert speech_presence_score(voiced, sample_rate=sample_rate) >= 0.20


def test_cosine_similarity_rejects_dimension_drift() -> None:
    assert cosine_similarity([1.0, 0.0], [1.0, 0.0]) == pytest.approx(1.0)
    with pytest.raises(BackendError, match="dimensions"):
        cosine_similarity([1.0], [1.0, 0.0])


def test_campplus_embedding_values_flatten_batched_vectors() -> None:
    assert embedding_values([[0.25, -0.5, 0.75]]) == [0.25, -0.5, 0.75]


def test_campplus_frontend_receives_audio_path_not_preloaded_tensor() -> None:
    source = Path(__file__).resolve().parents[1] / "src" / "diony_producer" / "cosyvoice_worker.py"
    text = source.read_text()

    assert "_extract_spk_embedding(args.embed_audio)" in text


def test_cosyvoice_synthesis_receives_prompt_audio_path() -> None:
    source = Path(__file__).resolve().parents[1] / "src" / "diony_producer" / "cosyvoice_worker.py"
    text = source.read_text()

    assert "inference_cross_lingual(args.text, args.prompt_audio)" in text


def test_cosyvoice_worker_can_measure_the_render_in_the_same_model_session() -> None:
    source = Path(__file__).resolve().parents[1] / "src" / "diony_producer" / "cosyvoice_worker.py"
    text = source.read_text()

    assert "embedding = voice.frontend._extract_spk_embedding(str(output))" in text


def test_backend_module_is_a_command_adapter() -> None:
    source = Path(__file__).resolve().parents[1] / "src" / "diony_producer" / "cosyvoice_backend.py"
    text = source.read_text()

    for action in (
        "synthesize",
        "embed",
        "embed-reference",
        "enroll-synthetic",
        "align",
        "studio-audio",
        "audiovisual-qc",
    ):
        assert f'"{action}"' in text
    assert '"cosyvoice-campplus-v1"' in text
    assert '"cosyvoice2-0.5b-local-v1"' in text
    assert "measured_I=" in text
    assert "linear=true" in text


def test_prompt_audio_prefers_reference_then_library_then_shared_default(
    tmp_path, monkeypatch
) -> None:
    reference = tmp_path / "marcus-reference.wav"
    reference.write_bytes(b"RIFF" + b"0" * 64)
    library = tmp_path / "prompt-library"
    library.mkdir()
    clips = []
    for name in ("voice-a.wav", "voice-b.wav", "voice-c.wav"):
        clip = library / name
        clip.write_bytes(b"RIFF" + name.encode())
        clips.append(str(clip))
    shared = tmp_path / "zero_shot_prompt.wav"
    shared.write_bytes(b"RIFF" + b"1" * 64)
    monkeypatch.setenv("DIONY_COSYVOICE_PROMPT_LIBRARY", str(library))
    monkeypatch.setenv("DIONY_COSYVOICE_PROMPT_AUDIO", str(shared))

    audio, source = prompt_audio_for(
        {
            "reference_paths": [str(reference)],
            "profile": {"character_id": "char-marcus"},
        }
    )
    assert (audio, source) == (str(reference), "reference-sample")

    # Cast order maps distinct characters to distinct library clips.
    first, source = prompt_audio_for(
        {"profile": {"character_id": "char-marcus"}, "cast_index": 0}
    )
    second, _ = prompt_audio_for(
        {"profile": {"character_id": "char-lila"}, "cast_index": 1}
    )
    assert source == "voice-library"
    assert first == clips[0]
    assert second == clips[1]
    assert first != second

    monkeypatch.delenv("DIONY_COSYVOICE_PROMPT_LIBRARY", raising=False)
    audio, source = prompt_audio_for({"profile": {"character_id": "char-marcus"}})
    assert (audio, source) == (str(shared), "shared-default")


def test_cast_distinct_gate_rejects_a_collapsed_two_hander() -> None:
    from diony_contracts.voice import EmotionPerformance, ProsodySignature, VoiceProfile

    def profile(character_id: str, embedding: list[float]) -> VoiceProfile:
        return VoiceProfile(
            id=f"voice-{character_id}-v1",
            project_id="studio-test",
            character_id=character_id,
            version=1,
            source="synthetic",
            speaker_embedding_model="cosyvoice-campplus-v1",
            speaker_embedding=embedding,
            prosody=ProsodySignature(),
            emotion_map=[EmotionPerformance(emotion="neutral")],
            seed=1,
            style_lock=f"identity:{character_id}",
            locked=True,
            locked_at=datetime.now(timezone.utc),
        )

    same = [0.4, 0.3, 0.2, 0.1]
    with pytest.raises(VoicePipelineError, match="cast-distinct"):
        VoicePerformancePipeline._verify_cast_distinct(
            [profile("char-marcus", same), profile("char-lila", list(same))]
        )
    # Distinct voices pass, and one character never competes with itself.
    VoicePerformancePipeline._verify_cast_distinct(
        [
            profile("char-marcus", [1.0, 0.0, 0.0, 0.0]),
            profile("char-marcus", [1.0, 0.0, 0.0, 0.0]),
            profile("char-lila", [0.0, 1.0, 0.0, 0.0]),
        ]
    )


def _write_correlated_clip(tmp_path, *, audio_shift_frames: float = 0.0):
    """Render a 160x90 clip whose luma changes are echoed by audio bursts."""
    ffmpeg = shutil.which("ffmpeg")
    ffprobe = shutil.which("ffprobe")
    if not ffmpeg or not ffprobe:
        pytest.skip("FFmpeg tools are required")
    tmp_path.mkdir(parents=True, exist_ok=True)
    fps, seconds, sample_rate = 24, 4, 16_000
    frame_count = fps * seconds
    rng = np.random.default_rng(11)
    toggles = rng.random(frame_count) < 0.35
    luma = np.empty(frame_count, dtype=np.uint8)
    level = 30
    for index in range(frame_count):
        if toggles[index]:
            level = 220 if level == 30 else 30
        luma[index] = level
    raw = np.repeat(luma, 160 * 90).tobytes()
    video = tmp_path / "clip.mp4"
    subprocess.run(
        [
            ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
            "-f", "rawvideo", "-pix_fmt", "gray", "-s", "160x90", "-r", str(fps), "-i", "-",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", str(video),
        ],
        input=raw,
        check=True,
    )
    window = sample_rate // fps
    gains = np.full(frame_count, 0.03)
    motion_frames = np.flatnonzero(np.abs(np.diff(luma.astype(int))) > 0)
    gains[np.clip(motion_frames + 1, 0, frame_count - 1)] = 0.9
    carrier = np.sin(2 * np.pi * 220 * np.arange(frame_count * window) / sample_rate)
    samples = (carrier * np.repeat(gains, window)).astype("<f4")
    if audio_shift_frames:
        samples = np.roll(samples, round(audio_shift_frames * window))
    audio = tmp_path / "dialogue.wav"
    subprocess.run(
        [
            ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
            "-f", "f32le", "-ar", str(sample_rate), "-ac", "1", "-i", "-",
            "-c:a", "pcm_s16le", str(audio),
        ],
        input=samples.tobytes(),
        check=True,
    )
    metadata = tmp_path / "performance.json"
    metadata.write_text(json.dumps({"lines": []}))
    return video, audio, metadata


def test_av_qc_qualifies_synced_performance_and_measures_a_real_offset(tmp_path) -> None:
    video, audio, metadata = _write_correlated_clip(tmp_path)
    payload = _av_qc(
        {"video": str(video), "audio": str(audio), "performance_metadata": str(metadata)}
    )
    assert payload["backend"] == "diony-local-av-motion-sync-v2"
    assert payload["qualified_for_visual_retake"] is True
    assert payload["audiovisual_offset_ms_p95"] <= 25.0
    assert payload["lip_sync_confidence"] >= 0.90

    shifted_video, shifted_audio, shifted_metadata = _write_correlated_clip(
        tmp_path / "shifted", audio_shift_frames=3.0
    )
    shifted = _av_qc(
        {
            "video": str(shifted_video),
            "audio": str(shifted_audio),
            "performance_metadata": str(shifted_metadata),
        }
    )
    # A three-frame slip at 24 fps is 125 ms; the measurement must see it and
    # still stand as qualified evidence so the farm can act on the offset gate.
    assert shifted["qualified_for_visual_retake"] is True
    assert 90.0 <= shifted["audiovisual_offset_ms_p95"] <= 160.0


def test_av_qc_stays_unqualified_on_a_static_plate(tmp_path) -> None:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        pytest.skip("FFmpeg tools are required")
    video = tmp_path / "static.mp4"
    subprocess.run(
        [
            ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
            "-f", "lavfi", "-i", "color=c=gray:s=160x90:r=24:d=4",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", str(video),
        ],
        check=True,
    )
    audio = tmp_path / "dialogue.wav"
    subprocess.run(
        [
            ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
            "-f", "lavfi", "-i", "sine=frequency=220:sample_rate=16000:duration=4",
            "-c:a", "pcm_s16le", str(audio),
        ],
        check=True,
    )
    metadata = tmp_path / "performance.json"
    metadata.write_text(json.dumps({"lines": []}))
    payload = _av_qc(
        {"video": str(video), "audio": str(audio), "performance_metadata": str(metadata)}
    )
    assert payload["qualified_for_visual_retake"] is False
    assert any("unqualified" in row for row in payload["diagnostics"])
