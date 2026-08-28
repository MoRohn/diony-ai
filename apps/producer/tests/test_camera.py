from pathlib import Path

import httpx
import pytest

from diony_producer.camera import _generate_http_job, resolve_camera_task


def test_conditioned_engines_require_their_purpose_built_inputs(tmp_path) -> None:
    still = tmp_path / "hero.jpg"
    still.write_bytes(b"jpg")
    clip = tmp_path / "prev.mp4"
    clip.write_bytes(b"mp4")
    audio = tmp_path / "line.wav"
    audio.write_bytes(b"wav")
    animate_root = tmp_path / "animate"
    animate_root.mkdir()
    assert resolve_camera_task("i2v-A14B", still) == "i2v-A14B"
    with pytest.raises(RuntimeError, match="I2V.*still"):
        resolve_camera_task("i2v-A14B", None)
    with pytest.raises(RuntimeError, match="I2V.*still"):
        resolve_camera_task("i2v-A14B", clip)
    assert resolve_camera_task("s2v-14B", still, audio=audio) == "s2v-14B"
    with pytest.raises(RuntimeError, match="recorded audio"):
        resolve_camera_task("s2v-14B", still)
    assert (
        resolve_camera_task("animate-14B", still, animate_root=animate_root)
        == "animate-14B"
    )
    with pytest.raises(RuntimeError, match="preprocessed motion"):
        resolve_camera_task("animate-14B", still)
    assert resolve_camera_task("t2v-A14B", still) == "t2v-A14B"


def test_camera_refuses_a_typed_prompt_that_drifted_after_compilation(tmp_path) -> None:
    from diony_contracts.enums import CustomerKind
    from diony_contracts.models import Brief
    from diony_contracts.studio import expand_idea
    from diony_producer.camera import generate_clip

    project = expand_idea(
        Brief(
            idea="A watchmaker follows a loose spring across a workbench.",
            customer_kind=CustomerKind.COMMERCIAL,
            target_minutes=0.5,
        )
    )
    shot = project.shots[0]
    shot.negative_prompt = "watermark"

    with pytest.raises(RuntimeError, match="negative prompt does not match its typed plan"):
        generate_clip(project, shot, tmp_path / "should-not-roll.mp4", seed=shot.seed)


def test_following_reference_locked_shot_uses_previous_boundary_frame(tmp_path, monkeypatch) -> None:
    from diony_contracts.enums import AssetKind, CustomerKind
    from diony_contracts.models import Asset, Brief
    from diony_contracts.studio import expand_idea
    from diony_producer import camera

    still = Asset(
        id="ast-character",
        kind=AssetKind.IMAGE,
        filename="character.png",
        mime="image/png",
        bytes=12,
        path="assets/ast-character/character.png",
    )
    project = expand_idea(
        Brief(
            idea="Use this attached character and same background for a continuous scene.",
            customer_kind=CustomerKind.SERIES,
            target_minutes=10 / 60,
            production_kind="sample",
            sample_seconds=10,
            attachment_ids=[still.id],
        ),
        assets=[still],
    )
    canonical = tmp_path / "canonical.png"
    canonical.write_bytes(b"png")
    boundary = tmp_path / "previous-last.png"
    boundary.write_bytes(b"png")
    captured = {}
    monkeypatch.setattr(camera, "reference_frame", lambda *_args: canonical)
    monkeypatch.setattr(camera, "continuity_frame", lambda *_args: (boundary, "sh-010-t1"))
    monkeypatch.setattr(camera, "generate_py", lambda: tmp_path / "generate.py")
    monkeypatch.setattr(camera, "_wan_http_up", lambda: False)

    def run_generate(**kwargs):
        captured.update(kwargs)
        kwargs["save_file"].write_bytes(b"video")

    monkeypatch.setattr(camera, "run_generate", run_generate)
    destination = tmp_path / "shot-2.mp4"

    camera.generate_clip(
        project,
        project.shots[1],
        destination,
        seed=7,
        attention_backend="cudnn_sdpa",
    )

    assert captured["task"] == "i2v-A14B"
    assert captured["image"] == boundary
    assert captured["attention_backend"] == "cudnn_sdpa"
    assert "picture-in-picture" in captured["negative_prompt"]


