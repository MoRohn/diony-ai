import json
from pathlib import Path

import httpx
import pytest

from diony_wan.comfy import (
    DEFAULT_NEGATIVE_PROMPT,
    ComfyClient,
    ComfyError,
    ComfyWanRunner,
    GenerationConfig,
    build_cinematic_prompt,
    load_api_workflow,
    mutate_workflow,
    preflight_workflow,
    validate_comfy_version,
)


def api_workflow() -> dict:
    return {
        "3": {
            "class_type": "KSampler",
            "inputs": {
                "seed": 1,
                "steps": 20,
                "cfg": 4.0,
                "sampler_name": "euler",
                "scheduler": "normal",
            },
        },
        "5": {
            "class_type": "EmptyHunyuanLatentVideo",
            "inputs": {"width": 832, "height": 480, "length": 81},
        },
        "6": {
            "class_type": "CLIPTextEncode",
            "_meta": {"title": "CLIP Text Encode (Positive Prompt)"},
            "inputs": {"text": "old positive"},
        },
        "7": {
            "class_type": "CLIPTextEncode",
            "_meta": {"title": "CLIP Text Encode (Negative Prompt)"},
            "inputs": {"text": "old negative"},
        },
        "74": {
            "class_type": "UNETLoader",
            "inputs": {"unet_name": "old_high_noise.safetensors"},
        },
        "75": {
            "class_type": "UNETLoader",
            "inputs": {"unet_name": "old_low_noise.safetensors"},
        },
        "90": {
            "class_type": "SaveVideo",
            "inputs": {"filename_prefix": "ComfyUI"},
        },
    }


def test_load_requires_api_format_json(tmp_path: Path) -> None:
    gui = tmp_path / "gui.json"
    gui.write_text(json.dumps({"nodes": [{"id": 1, "type": "KSampler"}]}))

    with pytest.raises(ComfyError, match="API format"):
        load_api_workflow(gui)


def test_comfy_version_gate_requires_native_wan_template_support() -> None:
    assert validate_comfy_version({"system": {"comfyui_version": "v0.3.46"}}) == "v0.3.46"
    with pytest.raises(ComfyError, match="0.3.46 or newer"):
        validate_comfy_version({"system": {"comfyui_version": "0.3.45"}})


def test_mutate_14b_t2v_updates_dual_experts_and_parameters() -> None:
    workflow = api_workflow()
    config = GenerationConfig(
        mode="t2v-A14B",
        prompt="A deliberate tracking shot follows the reporter through the newsroom.",
        negative_prompt=DEFAULT_NEGATIVE_PROMPT,
        seed=42,
        width=1280,
        height=720,
        frames=81,
        steps=28,
        cfg=3.5,
        sampler="euler",
        scheduler="beta",
    )

    mutated = mutate_workflow(workflow, config, output_prefix="diony/sh-010")

    assert mutated["6"]["inputs"]["text"] == config.prompt
    assert mutated["7"]["inputs"]["text"] == DEFAULT_NEGATIVE_PROMPT
    assert mutated["5"]["inputs"] == {"width": 1280, "height": 720, "length": 81}
    assert mutated["3"]["inputs"]["seed"] == 42
    assert mutated["3"]["inputs"]["steps"] == 28
    assert mutated["3"]["inputs"]["cfg"] == 3.5
    assert mutated["3"]["inputs"]["scheduler"] == "beta"
    assert "high_noise_14B" in mutated["74"]["inputs"]["unet_name"]
    assert "low_noise_14B" in mutated["75"]["inputs"]["unet_name"]
    assert mutated["90"]["inputs"]["filename_prefix"] == "diony/sh-010"
    assert workflow["6"]["inputs"]["text"] == "old positive"


def test_mutate_supports_official_modular_sampler_nodes() -> None:
    workflow = api_workflow()
    workflow.pop("3")
    workflow.update(
        {
            "30": {"class_type": "RandomNoise", "inputs": {"noise_seed": 1}},
            "31": {"class_type": "BasicScheduler", "inputs": {"scheduler": "normal", "steps": 20}},
            "32": {"class_type": "KSamplerSelect", "inputs": {"sampler_name": "euler"}},
            "33": {"class_type": "CFGGuider", "inputs": {"cfg": 4.0}},
        }
    )
    config = GenerationConfig(
        mode="t2v-A14B",
        prompt="A static shot.",
        seed=99,
        steps=26,
        cfg=3.25,
        sampler="dpmpp_2m",
        scheduler="beta",
    )

    mutated = mutate_workflow(workflow, config, output_prefix="diony/test")

    assert mutated["30"]["inputs"]["noise_seed"] == 99
    assert mutated["31"]["inputs"] == {"scheduler": "beta", "steps": 26}
    assert mutated["32"]["inputs"]["sampler_name"] == "dpmpp_2m"
    assert mutated["33"]["inputs"]["cfg"] == 3.25


