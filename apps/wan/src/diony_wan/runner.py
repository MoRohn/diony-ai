"""Official Wan2.2 generate.py. Never Dashscope. Native 720p negative only."""

from __future__ import annotations

import functools
import hashlib
import json
import os
import re
import shutil
import signal
import subprocess
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from diony_contracts.live import ckpt_dir, generate_py, wan_frame_num, wan_repo
from diony_wan.resident import (
    QUALITY_CONTRACT_SHA256,
    ResidentError,
    resident_canary_selected,
    resident_decision,
    resident_job_payload,
    run_resident,
    suppress_resident,
    stop_resident,
)

SIZE_720 = "1280*720"
TASKS = {
    "t2v-A14B": "t2v-A14B",
    "i2v-A14B": "i2v-A14B",
    "ti2v-5B": "ti2v-5B",
    "s2v-14B": "s2v-14B",
    "animate-14B": "animate-14B",
}


class WanError(RuntimeError):
    pass


def generation_receipt_path(save_file: Path) -> Path:
    return save_file.with_suffix(".generation.json")


def _write_generation_receipt(
    save_file: Path,
    *,
    started_at: datetime,
    started_monotonic: float,
    status: str,
    backend: str,
    selected_lane: str,
    task: str,
    frame_num: int,
    width: int,
    height: int,
    steps: int,
    negative_conditioning: bool,
    attention_backend: str,
    resident_reason: str = "",
    resident_receipt_sha256: str = "",
    resident_kernel_mode: str = "",
    fallback_reason: str = "",
    error: str = "",
) -> None:
    output_sha256 = ""
    output_bytes = 0
    if status == "succeeded" and save_file.is_file():
        output_bytes = save_file.stat().st_size
        digest = hashlib.sha256()
        with save_file.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        output_sha256 = digest.hexdigest()
    quality_contract_matched = bool(
        backend == "native"
        and width == 1280
        and height == 720
        and frame_num == 81
        and steps == 24
        and negative_conditioning
    )
    payload = {
        "schema_version": 1,
        "status": status,
        "backend": backend,
        "selected_lane": selected_lane,
        "task": task,
        "native_width": width,
        "native_height": height,
        "frame_num": frame_num,
        "sample_steps": steps,
        "sample_solver": "unipc" if backend == "native" else "backend_configured",
        "precision": "bfloat16" if backend == "native" else "backend_configured",
        "negative_conditioning": negative_conditioning,
        "finish_required": True,
        "quality_contract_sha256": QUALITY_CONTRACT_SHA256,
        "quality_contract_matched": quality_contract_matched,
        "attention_backend": attention_backend or "auto",
        "resident_reason": resident_reason,
        "resident_receipt_sha256": resident_receipt_sha256,
        "resident_kernel_mode": resident_kernel_mode,
        "fallback_reason": fallback_reason[:500],
        "started_at": started_at.isoformat(),
        "completed_at": datetime.now(timezone.utc).isoformat(),
        "elapsed_seconds": round(max(0.0, time.monotonic() - started_monotonic), 6),
        "output_sha256": output_sha256,
        "output_bytes": output_bytes,
        "error": error[:500],
    }
    path = generation_receipt_path(save_file)
    path.parent.mkdir(parents=True, exist_ok=True)
    pending = path.with_suffix(path.suffix + ".part")
    pending.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    pending.replace(path)


def wan_backend() -> str:
    backend = os.environ.get("DIONY_WAN_BACKEND", "native").strip().lower()
    if backend not in {"native", "comfyui"}:
        raise WanError(f"Unsupported DIONY_WAN_BACKEND={backend!r}; use native or comfyui.")
    return backend


_PROGRESS = re.compile(
    r"(?P<pct>\d+)%\|[^|\n]*\|\s*(?P<step>\d+)/(?P<total>\d+)\s*"
    r"\[(?P<elapsed>[^<]+)<(?P<eta>[^,]+),\s*(?P<rate>[^\]]+)\]"
)


def _measured_progress_value(value: str) -> str:
    """Return only tqdm values that carry an actual measurement."""
    clean = (value or "").strip()
    if not clean or "?" in clean or clean.lower() in {"n/a", "na", "none", "--"}:
        return ""
    return clean