def test_camera_task_for_uses_previous_boundary_frame_for_i2v(tmp_path, monkeypatch) -> None:
    """Preflight must resolve the same continuity image that generation will use."""
    from diony_contracts.enums import ContinuityMode, Engine
    from diony_contracts.fixture import demo_project
    from diony_producer import camera

    project = demo_project()
    shot = project.shots[1]
    shot.engine = Engine.I2V_A14B
    shot.continuity_mode = ContinuityMode.PREVIOUS_FRAME
    shot.keyframe_asset_ids = []
    boundary = tmp_path / "previous-last.png"
    boundary.write_bytes(b"png")
    monkeypatch.setattr(camera, "continuity_frame", lambda *_args: (boundary, "sh-010-t1"))

    assert camera.camera_task_for(project, shot) == "i2v-A14B"


def test_comfy_only_site_is_a_live_camera(monkeypatch) -> None:
    from diony_producer import camera

    monkeypatch.setenv("DIONY_WAN_BACKEND", "comfyui")
    monkeypatch.setattr(camera, "inference_live", lambda: True)
    monkeypatch.setattr(camera, "generate_py", lambda: Path("/definitely/missing/generate.py"))
    monkeypatch.setattr(camera, "_comfy_http_up", lambda: True)

    assert camera.live_camera() is True


def test_only_backend_qualified_beneficial_lora_is_selected(monkeypatch) -> None:
    from diony_contracts.enums import CustomerKind
    from diony_contracts.models import Brief
    from diony_contracts.studio import expand_idea
    from diony_producer.camera import qualified_quality_loras

    project = expand_idea(
        Brief(
            idea="A dancer follows a prepared driving performance under shifting stage light.",
            customer_kind=CustomerKind.MUSIC_VIDEO,
            target_minutes=0.5,
        )
    )
    monkeypatch.setenv(
        "DIONY_WAN_QUALIFIED_LORAS",
        "downloaded-face-lora,wan2.2-animate-relighting",
    )

    assert qualified_quality_loras(project.shots[0], "t2v-A14B") == []
    assert qualified_quality_loras(project.shots[0], "animate-14B") == [
        "wan2.2-animate-relighting"
    ]


def test_http_camera_uses_durable_job_api_and_waits_for_owned_output(tmp_path, monkeypatch) -> None:
    from diony_producer import camera

    destination = tmp_path / "negative.mp4"
    submitted = {}
    polls = iter(
        [
            {"id": "wan-1", "state": "running", "progress": {"prompt_id": "comfy-1"}},
            {
                "id": "wan-1",
                "state": "completed",
                "output": {"path": str(destination), "bytes": 64},
            },
        ]
    )

    def post(url, **kwargs):
        assert url.endswith("/v1/jobs")
        submitted.update(kwargs["json"])
        return httpx.Response(202, json={"id": "wan-1", "state": "queued"})

    def get(url, **kwargs):
        assert url.endswith("/v1/jobs/wan-1")
        row = next(polls)
        if row["state"] == "completed":
            destination.write_bytes(b"x" * 64)
        return httpx.Response(200, json=row)

    monkeypatch.setattr(camera.httpx, "post", post)
    monkeypatch.setattr(camera.httpx, "get", get)
    monkeypatch.setattr(camera.time, "sleep", lambda _seconds: None)
    payload = {
        "task": "t2v-A14B",
        "prompt": "A controlled tracking shot.",
        "seed": 91,
        "save_file": str(destination),
        "duration_frames": 80,
        "fps": 23.976,
        "image": None,
    }

    assert _generate_http_job(payload, destination, timeout=30) == destination
    assert submitted["quality_tier"] == "production"
    assert submitted["frames"] == 81
    assert "duration_frames" not in submitted