def test_preflight_fails_loudly_for_bad_shapes_and_missing_dual_expert(tmp_path: Path) -> None:
    config = GenerationConfig(mode="t2v-A14B", prompt="A static shot.", width=1279, height=720, frames=80)
    with pytest.raises(ComfyError, match="width.*16"):
        preflight_workflow(api_workflow(), config, model_root=tmp_path)

    valid = GenerationConfig(mode="t2v-A14B", prompt="A static shot.")
    with pytest.raises(ComfyError) as raised:
        preflight_workflow(api_workflow(), valid, model_root=tmp_path)
    text = str(raised.value)
    assert "wan2.2_t2v_high_noise_14B_fp8_scaled.safetensors" in text
    assert "wan2.2_t2v_low_noise_14B_fp8_scaled.safetensors" in text
    assert "umt5_xxl_fp8_e4m3fn_scaled.safetensors" in text


def test_preflight_accepts_exact_14b_file_placement(tmp_path: Path) -> None:
    for folder, name in (
        ("diffusion_models", "wan2.2_t2v_high_noise_14B_fp8_scaled.safetensors"),
        ("diffusion_models", "wan2.2_t2v_low_noise_14B_fp8_scaled.safetensors"),
        ("text_encoders", "umt5_xxl_fp8_e4m3fn_scaled.safetensors"),
        ("vae", "wan_2.1_vae.safetensors"),
    ):
        path = tmp_path / "models" / folder / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"model")

    report = preflight_workflow(
        api_workflow(),
        GenerationConfig(mode="t2v-A14B", prompt="A static shot."),
        model_root=tmp_path,
    )
    assert report["ok"] is True
    assert report["dual_expert"] is True
    assert report["vram_estimate_bytes"] >= 24 * 1024**3


def test_cinematic_prompt_builder_uses_the_five_layer_identity_first_contract() -> None:
    t2v = build_cinematic_prompt(
        subject="a night-shift reporter at the assignment desk",
        motion="she closes the ledger, rises, and crosses toward the live studio",
        atmosphere="tungsten practicals, cool window spill, restrained teal and amber grade",
        camera="slow dolly in",
        mode="t2v-A14B",
    )
    lowered = t2v.lower()
    assert (
        lowered.index("night-shift reporter")
        < lowered.index("she closes")
        < lowered.index("slow dolly in")
        < lowered.index("tungsten")
        < lowered.index("foreground atmosphere")
    )
    assert 80 <= len(t2v.split()) <= 140

    i2v = build_cinematic_prompt(
        subject="this description must be ignored",
        motion="the subject turns toward camera and takes one measured step",
        atmosphere="soft practical light stays stable with restrained film grain",
        camera="static shot",
        mode="i2v-A14B",
    )
    assert "must be ignored" not in i2v
    assert i2v.startswith("The exact supplied subject")


def test_client_surfaces_exact_node_errors_and_frees_vram() -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        if request.url.path == "/prompt":
            return httpx.Response(
                400,
                json={
                    "error": {"message": "Prompt outputs failed validation"},
                    "node_errors": {
                        "75": {
                            "class_type": "UNETLoader",
                            "errors": [{"message": "model not found", "details": "low-noise expert"}],
                        }
                    },
                },
            )
        if request.url.path == "/free":
            return httpx.Response(200, json={})
        raise AssertionError(request.url)

    http = httpx.Client(transport=httpx.MockTransport(handler), base_url="http://comfy.test")
    client = ComfyClient("http://comfy.test", http=http)

    with pytest.raises(ComfyError) as raised:
        client.queue(api_workflow(), client_id="test-client")
    assert "node 75" in str(raised.value)
    assert "model not found" in str(raised.value)
    assert "low-noise expert" in str(raised.value)

    client.free_vram()
    assert calls == ["/prompt", "/free"]


