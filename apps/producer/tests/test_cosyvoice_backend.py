import json
from pathlib import Path

import pytest

from diony_producer.cosyvoice_backend import (
    BackendError,
    alignment_payload,
    cosine_similarity,
    snr_db_from_frame_rms,
    studio_audio_filters,
)
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