def test_http_camera_surfaces_durable_node_error(tmp_path, monkeypatch) -> None:
    from diony_producer import camera

    monkeypatch.setattr(
        camera.httpx,
        "post",
        lambda *args, **kwargs: httpx.Response(202, json={"id": "wan-bad", "state": "queued"}),
    )
    monkeypatch.setattr(
        camera.httpx,
        "get",
        lambda *args, **kwargs: httpx.Response(
            200, json={"id": "wan-bad", "state": "failed", "error": "node 75: low-noise missing"}
        ),
    )
    monkeypatch.setattr(camera.time, "sleep", lambda _seconds: None)

    with pytest.raises(RuntimeError, match="node 75: low-noise missing"):
        _generate_http_job(
            {
                "task": "t2v-A14B",
                "prompt": "A static shot.",
                "seed": 1,
                "save_file": str(tmp_path / "bad.mp4"),
                "duration_frames": 81,
                "fps": 24.0,
                "image": None,
            },
            tmp_path / "bad.mp4",
            timeout=30,
        )


def test_staged_camera_task_predicts_s2v_for_a_spoken_shot_before_voice_lock() -> None:
    """The farm stages the family the take will roll on, not the pre-voice engine."""
    from diony_contracts.enums import Engine
    from diony_contracts.fixture import demo_project
    from diony_producer import camera

    project = demo_project()
    spoken = next(shot for shot in project.shots if shot.audio_line or shot.dialogue_turns)
    spoken.engine = Engine.I2V_A14B
    assert camera.staged_camera_task(project, spoken) == "s2v-14B"

    silent = next(shot for shot in project.shots if not (shot.audio_line or shot.dialogue_turns))
    silent.engine = Engine.T2V_A14B
    assert camera.staged_camera_task(project, silent) == camera.camera_task_for(project, silent) == "t2v-A14B"


def test_previous_frame_chain_only_continues_from_a_print_that_passed_qc(tmp_path, monkeypatch) -> None:
    """A selected take that merely won among rejections must not seed the next Shot."""
    from diony_contracts.enums import ContinuityMode, QCVerdict
    from diony_contracts.fixture import demo_project
    from diony_producer import camera

    project = demo_project()
    first, second = project.shots[0], project.shots[1]
    second.continuity_mode = ContinuityMode.PREVIOUS_FRAME
    second.character_ref_ids = list(first.character_ref_ids) or ["char-lead"]
    first.character_ref_ids = list(second.character_ref_ids)
    takes = project.takes_for(first.id)
    assert len(takes) >= 2
    negative = tmp_path / "negative_720.mp4"
    negative.write_bytes(b"x" * 64)
    monkeypatch.setattr(camera, "_negative_for_take", lambda take: (negative, ""))
    monkeypatch.setattr(camera, "record_activity", lambda *a, **k: None)

    passing, failing = takes[0], takes[1]
    passing.qc = passing.qc.model_copy(update={"verdict": QCVerdict.PASS})
    failing.qc = failing.qc.model_copy(update={"verdict": QCVerdict.FAIL})
    first.selected_take_id = failing.id
    chosen = camera._previous_take(project, second)
    assert chosen is not None and chosen.id == passing.id

    for take in takes:
        take.qc = take.qc.model_copy(update={"verdict": QCVerdict.FAIL})
    assert camera._previous_take(project, second) is None
    assert camera.continuity_frame(project, second) == (None, None)


