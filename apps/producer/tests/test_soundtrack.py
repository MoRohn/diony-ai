"""Library-backed soundtrack planning and the 4-stem mix rails."""

import json
import shutil
import subprocess

import pytest

from diony_contracts.models import Brief
from diony_contracts.stitch import assemble_program
from diony_contracts.studio import expand_idea
from diony_producer import soundtrack as soundtrack_module
from diony_producer.soundtrack import (
    load_soundtrack_plan,
    plan_path,
    plan_soundtrack,
    scan_sound_library,
)


def _write_tone(path, *, seconds: float, frequency: int = 110, volume: float = 0.2) -> None:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        pytest.skip("FFmpeg tools are required")
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
            "-f", "lavfi",
            "-i", f"sine=frequency={frequency}:sample_rate=48000:duration={seconds}",
            "-af", f"volume={volume}",
            "-c:a", "pcm_s16le", str(path),
        ],
        check=True,
    )


def _library(tmp_path, monkeypatch):
    root = tmp_path / "library"
    _write_tone(root / "ambience" / "rain-city-night.wav", seconds=6.0, frequency=90)
    _write_tone(root / "foley" / "footsteps-wet-stone.wav", seconds=1.0, frequency=700, volume=0.4)
    _write_tone(root / "score" / "letter-theme-strings.wav", seconds=5.0, frequency=220)
    monkeypatch.setenv("DIONY_SOUND_LIBRARY", str(root))
    return root


def _project():
    project = expand_idea(
        Brief(
            idea="A courier delivers a letter that rewrites the city map.",
            customer_kind="feature",
            target_minutes=0.5,
        )
    )
    assemble_program(project)
    return project


def test_scan_sound_library_reads_roles_tags_and_durations(tmp_path, monkeypatch) -> None:
    _library(tmp_path, monkeypatch)
    cues = scan_sound_library()
    by_id = {cue.id: cue for cue in cues}
    assert set(by_id) == {
        "ambience/rain-city-night.wav",
        "foley/footsteps-wet-stone.wav",
        "score/letter-theme-strings.wav",
    }
    rain = by_id["ambience/rain-city-night.wav"]
    assert rain.role == "ambience"
    assert "rain" in rain.tags
    assert 5.5 <= rain.seconds <= 6.5
    assert len(rain.sha256) == 64


