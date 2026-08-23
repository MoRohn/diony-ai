import importlib
from pathlib import Path

from fastapi.testclient import TestClient

app_module = importlib.import_module("diony_wan.app")


def test_cinematic_prompt_endpoint_builds_motion_only_i2v() -> None:
    client = TestClient(app_module.app)
    response = client.post(
        "/v1/prompts/cinematic",
        json={
            "mode": "i2v-A14B",
            "subject": "ignored source description",
            "motion": "the performer turns toward camera and takes one measured step",
            "atmosphere": "stable tungsten practicals and restrained teal shadows",
            "camera": "static shot",
        },
    )
    assert response.status_code == 200
    assert response.json()["word_count"] >= 80
    assert "ignored source description" not in response.json()["prompt"]


def test_generate_forwards_comfy_parameters_and_reports_backend(tmp_path, monkeypatch) -> None:
    captured = {}

    def fake_generate(**kwargs):
        captured.update(kwargs)
        kwargs["save_file"].write_bytes(b"x" * 64)

    monkeypatch.setenv("DIONY_WAN_BACKEND", "comfyui")
    monkeypatch.setattr(app_module, "run_generate", fake_generate)
    client = TestClient(app_module.app)
    destination = tmp_path / "clip.mp4"
    response = client.post(
        "/v1/generate",
        json={
            "task": "t2v-A14B",
            "prompt": "A controlled static shot.",
            "negative_prompt": "no flicker",
            "seed": 77,
            "save_file": str(destination),
            "duration_frames": 81,
            "width": 1280,
            "height": 720,
            "steps": 28,
            "cfg": 3.5,
            "sampler": "euler",
            "scheduler": "beta",
        },
    )
    assert response.status_code == 200
    assert response.json()["backend"] == "comfyui"
    assert captured["negative_prompt"] == "no flicker"
    assert captured["steps"] == 28
    assert captured["width"] == 1280


def test_ready_surfaces_workflow_and_model_failures(monkeypatch) -> None:
    monkeypatch.setenv("DIONY_WAN_BACKEND", "comfyui")
    monkeypatch.setattr(
        "diony_wan.comfy.readiness_report",
        lambda: {"ok": False, "backend": "comfyui", "errors": ["workflow missing"]},
    )
    response = TestClient(app_module.app).get("/ready")
    assert response.status_code == 503
    assert response.json()["detail"]["errors"] == ["workflow missing"]


def test_job_api_resolves_quality_tier_and_exposes_owned_status(monkeypatch, tmp_path) -> None:
    captured = {}

    class FakeJobs:
        def submit(self, **kwargs):
            captured.update(kwargs)
            return {"id": "wan-test", "state": "queued", "config": kwargs["config"].__dict__}

        def status(self, job_id):
            assert job_id == "wan-test"
            return {"id": job_id, "state": "running", "progress": {"source": "comfy-poll"}}

        def metrics(self):
            return {"total": 1, "running": 1, "success_rate": None}

        def cancel(self, job_id, **kwargs):
            return {"id": job_id, "state": "cancelling"}

    monkeypatch.setattr(app_module, "get_job_manager", lambda: FakeJobs())
    response = TestClient(app_module.app).post(
        "/v1/jobs",
        json={
            "quality_tier": "hero",
            "mode": "i2v-A14B",
            "prompt": "A controlled identity-locked shot.",
            "seed": 444,
            "save_file": str(tmp_path / "shot.mp4"),
        },
    )
    assert response.status_code == 202
    assert captured["config"].mode == "i2v-A14B"
    assert captured["config"].steps == 30
    assert captured["quality_tier"] == "hero"

    client = TestClient(app_module.app)
    assert client.get("/v1/jobs/wan-test").json()["state"] == "running"
    assert client.get("/v1/metrics").json()["running"] == 1
    assert client.post("/v1/jobs/wan-test/cancel").json()["state"] == "cancelling"


def test_job_executor_streams_comfy_progress_and_restart_resume(monkeypatch, tmp_path) -> None:
    from diony_wan import comfy

    progress: list[tuple[str, dict]] = []

    class FakeJobs:
        def update_progress(self, job_id, event):
            progress.append((job_id, event))

    class FakeRunner:
        def run(self, config, destination, **kwargs):
            kwargs["status_callback"]({"state": "processing", "prompt_id": "prompt-9"})
            destination.write_bytes(b"x" * 64)
            return destination

        def resume(self, destination, **kwargs):
            kwargs["status_callback"]({"state": "recovering", "prompt_id": "prompt-9"})
            destination.write_bytes(b"r" * 64)
            return destination

    monkeypatch.setattr(app_module, "wan_backend", lambda: "comfyui")
    monkeypatch.setattr(app_module, "get_job_manager", lambda: FakeJobs())
    monkeypatch.setattr(comfy, "configured_runner", lambda: FakeRunner())
    job = {
        "id": "wan-stream",
        "config": {
            "mode": "t2v-A14B",
            "prompt": "A controlled static shot.",
            "negative_prompt": "no flicker",
            "seed": 9,
            "width": 1280,
            "height": 720,
            "frames": 81,
            "steps": 24,
            "cfg": 3.5,
            "sampler": "euler",
            "scheduler": "beta",
        },
        "destination": str(tmp_path / "take.mp4"),
        "image": None,
        "last_image": None,
    }

    assert app_module._execute_job(job) == Path(job["destination"])
    Path(job["destination"]).unlink()
    assert app_module._resume_job(job) == Path(job["destination"])
    assert progress == [
        ("wan-stream", {"state": "processing", "prompt_id": "prompt-9"}),
        ("wan-stream", {"state": "recovering", "prompt_id": "prompt-9"}),
    ]


def test_job_rerun_reuses_exact_config_and_seed_with_new_destination(monkeypatch, tmp_path) -> None:
    captured = {}
    original = {
        "id": "wan-original",
        "state": "completed",
        "quality_tier": "hero",
        "config": {
            "mode": "i2v-A14B",
            "prompt": "The subject takes one measured step.",
            "negative_prompt": "no flicker",
            "seed": 991,
            "width": 1280,
            "height": 720,
            "frames": 81,
            "steps": 30,
            "cfg": 3.5,
            "sampler": "euler",
            "scheduler": "beta",
        },
        "image": str(tmp_path / "keyframe.png"),
        "last_image": None,
    }

    class FakeJobs:
        def status(self, job_id):
            assert job_id == "wan-original"
            return original

        def submit(self, **kwargs):
            captured.update(kwargs)
            return {"id": "wan-rerun", "state": "queued", "config": kwargs["config"].__dict__}

    monkeypatch.setattr(app_module, "get_job_manager", lambda: FakeJobs())
    destination = tmp_path / "rerun.mp4"
    response = TestClient(app_module.app).post(
        "/v1/jobs/wan-original/rerun", json={"save_file": str(destination)}
    )

    assert response.status_code == 202
    assert captured["config"].seed == 991
    assert captured["config"].steps == 30
    assert captured["destination"] == destination
    assert captured["quality_tier"] == "hero"


def test_native_job_api_rejects_comfy_only_flf2v(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(app_module, "wan_backend", lambda: "native")
    response = TestClient(app_module.app).post(
        "/v1/jobs",
        json={
            "quality_tier": "hero",
            "task": "flf2v-A14B",
            "prompt": "One continuous transition.",
            "seed": 12,
            "image": str(tmp_path / "first.png"),
            "last_image": str(tmp_path / "last.png"),
            "save_file": str(tmp_path / "flf.mp4"),
        },
    )
    assert response.status_code == 400
    assert "ComfyUI backend" in response.json()["detail"]
