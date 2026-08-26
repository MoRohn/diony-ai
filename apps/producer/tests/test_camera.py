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
