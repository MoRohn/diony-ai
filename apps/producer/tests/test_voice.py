from __future__ import annotations

from pathlib import Path

from diony_contracts.enums import Engine, VoiceProfileSource, VoiceRenderState
from diony_contracts.fixture import demo_project
from diony_producer.voice import (
    DeterministicVoiceTestBackend,
    VoicePerformancePipeline,
    VoiceProfileRegistry,
    optimize_voice_script,
)


def _spoken_take():
    project = demo_project()
    shot = project.shots[0]
    take = project.take(shot.selected_take_id)
    take.artifacts = []
    take.asset_720p_id = None
    take.asset_1080p_id = None
    take.qc = None
    return project, shot, take


def test_registry_creates_one_project_scoped_locked_synthetic_profile() -> None:
    project, _, _ = _spoken_take()
    registry = VoiceProfileRegistry(project)

    first = registry.ensure_synthetic("char-mara")
    second = registry.ensure_synthetic("char-mara")

    assert first is second
    assert first.project_id == project.id
    assert first.source == VoiceProfileSource.SYNTHETIC
    assert first.locked is True
    assert project.bible.characters[0].voice_profile_id == first.id
    assert project.voice_profiles == [first]


def test_registry_can_lock_a_measured_local_synthetic_prompt_embedding() -> None:
    project, _, _ = _spoken_take()
    measured = [0.1, 0.2, 0.3, 0.4]

    profile = VoiceProfileRegistry(project).ensure_synthetic(
        "char-mara",
        speaker_embedding=measured,
        speaker_embedding_model="cosyvoice-campplus-v1",
    )

    assert list(profile.speaker_embedding) == measured
    assert profile.speaker_embedding_model == "cosyvoice-campplus-v1"


def test_same_character_lock_is_identical_across_episode_projects() -> None:
    first_project, _, _ = _spoken_take()
    second_project, _, _ = _spoken_take()
    second_project.id = "ep-night-press-002"

    first = VoiceProfileRegistry(first_project).ensure_synthetic("char-mara")
    second = VoiceProfileRegistry(second_project).ensure_synthetic("char-mara")

    assert first.project_id != second.project_id
    assert first.identity_lineage_id == second.identity_lineage_id
    assert first.seed == second.seed
    assert first.style_lock == second.style_lock
    assert first.speaker_embedding == second.speaker_embedding


def test_voice_script_optimizer_is_deterministic_and_profile_aware() -> None:
    project, shot, _ = _spoken_take()
    profile = VoiceProfileRegistry(project).ensure_synthetic("char-mara")

    first = optimize_voice_script(project, shot, [profile])
    second = optimize_voice_script(project, shot, [profile])

    assert first.content_sha256 == second.content_sha256
    assert first.lines[0].voice_profile_id == profile.id
    assert first.lines[0].target_duration_ms <= round(shot.duration_frames / shot.fps * 1000)
    assert first.lines[0].breath_after_word_indices
    assert first.lines[0].intensity_curve
    assert shot.camera_move.value in first.camera_language
    assert first.pacing_target_ms == round(shot.duration_frames / shot.fps * 1000)
    assert project.bible.logline in first.narrative_context


def test_voice_pipeline_locks_audio_before_s2v_and_reuses_content_cache(tmp_path) -> None:
    project, shot, take = _spoken_take()
    backend = DeterministicVoiceTestBackend()
    pipeline = VoicePerformancePipeline(
        vault=tmp_path,
        synthesizer=backend,
        embedder=backend,
        aligner=backend,
        audio_processor=backend,
        audiovisual_qc=backend,
    )

    first = pipeline.prepare_take(project, take)
    second = pipeline.prepare_take(project, take)

    assert backend.synthesis_calls == 1
    assert first.cache_key == second.cache_key
    assert first.state == VoiceRenderState.AUDIO_LOCKED
    assert first.qc.audio_passed is True
    assert first.qc.passed is False
    assert shot.engine == Engine.S2V_14B
    assert take.engine == Engine.S2V_14B
    assert shot.audio_asset_ids == [first.clean_dialogue_asset_id]
    roles = {project.asset(asset_id).role for asset_id in (
        first.clean_dialogue_asset_id,
        first.final_mix_asset_id,
        first.performance_metadata_asset_id,
    )}
    assert roles == {"voice_clean_dialogue", "voice_final_mix", "voice_performance_metadata"}
    assert all((tmp_path / project.asset(asset_id).path).is_file() for asset_id in (
        first.clean_dialogue_asset_id,
        first.final_mix_asset_id,
        first.performance_metadata_asset_id,
    ))