def test_client_preserves_cloud_api_path_prefix() -> None:
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.path)
        return httpx.Response(200, json={"prompt_id": "cloud-prompt"})

    client = ComfyClient(
        "https://cloud.test/api",
        http=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    assert client.queue(api_workflow(), client_id="cloud-client")["prompt_id"] == "cloud-prompt"
    assert seen == ["/api/prompt"]


def test_client_follows_cloud_output_redirect() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "cloud.test":
            return httpx.Response(302, headers={"location": "https://cdn.test/output.mp4"})
        if request.url.host == "cdn.test":
            return httpx.Response(200, content=b"video-bytes")
        raise AssertionError(request.url)

    client = ComfyClient(
        "https://cloud.test/api",
        http=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    assert client.download(
        {"filename": "output.mp4", "subfolder": "", "type": "output"}
    ) == b"video-bytes"


def test_runner_persists_status_request_history_and_output(tmp_path: Path) -> None:
    workflow_path = tmp_path / "t2v-api.json"
    workflow_path.write_text(json.dumps(api_workflow()))
    for folder, name in (
        ("diffusion_models", "wan2.2_t2v_high_noise_14B_fp8_scaled.safetensors"),
        ("diffusion_models", "wan2.2_t2v_low_noise_14B_fp8_scaled.safetensors"),
        ("text_encoders", "umt5_xxl_fp8_e4m3fn_scaled.safetensors"),
        ("vae", "wan_2.1_vae.safetensors"),
    ):
        path = tmp_path / "models" / folder / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"model")

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/prompt":
            return httpx.Response(200, json={"prompt_id": "prompt-123", "number": 7})
        if request.url.path == "/history/prompt-123":
            return httpx.Response(
                200,
                json={
                    "prompt-123": {
                        "status": {"completed": True, "status_str": "success"},
                        "outputs": {
                            "90": {
                                "videos": [
                                    {"filename": "diony_00001.mp4", "subfolder": "diony", "type": "output"}
                                ]
                            }
                        },
                    }
                },
            )
        if request.url.path == "/view":
            return httpx.Response(200, content=b"x" * 64)
        if request.url.path == "/free":
            return httpx.Response(200, json={})
        raise AssertionError(request.url)

    client = ComfyClient(
        "http://comfy.test",
        http=httpx.Client(transport=httpx.MockTransport(handler), base_url="http://comfy.test"),
    )
    runner = ComfyWanRunner(
        client=client,
        workflows={"t2v-A14B": workflow_path},
        model_root=tmp_path,
        poll_interval=0,
    )
    dest = tmp_path / "negative.mp4"

    result = runner.run(GenerationConfig(mode="t2v-A14B", prompt="A static shot."), dest)

    assert result == dest
    assert dest.read_bytes() == b"x" * 64
    status = json.loads(dest.with_suffix(".comfy.status.json").read_text())
    assert status["state"] == "completed"
    assert status["prompt_id"] == "prompt-123"
    log = json.loads(dest.with_suffix(".comfy.json").read_text())
    assert log["request"]["mode"] == "t2v-A14B"
    assert log["history"]["status"]["completed"] is True


def test_runner_reports_queue_progress_and_vram_peak(tmp_path: Path) -> None:
    workflow_path = tmp_path / "t2v-api.json"
    workflow_path.write_text(json.dumps(api_workflow()))
    for folder, name in (
        ("diffusion_models", "wan2.2_t2v_high_noise_14B_fp8_scaled.safetensors"),
        ("diffusion_models", "wan2.2_t2v_low_noise_14B_fp8_scaled.safetensors"),
        ("text_encoders", "umt5_xxl_fp8_e4m3fn_scaled.safetensors"),
        ("vae", "wan_2.1_vae.safetensors"),
    ):
        path = tmp_path / "models" / folder / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"model")

    histories = iter(
        [
            None,
            {
                "status": {"completed": True, "status_str": "success"},
                "outputs": {"90": {"videos": [{"filename": "take.mp4", "type": "output"}]}},
            },
        ]
    )

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/prompt" and request.method == "POST":
            return httpx.Response(200, json={"prompt_id": "progress-1", "number": 3})
        if request.url.path == "/queue":
            return httpx.Response(
                200,
                json={"queue_running": [[3, "progress-1"]], "queue_pending": [[4, "later"]]},
            )
        if request.url.path == "/prompt" and request.method == "GET":
            return httpx.Response(200, json={"exec_info": {"queue_remaining": 2}})
        if request.url.path == "/system_stats":
            return httpx.Response(
                200,
                json={
                    "devices": [
                        {"name": "GPU 0", "vram_total": 80 * 1024**3, "vram_free": 28 * 1024**3}
                    ]
                },
            )
        if request.url.path == "/history/progress-1":
            row = next(histories)
            return httpx.Response(200, json={} if row is None else {"progress-1": row})
        if request.url.path == "/view":
            return httpx.Response(200, content=b"v" * 64)
        if request.url.path == "/free":
            return httpx.Response(200, json={})
        raise AssertionError(request.url)

    events: list[dict] = []
    runner = ComfyWanRunner(
        client=ComfyClient(
            "http://comfy.test",
            http=httpx.Client(transport=httpx.MockTransport(handler), base_url="http://comfy.test"),
        ),
        workflows={"t2v-A14B": workflow_path},
        model_root=tmp_path,
        poll_interval=0,
    )
    dest = tmp_path / "progress.mp4"
    runner.run(
        GenerationConfig(mode="t2v-A14B", prompt="A static shot."),
        dest,
        status_callback=events.append,
    )

    assert [row["state"] for row in events][:2] == ["queued", "processing"]
    assert any(row.get("queue_pending") == 1 for row in events)
    assert any(row.get("queue_remaining") == 2 for row in events)
    completed = events[-1]
    assert completed["state"] == "completed"
    assert completed["vram_peak_bytes"] == 52 * 1024**3
    status = json.loads(dest.with_suffix(".comfy.status.json").read_text())
    assert status["vram_peak_bytes"] == 52 * 1024**3
    assert status["elapsed_ms"] >= 0