def parse_wan_progress(text: str, *, expected_passes: int = 1) -> dict:
    """Last denoise progress, folded across repeated S2V clip passes."""
    best: dict = {}
    blob = (text or "").replace("\r", "\n")
    expected_passes = max(int(expected_passes), 1)
    completed_steps = 0
    pass_index = 1
    previous_step: int | None = None
    previous_total: int | None = None
    for match in _PROGRESS.finditer(blob):
        total = int(match.group("total"))
        if total < 8:
            continue
        step = int(match.group("step"))
        if (
            previous_step is not None
            and previous_total == total
            and previous_step >= previous_total
            and step < previous_step
        ):
            completed_steps += previous_total
            pass_index += 1
        pass_total = max(expected_passes, pass_index)
        overall_step = completed_steps + step
        overall_total = total * pass_total
        best = {
            "step": overall_step,
            "total": overall_total,
            "pct": round(100 * overall_step / overall_total),
            "elapsed": match.group("elapsed").strip(),
            "eta": (
                _measured_progress_value(match.group("eta"))
                if pass_index == pass_total
                else ""
            ),
            "rate": _measured_progress_value(match.group("rate")),
            "pass_index": pass_index,
            "pass_total": pass_total,
        }
        previous_step = step
        previous_total = total
    return best


def progress_label(row: dict) -> str:
    if not row:
        return ""
    step = row.get("step")
    total = row.get("total")
    rate = row.get("rate") or ""
    eta = row.get("eta") or ""
    bits = ["Denoise starting" if not step else f"Denoise {step}/{total}"]
    if row.get("pass_total", 1) > 1:
        bits.append(f"pass {row.get('pass_index', 1)} of {row['pass_total']}")
    if rate:
        bits.append(rate)
    if eta:
        bits.append(f"{eta} left")
    return " · ".join(bits)


def start_wan_attempt_log(log_path: Path) -> Path | None:
    """Start one observable attempt without mixing progress from an earlier retry."""
    log_path.parent.mkdir(parents=True, exist_ok=True)
    archive: Path | None = None
    if log_path.is_file() and log_path.stat().st_size:
        for index in range(1, 10_000):
            candidate = log_path.with_name(
                f"{log_path.stem}.attempt-{index:03d}{log_path.suffix}"
            )
            if not candidate.exists():
                log_path.replace(candidate)
                archive = candidate
                break
        else:
            raise WanError(f"Wan attempt log archive is full for {log_path}")
    log_path.touch(exist_ok=True)
    return archive


_PROGRESS_LINE = re.compile(
    r"Loading checkpoint|Loading ch|it/s]|%\s*\|"
    r"|Casting directly with|kept in float32|set `torch_dtype`"
    r"|offload_model is not specified|tqdm",
    re.I,
)


def wan_needs_offload() -> bool:
    """Spark GB10 needs offload. B200/B300 run A14B on the GPU.

    Official generate.py defaults `--offload_model True` on one GPU. B200/B300
    must pass False. Do not paste Spark `--convert_model_dtype --t5_cpu` there.
    """
    try:
        from diony_contracts.cluster import site_status

        path = str(site_status().get("path") or "local")
    except Exception:
        path = "local"
    return path not in {"b200", "b300"}


def classify_wan_log(text: str, returncode: int = 1) -> str:
    """Human farm/QC line. Never surface tqdm or the float32 dtype warning."""
    blob = text or ""
    if returncode < 0:
        return f"Wan2.2 was interrupted (signal {-returncode}) before the 720p negative landed."
    if "CUDA out of memory" in blob or "OutOfMemoryError" in blob:
        return "GPU out of memory during Wan2.2. B200/B300 run A14B on-GPU; Spark uses offload."
    if "No space left" in blob:
        return "GPU overlay disk is full. Scratch stays on the vault (/workspace), not container overlay."
    if "FLASH_ATTN_2_AVAILABLE" in blob or (
        "AssertionError" in blob and "flash_attention" in blob
    ) or blob.strip() == "AssertionError":
        return (
            "Wan2.2 attention needs FlashAttention-2. This GPU has no flash_attn wheel; "
            "Diony uses PyTorch SDPA until that wheel exists."
        )
    if "mat1 and mat2 shapes cannot be multiplied" in blob:
        return (
            "Wan2.2 SDPA ran on flattened FlashAttention tensors. "
            "Diony now falls back on the original [B, L, N, C] layout."
        )
    idx = blob.rfind("Traceback (most recent call last):")
    if idx >= 0:
        lines = [ln.strip() for ln in blob[idx:].splitlines() if ln.strip()]
        for ln in reversed(lines):
            if "Error" in ln or ln.startswith("Exception"):
                return ln[:240]
        return (lines[-1] if lines else "Wan2.2 failed")[:240]
    kept: list[str] = []
    for ln in blob.splitlines():
        one = ln.strip()
        if not one or _PROGRESS_LINE.search(one):
            continue
        if re.match(r"^\d+%\|", one) or re.search(r"\|\s*\d+/\d+", one):
            continue
        kept.append(one)
    if kept:
        return kept[-1][:240]
    if "0/40" in blob or "Loading checkpoint" in blob or "kept in float32" in blob:
        return (
            "Wan2.2 stopped during the 720p pass after loading weights. "
            "That is not a clip QC fail — the camera process was cut off."
        )
    return "Wan2.2 stopped before the 720p negative landed."