def test_local_synthetic_voice_enrolls_from_a_render_before_identity_qc(
    tmp_path, monkeypatch
) -> None:
    class RenderedEnrollmentBackend(DeterministicVoiceTestBackend):
        name = "measured-local-voice"

        def __init__(self) -> None:
            super().__init__()
            self.enrollment_calls = 0
            self.enrolled_embedding = []

        def reference_embedding(self, audio, model):
            del audio, model
            return [1.0, 0.0]

        def enroll_synthetic(self, line, profile, output):
            self.enrollment_calls += 1
            self.synthesize(line, profile, output, seed=profile.seed, reference_paths=[])
            self.enrolled_embedding = [0.0] * len(profile.speaker_embedding)
            self.enrolled_embedding[1] = 1.0
            return self.enrolled_embedding

        def speaker_embedding(self, audio, profile):
            del audio, profile
            return self.enrolled_embedding

    prompt = tmp_path / "conditioning-prompt.wav"
    prompt.write_bytes(b"RIFF" + b"0" * 128)
    monkeypatch.setenv("DIONY_COSYVOICE_PROMPT_AUDIO", str(prompt))
    monkeypatch.setenv("DIONY_SPEAKER_EMBEDDING_MODEL", "cosyvoice-campplus-v1")
    project, _, take = _spoken_take()
    backend = RenderedEnrollmentBackend()
    pipeline = VoicePerformancePipeline(
        vault=tmp_path,
        synthesizer=backend,
        embedder=backend,
        aligner=backend,
        audio_processor=backend,
        audiovisual_qc=backend,
    )

    package = pipeline.prepare_take(project, take)

    profile = project.voice_profiles[0]
    assert backend.enrollment_calls == 1
    assert backend.synthesis_calls == 2
    assert profile.speaker_embedding == tuple(backend.enrolled_embedding)
    assert profile.speaker_embedding_model == "cosyvoice-campplus-v1:synthetic-enrollment-v1"
    assert package.qc.speaker_similarity == 1.0
    assert package.qc.speaker_similarity_min == 0.92


def test_audiovisual_qc_promotes_package_only_after_video_evidence(tmp_path) -> None:
    project, _, take = _spoken_take()
    backend = DeterministicVoiceTestBackend()
    pipeline = VoicePerformancePipeline(
        vault=tmp_path,
        synthesizer=backend,
        embedder=backend,
        aligner=backend,
        audio_processor=backend,
        audiovisual_qc=backend,
    )
    pipeline.prepare_take(project, take)
    negative = Path(tmp_path / "negative.mp4")
    negative.write_bytes(b"video evidence" * 8)

    verified = pipeline.verify_animation(project, take, negative)

    assert verified.state == VoiceRenderState.VERIFIED
    assert verified.qc.passed is True
    assert verified.video_sha256 == __import__("hashlib").sha256(negative.read_bytes()).hexdigest()
    assert verified.qc.lip_sync_confidence >= verified.qc.lip_sync_confidence_min
    assert verified.qc.audiovisual_offset_ms_p95 <= verified.qc.audiovisual_offset_ms_max
    assert verified.qc.performance_consistency >= verified.qc.performance_consistency_min


def test_multiple_dialogue_turns_keep_separate_voice_profiles_and_overlap_intent(tmp_path) -> None:
    from diony_contracts.models import Character, DialogueTurn

    project, shot, take = _spoken_take()
    project.bible.characters.append(Character(id="char-editor", name="The Editor"))
    shot.dialogue_turns = [
        DialogueTurn(
            id="turn-mara",
            character_id="char-mara",
            text="The page is already locked.",
            emotion="tension",
            intensity_curve=[0.3, 0.65],
            target_duration_ms=1800,
        ),
        DialogueTurn(
            id="turn-editor",
            character_id="char-editor",
            text="Then stop the presses.",
            emotion="command",
            intensity_curve=[0.6, 0.9],
            start_ms=1500,
            overlap_ms=300,
            interrupts_turn_id="turn-mara",
            target_duration_ms=1400,
        ),
    ]
    backend = DeterministicVoiceTestBackend()
    pipeline = VoicePerformancePipeline(
        vault=tmp_path,
        synthesizer=backend,
        embedder=backend,
        aligner=backend,
        audio_processor=backend,
        audiovisual_qc=backend,
    )

    package = pipeline.prepare_take(project, take)

    assert len(package.voice_profile_ids) == 2
    assert len(package.script.lines) == 2
    assert package.script.lines[1].overlap_ms == 300
    assert package.script.lines[1].interrupts_line_id == "turn-mara"
    assert backend.synthesis_calls == 2