def test_runner_resumes_existing_comfy_prompt_without_requeue(tmp_path: Path) -> None:
    calls: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append((request.method, request.url.path))
        if request.url.path == "/history/existing-123":
            return httpx.Response(
                200,
                json={
                    "existing-123": {
                        "status": {"completed": True, "status_str": "success"},
                        "outputs": {
                            "90": {"videos": [{"filename": "existing.mp4", "type": "output"}]}
                        },
                    }
                },
            )
        if request.url.path == "/view":
            return httpx.Response(200, content=b"r" * 64)
        if request.url.path == "/free":
            return httpx.Response(200, json={})
        if request.url.path in {"/queue", "/prompt", "/system_stats"} and request.method == "GET":
            return httpx.Response(200, json={})
        raise AssertionError(request.url)

    client = ComfyClient(
        "http://comfy.test",
        http=httpx.Client(transport=httpx.MockTransport(handler), base_url="http://comfy.test"),
    )
    runner = ComfyWanRunner(client=client, workflows={}, model_root=tmp_path, poll_interval=0)
    dest = tmp_path / "recovered.mp4"
    dest.with_suffix(".comfy.status.json").write_text(
        json.dumps(
            {
                "state": "processing",
                "mode": "t2v-A14B",
                "attempt": 1,
                "prompt_id": "existing-123",
                "seed": 99,
            }
        )
    )

    assert runner.resume(dest) == dest
    assert dest.read_bytes() == b"r" * 64
    assert ("POST", "/prompt") not in calls
    status = json.loads(dest.with_suffix(".comfy.status.json").read_text())
    assert status["state"] == "completed"
    assert status["recovered"] is True


def test_completed_output_is_not_retried_when_post_job_free_is_unavailable(tmp_path: Path) -> None:
    workflow_path = tmp_path / "t2v-api.json"
    workflow_path.write_text(json.dumps(api_workflow()))
    for folder, name in (
        ("diffusion_models", "wan2.2_t2v_high_noise_14B_fp8_scaled.safetensors"),
        ("diffusion_models", "wan2.2_t2v_low_noise_14B_fp8_scaled.safetensors"),
        ("text_encoders", "umt5_xxl_fp8_e4m3fn_scaled.safetensors"),
        ("vae", "wan_2.1_vae.safetensors"),
    ):
        path = tmp_path / "models" / folder / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"model")

    queued = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal queued
        if request.url.path == "/prompt":
            queued += 1
            return httpx.Response(200, json={"prompt_id": "prompt-1"})
        if request.url.path == "/history/prompt-1":
            return httpx.Response(
                200,
                json={
                    "prompt-1": {
                        "status": {"completed": True, "status_str": "success"},
                        "outputs": {"90": {"videos": [{"filename": "clip.mp4", "type": "output"}]}},
                    }
                },
            )
        if request.url.path == "/view":
            return httpx.Response(200, content=b"x" * 64)
        if request.url.path == "/free":
            return httpx.Response(404, text="not supported")
        raise AssertionError(request.url)

    runner = ComfyWanRunner(
        client=ComfyClient(
            "http://comfy.test",
            http=httpx.Client(transport=httpx.MockTransport(handler), base_url="http://comfy.test"),
        ),
        workflows={"t2v-A14B": workflow_path},
        model_root=tmp_path,
        retries=1,
    )
    destination = tmp_path / "landed.mp4"

    assert runner.run(GenerationConfig(mode="t2v-A14B", prompt="A static shot."), destination) == destination
    assert queued == 1
    status = json.loads(destination.with_suffix(".comfy.status.json").read_text())
    assert status["state"] == "completed"
    assert "free" in status["warning"].lower()