def test_generate_clip_restarts_from_the_reference_when_the_prior_print_failed(tmp_path, monkeypatch) -> None:
    from diony_contracts.enums import ContinuityMode, QCVerdict
    from diony_contracts.fixture import demo_project
    from diony_producer import camera

    project = demo_project()
    first, second = project.shots[0], project.shots[1]
    second.continuity_mode = ContinuityMode.PREVIOUS_FRAME
    second.character_ref_ids = list(first.character_ref_ids) or ["char-lead"]
    first.character_ref_ids = list(second.character_ref_ids)
    negative = tmp_path / "negative_720.mp4"
    negative.write_bytes(b"x" * 64)
    monkeypatch.setattr(camera, "_negative_for_take", lambda take: (negative, ""))
    monkeypatch.setattr(camera, "record_activity", lambda *a, **k: None)
    for take in project.takes_for(first.id):
        take.qc = take.qc.model_copy(update={"verdict": QCVerdict.FAIL})
    canonical = tmp_path / "canonical.png"
    canonical.write_bytes(b"png")
    monkeypatch.setattr(camera, "reference_frame", lambda *_args: canonical)
    monkeypatch.setattr(camera, "generate_py", lambda: tmp_path / "generate.py")
    monkeypatch.setattr(camera, "_wan_http_up", lambda: False)
    monkeypatch.setattr(camera, "validate_wan_prompt_dispatch", lambda shot: [])
    captured = {}

    def run_generate(**kwargs):
        captured.update(kwargs)
        kwargs["save_file"].write_bytes(b"mp4")
        return kwargs["save_file"]

    monkeypatch.setattr(camera, "run_generate", run_generate)
    dest = tmp_path / "takes" / "sh-020-t1" / "negative_720.mp4"
    camera.generate_clip(project, second, dest, seed=1)
    assert captured["image"] == canonical
    # The camera records the still it conditioned on, for the reference lock.
    assert camera.conditioning_image(dest) == canonical
    import json

    assert json.loads(camera.conditioning_sidecar(dest).read_text())["source"] == "reference"
    # Preflight resolves the same fallback instead of rejecting the shot.
    monkeypatch.setattr(camera, "_keyframe", lambda project, shot: canonical)
    monkeypatch.setattr(camera, "_audio_reference", lambda project, shot: None)
    from diony_contracts.enums import Engine

    second.engine = Engine.I2V_A14B
    assert camera.camera_task_for(project, second) == "i2v-A14B"

    # A prior Shot that never printed (voice hold before camera) also restarts
    # from the reference; only a Shot with no reference still at all stops.
    project.takes = [take for take in project.takes if take.shot_id != first.id]
    camera.generate_clip(project, second, dest, seed=1)
    assert captured["image"] == canonical
    monkeypatch.setattr(camera, "reference_frame", lambda *_args: None)
    with pytest.raises(RuntimeError, match="Previous-frame continuity is required"):
        camera.generate_clip(project, second, dest, seed=1)


def test_a_print_settled_by_policy_is_a_boundary_to_continue_from(tmp_path, monkeypatch) -> None:
    from diony_contracts.enums import ContinuityMode, QCVerdict
    from diony_contracts.fixture import demo_project
    from diony_producer import camera

    project = demo_project()
    first, second = project.shots[0], project.shots[1]
    second.continuity_mode = ContinuityMode.PREVIOUS_FRAME
    second.character_ref_ids = list(first.character_ref_ids) or ["char-lead"]
    first.character_ref_ids = list(second.character_ref_ids)
    negative = tmp_path / "negative_720.mp4"
    negative.write_bytes(b"x" * 64)
    monkeypatch.setattr(camera, "_negative_for_take", lambda take: (negative, ""))
    monkeypatch.setattr(camera, "record_activity", lambda *a, **k: None)
    takes = project.takes_for(first.id)
    for take in takes:
        take.qc = take.qc.model_copy(update={"verdict": QCVerdict.FAIL})
    settled = takes[-1]
    settled.qc = settled.qc.model_copy(update={"verdict": QCVerdict.WARN, "settled_best_of": 4})
    first.selected_take_id = settled.id
    chosen = camera._previous_take(project, second)
    assert chosen is not None and chosen.id == settled.id


def test_a_speaker_change_is_a_cut_not_a_continuation(tmp_path, monkeypatch) -> None:
    from diony_contracts.enums import ContinuityMode, QCVerdict
    from diony_contracts.fixture import demo_project
    from diony_contracts.models import DialogueTurn
    from diony_producer import camera

    project = demo_project()
    first, second = project.shots[0], project.shots[1]
    second.continuity_mode = ContinuityMode.PREVIOUS_FRAME
    second.character_ref_ids = list(first.character_ref_ids) or ["char-lead"]
    first.character_ref_ids = list(second.character_ref_ids)
    first.dialogue_turns = [DialogueTurn(id="t1", character_id="char-marcus", text="Go first.")]
    second.dialogue_turns = [DialogueTurn(id="t2", character_id="char-lila", text="Over in a minute.")]
    negative = tmp_path / "negative_720.mp4"
    negative.write_bytes(b"x" * 64)
    monkeypatch.setattr(camera, "_negative_for_take", lambda take: (negative, ""))
    notes: list[str] = []
    monkeypatch.setattr(camera, "record_activity", lambda _src, text, **_kw: notes.append(text))
    for take in project.takes_for(first.id):
        take.qc = take.qc.model_copy(update={"verdict": QCVerdict.PASS})
    assert camera._previous_take(project, second) is None
    assert "speaker changes from char-marcus to char-lila" in camera.boundary_fallback_reason(project, second)
    assert any("speaker changes" in note for note in notes)
    # Same speaker: the passed print is the boundary.
    second.dialogue_turns = [DialogueTurn(id="t2", character_id="char-marcus", text="And again.")]
    assert camera._previous_take(project, second) is not None