def test_independent_dialogue_lines_synthesize_in_parallel(tmp_path) -> None:
    import threading

    from diony_contracts.models import Character, DialogueTurn

    class RendezvousBackend(DeterministicVoiceTestBackend):
        def __init__(self) -> None:
            super().__init__()
            self.rendezvous = threading.Barrier(2)

        def synthesize(self, *args, **kwargs) -> None:
            self.rendezvous.wait(timeout=2)
            super().synthesize(*args, **kwargs)

    project, shot, take = _spoken_take()
    project.bible.characters.append(Character(id="char-editor", name="The Editor"))
    shot.dialogue_turns = [
        DialogueTurn(id="turn-mara", character_id="char-mara", text="Hold the presses."),
        DialogueTurn(id="turn-editor", character_id="char-editor", text="The edition is locked."),
    ]
    backend = RendezvousBackend()
    pipeline = VoicePerformancePipeline(
        vault=tmp_path,
        synthesizer=backend,
        embedder=backend,
        aligner=backend,
        audio_processor=backend,
        audiovisual_qc=backend,
    )

    package = pipeline.prepare_take(project, take)

    assert package.qc.audio_passed is True
    assert backend.synthesis_calls == 2


def test_run_one_refreshes_creative_contract_then_locks_voice_before_camera(monkeypatch) -> None:
    from diony_contracts.enums import QCVerdict
    from diony_contracts.models import QCReport
    from diony_producer import farm

    project, _, take = _spoken_take()
    order: list[str] = []

    def prepare(current_project, current_take):
        order.append("voice")
        current_project.shot(current_take.shot_id).engine = Engine.S2V_14B
        current_take.engine = Engine.S2V_14B

    def preflight(current_project, current_shot):
        del current_project
        order.append("camera")
        assert current_shot.engine == Engine.S2V_14B
        return Engine.S2V_14B.value

    def attach(current_project, current_take, *_args, **_kwargs):
        current_take.qc = QCReport(
            id=f"qc-{current_take.id}",
            take_id=current_take.id,
            shot_id=current_take.shot_id,
            verdict=QCVerdict.PASS,
            identity_ok=True,
            morph_ok=True,
            flicker_ok=True,
            duration_ok=True,
            legal_ok=True,
            voice_ready_ok=True,
        )

    monkeypatch.setattr(
        farm,
        "refresh_reference_shot",
        lambda *_args: order.append("contract"),
    )
    monkeypatch.setattr(farm, "prepare_voice_for_take", prepare)
    monkeypatch.setattr(farm, "camera_task_for", preflight)
    monkeypatch.setattr(farm, "generate_clip", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(farm, "attach_negative", attach)
    monkeypatch.setattr(farm, "camera_disk_block", lambda: "")
    monkeypatch.setattr(farm, "persist_project", lambda *_args: None)
    monkeypatch.setattr(farm, "record_activity", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(farm, "list_generate_jobs", lambda: [])
    monkeypatch.setattr(farm, "clear_camera_lock", lambda: None)
    monkeypatch.setattr(farm, "write_camera_lock", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(farm._PERFORMANCE, "record", lambda **_kwargs: None)

    farm.run_one(project, take)

    assert order[:3] == ["contract", "voice", "camera"]


def test_camera_qc_requires_verified_voice_performance(tmp_path, monkeypatch) -> None:
    from diony_producer import farm

    project, shot, take = _spoken_take()
    backend = DeterministicVoiceTestBackend()
    pipeline = VoicePerformancePipeline(
        vault=tmp_path,
        synthesizer=backend,
        embedder=backend,
        aligner=backend,
        audio_processor=backend,
        audiovisual_qc=backend,
    )
    pipeline.prepare_take(project, take)
    relative = farm.negative_rel(project, take)
    negative = tmp_path / relative
    negative.parent.mkdir(parents=True, exist_ok=True)
    negative.write_bytes(b"video evidence" * 8)
    monkeypatch.setattr(farm, "vault_root", lambda: tmp_path)
    monkeypatch.setattr(
        farm,
        "probe_media",
        lambda *_args, **_kwargs: {
            "width": 1280,
            "height": 720,
            "fps": shot.fps,
            "frames": shot.duration_frames,
            "codec": "h264",
        },
    )
    monkeypatch.setattr(farm, "_reference_similarities", lambda *_args: {})
    monkeypatch.setattr(farm, "_reference_similarity", lambda *_args: 0.98)
    monkeypatch.setattr(farm, "camera_task_for", lambda *_args: Engine.S2V_14B.value)
    monkeypatch.setattr(
        farm,
        "verify_voice_animation",
        lambda current_project, current_take, path: pipeline.verify_animation(
            current_project, current_take, path
        ),
    )

    farm.attach_negative(
        project,
        take,
        relative,
        shot.duration_frames,
        live=True,
        measured_qc_required=False,
    )

    assert take.qc is not None
    assert take.qc.voice_ready_ok is True
    assert take.qc.lipsync_ok is True
    assert take.qc.loudness_ok is True
    assert take.qc.voice_measurement_version == "diony-voice-qc-v1"
    assert take.qc.speaker_similarity >= 0.92
    assert take.qc.lip_sync_confidence >= 0.90
    assert take.qc.audiovisual_offset_ms_p95 <= 40
    assert take.qc.voice_performance_consistency >= 0.90
    assert farm.qc_contract_valid(project, take) is True


def test_voice_clip_cannot_pass_camera_contract_before_animation_qc(tmp_path) -> None:
    from diony_contracts.enums import QCVerdict
    from diony_contracts.models import QCReport
    from diony_producer.farm import qc_contract_valid

    project, _, take = _spoken_take()
    backend = DeterministicVoiceTestBackend()
    pipeline = VoicePerformancePipeline(
        vault=tmp_path,
        synthesizer=backend,
        embedder=backend,
        aligner=backend,
        audio_processor=backend,
        audiovisual_qc=backend,
    )
    pipeline.prepare_take(project, take)
    take.qc = QCReport(
        id=f"qc-{take.id}",
        take_id=take.id,
        shot_id=take.shot_id,
        verdict=QCVerdict.PASS,
        identity_ok=True,
        morph_ok=True,
        flicker_ok=True,
        duration_ok=True,
        legal_ok=True,
    )

    assert qc_contract_valid(project, take) is False


def test_voice_identity_failure_retries_with_diagnostic_seed_before_camera(tmp_path) -> None:
    class DriftingThenLockedBackend(DeterministicVoiceTestBackend):
        def speaker_embedding(self, audio, profile):
            if self.synthesis_calls == 1:
                return [-value for value in profile.speaker_embedding]
            return list(profile.speaker_embedding)

    project, _, take = _spoken_take()
    backend = DriftingThenLockedBackend()
    pipeline = VoicePerformancePipeline(
        vault=tmp_path,
        synthesizer=backend,
        embedder=backend,
        aligner=backend,
        audio_processor=backend,
        audiovisual_qc=backend,
    )

    package = pipeline.prepare_take(project, take)

    assert backend.synthesis_calls == 2
    assert package.qc.audio_passed is True
    metadata = project.asset(package.performance_metadata_asset_id)
    payload = __import__("json").loads((tmp_path / metadata.path).read_text())
    assert payload["audio_attempts"][0]["diagnostic"].startswith("speaker identity drift")


def test_studio_audio_failure_retries_with_measured_diagnostic(tmp_path) -> None:
    from diony_producer.voice import AudioMeasurements

    class NoisyThenCleanBackend(DeterministicVoiceTestBackend):
        def __init__(self):
            super().__init__()
            self.audio_process_calls = 0

        def process_audio(self, *args, **kwargs):
            self.audio_process_calls += 1
            measured = super().process_audio(*args, **kwargs)
            if self.audio_process_calls == 1:
                return AudioMeasurements(
                    snr_db=12.0,
                    integrated_lufs=measured.integrated_lufs,
                    true_peak_dbtp=measured.true_peak_dbtp,
                    harshness_score=measured.harshness_score,
                    sibilance_score=measured.sibilance_score,
                )
            return measured

    project, _, take = _spoken_take()
    backend = NoisyThenCleanBackend()
    pipeline = VoicePerformancePipeline(
        vault=tmp_path,
        synthesizer=backend,
        embedder=backend,
        aligner=backend,
        audio_processor=backend,
        audiovisual_qc=backend,
    )

    package = pipeline.prepare_take(project, take)

    assert backend.audio_process_calls == 2
    assert package.qc.audio_passed is True
    metadata = project.asset(package.performance_metadata_asset_id)
    payload = __import__("json").loads((tmp_path / metadata.path).read_text())
    post_attempts = [row for row in payload["audio_attempts"] if row.get("stage") == "studio_audio"]
    assert post_attempts[0]["diagnostic"].startswith("studio audio QC failed")


def test_visual_retake_reuses_locked_audio_but_requires_fresh_animation_qc(tmp_path) -> None:
    from diony_producer.farm import new_generation_take

    project, shot, take = _spoken_take()
    backend = DeterministicVoiceTestBackend()
    pipeline = VoicePerformancePipeline(
        vault=tmp_path,
        synthesizer=backend,
        embedder=backend,
        aligner=backend,
        audio_processor=backend,
        audiovisual_qc=backend,
    )
    first = pipeline.prepare_take(project, take)
    negative = tmp_path / "first-negative.mp4"
    negative.write_bytes(b"video evidence" * 8)
    pipeline.verify_animation(project, take, negative)
    retake = new_generation_take(project, shot, live=True)
    project.takes.append(retake)
    shot.selected_take_id = retake.id

    reused = pipeline.prepare_take(project, retake)

    assert backend.synthesis_calls == 1
    assert reused.cache_key == first.cache_key
    assert reused.clean_dialogue_asset_id == first.clean_dialogue_asset_id
    assert reused.state == VoiceRenderState.AUDIO_LOCKED
    assert reused.qc.audio_passed is True
    assert reused.qc.animation_passed is False
    assert reused.qc.passed is False


def test_reference_voice_lock_builds_multi_emotion_few_shot_profile(tmp_path) -> None:
    from diony_contracts.enums import AssetKind
    from diony_contracts.models import Asset
    from diony_producer.voice import lock_reference_voice_profile

    project, _, _ = _spoken_take()
    project.voice_profiles = []
    project.bible.characters[0].voice_profile_id = ""
    references = []
    for index, emotion in enumerate(("neutral", "grief"), start=1):
        path = tmp_path / f"mara-{emotion}.wav"
        path.write_bytes(b"RIFF" + bytes([index]) * 64)
        asset = Asset(
            id=f"ast-mara-{emotion}",
            kind=AssetKind.AUDIO,
            filename=path.name,
            mime="audio/wav",
            bytes=path.stat().st_size,
            path=path.name,
            sha256=__import__("hashlib").sha256(path.read_bytes()).hexdigest(),
            metadata={
                "duration_seconds": 5.0,
                "emotion": emotion,
                "transcript": f"Mara reference in {emotion}.",
            },
        )
        project.assets.append(asset)
        references.append(asset.id)
    backend = DeterministicVoiceTestBackend()

    profile = lock_reference_voice_profile(
        project,
        "char-mara",
        references,
        vault=tmp_path,
        backend=backend,
        descriptors={"accent": "inland American", "texture": "warm alto"},
    )

    assert profile.source == VoiceProfileSource.FEW_SHOT
    assert [sample.emotion for sample in profile.reference_samples] == ["neutral", "grief"]
    assert sum(sample.duration_seconds for sample in profile.reference_samples) == 10.0
    assert profile.accent_descriptor == "inland American"
    assert project.bible.characters[0].voice_profile_id == profile.id


def test_reference_voice_lock_measures_wav_duration_when_metadata_is_absent(tmp_path) -> None:
    import wave

    from diony_contracts.enums import AssetKind
    from diony_contracts.models import Asset
    from diony_producer.voice import lock_reference_voice_profile

    project, _, _ = _spoken_take()
    project.voice_profiles = []
    project.bible.characters[0].voice_profile_id = ""
    path = tmp_path / "mara-five-seconds.wav"
    with wave.open(str(path), "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(8000)
        output.writeframes(b"\x00\x00" * 8000 * 5)
    asset = Asset(
        id="ast-mara-five-seconds",
        kind=AssetKind.AUDIO,
        filename=path.name,
        mime="audio/wav",
        bytes=path.stat().st_size,
        path=path.name,
        sha256=__import__("hashlib").sha256(path.read_bytes()).hexdigest(),
    )
    project.assets.append(asset)

    profile = lock_reference_voice_profile(
        project,
        "char-mara",
        [asset.id],
        vault=tmp_path,
        backend=DeterministicVoiceTestBackend(),
    )

    assert profile.source == VoiceProfileSource.ZERO_SHOT
    assert profile.reference_samples[0].duration_seconds == 5.0


def test_voice_registry_api_locks_synthetic_identity_once() -> None:
    from fastapi.testclient import TestClient
    from diony_producer.app import app

    client = TestClient(app)
    client.post("/api/reset")

    locked = client.post(
        "/api/characters/char-mara/voice-profiles/lock",
        json={"source": "synthetic", "descriptors": {"texture": "warm alto"}},
    )
    registry = client.get("/api/voice-profiles")

    assert locked.status_code == 200
    assert registry.status_code == 200
    assert len(registry.json()["profiles"]) == 1
    assert registry.json()["profiles"][0]["locked"] is True
    assert registry.json()["profiles"][0]["project_id"] == locked.json()["project_id"]
    assert registry.json()["profiles"][0]["texture_descriptor"] == "warm alto"