def test_runner_logs_full_mutated_payload_when_queue_rejects(tmp_path: Path) -> None:
    workflow_path = tmp_path / "t2v-api.json"
    workflow_path.write_text(json.dumps(api_workflow()))
    for folder, name in (
        ("diffusion_models", "wan2.2_t2v_high_noise_14B_fp8_scaled.safetensors"),
        ("diffusion_models", "wan2.2_t2v_low_noise_14B_fp8_scaled.safetensors"),
        ("text_encoders", "umt5_xxl_fp8_e4m3fn_scaled.safetensors"),
        ("vae", "wan_2.1_vae.safetensors"),
    ):
        path = tmp_path / "models" / folder / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"model")

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/prompt":
            return httpx.Response(
                400,
                json={
                    "error": {"message": "Prompt outputs failed validation"},
                    "node_errors": {"75": {"errors": [{"message": "low-noise model missing"}]}},
                },
            )
        if request.url.path == "/free":
            return httpx.Response(200, json={})
        raise AssertionError(request.url)

    runner = ComfyWanRunner(
        client=ComfyClient(
            "http://comfy.test",
            http=httpx.Client(transport=httpx.MockTransport(handler), base_url="http://comfy.test"),
        ),
        workflows={"t2v-A14B": workflow_path},
        model_root=tmp_path,
        poll_interval=0,
        retries=0,
    )
    destination = tmp_path / "failed.mp4"

    with pytest.raises(ComfyError, match="node 75"):
        runner.run(GenerationConfig(mode="t2v-A14B", prompt="A static shot."), destination)

    log = json.loads(destination.with_suffix(".comfy.json").read_text())
    assert log["workflow_api"]["6"]["inputs"]["text"] == "A static shot."
    assert "node 75" in log["attempts"][0]["error"]


def test_runner_persists_preflight_failure_state(tmp_path: Path) -> None:
    runner = ComfyWanRunner(
        client=ComfyClient(
            "http://comfy.test",
            http=httpx.Client(
                transport=httpx.MockTransport(lambda request: httpx.Response(500)),
                base_url="http://comfy.test",
            ),
        ),
        workflows={"t2v-A14B": tmp_path / "missing.api.json"},
        model_root=tmp_path,
        retries=0,
    )
    destination = tmp_path / "preflight.mp4"

    with pytest.raises(ComfyError, match="workflow missing"):
        runner.run(GenerationConfig(mode="t2v-A14B", prompt="A static shot."), destination)

    status = json.loads(destination.with_suffix(".comfy.status.json").read_text())
    assert status["state"] == "failed"
    assert status["stage"] == "preflight"