def test_plan_soundtrack_validates_clamps_and_persists_a_digest_receipt(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.delenv("DIONY_DRY_RUN", raising=False)
    monkeypatch.setenv("DIONY_VAULT", str(tmp_path / "vault"))
    monkeypatch.setenv("DIONY_PRODUCER_VAULT", str(tmp_path / "vault"))
    _library(tmp_path, monkeypatch)
    project = _project()
    response = {
        "cues": [
            {
                "cue_id": "ambience/rain-city-night.wav",
                "role": "ambience",
                "shot_id": None,
                "offset_frames": 0,
                "duration_frames": 100000,  # clamped to the cue's real length
                "gain_db": -80,  # clamped to the gain floor
            },
            {
                "cue_id": "foley/footsteps-wet-stone.wav",
                "role": "foley",
                "shot_id": project.shots[1].id,
                "offset_frames": 12,
                "duration_frames": 20,
                "gain_db": -6,
            },
            {"cue_id": "not/in-the-library.wav", "role": "score", "offset_frames": 0,
             "duration_frames": 24, "gain_db": 0},
            {"cue_id": "score/letter-theme-strings.wav", "role": "ambience",  # role mismatch
             "offset_frames": 0, "duration_frames": 24, "gain_db": 0},
        ],
        "rationale": "Rain bed under everything; one footstep hit; no score needed.",
    }
    activity = []
    monkeypatch.setattr(soundtrack_module, "_brain_reachable", lambda: True)
    monkeypatch.setattr(soundtrack_module, "brain_up", lambda: True)
    monkeypatch.setattr(soundtrack_module, "_chat", lambda *a, **k: json.dumps(response))
    monkeypatch.setattr(
        soundtrack_module, "record_activity", lambda src, text, **kw: activity.append(text)
    )

    receipt = plan_soundtrack(project)

    assert receipt is not None
    assert len(receipt["cues"]) == 2  # unknown id and role mismatch rejected
    ambience = receipt["cues"][0]
    assert ambience["gain_db"] == -36.0
    fps = project.bible.fps
    assert ambience["duration_frames"] <= int(6.5 * fps)
    assert any("planned 2 soundtrack cues" in text for text in activity)

    resolved = load_soundtrack_plan(project)
    assert resolved is not None and len(resolved) == 2
    foley = next(cue for cue in resolved if cue.role == "foley")
    anchor = next(
        event.start_frame for event in project.timeline.events
        if event.shot_id == project.shots[1].id
    )
    assert foley.start_seconds == pytest.approx((anchor + 12) / fps, abs=1e-4)

    # Tampering with a cue's bytes invalidates the whole plan.
    cue_file = tmp_path / "library" / "foley" / "footsteps-wet-stone.wav"
    cue_file.write_bytes(cue_file.read_bytes() + b"tampered")
    assert load_soundtrack_plan(project) is None
    # A tampered receipt also never applies.
    _write_tone(cue_file, seconds=1.0, frequency=700, volume=0.4)
    payload = json.loads(plan_path(project).read_text())
    payload["cues"][0]["gain_db"] = 6.0
    plan_path(project).write_text(json.dumps(payload))
    assert load_soundtrack_plan(project) is None


def test_plan_soundtrack_offline_brain_is_visible_and_keeps_dialogue_only(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.delenv("DIONY_DRY_RUN", raising=False)
    monkeypatch.setenv("DIONY_VAULT", str(tmp_path / "vault"))
    monkeypatch.setenv("DIONY_PRODUCER_VAULT", str(tmp_path / "vault"))
    _library(tmp_path, monkeypatch)
    project = _project()
    activity = []
    monkeypatch.setattr(soundtrack_module, "_brain_reachable", lambda: True)
    monkeypatch.setattr(soundtrack_module, "brain_up", lambda: False)
    monkeypatch.setattr(
        soundtrack_module, "record_activity", lambda src, text, **kw: activity.append(text)
    )

    assert plan_soundtrack(project) is None
    assert any("brain is offline" in text for text in activity)
    assert not plan_path(project).is_file()


def test_no_library_means_no_plan_and_no_noise(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("DIONY_VAULT", str(tmp_path / "vault"))
    monkeypatch.setenv("DIONY_SOUND_LIBRARY", str(tmp_path / "missing"))
    project = _project()
    assert plan_soundtrack(project) is None


@pytest.mark.skipif(
    not shutil.which("ffmpeg") or not shutil.which("ffprobe"), reason="FFmpeg is required"
)
def test_picture_only_program_mixes_planned_library_stems(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("DIONY_DRY_RUN", raising=False)
    """With a licensed library plan, a dialogue-free program becomes deliverable."""
    from diony_producer.mix import bounce_mix

    monkeypatch.setenv("DIONY_VAULT", str(tmp_path / "vault"))
    monkeypatch.setenv("DIONY_PRODUCER_VAULT", str(tmp_path / "vault"))
    monkeypatch.setenv("DIONY_FINISH_FAKE", "1")
    _library(tmp_path, monkeypatch)
    project = _project()
    assert all(not shot.audio_line and not shot.dialogue_turns for shot in project.shots)
    response = {
        "cues": [
            {
                "cue_id": "ambience/rain-city-night.wav",
                "role": "ambience",
                "shot_id": None,
                "offset_frames": 0,
                "duration_frames": 120,
                "gain_db": -20,
            },
            {
                "cue_id": "score/letter-theme-strings.wav",
                "role": "score",
                "shot_id": None,
                "offset_frames": 24,
                "duration_frames": 96,
                "gain_db": -14,
            },
        ],
        "rationale": "Rain bed with a late string entrance.",
    }
    monkeypatch.setattr(soundtrack_module, "_brain_reachable", lambda: True)
    monkeypatch.setattr(soundtrack_module, "brain_up", lambda: True)
    monkeypatch.setattr(soundtrack_module, "_chat", lambda *a, **k: json.dumps(response))
    monkeypatch.setattr(soundtrack_module, "record_activity", lambda *a, **kw: None)
    assert plan_soundtrack(project) is not None

    project.timeline.preview_path = (
        f"projects/{project.id}/program/{project.timeline.id}/master_1080.mov"
    )
    master = tmp_path / "vault" / project.timeline.preview_path
    master.parent.mkdir(parents=True)
    subprocess.run(
        [
            shutil.which("ffmpeg"), "-hide_banner", "-loglevel", "error",
            "-f", "lavfi", "-i", "color=c=black:s=1920x1080:r=24:d=6",
            "-an", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(master),
        ],
        check=True,
    )
    monkeypatch.setattr(
        "diony_producer.mix.gallery_program_preview_path",
        lambda *_args: tmp_path / "preview.mp4",
    )

    bounce_mix(project)

    roles = [project.asset(asset_id).role for asset_id in project.mix.stem_asset_ids]
    assert roles == ["ambience_stem", "score_stem", "program_mix_stem"]
    target = -24.0
    assert abs(project.mix.measured_lufs - target) <= 1.0
    assert project.mix.true_peak_dbtp <= project.bible.true_peak_dbtp
    from diony_producer.mix import program_master_has_audio

    assert program_master_has_audio(project) is True


def _enable_generation(tmp_path, monkeypatch, project):
    import sys
    import textwrap

    from diony_contracts.enums import SoundtrackPolicy

    project.bible.soundtrack_policy = SoundtrackPolicy.GENERATED_OK
    monkeypatch.setenv("DIONY_AUDIOGEN", "enabled")
    backend = "apps/producer/src/diony_producer/audiogen_backend.py"
    monkeypatch.setenv("DIONY_AUDIOGEN_COMMAND", f"{sys.executable} {backend}")
    stub = tmp_path / "stub_generator.py"
    stub.write_text(
        textwrap.dedent(
            """
            import subprocess, sys
            args = dict(zip(sys.argv[1::2], sys.argv[2::2]))
            subprocess.run(
                [
                    "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                    "-f", "lavfi",
                    "-i", f"anoisesrc=color=pink:amplitude=0.05:duration={args['--seconds']}:seed={args['--seed']}",
                    "-c:a", "pcm_s16le", args["--output"],
                ],
                check=True,
            )
            """
        )
    )
    template = f"{sys.executable} {stub} --seconds {{seconds}} --seed {{seed}} --output {{output}}"
    monkeypatch.setenv("DIONY_AUDIOGEN_CLI_ACE_STEP", template)
    monkeypatch.setenv("DIONY_AUDIOGEN_CLI_STABLE_AUDIO_OPEN", template)
    return stub


def test_generation_needs_site_optin_bible_policy_and_adapter(tmp_path, monkeypatch) -> None:
    from diony_contracts.enums import SoundtrackPolicy
    from diony_producer.soundtrack import generation_enabled

    monkeypatch.setenv("DIONY_VAULT", str(tmp_path / "vault"))
    project = _project()
    monkeypatch.delenv("DIONY_AUDIOGEN", raising=False)
    monkeypatch.setenv("DIONY_AUDIOGEN_COMMAND", "python backend.py")
    assert generation_enabled(project) is False  # no site opt-in
    monkeypatch.setenv("DIONY_AUDIOGEN", "enabled")
    assert generation_enabled(project) is False  # Bible policy still licensed_only
    project.bible.soundtrack_policy = SoundtrackPolicy.GENERATED_OK
    assert generation_enabled(project) is True
    monkeypatch.delenv("DIONY_AUDIOGEN_COMMAND", raising=False)
    assert generation_enabled(project) is False  # no adapter


def test_generate_rows_validate_roles_video_anchors_and_budgets(tmp_path, monkeypatch) -> None:
    from diony_producer.soundtrack import _validated_rows

    monkeypatch.setenv("DIONY_VAULT", str(tmp_path / "vault"))
    project = _project()
    fps = project.bible.fps
    candidate = [
        {"source": "generate", "generator": "ace-step", "role": "score",
         "prompt": "restrained string theme, rainy city night", "shot_id": None,
         "offset_frames": 24, "duration_frames": 100000, "gain_db": -12},
        # ThinkSound is video-conditioned: a row without a shot anchor is invalid.
        {"source": "generate", "generator": "thinksound", "role": "foley",
         "prompt": "footsteps on wet stone", "shot_id": None,
         "offset_frames": 0, "duration_frames": 24, "gain_db": -6},
        # Role must match the instrument.
        {"source": "generate", "generator": "ace-step", "role": "foley",
         "prompt": "impact", "shot_id": None, "offset_frames": 0,
         "duration_frames": 24, "gain_db": 0},
        {"source": "generate", "generator": "thinksound", "role": "foley",
         "prompt": "satchel strap settles", "shot_id": project.shots[1].id,
         "offset_frames": 10, "duration_frames": 4000, "gain_db": -4},
    ]
    rows = _validated_rows(candidate, {}, project, allow_generation=True)
    assert [row["generator"] for row in rows] == ["ace-step", "thinksound"]
    assert rows[0]["duration_frames"] <= int(240.0 * fps)
    # A shot-anchored ThinkSound cue never outruns its shot's booked frames.
    assert rows[1]["duration_frames"] == project.shots[1].duration_frames
    assert isinstance(rows[1]["seed"], int)
    assert _validated_rows(candidate, {}, project, allow_generation=False) == []


@pytest.mark.skipif(
    not shutil.which("ffmpeg") or not shutil.which("ffprobe"), reason="FFmpeg is required"
)
def test_generated_cues_render_through_the_adapter_and_mix(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("DIONY_DRY_RUN", raising=False)
    """Picture-only program + generated score/ambience becomes a deliverable mix."""
    from diony_producer.mix import bounce_mix
    from diony_producer.soundtrack import ensure_generated_cues, load_soundtrack_plan

    monkeypatch.setenv("DIONY_VAULT", str(tmp_path / "vault"))
    monkeypatch.setenv("DIONY_PRODUCER_VAULT", str(tmp_path / "vault"))
    monkeypatch.setenv("DIONY_FINISH_FAKE", "1")
    monkeypatch.setenv("DIONY_SOUND_LIBRARY", str(tmp_path / "no-library"))
    project = _project()
    _enable_generation(tmp_path, monkeypatch, project)
    response = {
        "cues": [
            {"source": "generate", "generator": "stable-audio-open", "role": "ambience",
             "prompt": "steady night rain on cobblestones", "shot_id": None,
             "offset_frames": 0, "duration_frames": 120, "gain_db": -18},
            {"source": "generate", "generator": "ace-step", "role": "score",
             "prompt": "sparse string theme, hopeful", "shot_id": None,
             "offset_frames": 24, "duration_frames": 96, "gain_db": -14},
        ],
        "rationale": "No library on this site; generated bed and a late theme.",
    }
    monkeypatch.setattr(soundtrack_module, "_brain_reachable", lambda: True)
    monkeypatch.setattr(soundtrack_module, "brain_up", lambda: True)
    monkeypatch.setattr(soundtrack_module, "_chat", lambda *a, **k: json.dumps(response))
    monkeypatch.setattr(soundtrack_module, "record_activity", lambda *a, **kw: None)
    monkeypatch.setattr("diony_producer.mix.record_activity", lambda *a, **kw: None)
    receipt = plan_soundtrack(project)
    assert receipt is not None and len(receipt["cues"]) == 2

    ensure_generated_cues(project)
    resolved = load_soundtrack_plan(project)
    assert resolved is not None and len(resolved) == 2
    assert all(cue.source == "generate" for cue in resolved)
    for cue in resolved:
        assert (tmp_path / "vault").as_posix() in cue.path
        receipt_file = json.loads((cue.path + ".receipt.json") and open(cue.path + ".receipt.json").read())
        assert receipt_file["generator"] in {"stable-audio-open", "ace-step"}
        assert receipt_file["measurements"]["speech_presence_score"] < 0.20
        assert len(receipt_file["sha256"]) == 64
    project.timeline.preview_path = (
        f"projects/{project.id}/program/{project.timeline.id}/master_1080.mov"
    )
    master = tmp_path / "vault" / project.timeline.preview_path
    master.parent.mkdir(parents=True)
    subprocess.run(
        [
            shutil.which("ffmpeg"), "-hide_banner", "-loglevel", "error",
            "-f", "lavfi", "-i", "color=c=black:s=1920x1080:r=24:d=6",
            "-an", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(master),
        ],
        check=True,
    )
    monkeypatch.setattr(
        "diony_producer.mix.gallery_program_preview_path",
        lambda *_args: tmp_path / "preview.mp4",
    )

    bounce_mix(project)

    roles = [project.asset(asset_id).role for asset_id in project.mix.stem_asset_ids]
    assert roles == ["ambience_stem", "score_stem", "program_mix_stem"]
    assert abs(project.mix.measured_lufs + 24.0) <= 1.0


@pytest.mark.skipif(
    not shutil.which("ffmpeg") or not shutil.which("ffprobe"), reason="FFmpeg is required"
)
def test_adapter_rejects_a_cue_that_contains_speech(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("DIONY_DRY_RUN", raising=False)
    """CosyVoice stays the only speech instrument: voiced cues fail the gate."""
    import sys
    import textwrap

    from diony_producer.soundtrack import ensure_generated_cues, load_soundtrack_plan

    monkeypatch.setenv("DIONY_VAULT", str(tmp_path / "vault"))
    monkeypatch.setenv("DIONY_SOUND_LIBRARY", str(tmp_path / "no-library"))
    project = _project()
    _enable_generation(tmp_path, monkeypatch, project)
    voiced_stub = tmp_path / "voiced_stub.py"
    voiced_stub.write_text(
        textwrap.dedent(
            """
            import subprocess, sys
            args = dict(zip(sys.argv[1::2], sys.argv[2::2]))
            subprocess.run(
                [
                    "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                    "-f", "lavfi",
                    "-i", f"sine=frequency=150:sample_rate=16000:duration={args['--seconds']}",
                    "-af", "volume=0.2*(0.5+0.5*sin(2.2*2*PI*t)):eval=frame",
                    "-c:a", "pcm_s16le", args["--output"],
                ],
                check=True,
            )
            """
        )
    )
    monkeypatch.setenv(
        "DIONY_AUDIOGEN_CLI_ACE_STEP",
        f"{sys.executable} {voiced_stub} --seconds {{seconds}} --seed {{seed}} --output {{output}}",
    )
    response = {
        "cues": [
            {"source": "generate", "generator": "ace-step", "role": "score",
             "prompt": "humming melody", "shot_id": None,
             "offset_frames": 0, "duration_frames": 96, "gain_db": -14},
        ],
        "rationale": "One theme.",
    }
    warns = []
    monkeypatch.setattr(soundtrack_module, "_brain_reachable", lambda: True)
    monkeypatch.setattr(soundtrack_module, "brain_up", lambda: True)
    monkeypatch.setattr(soundtrack_module, "_chat", lambda *a, **k: json.dumps(response))
    monkeypatch.setattr(
        soundtrack_module, "record_activity", lambda src, text, **kw: warns.append(text)
    )
    assert plan_soundtrack(project) is not None

    ensure_generated_cues(project)

    assert any("render or QC failed" in text for text in warns)
    assert load_soundtrack_plan(project) == []
