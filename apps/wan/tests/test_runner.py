import json
from pathlib import Path

import pytest

from diony_wan.runner import WanError, prove_generate, spark_argv


def test_argv_is_720p_official_and_never_dashscope(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("DIONY_VAULT", str(tmp_path / "vault"))
    monkeypatch.setenv("DIONY_PRODUCER_VAULT", str(tmp_path / "vault"))
    gen = tmp_path / "generate.py"
    gen.write_text("# fake\n")
    ckpt = tmp_path / "Wan2.2-T2V-A14B"
    ckpt.mkdir()
    (ckpt / "config.json").write_text("{}")
    monkeypatch.setenv("DIONY_WAN_GENERATE", str(gen))
    monkeypatch.setenv("WAN_CKPT_T2V", str(ckpt))
    dest = tmp_path / "out.mp4"
    cmd = spark_argv(
        task="t2v-A14B",
        prompt="A tungsten newsroom, 35mm, held master.",
        negative_prompt="live-action presenter, screen within a screen",
        seed=42,
        save_file=dest,
        frame_num=81,
    )
    joined = " ".join(cmd)
    assert cmd[1] == str(gen)
    assert cmd.count(str(gen)) == 1
    assert "--task t2v-A14B" in joined
    assert "--size 1280*720" in joined
    assert "--offload_model True" in joined
    assert "--sample_steps 24" in joined
    assert "--t5_cpu" in joined
    assert "--use_prompt_extend" not in cmd
    assert "dashscope" not in joined.lower()
    assert str(dest) in cmd
    prompt_arg = cmd[cmd.index("--prompt") + 1]
    assert prompt_arg == "A tungsten newsroom, 35mm, held master."
    assert cmd[cmd.index("--negative_prompt") + 1] == "live-action presenter, screen within a screen"
    monkeypatch.setenv("DIONY_PYTHON", "/usr/bin/python3")
    with_py = spark_argv(
        task="t2v-A14B",
        prompt="A tungsten newsroom, 35mm, held master.",
        seed=42,
        save_file=dest,
        frame_num=81,
    )
    assert with_py[0] == "/usr/bin/python3"
    still = tmp_path / "key.png"
    still.write_bytes(b"png")
    i2v_ckpt = tmp_path / "i2v"
    i2v_ckpt.mkdir()
    monkeypatch.setenv("WAN_CKPT_I2V", str(i2v_ckpt))
    i2v = spark_argv(
        task="i2v-A14B",
        prompt="Same room, push in.",
        seed=7,
        save_file=dest,
        frame_num=81,
        image=still,
    )
    assert "--task" in i2v and "i2v-A14B" in i2v
    assert "--image" in i2v
    assert str(still) in i2v
    clip = tmp_path / "prev.mp4"
    clip.write_bytes(b"not-a-still")
    no_img = spark_argv(
        task="t2v-A14B",
        prompt="Held master.",
        seed=1,
        save_file=dest,
        frame_num=81,
        image=clip,
    )
    assert "--image" not in no_img


def test_argv_accepts_only_a_verified_hot_checkpoint_override(tmp_path, monkeypatch) -> None:
    gen = tmp_path / "generate.py"
    gen.write_text("# fake\n")
    source = tmp_path / "volume" / "Wan2.2-T2V-A14B"
    hot = tmp_path / "overlay" / "Wan2.2-T2V-A14B"
    source.mkdir(parents=True)
    hot.mkdir(parents=True)
    (source / "weights.safetensors").write_bytes(b"source")
    (hot / "weights.safetensors").write_bytes(b"hot")
    monkeypatch.setenv("DIONY_WAN_GENERATE", str(gen))
    monkeypatch.setenv("WAN_CKPT_T2V", str(source))

    command = spark_argv(
        task="t2v-A14B",
        prompt="A locked studio master.",
        seed=42,
        save_file=tmp_path / "out.mp4",
        frame_num=81,
        checkpoint=hot,
    )

    assert command[command.index("--ckpt_dir") + 1] == str(hot)


def test_native_s2v_and_animate_argv_preserve_their_conditioning_inputs(
    tmp_path, monkeypatch
) -> None:
    gen = tmp_path / "generate.py"
    gen.write_text("# fake\n")
    s2v_ckpt = tmp_path / "s2v"
    animate_ckpt = tmp_path / "animate"
    s2v_ckpt.mkdir()
    animate_ckpt.mkdir()
    still = tmp_path / "performer.png"
    audio = tmp_path / "line.wav"
    source_root = tmp_path / "prepared-motion"
    still.write_bytes(b"png")
    audio.write_bytes(b"wav")
    source_root.mkdir()
    monkeypatch.setenv("DIONY_WAN_GENERATE", str(gen))
    monkeypatch.setenv("WAN_CKPT_S2V", str(s2v_ckpt))
    monkeypatch.setenv("WAN_CKPT_ANIMATE", str(animate_ckpt))

    s2v = spark_argv(
        task="s2v-14B",
        prompt="The performer sings in one continuous take.",
        seed=17,
        save_file=tmp_path / "s2v.mp4",
        frame_num=81,
        image=still,
        audio=audio,
    )
    assert s2v[s2v.index("--image") + 1] == str(still)
    assert s2v[s2v.index("--audio") + 1] == str(audio)
    # One VAE-aligned 84-frame S2V window avoids the old second denoise pass;
    # Diony's exact publish overlay trims the encoded negative to 81 frames.
    assert s2v[s2v.index("--infer_frames") + 1] == "84"
    assert "--start_from_ref" in s2v

    animate = spark_argv(
        task="animate-14B",
        prompt="The exact subject follows the prepared performance.",
        seed=18,
        save_file=tmp_path / "animate.mp4",
        frame_num=81,
        image=still,
        animate_root=source_root,
        quality_lora_ids=["wan2.2-animate-relighting"],
    )
    assert animate[animate.index("--src_root_path") + 1] == str(source_root)
    assert "--use_relighting_lora" in animate

    with pytest.raises(WanError, match="not qualified"):
        spark_argv(
            task="animate-14B",
            prompt="A clean hero shot.",
            seed=19,
            save_file=tmp_path / "unsafe.mp4",
            frame_num=81,
            image=still,
            animate_root=source_root,
            quality_lora_ids=["downloaded-face-lora"],
        )


def test_b200_argv_runs_a14b_on_gpu(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("DIONY_VAULT", str(tmp_path / "vault"))
    monkeypatch.setenv("DIONY_PRODUCER_VAULT", str(tmp_path / "vault"))
    from diony_contracts.cluster import write_site_lock
    from diony_wan.runner import spark_argv

    write_site_lock(
        {
            "sku": "b200-v1",
            "compute_path": "b200",
            "gpu_ready": True,
            "compute": {
                "path": "b200",
                "profile": "b200-v1",
                "gpu_class": "1×B200",
                "exception_by": "Rohn",
            },
        }
    )
    gen = tmp_path / "generate.py"
    gen.write_text("# fake\n")
    ckpt = tmp_path / "Wan2.2-T2V-A14B"
    ckpt.mkdir()
    (ckpt / "config.json").write_text("{}")
    monkeypatch.setenv("DIONY_WAN_GENERATE", str(gen))
    monkeypatch.setenv("WAN_CKPT_T2V", str(ckpt))
    dest = tmp_path / "out.mp4"
    joined = " ".join(
        spark_argv(
            task="t2v-A14B",
            prompt="A tungsten newsroom, 35mm, held master.",
            seed=42,
            save_file=dest,
            frame_num=81,
        )
    )
    assert "--offload_model False" in joined
    assert "--sample_steps 24" in joined
    assert "--t5_cpu" not in joined
    assert "--convert_model_dtype" not in joined
    assert "--use_prompt_extend" not in joined


def test_classify_wan_log_ignores_tqdm_and_float32_warning() -> None:
    from diony_wan.runner import classify_wan_log

    blob = (
        "Loading checkpoint shards: 100%|██████████| 6/6\n"
        "There are modules in WanModel that should be kept in float32: []. "
        "Casting directly with `to()` can lead to inconsistent results; "
        "set `torch_dtype` in `from_pretrained()` instead to keep these modules in float32.\n"
        "  0%|          | 0/40 [00:00<?, ?it/s]\n"
    )
    msg = classify_wan_log(blob, 1)
    assert "Loading ch" not in msg
    assert "0/40" not in msg
    assert "float32" not in msg
    assert "720p" in msg
    assert classify_wan_log("CUDA out of memory", 1).startswith("GPU out of memory")
    assert "signal 9" in classify_wan_log("", -9)


def test_classify_wan_log_flash_attn_assert() -> None:
    from diony_wan.runner import classify_wan_log

    blob = (
        "File \"/opt/wan22/wan/modules/attention.py\", line 112, in flash_attention\n"
        "    assert FLASH_ATTN_2_AVAILABLE\n"
        "AssertionError\n"
    )
    msg = classify_wan_log(blob, 1)
    assert "AssertionError" not in msg or "flash_attn" in msg.lower() or "SDPA" in msg
    assert "flash_attn" in msg.lower() or "SDPA" in msg or "attention" in msg.lower()


def test_classify_wan_log_sdpa_shape_crash() -> None:
    from diony_wan.runner import classify_wan_log

    msg = classify_wan_log(
        "RuntimeError: mat1 and mat2 shapes cannot be multiplied (3024000x128 and 5120x5120)",
        1,
    )
    assert "flattened" in msg.lower() or "SDPA" in msg or "[B, L, N, C]" in msg


def test_ensure_attention_fallback_is_idempotent(tmp_path) -> None:
    from diony_wan.runner import _SDPA_MARKER, ensure_attention_fallback

    mods = tmp_path / "wan" / "modules"
    mods.mkdir(parents=True)
    attn = mods / "attention.py"
    attn.write_text(
        "def flash_attention():\n"
        "    \"\"\"\n"
        "    dtype:          torch.dtype. Apply when dtype of q/k/v "
        "is not float16/bfloat16.\n"
        "    \"\"\"\n"
        "    half_dtypes = ()\n"
        "        assert FLASH_ATTN_2_AVAILABLE\n"
        "    x = 1\n",
        encoding="utf-8",
    )
    (mods / "model.py").write_text("from .attention import flash_attention\n", encoding="utf-8")
    ensure_attention_fallback(tmp_path)
    text = attn.read_text(encoding="utf-8")
    assert _SDPA_MARKER in text
    assert "not FLASH_ATTN_2_AVAILABLE and not FLASH_ATTN_3_AVAILABLE" in text
    assert text.index("return attention(") < text.index("assert FLASH_ATTN_2_AVAILABLE")
    assert (mods / "model.py").read_text(encoding="utf-8").startswith(
        "from .attention import attention as flash_attention"
    )
    ensure_attention_fallback(tmp_path)
    assert attn.read_text(encoding="utf-8").count(_SDPA_MARKER) == 1
    assert (mods / "model.py").read_text(encoding="utf-8").count(_SDPA_MARKER) == 1


def test_s2v_overlay_forces_the_production_1280_by_720_raster(tmp_path) -> None:
    from diony_wan.runner import _S2V_RASTER_MARKER, ensure_s2v_production_raster

    wan = tmp_path / "wan"
    wan.mkdir()
    source = wan / "speech2video.py"
    source.write_text(
        "        size = self.get_gen_size(\n"
        "            size=None,\n"
        "            max_area=max_area,\n"
        "            ref_image_path=ref_image_path,\n"
        "            pre_video_path=None)\n",
        encoding="utf-8",
    )

    ensure_s2v_production_raster(tmp_path)
    first = source.read_text(encoding="utf-8")
    ensure_s2v_production_raster(tmp_path)

    assert source.read_text(encoding="utf-8") == first
    assert _S2V_RASTER_MARKER in first
    assert "size = (720, 1280)" in first


def test_native_generate_overlay_routes_negative_conditioning(tmp_path) -> None:
    from diony_wan.runner import _NEGATIVE_PROMPT_MARKER, ensure_negative_prompt_input

    generate = tmp_path / "generate.py"
    generate.write_text(
        "parser.add_argument(\n"
        "        '--prompt',\n"
        "        type=str,\n"
        "        default=None,\n"
        "        help='The prompt to generate the video from.')\n"
        "parser.add_argument(\n"
        "        '--use_prompt_extend',\n"
        "        action='store_true')\n"
        "video = wan_i2v.generate(\n"
        "            args.prompt,\n"
        "            img,\n"
        "            guide_scale=args.sample_guide_scale,\n"
        "            seed=args.base_seed,\n"
        "            offload_model=args.offload_model)\n",
        encoding="utf-8",
    )

    ensure_negative_prompt_input(tmp_path)
    first = generate.read_text(encoding="utf-8")
    ensure_negative_prompt_input(tmp_path)

    assert generate.read_text(encoding="utf-8") == first
    assert _NEGATIVE_PROMPT_MARKER in first
    assert "--negative_prompt" in first
    assert "args.negative_prompt" in first
    assert (tmp_path / "generate.py.diony-upstream").is_file()
    assert _NEGATIVE_PROMPT_MARKER not in (tmp_path / "generate.py.diony-upstream").read_text()


def test_ensure_attention_fallback_migrates_after_flatten_patch(tmp_path) -> None:
    from diony_wan.runner import _SDPA_AFTER_FLATTEN, ensure_attention_fallback

    mods = tmp_path / "wan" / "modules"
    mods.mkdir(parents=True)
    (mods / "attention.py").write_text(
        "def flash_attention():\n"
        "    \"\"\"\n"
        "    dtype:          torch.dtype. Apply when dtype of q/k/v "
        "is not float16/bfloat16.\n"
        "    \"\"\"\n"
        "    q = q.flatten(0, 1)\n"
        + _SDPA_AFTER_FLATTEN
        + "        x = 1\n",
        encoding="utf-8",
    )
    (mods / "model.py").write_text("from .attention import flash_attention\n", encoding="utf-8")
    ensure_attention_fallback(tmp_path)
    text = (mods / "attention.py").read_text(encoding="utf-8")
    assert text.index("return attention(") < text.index("q = q.flatten(0, 1)")
    assert text.count("return attention(") == 1


def test_attention_overlay_can_force_cudnn_sdpa_with_safe_default_fallback(tmp_path) -> None:
    from diony_wan.runner import _ATTENTION_BACKEND_MARKER, ensure_attention_fallback

    mods = tmp_path / "wan" / "modules"
    mods.mkdir(parents=True)
    attn = mods / "attention.py"
    attn.write_text(
        "import torch\n"
        "import warnings\n"
        "def attention(q, k, v, attn_mask=None, causal=False, dropout_p=0.0):\n"
        "        out = torch.nn.functional.scaled_dot_product_attention(\n"
        "            q, k, v, attn_mask=attn_mask, is_causal=causal, dropout_p=dropout_p)\n"
        "        return out\n",
        encoding="utf-8",
    )

    ensure_attention_fallback(tmp_path)
    first = attn.read_text(encoding="utf-8")
    ensure_attention_fallback(tmp_path)

    assert attn.read_text(encoding="utf-8") == first
    assert _ATTENTION_BACKEND_MARKER in first
    assert "DIONY_WAN_ATTENTION_BACKEND" in first
    assert "SDPBackend.CUDNN_ATTENTION" in first
    assert "except (RuntimeError, NotImplementedError)" in first
    assert first.count(_ATTENTION_BACKEND_MARKER) == 1


def test_optional_engines_are_lazy_and_idempotent(tmp_path) -> None:
    from diony_wan.runner import ensure_optional_engine_lazy_imports

    package = tmp_path / "wan"
    package.mkdir()
    init = package / "__init__.py"
    init.write_text(
        "from . import configs, distributed, modules\n"
        "from .image2video import WanI2V\n"
        "from .speech2video import WanS2V\n"
        "from .text2video import WanT2V\n"
        "from .textimage2video import WanTI2V\n"
        "from .animate import WanAnimate\n",
        encoding="utf-8",
    )

    ensure_optional_engine_lazy_imports(tmp_path)
    first = init.read_text(encoding="utf-8")
    assert "DIONY_LAZY_OPTIONAL_ENGINES" in first
    assert "from .speech2video import WanS2V" not in first.split("def __getattr__", 1)[0]
    assert "from .animate import WanAnimate" not in first.split("def __getattr__", 1)[0]
    ensure_optional_engine_lazy_imports(tmp_path)
    assert init.read_text(encoding="utf-8") == first


def test_abort_wan_does_not_name_diony_producer() -> None:
    from diony_wan.runner import abort_wan, stop_stray_generate
    import inspect

    blob = inspect.getsource(abort_wan) + inspect.getsource(stop_stray_generate)
    assert "pkill" not in blob
    assert "diony-producer" in blob  # skip that process name
    assert "generate.py" in blob


def test_parse_wan_progress_reads_denoise_not_shards() -> None:
    from diony_wan.runner import parse_wan_progress, progress_label

    blob = (
        "Loading checkpoint shards: 100%|██████████| 6/6\n"
        "  60%|██████    | 24/40 [12:18<08:04, 30.26s/it]\n"
    )
    row = parse_wan_progress(blob)
    assert row["step"] == 24
    assert row["total"] == 40
    assert row["pct"] == 60
    assert "24/40" in progress_label(row)
    assert parse_wan_progress("Loading checkpoint shards: 100%|█| 6/6") == {}


def test_new_wan_attempt_rotates_prior_progress_log(tmp_path) -> None:
    from diony_wan.runner import start_wan_attempt_log

    log = tmp_path / "negative_720.wan.log"
    log.write_text("prior attempt\r100%|x| 24/24 [14:00<00:00, 35s/it]\n")

    archive = start_wan_attempt_log(log)

    assert archive == tmp_path / "negative_720.wan.attempt-001.log"
    assert archive.read_text().startswith("prior attempt")
    assert log.read_bytes() == b""

    log.write_text("second attempt\n")
    second_archive = start_wan_attempt_log(log)

    assert second_archive == tmp_path / "negative_720.wan.attempt-002.log"
    assert second_archive.read_text() == "second attempt\n"
    assert log.read_bytes() == b""


def test_wan_progress_hides_unmeasured_rate_and_eta() -> None:
    from diony_wan.runner import parse_wan_progress, progress_label

    row = parse_wan_progress("  0%|          | 0/40 [00:00<?, ?it/s]\n")

    assert row["rate"] == ""
    assert row["eta"] == ""
    assert progress_label(row) == "Denoise starting"
    assert "?" not in progress_label(row)


def test_s2v_progress_combines_repeated_denoise_passes_monotonically() -> None:
    from diony_wan.runner import parse_wan_progress, progress_label

    first_pass = " 50%|█████| 12/24 [07:12<07:12, 36.00s/it]\n"
    second_pass = (
        first_pass
        + "100%|██████████| 24/24 [14:24<00:00, 36.00s/it]\n"
        + "  8%|▊| 2/24 [01:12<13:12, 36.00s/it]\n"
    )

    first = parse_wan_progress(first_pass, expected_passes=2)
    second = parse_wan_progress(second_pass, expected_passes=2)

    assert first["step"] == 12
    assert first["total"] == 48
    assert first["pct"] == 25
    assert second["step"] == 26
    assert second["total"] == 48
    assert second["pct"] == 54
    assert second["pass_index"] == 2
    assert second["pass_total"] == 2
    assert "26/48" in progress_label(second)
    assert "pass 2 of 2" in progress_label(second)


def test_s2v_execution_receipt_exposes_measured_raster_and_effective_denoise_work(
    tmp_path, monkeypatch
) -> None:
    from datetime import datetime, timezone
    import time

    import diony_wan.runner as runner

    output = tmp_path / "negative.mp4"
    output.write_bytes(b"addressed output" * 8)
    monkeypatch.setattr(
        runner,
        "_probe_generated_video",
        lambda _path: {"width": 1280, "height": 704, "frames": 77},
    )
    runner._write_generation_receipt(
        output,
        started_at=datetime.now(timezone.utc),
        started_monotonic=time.monotonic(),
        status="succeeded",
        backend="native",
        selected_lane="wan22_quality_cli_cudnn_sdpa",
        task="s2v-14B",
        frame_num=81,
        width=1280,
        height=720,
        steps=24,
        negative_conditioning=True,
        attention_backend="cudnn_sdpa",
        inference_frames_per_pass=84,
        denoise_passes=1,
    )

    receipt = json.loads(output.with_suffix(".generation.json").read_text())
    assert receipt["native_width"] == 1280
    assert receipt["native_height"] == 704
    assert receipt["frame_num"] == 77
    assert receipt["quality_contract_matched"] is False
    assert receipt["inference_frames_per_pass"] == 84
    assert receipt["denoise_passes"] == 1
    assert receipt["effective_denoise_steps"] == 24


def test_prove_generate_is_a_noop_in_pytest() -> None:
    prove_generate()


def test_s2v_publish_overlay_pads_audio_and_caps_the_video_at_contract_frames(
    tmp_path,
) -> None:
    from diony_wan.runner import ensure_s2v_exact_publish

    generate = tmp_path / "generate.py"
    generate.write_text(
        "merge_video_audio(video_path=args.save_file, audio_path=args.audio)\n"
        'merge_video_audio(video_path=args.save_file, audio_path="tts.wav")\n'
    )
    utils = tmp_path / "wan" / "utils" / "utils.py"
    utils.parent.mkdir(parents=True)
    utils.write_text(
        "def merge_video_audio(video_path: str, audio_path: str):\n"
        "        command = [\n"
        "            '-map', '0:v:0',\n"
        "            '-map', '1:a:0',\n"
        "            '-shortest',\n"
        "            temp_output\n"
        "        ]\n"
    )

    assert ensure_s2v_exact_publish(tmp_path) == generate
    assert ensure_s2v_exact_publish(tmp_path) == generate

    generated = generate.read_text()
    utility = utils.read_text()
    assert generated.count("DIONY_S2V_EXACT_PUBLISH") == 1
    assert generated.count("video_frames=args.frame_num") == 2
    assert generated.count("fps=cfg.sample_fps") == 2
    assert "video_frames: int | None = None" in utility
    assert "'-af', 'apad', '-frames:v', str(int(video_frames))" in utility
    assert utility.count("DIONY_S2V_EXACT_PUBLISH") == 1


def test_wan_child_env_can_select_cudnn_per_take_without_mutating_producer(
    tmp_path, monkeypatch
) -> None:
    import os
    from diony_wan.runner import _wan_env

    monkeypatch.delenv("DIONY_WAN_ATTENTION_BACKEND", raising=False)
    monkeypatch.setenv("DIONY_WAN_REPO", str(tmp_path))

    child, _ = _wan_env(attention_backend="cudnn_sdpa")

    assert child["DIONY_WAN_ATTENTION_BACKEND"] == "cudnn_sdpa"
    assert "DIONY_WAN_ATTENTION_BACKEND" not in os.environ


def test_comfy_backend_dispatches_without_touching_generate_py(tmp_path, monkeypatch) -> None:
    from diony_wan import comfy
    from diony_wan.runner import run_generate

    destination = tmp_path / "negative.mp4"
    captured = {}

    class FakeRunner:
        def run(self, config, dest, *, image=None, last_image=None):
            captured.update(config=config, dest=dest, image=image, last_image=last_image)
            dest.write_bytes(b"x" * 64)
            return dest

    monkeypatch.setenv("DIONY_WAN_BACKEND", "comfyui")
    monkeypatch.setattr(comfy, "configured_runner", lambda: FakeRunner())

    result = run_generate(
        task="t2v-A14B",
        prompt="A controlled tracking shot.",
        negative_prompt="no flicker",
        seed=11,
        save_file=destination,
        duration_frames=80,
        fps=24.0,
        width=1280,
        height=720,
        steps=28,
        cfg=3.5,
        sampler="euler",
        scheduler="beta",
    )

    assert result == destination
    assert captured["config"].mode == "t2v-A14B"
    assert captured["config"].frames == 81
    assert captured["config"].negative_prompt == "no flicker"
    assert captured["dest"] == destination
    receipt = json.loads(destination.with_suffix(".generation.json").read_text())
    assert receipt["status"] == "succeeded"
    assert receipt["selected_lane"] == "comfyui"
    assert receipt["quality_contract_matched"] is False
    assert len(receipt["output_sha256"]) == 64


def test_validated_native_lane_uses_the_resident_worker_without_changing_inputs(
    tmp_path, monkeypatch
) -> None:
    import diony_wan.runner as runner
    from diony_wan.resident import ResidentDecision

    destination = tmp_path / "negative.mp4"
    locked = tmp_path / "locked.png"
    locked.write_bytes(b"locked reference")
    checkpoint = tmp_path / "Wan2.2-I2V-A14B"
    checkpoint.mkdir()
    generate = tmp_path / "generate.py"
    generate.write_text("# pinned upstream")
    monkeypatch.setenv("DIONY_WAN_GENERATE", str(generate))
    monkeypatch.setenv("WAN_CKPT_I2V", str(checkpoint))
    monkeypatch.setenv("DIONY_WAN_RESIDENT_CANARY_PERCENT", "100")
    monkeypatch.setattr(runner, "wan_needs_offload", lambda: False)
    monkeypatch.setattr(
        runner,
        "resident_decision",
        lambda **_kwargs: ResidentDecision(True, "validated", "receipt-sha"),
    )
    captured = {}

    def resident(payload, **_kwargs):
        captured.update(payload)
        destination.write_bytes(b"resident output" * 8)
        return {"ok": True, "pipeline_reused": True}

    monkeypatch.setattr(runner, "run_resident", resident)
    monkeypatch.setattr(
        runner,
        "_probe_generated_video",
        lambda _path: {"width": 1280, "height": 720, "frames": 81},
    )
    monkeypatch.setattr(
        runner.subprocess,
        "Popen",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("CLI fallback ran")),
    )

    result = runner.run_generate(
        task="i2v-A14B",
        prompt="The locked subject crosses the room.",
        negative_prompt="identity drift, flicker",
        seed=10401,
        save_file=destination,
        duration_frames=120,
        fps=23.976,
        image=locked,
        steps=24,
    )

    assert result == destination
    assert captured["quality_contract_sha256"]
    assert captured["task"] == "i2v-A14B"
    assert captured["frame_num"] == 81
    assert captured["sample_steps"] == 24
    assert captured["offload_model"] is False
    assert captured["image"] == str(locked)
    receipt = json.loads(destination.with_suffix(".generation.json").read_text())
    assert receipt["status"] == "succeeded"
    assert receipt["selected_lane"] == "resident_bf16_eager"
    assert receipt["resident_receipt_sha256"] == "receipt-sha"
    assert receipt["quality_contract_matched"] is True


def test_validated_resident_prewarm_is_gated_and_finishes_before_shot_timing(
    tmp_path, monkeypatch
) -> None:
    import diony_wan.runner as runner
    from diony_wan.resident import ResidentDecision

    checkpoint = tmp_path / "Wan2.2-S2V-14B"
    checkpoint.mkdir()
    captured = {}
    monkeypatch.setattr(runner, "wan_needs_offload", lambda: False)
    monkeypatch.setattr(
        runner,
        "resident_decision",
        lambda **_kwargs: ResidentDecision(True, "validated", "receipt-sha"),
    )
    monkeypatch.setattr(runner, "resident_canary_selected", lambda _identifier: True)
    monkeypatch.setattr(
        runner,
        "_wan_env",
        lambda **_kwargs: ({"DIONY_WAN_ATTENTION_BACKEND": "cudnn_sdpa"}, str(tmp_path)),
    )
    monkeypatch.setattr(runner, "wan_python", lambda: "/usr/bin/python3")

    def resident(payload, **kwargs):
        captured.update(payload=payload, kwargs=kwargs)
        return {"ok": True, "pipeline_reused": False, "prewarm_seconds": 89.0}

    monkeypatch.setattr(runner, "run_resident", resident)

    warmed = runner.prewarm_validated_resident(
        task="s2v-14B",
        checkpoint=checkpoint,
        identifier="project/take/negative.mp4",
        attention_backend="cudnn_sdpa",
    )

    assert warmed is True
    assert captured["payload"]["operation"] == "prewarm"
    assert captured["payload"]["task"] == "s2v-14B"
    assert captured["payload"]["checkpoint"] == str(checkpoint)
    assert captured["kwargs"]["python"] == "/usr/bin/python3"


def test_validated_s2v_lane_preserves_locked_audio_and_one_denoise_window(
    tmp_path, monkeypatch
) -> None:
    import diony_wan.runner as runner
    from diony_wan.resident import ResidentDecision

    destination = tmp_path / "negative.mp4"
    locked = tmp_path / "locked.png"
    audio = tmp_path / "clean-dialogue.wav"
    locked.write_bytes(b"locked reference")
    audio.write_bytes(b"locked audio")
    checkpoint = tmp_path / "Wan2.2-S2V-14B"
    checkpoint.mkdir()
    generate = tmp_path / "generate.py"
    generate.write_text("# pinned upstream")
    monkeypatch.setenv("DIONY_WAN_GENERATE", str(generate))
    monkeypatch.setenv("WAN_CKPT_S2V", str(checkpoint))
    monkeypatch.setenv("DIONY_WAN_RESIDENT_CANARY_PERCENT", "100")
    monkeypatch.setattr(runner, "wan_needs_offload", lambda: False)
    monkeypatch.setattr(
        runner,
        "resident_decision",
        lambda **_kwargs: ResidentDecision(True, "validated", "receipt-sha"),
    )
    captured = {}

    def resident(payload, **_kwargs):
        captured.update(payload)
        destination.write_bytes(b"resident s2v output" * 8)
        return {"ok": True, "pipeline_reused": True}

    monkeypatch.setattr(runner, "run_resident", resident)
    monkeypatch.setattr(
        runner,
        "_probe_generated_video",
        lambda _path: {"width": 1280, "height": 720, "frames": 81},
    )
    monkeypatch.setattr(
        runner.subprocess,
        "Popen",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("CLI fallback ran")),
    )

    result = runner.run_generate(
        task="s2v-14B",
        prompt="The locked performer delivers one measured line.",
        negative_prompt="identity drift, lip drift, flicker",
        seed=10401,
        save_file=destination,
        duration_frames=120,
        fps=24.0,
        image=locked,
        audio=audio,
        steps=24,
    )

    assert result == destination
    assert captured["task"] == "s2v-14B"
    assert captured["image"] == str(locked)
    assert captured["audio"] == str(audio)
    assert captured["inference_frames_per_pass"] == 84
    receipt = json.loads(destination.with_suffix(".generation.json").read_text())
    assert receipt["quality_contract_matched"] is True
    assert receipt["inference_frames_per_pass"] == 84
    assert receipt["denoise_passes"] == 1