def wan_python() -> str:
    """Interpreter that can `import torch`. Not the Diony venv unless it has CUDA torch.

    Producer often runs from `$DIONY_REPO/.venv` (FastAPI only). generate.py must use
    the image Python (`/usr/bin/python3` on RunPod) that already has torch.
    """
    override = os.environ.get("DIONY_PYTHON", "").strip()
    if override:
        return override
    if os.environ.get("PYTEST_CURRENT_TEST"):
        return "python3"
    return _probed_torch_python()


@functools.lru_cache(maxsize=1)
def _probed_torch_python() -> str:
    seen: list[str] = []
    for raw in (
        "/usr/bin/python3",
        "/usr/local/bin/python",
        shutil.which("python3") or "",
        "python3",
    ):
        py = str(raw).strip()
        if not py or py in seen:
            continue
        seen.append(py)
        try:
            proc = subprocess.run(
                [py, "-c", "import torch"],
                capture_output=True,
                timeout=8,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            continue
        if proc.returncode == 0:
            return py
    return "python3"


def spark_argv(
    *,
    task: str,
    prompt: str,
    negative_prompt: str | None = None,
    seed: int,
    save_file: Path,
    frame_num: int,
    image: Path | None = None,
    audio: Path | None = None,
    animate_root: Path | None = None,
    quality_lora_ids: list[str] | tuple[str, ...] | None = None,
    offload: bool | None = None,
    steps: int = 24,
    checkpoint: Path | None = None,
) -> list[str]:
    if task not in TASKS:
        task = "t2v-A14B"
    if "dashscope" in prompt.lower() or "qwen-plus" in prompt.lower():
        raise WanError("Prompt must not request hosted extend.")
    ckpt = checkpoint or ckpt_dir(task)
    py = generate_py()
    if not py.is_file():
        raise WanError(
            f"Wan2.2 generate.py not at {py}. Clone https://github.com/Wan-Video/Wan2.2 to /opt/wan22."
        )
    if not ckpt.is_dir():
        raise WanError(f"Wan checkpoint missing: {ckpt}. Pull the Dual-Spark pack first.")
    if offload is None:
        offload = wan_needs_offload()
    if task == "s2v-14B":
        if image is None or image.suffix.lower() not in {".png", ".jpg", ".jpeg", ".webp"}:
            raise WanError("S2V requires a reference image.")
        if audio is None or audio.suffix.lower() not in {".wav", ".mp3", ".flac"}:
            raise WanError("S2V requires recorded WAV, MP3, or FLAC audio.")
    if task == "animate-14B" and (animate_root is None or not animate_root.is_dir()):
        raise WanError("Animate requires an official preprocessed source root.")
    lora_ids = list(dict.fromkeys(quality_lora_ids or []))
    qualified = {"wan2.2-animate-relighting"}
    unsupported = [lora_id for lora_id in lora_ids if lora_id not in qualified]
    if unsupported:
        raise WanError("Quality LoRA is not qualified for Diony's native Wan path: " + ", ".join(unsupported))
    if lora_ids and task != "animate-14B":
        raise WanError("The qualified relighting LoRA is only valid for animate-14B.")
    cmd = [
        wan_python(),
        str(py),
        "--task",
        task,
        "--size",
        SIZE_720,
        "--ckpt_dir",
        str(ckpt),
        "--offload_model",
        "True" if offload else "False",
        "--prompt",
        prompt,
        "--base_seed",
        str(int(seed)),
        "--frame_num",
        str(int(frame_num)),
        "--sample_steps",
        str(int(steps)),
        "--save_file",
        str(save_file),
    ]
    if negative_prompt and negative_prompt.strip():
        cmd.extend(["--negative_prompt", negative_prompt.strip()])
    if offload:
        cmd.extend(["--convert_model_dtype", "--t5_cpu"])
    if image is not None and image.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp"}:
        cmd.extend(["--image", str(image)])
    if audio is not None and task == "s2v-14B" and audio.suffix.lower() in {".wav", ".mp3", ".flac"}:
        cmd.extend(["--audio", str(audio)])
        cmd.extend(["--infer_frames", str(max(16, int(frame_num) - 1)), "--start_from_ref"])
    if animate_root is not None and task == "animate-14B":
        cmd.extend(["--src_root_path", str(animate_root)])
    if "wan2.2-animate-relighting" in lora_ids:
        cmd.append("--use_relighting_lora")
    return cmd


def _wan_env(*, attention_backend: str | None = None) -> tuple[dict[str, str], str]:
    """Host env for generate.py: PYTHONPATH=/opt/wan22, Dashscope keys empty."""
    env = os.environ.copy()
    if attention_backend:
        env["DIONY_WAN_ATTENTION_BACKEND"] = attention_backend
    env["DASH_API_KEY"] = ""
    env.pop("DASHSCOPE_API_KEY", None)
    env["PYTHONUNBUFFERED"] = "1"
    repo = wan_repo()
    py_root = str(repo if repo.is_dir() else generate_py().parent)
    env["PYTHONPATH"] = py_root + os.pathsep + env.get("PYTHONPATH", "")
    vault = Path(os.environ.get("DIONY_VAULT") or "/diony/vault")
    cache = vault / ".cache"
    scratch = vault / ".tmp"
    try:
        cache.mkdir(parents=True, exist_ok=True)
        scratch.mkdir(parents=True, exist_ok=True)
        env.setdefault("HF_HOME", str(cache / "huggingface"))
        env.setdefault("TORCH_HOME", str(cache / "torch"))
        env.setdefault("XDG_CACHE_HOME", str(cache))
        env.setdefault("TMPDIR", str(scratch))
        env.setdefault("TMP", str(scratch))
        env.setdefault("TEMP", str(scratch))
    except OSError:
        pass
    return env, py_root


_SDPA_MARKER = "DIONY_SDPA_FALLBACK"
_ATTENTION_BACKEND_MARKER = "DIONY_ATTENTION_BACKEND"
_ATTENTION_SDPA_CALL = """        out = torch.nn.functional.scaled_dot_product_attention(
            q, k, v, attn_mask=attn_mask, is_causal=causal, dropout_p=dropout_p)
"""
_ATTENTION_ROUTED_CALL = """        # DIONY_ATTENTION_BACKEND: exact SDPA kernel selection for qualified B200 runs.
        _diony_backend = os.environ.get('DIONY_WAN_ATTENTION_BACKEND', 'auto').strip().lower()
        if _diony_backend == 'cudnn_sdpa':
            try:
                from torch.nn.attention import SDPBackend, sdpa_kernel
                with sdpa_kernel([SDPBackend.CUDNN_ATTENTION]):
                    out = torch.nn.functional.scaled_dot_product_attention(
                        q, k, v, attn_mask=attn_mask, is_causal=causal, dropout_p=dropout_p)
            except (RuntimeError, NotImplementedError) as exc:
                warnings.warn(f'Qualified cuDNN SDPA unavailable; using automatic SDPA: {exc}')
                out = torch.nn.functional.scaled_dot_product_attention(
                    q, k, v, attn_mask=attn_mask, is_causal=causal, dropout_p=dropout_p)
        else:
            out = torch.nn.functional.scaled_dot_product_attention(
                q, k, v, attn_mask=attn_mask, is_causal=causal, dropout_p=dropout_p)
"""
_SDPA_AFTER_FLATTEN = """        if not FLASH_ATTN_2_AVAILABLE:
            # DIONY_SDPA_FALLBACK: official generate.py requires flash_attn.
            # Lease host pip skips that wheel. Use Wan's own SDPA path.
            return attention(
                q,
                k,
                v,
                q_lens=q_lens,
                k_lens=k_lens,
                dropout_p=dropout_p,
                softmax_scale=softmax_scale,
                q_scale=q_scale,
                causal=causal,
                window_size=window_size,
                deterministic=deterministic,
                dtype=dtype,
                fa_version=version,
            )
        assert FLASH_ATTN_2_AVAILABLE
"""
_SDPA_EARLY_ANCHOR = (
    "    dtype:          torch.dtype. Apply when dtype of q/k/v "
    "is not float16/bfloat16.\n    \"\"\"\n"
)
_SDPA_EARLY = (
    _SDPA_EARLY_ANCHOR
    + """    # DIONY_SDPA_FALLBACK: SDPA on [B, L, N, C] before FA2 flatten.
    if not FLASH_ATTN_2_AVAILABLE and not FLASH_ATTN_3_AVAILABLE:
        return attention(
            q,
            k,
            v,
            q_lens=q_lens,
            k_lens=k_lens,
            dropout_p=dropout_p,
            softmax_scale=softmax_scale,
            q_scale=q_scale,
            causal=causal,
            window_size=window_size,
            deterministic=deterministic,
            dtype=dtype,
            fa_version=version,
        )
"""
)
_MODEL_IMPORT = "from .attention import flash_attention\n"
_MODEL_ALIAS = (
    "from .attention import attention as flash_attention  # DIONY_SDPA_FALLBACK\n"
)
_LAZY_ENGINE_MARKER = "DIONY_LAZY_OPTIONAL_ENGINES"
_NEGATIVE_PROMPT_MARKER = "DIONY_NEGATIVE_PROMPT_INPUT"
_WAN_LAZY_IMPORTS = """# DIONY_LAZY_OPTIONAL_ENGINES: optional engines cannot block T2V/I2V.
def __getattr__(name):
    if name == "WanS2V":
        from .speech2video import WanS2V
        return WanS2V
    if name == "WanAnimate":
        from .animate import WanAnimate
        return WanAnimate
    raise AttributeError(name)
"""


def ensure_optional_engine_lazy_imports(repo: Path | None = None) -> Path | None:
    """Keep optional S2V/Animate dependencies out of the core camera import path."""
    if os.environ.get("PYTEST_CURRENT_TEST") and repo is None:
        return None
    root = Path(repo) if repo is not None else wan_repo()
    package = root / "wan" / "__init__.py"
    if not package.is_file():
        return None
    text = package.read_text(encoding="utf-8")
    if _LAZY_ENGINE_MARKER not in text:
        text = text.replace("from .speech2video import WanS2V\n", "")
        text = text.replace("from .speech2video import WanS2V", "")
        text = text.replace("from .animate import WanAnimate\n", "")
        text = text.replace("from .animate import WanAnimate", "")
        package.write_text(text.rstrip() + "\n\n" + _WAN_LAZY_IMPORTS, encoding="utf-8")
    return package


def ensure_negative_prompt_input(repo: Path | None = None) -> Path | None:
    """Expose Wan's existing `n_prompt` input through official generate.py.

    Upstream T2V/I2V already implements true classifier-free negative
    conditioning, but its CLI does not expose that argument. This idempotent
    host overlay retains the upstream default negatives and appends Diony's
    Shot exclusions instead of mentioning unwanted content in the positive
    prompt.
    """
    if os.environ.get("PYTEST_CURRENT_TEST") and repo is None:
        return None
    root = Path(repo) if repo is not None else wan_repo()
    generate = root / "generate.py"
    if not generate.is_file():
        return None
    original = generate.read_text(encoding="utf-8")
    if _NEGATIVE_PROMPT_MARKER in original:
        return generate
    flag_at = original.find('"--use_prompt_extend"')
    if flag_at < 0:
        flag_at = original.find("'--use_prompt_extend'")
    parser_at = original.rfind("parser.add_argument(", 0, flag_at)
    if flag_at < 0 or parser_at < 0:
        raise WanError("Wan generate.py cannot expose Diony's negative-conditioning input")
    line_at = original.rfind("\n", 0, parser_at) + 1
    indent = original[line_at:parser_at]
    argument = (
        f"{indent}# {_NEGATIVE_PROMPT_MARKER}: Diony host overlay.\n"
        f"{indent}parser.add_argument(\n"
        f'{indent}    "--negative_prompt",\n'
        f"{indent}    type=str,\n"
        f'{indent}    default="",\n'
        f'{indent}    help="Negative conditioning appended to the pinned Wan defaults.")\n'
    )
    patched = original[:line_at] + argument + original[line_at:]
    call_anchor = (
        "            guide_scale=args.sample_guide_scale,\n"
        "            seed=args.base_seed,"
    )
    call_patch = (
        "            guide_scale=args.sample_guide_scale,\n"
        "            n_prompt=(f\"{cfg.sample_neg_prompt}, {args.negative_prompt}\" "
        "if args.negative_prompt else \"\"),\n"
        "            seed=args.base_seed,"
    )
    patched, count = patched.replace(call_anchor, call_patch), patched.count(call_anchor)
    if count < 1:
        raise WanError("Wan generate.py has no compatible negative-conditioning call site")
    upstream = generate.with_name(f"{generate.name}.diony-upstream")
    if not upstream.exists():
        shutil.copy2(generate, upstream)
    generate.write_text(patched, encoding="utf-8")
    return generate


def ensure_attention_fallback(repo: Path | None = None) -> Path | None:
    """Official Wan `flash_attention` asserts FlashAttention-2.

    SDPA must run on [B, L, N, C] *before* FA2 flatten. Calling SDPA after
    flatten makes `self.o` see (tokens×heads)×C vs hidden×hidden.
    """
    if os.environ.get("PYTEST_CURRENT_TEST") and repo is None:
        return None
    root = Path(repo) if repo is not None else wan_repo()
    attn = root / "wan" / "modules" / "attention.py"
    model = root / "wan" / "modules" / "model.py"
    patched: Path | None = None
    if attn.is_file():
        text = attn.read_text(encoding="utf-8")
        if _SDPA_AFTER_FLATTEN in text:
            text = text.replace(_SDPA_AFTER_FLATTEN, "        assert FLASH_ATTN_2_AVAILABLE\n")
        early = "not FLASH_ATTN_2_AVAILABLE and not FLASH_ATTN_3_AVAILABLE"
        if early not in text and _SDPA_EARLY_ANCHOR in text:
            text = text.replace(_SDPA_EARLY_ANCHOR, _SDPA_EARLY, 1)
        if _ATTENTION_BACKEND_MARKER not in text and _ATTENTION_SDPA_CALL in text:
            if "import os\n" not in text:
                text = text.replace("import torch\n", "import os\nimport torch\n", 1)
            text = text.replace(_ATTENTION_SDPA_CALL, _ATTENTION_ROUTED_CALL, 1)
        if text != attn.read_text(encoding="utf-8"):
            attn.write_text(text, encoding="utf-8")
        patched = attn
    if model.is_file():
        text = model.read_text(encoding="utf-8")
        if _MODEL_ALIAS not in text and _MODEL_IMPORT in text:
            model.write_text(text.replace(_MODEL_IMPORT, _MODEL_ALIAS, 1), encoding="utf-8")
            patched = model
    return patched


def prove_generate() -> None:
    """Load generate.py the same way a clip will. One import miss, not one per shot.

    Official generate.py imports DashScopePromptExpander at module load. The pip
    package `dashscope` must be present. Keys stay empty; we never pass
    --use_prompt_extend. Pytest skips (fake generate.py has no argparse).
    """
    if os.environ.get("PYTEST_CURRENT_TEST"):
        return
    if wan_backend() == "comfyui":
        from diony_wan.comfy import readiness_report

        report = readiness_report()
        if not report.get("ok"):
            errors = list(report.get("errors") or [])
            for mode, row in (report.get("modes") or {}).items():
                if mode == "t2v-A14B" and not row.get("ok"):
                    errors.append(str(row.get("error") or f"{mode} is not ready"))
            raise WanError("ComfyUI Wan readiness failed: " + "; ".join(errors))
        return
    py = generate_py()
    if not py.is_file():
        raise WanError(f"Wan2.2 generate.py not at {py}.")
    ensure_attention_fallback()
    ensure_optional_engine_lazy_imports()
    ensure_negative_prompt_input()
    env, py_root = _wan_env()
    proc = subprocess.run(
        [wan_python(), str(py), "--help"],
        cwd=py_root,
        env=env,
        capture_output=True,
        text=True,
        timeout=90,
        check=False,
    )
    if proc.returncode != 0:
        err = (proc.stderr or proc.stdout or "generate.py --help failed")[-4000:]
        raise WanError(err)


_wan_lock = threading.Lock()
_wan_proc: subprocess.Popen | None = None


def abort_wan() -> None:
    """SIGTERM the farm-owned generate.py. Never diony-producer."""
    if wan_backend() == "comfyui":
        try:
            from diony_wan.comfy import configured_runner

            configured_runner().client.interrupt()
        except Exception:  # noqa: BLE001 - abort stays best-effort
            pass
        return
    # A validated resident worker owns the active CUDA call instead of a
    # per-clip generate.py process. Stopping it is the same bounded FARM abort.
    stop_resident()
    with _wan_lock:
        proc = _wan_proc
    if proc is None or proc.poll() is not None:
        return
    proc.terminate()
    try:
        proc.wait(timeout=8)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()


def stop_stray_generate() -> None:
    """SIGTERM leftover `generate.py --task` writers. Never diony-producer."""
    if os.environ.get("PYTEST_CURRENT_TEST"):
        return
    try:
        out = subprocess.check_output(["ps", "-eo", "pid,args"], text=True, timeout=5)
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return
    for line in out.splitlines():
        if "generate.py" not in line or "--task" not in line:
            continue
        if "diony-producer" in line:
            continue
        if "takes/" not in line and "negative_720" not in line:
            continue
        try:
            pid = int(line.split(None, 1)[0])
            os.kill(pid, signal.SIGTERM)
        except (ValueError, ProcessLookupError, PermissionError):
            continue


def run_generate(
    *,
    task: str,
    prompt: str,
    seed: int,
    save_file: Path,
    duration_frames: int,
    fps: float = 23.976,
    image: Path | None = None,
    audio: Path | None = None,
    animate_root: Path | None = None,
    quality_lora_ids: list[str] | tuple[str, ...] | None = None,
    last_image: Path | None = None,
    negative_prompt: str | None = None,
    width: int = 1280,
    height: int = 720,
    steps: int = 24,
    cfg: float = 3.5,
    sampler: str = "euler",
    scheduler: str = "beta",
    attention_backend: str | None = None,
    timeout: int | None = None,
) -> Path:
    """Run the selected local Wan backend and write a 720p negative."""
    started_at = datetime.now(timezone.utc)
    started_monotonic = time.monotonic()
    frames = wan_frame_num(duration_frames, fps)
    save_file.parent.mkdir(parents=True, exist_ok=True)
    if wan_backend() == "comfyui":
        from diony_wan.comfy import (
            DEFAULT_NEGATIVE_PROMPT,
            ComfyError,
            GenerationConfig,
            configured_runner,
        )

        try:
            result = configured_runner().run(
                GenerationConfig(
                    mode=task,
                    prompt=prompt,
                    negative_prompt=negative_prompt or DEFAULT_NEGATIVE_PROMPT,
                    seed=seed,
                    width=width,
                    height=height,
                    frames=frames,
                    steps=steps,
                    cfg=cfg,
                    sampler=sampler,
                    scheduler=scheduler,
                ),
                save_file,
                image=image,
                last_image=last_image,
            )
        except ComfyError as exc:
            _write_generation_receipt(
                save_file,
                started_at=started_at,
                started_monotonic=started_monotonic,
                status="failed",
                backend="comfyui",
                selected_lane="comfyui",
                task=task,
                frame_num=frames,
                width=width,
                height=height,
                steps=steps,
                negative_conditioning=True,
                attention_backend=attention_backend or "auto",
                error=str(exc),
            )
            raise WanError(str(exc)) from exc
        _write_generation_receipt(
            save_file,
            started_at=started_at,
            started_monotonic=started_monotonic,
            status="succeeded",
            backend="comfyui",
            selected_lane="comfyui",
            task=task,
            frame_num=frames,
            width=width,
            height=height,
            steps=steps,
            negative_conditioning=True,
            attention_backend=attention_backend or "auto",
        )
        return result
    ensure_attention_fallback()
    ensure_negative_prompt_input()
    offload = wan_needs_offload()
    from diony_wan.storage import prepare_checkpoint

    checkpoint = prepare_checkpoint(task, source=ckpt_dir(task))
    cmd = spark_argv(
        task=task,
        prompt=prompt,
        negative_prompt=negative_prompt,
        seed=seed,
        save_file=save_file,
        frame_num=frames,
        image=image,
        audio=audio,
        animate_root=animate_root,
        quality_lora_ids=quality_lora_ids,
        offload=offload,
        steps=steps,
        checkpoint=checkpoint,
    )
    env, py_root = _wan_env(attention_backend=attention_backend)
    limit = timeout or int(os.environ.get("DIONY_WAN_TIMEOUT", "7200"))
    log_path = save_file.with_suffix(".wan.log")
    start_wan_attempt_log(log_path)
    decision = resident_decision(task=task, offload=offload)
    resident_selected = resident_canary_selected(str(save_file))
    resident_compatible = (
        decision.enabled
        and resident_selected
        and frames == 81
        and steps == 24
        and width == 1280
        and height == 720
        and last_image is None
        and audio is None
        and animate_root is None
        and (task != "i2v-A14B" or image is not None)
    )
    fallback_reason = ""
    if resident_compatible:
        payload = resident_job_payload(
            task=task,
            checkpoint=checkpoint,
            prompt=prompt,
            negative_prompt=negative_prompt,
            seed=seed,
            save_file=save_file,
            frame_num=frames,
            image=image,
            log_path=log_path,
        )
        try:
            run_resident(
                payload,
                python=wan_python(),
                cwd=py_root,
                env=env,
                timeout=limit,
            )
            if save_file.is_file() and save_file.stat().st_size >= 32:
                lane = f"resident_bf16_{decision.kernel_mode}"
                if attention_backend:
                    lane += f"_{attention_backend}"
                _write_generation_receipt(
                    save_file,
                    started_at=started_at,
                    started_monotonic=started_monotonic,
                    status="succeeded",
                    backend="native",
                    selected_lane=lane,
                    task=task,
                    frame_num=frames,
                    width=width,
                    height=height,
                    steps=steps,
                    negative_conditioning=True,
                    attention_backend=attention_backend or "auto",
                    resident_reason=decision.reason,
                    resident_receipt_sha256=decision.receipt_sha256,
                    resident_kernel_mode=decision.kernel_mode,
                )
                return save_file
            raise ResidentError("Resident Wan exited without a complete 720p negative.")
        except (ResidentError, OSError, subprocess.SubprocessError) as exc:
            # The exact pinned CLI is the rollback path. Remove only this
            # attempt's partial output, then rerun with the same quality inputs.
            save_file.unlink(missing_ok=True)
            suppress_resident(decision.receipt_sha256, str(exc))
            fallback_reason = f"Resident automatic rollback: {type(exc).__name__}: {exc}"
            try:
                log_path.write_text(
                    f"DIONY_RESIDENT_ROLLBACK {type(exc).__name__}: {exc}\n",
                    encoding="utf-8",
                )
            except OSError:
                pass
    elif decision.enabled and not resident_selected:
        fallback_reason = "Resident lane held by the receipt-capped canary rollout."
    elif decision.enabled:
        fallback_reason = "Resident lane is incompatible with this Take shape or conditioning."
    else:
        fallback_reason = decision.reason
    global _wan_proc
    with log_path.open("ab") as log:
        proc = subprocess.Popen(
            cmd,
            cwd=py_root,
            env=env,
            stdout=log,
            stderr=subprocess.STDOUT,
        )
        with _wan_lock:
            _wan_proc = proc
        try:
            returncode = proc.wait(timeout=limit)
        except subprocess.TimeoutExpired as exc:
            proc.kill()
            proc.wait()
            _write_generation_receipt(
                save_file,
                started_at=started_at,
                started_monotonic=started_monotonic,
                status="failed",
                backend="native",
                selected_lane="wan22_quality_cli",
                task=task,
                frame_num=frames,
                width=width,
                height=height,
                steps=steps,
                negative_conditioning=True,
                attention_backend=attention_backend or "auto",
                resident_reason=decision.reason,
                resident_receipt_sha256=decision.receipt_sha256,
                resident_kernel_mode=decision.kernel_mode,
                fallback_reason=fallback_reason,
                error="Wan2.2 timed out before the 720p negative landed.",
            )
            raise WanError("Wan2.2 timed out before the 720p negative landed.") from exc
        finally:
            with _wan_lock:
                if _wan_proc is proc:
                    _wan_proc = None
    blob = ""
    try:
        blob = log_path.read_text(errors="replace")[-12000:]
    except OSError:
        blob = ""
    if returncode != 0:
        message = classify_wan_log(blob, returncode)
        _write_generation_receipt(
            save_file,
            started_at=started_at,
            started_monotonic=started_monotonic,
            status="failed",
            backend="native",
            selected_lane="wan22_quality_cli",
            task=task,
            frame_num=frames,
            width=width,
            height=height,
            steps=steps,
            negative_conditioning=True,
            attention_backend=attention_backend or "auto",
            resident_reason=decision.reason,
            resident_receipt_sha256=decision.receipt_sha256,
            resident_kernel_mode=decision.kernel_mode,
            fallback_reason=fallback_reason,
            error=message,
        )
        raise WanError(message)
    if not save_file.is_file() or save_file.stat().st_size < 32:
        _write_generation_receipt(
            save_file,
            started_at=started_at,
            started_monotonic=started_monotonic,
            status="failed",
            backend="native",
            selected_lane="wan22_quality_cli",
            task=task,
            frame_num=frames,
            width=width,
            height=height,
            steps=steps,
            negative_conditioning=True,
            attention_backend=attention_backend or "auto",
            resident_reason=decision.reason,
            resident_receipt_sha256=decision.receipt_sha256,
            resident_kernel_mode=decision.kernel_mode,
            fallback_reason=fallback_reason,
            error=f"generate.py exited 0 but wrote no clip at {save_file}",
        )
        raise WanError(f"generate.py exited 0 but wrote no clip at {save_file}")
    lane = "wan22_quality_cli"
    if attention_backend:
        lane += f"_{attention_backend}"
    _write_generation_receipt(
        save_file,
        started_at=started_at,
        started_monotonic=started_monotonic,
        status="succeeded",
        backend="native",
        selected_lane=lane,
        task=task,
        frame_num=frames,
        width=width,
        height=height,
        steps=steps,
        negative_conditioning=True,
        attention_backend=attention_backend or "auto",
        resident_reason=decision.reason,
        resident_receipt_sha256=decision.receipt_sha256,
        resident_kernel_mode=decision.kernel_mode,
        fallback_reason=fallback_reason,
    )
    return save_file