def test_i2v_smoke_uploads_keyframe_binds_experts_and_writes_clip(tmp_path: Path) -> None:
    workflow = api_workflow()
    workflow["12"] = {"class_type": "LoadImage", "inputs": {"image": "old.png"}}
    workflow_path = tmp_path / "i2v-api.json"
    workflow_path.write_text(json.dumps(workflow))
    for folder, name in (
        ("diffusion_models", "wan2.2_i2v_high_noise_14B_fp8_scaled.safetensors"),
        ("diffusion_models", "wan2.2_i2v_low_noise_14B_fp8_scaled.safetensors"),
        ("text_encoders", "umt5_xxl_fp8_e4m3fn_scaled.safetensors"),
        ("vae", "wan_2.1_vae.safetensors"),
    ):
        path = tmp_path / "models" / folder / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"model")
    keyframe = tmp_path / "keyframe.png"
    keyframe.write_bytes(b"png-bytes")
    queued_workflow = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/upload/image":
            return httpx.Response(200, json={"name": "keyframe.png", "subfolder": "diony"})
        if request.url.path == "/prompt":
            queued_workflow.update(json.loads(request.content)["prompt"])
            return httpx.Response(200, json={"prompt_id": "i2v-smoke"})
        if request.url.path == "/history/i2v-smoke":
            return httpx.Response(
                200,
                json={
                    "i2v-smoke": {
                        "status": {"completed": True, "status_str": "success"},
                        "outputs": {"90": {"videos": [{"filename": "i2v.mp4", "type": "output"}]}},
                    }
                },
            )
        if request.url.path == "/view":
            return httpx.Response(200, content=b"i" * 64)
        if request.url.path == "/free":
            return httpx.Response(200, json={})
        raise AssertionError(request.url)

    runner = ComfyWanRunner(
        client=ComfyClient(
            "http://comfy.test",
            http=httpx.Client(transport=httpx.MockTransport(handler), base_url="http://comfy.test"),
        ),
        workflows={"i2v-A14B": workflow_path},
        model_root=tmp_path,
        poll_interval=0,
    )
    destination = tmp_path / "i2v-smoke.mp4"

    assert runner.run(
        GenerationConfig(mode="i2v-A14B", prompt="The subject takes one measured step."),
        destination,
        image=keyframe,
    ) == destination
    assert queued_workflow["12"]["inputs"]["image"] == "diony/keyframe.png"
    assert "i2v_high_noise" in queued_workflow["74"]["inputs"]["unet_name"]
    assert "i2v_low_noise" in queued_workflow["75"]["inputs"]["unet_name"]
    assert destination.stat().st_size == 64


def test_oom_failure_frees_vram_and_retries_without_losing_seed(tmp_path: Path) -> None:
    workflow_path = tmp_path / "t2v-api.json"
    workflow_path.write_text(json.dumps(api_workflow()))
    for folder, name in (
        ("diffusion_models", "wan2.2_t2v_high_noise_14B_fp8_scaled.safetensors"),
        ("diffusion_models", "wan2.2_t2v_low_noise_14B_fp8_scaled.safetensors"),
        ("text_encoders", "umt5_xxl_fp8_e4m3fn_scaled.safetensors"),
        ("vae", "wan_2.1_vae.safetensors"),
    ):
        path = tmp_path / "models" / folder / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"model")
    queued_seeds: list[int] = []
    frees = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal frees
        if request.url.path == "/prompt":
            queued_seeds.append(json.loads(request.content)["prompt"]["3"]["inputs"]["seed"])
            return httpx.Response(200, json={"prompt_id": f"attempt-{len(queued_seeds)}"})
        if request.url.path == "/history/attempt-1":
            return httpx.Response(
                200,
                json={
                    "attempt-1": {
                        "status": {
                            "completed": False,
                            "status_str": "error",
                            "messages": [
                                [
                                    "execution_error",
                                    {"node_id": "3", "exception_message": "CUDA out of memory"},
                                ]
                            ],
                        }
                    }
                },
            )
        if request.url.path == "/history/attempt-2":
            return httpx.Response(
                200,
                json={
                    "attempt-2": {
                        "status": {"completed": True, "status_str": "success"},
                        "outputs": {"90": {"videos": [{"filename": "retry.mp4", "type": "output"}]}},
                    }
                },
            )
        if request.url.path == "/view":
            return httpx.Response(200, content=b"o" * 64)
        if request.url.path == "/free":
            frees += 1
            return httpx.Response(200, json={})
        raise AssertionError(request.url)

    runner = ComfyWanRunner(
        client=ComfyClient(
            "http://comfy.test",
            http=httpx.Client(transport=httpx.MockTransport(handler), base_url="http://comfy.test"),
        ),
        workflows={"t2v-A14B": workflow_path},
        model_root=tmp_path,
        poll_interval=0,
        retries=1,
    )
    destination = tmp_path / "recovered-from-oom.mp4"

    assert runner.run(
        GenerationConfig(mode="t2v-A14B", prompt="A static shot.", seed=441), destination
    ) == destination
    assert queued_seeds == [441, 441]
    assert frees == 2
    log = json.loads(destination.with_suffix(".comfy.json").read_text())
    assert "CUDA out of memory" in log["attempts"][0]["error"]