_HUGO = (273.0, 141.0, 91.0, 139.0, 0.87)
_LILA = (515.0, 140.0, 87.0, 107.0, 0.89)
_MARCUS = (827.0, 115.0, 94.0, 144.0, 0.93)


def test_conditioning_sidecar_names_the_still_and_its_source(tmp_path) -> None:
    from diony_producer import camera

    negative = tmp_path / "negative_720.mp4"
    image = tmp_path / "still.png"
    image.write_bytes(b"png")
    camera.record_conditioning_image(negative, image, source="reference")
    assert camera.conditioning_image(negative) == image
    assert camera.conditioning_source(negative) == "reference"
    camera.record_conditioning_image(negative, image, source="boundary")
    assert camera.conditioning_source(negative) == "boundary"
    assert camera.conditioning_source(tmp_path / "none.mp4") == ""
    camera.record_conditioning_image(negative, None, source="reference")
    assert camera.conditioning_image(negative) is None


def test_generate_clip_hands_wan_the_line_and_the_prompt_clip(tmp_path, monkeypatch) -> None:
    """A spoken Shot without recorded audio speaks through Wan's own TTS inputs."""
    from diony_contracts.enums import Engine
    from diony_contracts.fixture import demo_project
    from diony_producer import camera

    project = demo_project()
    spoken = next(shot for shot in project.shots if shot.audio_line or shot.dialogue_turns)
    spoken.audio_asset_ids = []
    spoken.engine = Engine.S2V_14B
    canonical = tmp_path / "canonical.png"
    canonical.write_bytes(b"png")
    clip = tmp_path / "cast-00.wav"
    clip.write_bytes(b"wav")
    monkeypatch.setattr(camera, "reference_frame", lambda *_args: canonical)
    monkeypatch.setattr(camera, "generate_py", lambda: tmp_path / "generate.py")
    monkeypatch.setattr(camera, "_wan_http_up", lambda: False)
    monkeypatch.setattr(camera, "validate_wan_prompt_dispatch", lambda shot: [])
    monkeypatch.setattr(camera, "record_activity", lambda *a, **k: None)
    monkeypatch.setattr(
        "diony_producer.voice.wan_speech_inputs",
        lambda _project, _shot, **_k: {"tts_text": "Stop the presses.", "tts_prompt_audio": str(clip), "tts_prompt_text": None},
    )
    captured = {}

    def run_generate(**kwargs):
        captured.update(kwargs)
        kwargs["save_file"].write_bytes(b"mp4")
        return kwargs["save_file"]

    monkeypatch.setattr(camera, "run_generate", run_generate)
    dest = tmp_path / "takes" / "sh-spoken-t1" / "negative_720.mp4"
    camera.generate_clip(project, spoken, dest, seed=7)
    assert captured["task"] == "s2v-14B"
    assert captured["audio"] is None
    assert captured["speech"] == {"tts_text": "Stop the presses.", "tts_prompt_audio": str(clip), "tts_prompt_text": None}
    assert captured["image"] == canonical

    # Preflight resolves the spoken lane without a recorded asset, and refuses a silent S2V Shot.
    monkeypatch.setattr(camera, "_keyframe", lambda project, shot: canonical)
    assert camera.camera_task_for(project, spoken) == "s2v-14B"
    with pytest.raises(RuntimeError, match="recorded audio .* or an authored line"):
        camera.resolve_camera_task("s2v-14B", canonical, audio=None, speech=False)


