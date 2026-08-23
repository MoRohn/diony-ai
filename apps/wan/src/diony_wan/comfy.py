"""Validated ComfyUI API-format execution for official Wan 2.2 workflows.

The graph stays an operator-validated export of an official ComfyUI template.
Diony only validates and mutates named inputs; it never invents a node graph.
"""

from __future__ import annotations

import copy
import json
import logging
import os
import re
import time
import uuid
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlparse

import httpx


_LOG = logging.getLogger("diony.wan.comfy")


DEFAULT_NEGATIVE_PROMPT = (
    "blurry motion, flickering, inconsistent lighting, morphing faces, watermark, "
    "low quality, deformed hands, text overlay, logo"
)

MODEL_FILES: dict[str, dict[str, tuple[str, ...]]] = {
    "ti2v-5B": {
        "diffusion_models": ("wan2.2_ti2v_5B_fp16.safetensors",),
        "text_encoders": ("umt5_xxl_fp8_e4m3fn_scaled.safetensors",),
        "vae": ("wan2.2_vae.safetensors",),
    },
    "t2v-A14B": {
        "diffusion_models": (
            "wan2.2_t2v_high_noise_14B_fp8_scaled.safetensors",
            "wan2.2_t2v_low_noise_14B_fp8_scaled.safetensors",
        ),
        "text_encoders": ("umt5_xxl_fp8_e4m3fn_scaled.safetensors",),
        "vae": ("wan_2.1_vae.safetensors",),
    },
    "i2v-A14B": {
        "diffusion_models": (
            "wan2.2_i2v_high_noise_14B_fp8_scaled.safetensors",
            "wan2.2_i2v_low_noise_14B_fp8_scaled.safetensors",
        ),
        "text_encoders": ("umt5_xxl_fp8_e4m3fn_scaled.safetensors",),
        "vae": ("wan_2.1_vae.safetensors",),
    },
    "flf2v-A14B": {
        "diffusion_models": (
            "wan2.2_i2v_high_noise_14B_fp8_scaled.safetensors",
            "wan2.2_i2v_low_noise_14B_fp8_scaled.safetensors",
        ),
        "text_encoders": ("umt5_xxl_fp8_e4m3fn_scaled.safetensors",),
        "vae": ("wan_2.1_vae.safetensors",),
    },
}

NATIVE_ONLY_MODES = {"s2v-14B", "animate-14B"}

CAMERA_VOCABULARY = (
    "slow dolly in",
    "rack focus from foreground to background",
    "orbital camera",
    "static shot",
    "handheld camera",
    "pan left",
    "pan right",
    "tilt up",
    "tilt down",
    "tracking shot",
    "crane up",
)

MIN_COMFY_VERSION = (0, 3, 46)

QUALITY_TIERS: dict[str, dict[str, Any]] = {
    # Lightning/LoRA acceleration is intentionally not implicit. These remain
    # official-template settings and the prepared site must own its weights.
    "preview": {
        "mode": "ti2v-5B",
        "width": 832,
        "height": 480,
        "frames": 81,
        "steps": 20,
        "cfg": 3.5,
        "sampler": "euler",
        "scheduler": "beta",
    },
    "production": {
        "mode": "t2v-A14B",
        "width": 1280,
        "height": 720,
        "frames": 81,
        "steps": 24,
        "cfg": 3.5,
        "sampler": "euler",
        "scheduler": "beta",
    },
    "hero": {
        "mode": "t2v-A14B",
        "width": 1280,
        "height": 720,
        "frames": 81,
        "steps": 30,
        "cfg": 3.5,
        "sampler": "euler",
        "scheduler": "beta",
    },
}


class ComfyError(RuntimeError):
    """A preflight, workflow, transport, node, or output failure."""


@dataclass(frozen=True)
class GenerationConfig:
    mode: str
    prompt: str
    negative_prompt: str = DEFAULT_NEGATIVE_PROMPT
    seed: int = 0
    width: int = 1280
    height: int = 720
    frames: int = 81
    steps: int = 24
    cfg: float = 3.5
    sampler: str = "euler"
    scheduler: str = "beta"
    quality_lora_ids: tuple[str, ...] = ()


def resolve_quality_tier(
    *,
    quality_tier: str,
    prompt: str,
    seed: int,
    mode: str | None = None,
    negative_prompt: str = DEFAULT_NEGATIVE_PROMPT,
    width: int | None = None,
    height: int | None = None,
    frames: int | None = None,
    steps: int | None = None,
    cfg: float | None = None,
    sampler: str | None = None,
    scheduler: str | None = None,
    quality_lora_ids: list[str] | tuple[str, ...] | None = None,
) -> GenerationConfig:
    """Resolve named, reproducible quality defaults before caller overrides."""
    tier = QUALITY_TIERS.get(quality_tier.strip().lower())
    if tier is None:
        raise ComfyError("unknown quality tier; use preview, production, or hero")
    resolved = {
        **tier,
        "prompt": prompt,
        "seed": seed,
        "negative_prompt": negative_prompt,
    }
    for key, value in {
        "mode": mode,
        "width": width,
        "height": height,
        "frames": frames,
        "steps": steps,
        "cfg": cfg,
        "sampler": sampler,
        "scheduler": scheduler,
        "quality_lora_ids": tuple(quality_lora_ids) if quality_lora_ids is not None else None,
    }.items():
        if value is not None:
            resolved[key] = value
    config = GenerationConfig(**resolved)
    validate_config(config)
    return config


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(path)


def build_cinematic_prompt(
    *,
    subject: str,
    motion: str,
    atmosphere: str,
    camera: str,
    mode: str = "t2v-A14B",
) -> str:
    """Build the same five-layer 80-140 word contract used by Producer."""
    camera_clean = " ".join(camera.strip().split()).lower()
    if camera_clean not in CAMERA_VOCABULARY:
        raise ComfyError(
            "camera must use Diony's Wan vocabulary: " + ", ".join(CAMERA_VOCABULARY)
        )
    motion_clean = " ".join(motion.strip().split())
    atmosphere_clean = " ".join(atmosphere.strip().split())
    subject_clean = " ".join(subject.strip().split())
    if not motion_clean:
        raise ComfyError("motion is required")
    image_driven = mode in {"i2v-A14B", "flf2v-A14B"}
    opening = (
        "The exact supplied subject retains identical face or original design, stable silhouette "
        "and body proportions, unchanged wardrobe, and the same identifying marks"
        if image_driven
        else f"{subject_clean} retains the same face or design, silhouette, body proportions, wardrobe, and identifying marks"
    )
    prompt = (
        f"{opening}. {motion_clean.rstrip('.')} at a measured pace in one continuous, physically "
        "coherent action, with readable weight shifts and a stable final hold. "
        f"{camera_clean.capitalize()} remains the single uninterrupted camera movement with controlled "
        "depth of field and natural motion blur. "
        f"{atmosphere_clean.rstrip('.')} with stable lighting direction, exposure, cinematic Rec.709 "
        "color, natural contrast, and restrained film grain. Foreground atmosphere and small practical "
        "elements move gently at a different rate from the stable background, creating believable "
        "parallax, scale, contact, gravity, and clean temporal continuity through the final frame."
    )
    count = len(prompt.split())
    if count < 80 or count > 140:
        raise ComfyError(f"cinematic prompt must be 80-140 words after templating; got {count}")
    return prompt


def validate_config(config: GenerationConfig) -> None:
    if config.mode not in MODEL_FILES and config.mode not in NATIVE_ONLY_MODES:
        raise ComfyError(f"unsupported ComfyUI Wan mode: {config.mode}")
    if not config.prompt.strip():
        raise ComfyError("positive prompt is empty")
    lower = config.prompt.lower()
    if "dashscope" in lower or "qwen-plus" in lower:
        raise ComfyError("prompt must not request hosted prompt expansion")
    unknown_loras = [
        lora_id
        for lora_id in config.quality_lora_ids
        if lora_id != "wan2.2-animate-relighting"
    ]
    if unknown_loras:
        raise ComfyError("quality LoRA is not qualified: " + ", ".join(unknown_loras))
    if config.quality_lora_ids and config.mode != "animate-14B":
        raise ComfyError("qualified relighting LoRA requires animate-14B")
    if config.width <= 0 or config.width % 16:
        raise ComfyError(f"width must be a positive multiple of 16; got {config.width}")
    if config.height <= 0 or config.height % 16:
        raise ComfyError(f"height must be a positive multiple of 16; got {config.height}")
    if config.frames < 17 or config.frames > 81 or (config.frames - 1) % 4:
        raise ComfyError(
            f"frames must be 4n+1 between 17 and 81 for Diony shots; got {config.frames}"
        )
    if not 1 <= config.steps <= 100:
        raise ComfyError(f"steps must be between 1 and 100; got {config.steps}")
    if not 0 < config.cfg <= 10:
        raise ComfyError(f"CFG must be greater than 0 and no more than 10; got {config.cfg}")


def estimate_vram_bytes(config: GenerationConfig) -> int:
    """Return a conservative planning estimate, not a hard admission limit.

    Model residency dominates 14B while video activations scale with latent
    volume. Actual allocation remains visible from ComfyUI telemetry.
    """
    reference_volume = 1280 * 720 * 81
    volume_ratio = (config.width * config.height * config.frames) / reference_volume
    if config.mode == "ti2v-5B":
        gibibytes = 12.0 + (4.0 * volume_ratio)
    else:
        gibibytes = 30.0 + (10.0 * volume_ratio)
    return int(gibibytes * 1024**3)


def validate_comfy_version(stats: dict[str, Any]) -> str:
    system = stats.get("system") if isinstance(stats.get("system"), dict) else {}
    raw = str(system.get("comfyui_version") or "")
    numbers = [int(value) for value in re.findall(r"\d+", raw)[:3]]
    if len(numbers) < 3:
        raise ComfyError("ComfyUI /system_stats did not report a semantic comfyui_version")
    if tuple(numbers) < MIN_COMFY_VERSION:
        raise ComfyError(
            f"ComfyUI {raw} is too old for native Wan 2.2 templates; use 0.3.46 or newer"
        )
    return raw


def _validate_api_workflow(workflow: Any) -> dict[str, dict[str, Any]]:
    if isinstance(workflow, dict) and isinstance(workflow.get("nodes"), list):
        raise ComfyError(
            "workflow is UI/graph JSON, not API format; open the official template and export Save (API Format)"
        )
    if not isinstance(workflow, dict) or not workflow:
        raise ComfyError("workflow API JSON must be a non-empty object keyed by node id")
    for node_id, node in workflow.items():
        if not isinstance(node_id, str) or not isinstance(node, dict):
            raise ComfyError("workflow API JSON must be keyed by string node ids")
        if not isinstance(node.get("class_type"), str) or not isinstance(node.get("inputs"), dict):
            raise ComfyError(f"node {node_id} is not ComfyUI API format (class_type + inputs required)")
    return workflow


def load_api_workflow(path: Path) -> dict[str, dict[str, Any]]:
    if not path.is_file():
        raise ComfyError(f"validated API-format workflow missing: {path}")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ComfyError(f"workflow JSON is invalid at {path}: {exc}") from exc
    return _validate_api_workflow(payload)


def _title(node: dict[str, Any]) -> str:
    meta = node.get("_meta") if isinstance(node.get("_meta"), dict) else {}
    return str(meta.get("title") or node.get("title") or "").lower()


def _nodes_with_input(
    workflow: dict[str, dict[str, Any]], input_names: set[str]
) -> list[tuple[str, dict[str, Any]]]:
    return [
        (node_id, node)
        for node_id, node in workflow.items()
        if input_names.issubset(set(node["inputs"]))
    ]


def _prompt_nodes(workflow: dict[str, dict[str, Any]]) -> tuple[dict[str, Any], dict[str, Any]]:
    encoders = [node for node in workflow.values() if node["class_type"] == "CLIPTextEncode"]
    positive = [node for node in encoders if "positive" in _title(node)]
    negative = [node for node in encoders if "negative" in _title(node)]
    if len(positive) != 1 or len(negative) != 1:
        raise ComfyError(
            "workflow must retain exactly one titled Positive Prompt and one titled Negative Prompt "
            "CLIPTextEncode node in its API export"
        )
    return positive[0], negative[0]


def _model_nodes(workflow: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        node
        for node in workflow.values()
        if node["class_type"] in {"UNETLoader", "LoadDiffusionModel"}
        and "unet_name" in node["inputs"]
    ]


def _assign_model_names(nodes: list[dict[str, Any]], names: tuple[str, ...]) -> None:
    if len(nodes) < len(names):
        raise ComfyError(
            f"workflow has {len(nodes)} diffusion loader(s), but mode requires {len(names)}; "
            "14B workflows must retain both high-noise and low-noise experts"
        )
    remaining = list(nodes)
    for marker, name in (("high_noise", names[0]), ("low_noise", names[-1])):
        if len(names) == 1 and marker == "low_noise":
            break
        match = next(
            (
                node
                for node in remaining
                if marker in str(node["inputs"].get("unet_name", "")).lower()
                or marker.replace("_", "-") in _title(node)
            ),
            None,
        )
        if match is not None:
            match["inputs"]["unet_name"] = name
            remaining.remove(match)
    assigned = {str(node["inputs"].get("unet_name")) for node in nodes}
    for name in names:
        if name in assigned:
            continue
        if not remaining:
            raise ComfyError(f"could not bind required diffusion model {name}")
        remaining.pop(0)["inputs"]["unet_name"] = name


def mutate_workflow(
    workflow: dict[str, dict[str, Any]],
    config: GenerationConfig,
    *,
    output_prefix: str,
    image_name: str | None = None,
    last_image_name: str | None = None,
) -> dict[str, dict[str, Any]]:
    """Deep-copy and mutate only standard API node inputs."""
    validate_config(config)
    result = copy.deepcopy(_validate_api_workflow(workflow))
    positive, negative = _prompt_nodes(result)
    positive["inputs"]["text"] = config.prompt.strip()
    negative["inputs"]["text"] = config.negative_prompt.strip()

    latent_nodes = _nodes_with_input(result, {"width", "height", "length"})
    if not latent_nodes:
        raise ComfyError("workflow has no video latent node with width, height, and length inputs")
    for _, node in latent_nodes:
        node["inputs"].update(width=config.width, height=config.height, length=config.frames)

    parameter_nodes = [
        node
        for node in result.values()
        if set(node["inputs"]) & {"seed", "noise_seed", "steps", "cfg", "sampler_name", "scheduler"}
    ]
    if not any("sampler_name" in node["inputs"] for node in parameter_nodes):
        raise ComfyError("workflow has no sampler selector input")
    if not any(
        "seed" in node["inputs"] or "noise_seed" in node["inputs"] for node in parameter_nodes
    ):
        raise ComfyError("workflow has no deterministic seed input")
    for node in parameter_nodes:
        inputs = node["inputs"]
        for key in ("seed", "noise_seed"):
            if key in inputs:
                inputs[key] = int(config.seed)
        if "steps" in inputs:
            inputs["steps"] = int(config.steps)
        if "cfg" in inputs:
            inputs["cfg"] = float(config.cfg)
        if "sampler_name" in inputs:
            inputs["sampler_name"] = config.sampler
        if "scheduler" in inputs:
            inputs["scheduler"] = config.scheduler

    _assign_model_names(_model_nodes(result), MODEL_FILES[config.mode]["diffusion_models"])

    image_nodes = [node for node in result.values() if node["class_type"] == "LoadImage"]
    if config.mode in {"i2v-A14B", "ti2v-5B"} and image_name:
        if not image_nodes:
            raise ComfyError("image-driven workflow has no LoadImage node")
        image_nodes[0]["inputs"]["image"] = image_name
    if config.mode == "i2v-A14B" and not image_name:
        raise ComfyError("I2V requires an input image")
    if config.mode == "flf2v-A14B":
        if not image_name or not last_image_name:
            raise ComfyError("FLF2V requires both first and last images")
        if len(image_nodes) < 2:
            raise ComfyError("FLF2V workflow must retain two LoadImage nodes")
        first = next((node for node in image_nodes if "first" in _title(node)), image_nodes[0])
        last = next((node for node in image_nodes if "last" in _title(node)), image_nodes[-1])
        if first is last:
            raise ComfyError("FLF2V first and last image bindings are ambiguous")
        first["inputs"]["image"] = image_name
        last["inputs"]["image"] = last_image_name

    for node in result.values():
        if "filename_prefix" in node["inputs"]:
            node["inputs"]["filename_prefix"] = output_prefix
    return result


def preflight_workflow(
    workflow: dict[str, dict[str, Any]],
    config: GenerationConfig,
    *,
    model_root: Path | None = None,
    available_models: dict[str, set[str]] | None = None,
) -> dict[str, Any]:
    """Validate graph shape, exact model names, and local/remote placement."""
    validate_config(config)
    graph = _validate_api_workflow(workflow)
    _prompt_nodes(graph)
    if not _nodes_with_input(graph, {"width", "height", "length"}):
        raise ComfyError("workflow has no video latent node with width, height, and length inputs")
    expected_loaders = len(MODEL_FILES[config.mode]["diffusion_models"])
    actual_loaders = len(_model_nodes(graph))
    if actual_loaders < expected_loaders:
        raise ComfyError(
            f"workflow has {actual_loaders} diffusion loader(s); {config.mode} requires "
            f"{expected_loaders} (high-noise + low-noise for 14B)"
        )
    missing: list[str] = []
    for folder, names in MODEL_FILES[config.mode].items():
        for name in names:
            if model_root is not None:
                candidate = model_root / "models" / folder / name
                try:
                    present = candidate.is_file() and candidate.stat().st_size > 0
                except OSError:
                    present = False
            elif available_models is not None:
                present = name in available_models.get(folder, set())
            else:
                present = True
            if not present:
                location = f"models/{folder}/{name}"
                missing.append(location)
    if missing:
        raise ComfyError("missing required ComfyUI model files: " + ", ".join(missing))
    return {
        "ok": True,
        "mode": config.mode,
        "dual_expert": expected_loaders == 2,
        "models": MODEL_FILES[config.mode],
        "width": config.width,
        "height": config.height,
        "frames": config.frames,
        "vram_estimate_bytes": estimate_vram_bytes(config),
    }


def _node_error_text(payload: Any) -> str:
    if not isinstance(payload, dict):
        return str(payload)
    parts: list[str] = []
    error = payload.get("error")
    if isinstance(error, dict) and error.get("message"):
        parts.append(str(error["message"]))
    elif error:
        parts.append(str(error))
    node_errors = payload.get("node_errors")
    if isinstance(node_errors, dict):
        for node_id, row in node_errors.items():
            details: list[str] = []
            if isinstance(row, dict):
                for item in row.get("errors", []):
                    if isinstance(item, dict):
                        details.extend(
                            str(item[key]) for key in ("message", "details") if item.get(key)
                        )
                    elif item:
                        details.append(str(item))
            parts.append(f"node {node_id}: " + ("; ".join(details) or "validation failed"))
    return " | ".join(parts) or json.dumps(payload, sort_keys=True)


class ComfyClient:
    def __init__(
        self,
        base_url: str,
        *,
        api_key: str = "",
        timeout: float = 60.0,
        http: httpx.Client | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        self.http = http or httpx.Client(base_url=self.base_url, headers=headers, timeout=timeout)

    def _url(self, path: str) -> str:
        return self.base_url + "/" + path.lstrip("/")

    def queue(self, workflow: dict[str, Any], *, client_id: str) -> dict[str, Any]:
        response = self.http.post(
            self._url("/prompt"), json={"prompt": workflow, "client_id": client_id}
        )
        if response.status_code >= 400:
            try:
                payload = response.json()
            except ValueError:
                payload = {"error": response.text}
            raise ComfyError(f"ComfyUI rejected workflow: {_node_error_text(payload)}")
        payload = response.json()
        if payload.get("node_errors"):
            raise ComfyError(f"ComfyUI rejected workflow: {_node_error_text(payload)}")
        if not payload.get("prompt_id"):
            raise ComfyError(f"ComfyUI /prompt returned no prompt_id: {payload}")
        return payload

    def history(self, prompt_id: str) -> dict[str, Any] | None:
        response = self.http.get(self._url(f"/history/{prompt_id}"))
        response.raise_for_status()
        payload = response.json()
        row = payload.get(prompt_id) if isinstance(payload, dict) else None
        return row if isinstance(row, dict) else None

    def queue_status(self) -> dict[str, Any]:
        response = self.http.get(self._url("/queue"))
        response.raise_for_status()
        payload = response.json()
        result = payload if isinstance(payload, dict) else {}
        try:
            summary_response = self.http.get(self._url("/prompt"))
            summary_response.raise_for_status()
            summary = summary_response.json()
            if isinstance(summary, dict):
                result = {**result, **summary}
        except (httpx.HTTPError, ValueError, TypeError):
            pass
        return result

    def upload_image(self, path: Path) -> str:
        if not path.is_file():
            raise ComfyError(f"input image missing: {path}")
        with path.open("rb") as source:
            response = self.http.post(
                self._url("/upload/image"),
                data={"type": "input", "overwrite": "true"},
                files={"image": (path.name, source, "application/octet-stream")},
            )
        if response.status_code >= 400:
            raise ComfyError(f"ComfyUI image upload failed: {response.text}")
        payload = response.json()
        name = str(payload.get("name") or payload.get("filename") or "")
        subfolder = str(payload.get("subfolder") or "").strip("/")
        if not name:
            raise ComfyError(f"ComfyUI image upload returned no filename: {payload}")
        return f"{subfolder}/{name}" if subfolder else name

    def download(self, output: dict[str, Any]) -> bytes:
        response = self.http.get(
            self._url("/view"),
            params={
                "filename": output.get("filename", ""),
                "subfolder": output.get("subfolder", ""),
                "type": output.get("type", "output"),
            },
            follow_redirects=True,
        )
        response.raise_for_status()
        return response.content

    def free_vram(self) -> None:
        response = self.http.post(
            self._url("/free"), json={"unload_models": True, "free_memory": True}
        )
        response.raise_for_status()

    def interrupt(self) -> None:
        response = self.http.post(self._url("/interrupt"), json={})
        response.raise_for_status()

    def system_stats(self) -> dict[str, Any]:
        response = self.http.get(self._url("/system_stats"))
        response.raise_for_status()
        return response.json()

    def available_models(self) -> dict[str, set[str]]:
        result: dict[str, set[str]] = {}
        for folder in {folder for group in MODEL_FILES.values() for folder in group}:
            response = self.http.get(self._url(f"/models/{folder}"))
            response.raise_for_status()
            payload = response.json()
            values = payload if isinstance(payload, list) else payload.get(folder, [])
            result[folder] = {Path(str(value)).name for value in values}
        return result


def _history_error(history: dict[str, Any]) -> str:
    status = history.get("status") if isinstance(history.get("status"), dict) else {}
    status_text = str(status.get("status_str") or "").lower()
    messages = status.get("messages") if isinstance(status.get("messages"), list) else []
    details: list[str] = []
    for message in messages:
        if isinstance(message, (list, tuple)) and len(message) > 1:
            kind, row = message[0], message[1]
            if "error" in str(kind).lower():
                if isinstance(row, dict):
                    node_id = row.get("node_id") or row.get("node") or "?"
                    reason = row.get("exception_message") or row.get("message") or row
                    details.append(f"node {node_id}: {reason}")
                else:
                    details.append(str(row))
    if details:
        return " | ".join(details)
    if status_text in {"error", "failed", "failure"}:
        return f"ComfyUI execution {status_text} without node detail"
    return ""


def _output_candidates(history: dict[str, Any]) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    outputs = history.get("outputs") if isinstance(history.get("outputs"), dict) else {}
    for node_output in outputs.values():
        if not isinstance(node_output, dict):
            continue
        for key in ("videos", "gifs", "images", "files"):
            rows = node_output.get(key)
            if isinstance(rows, list):
                candidates.extend(row for row in rows if isinstance(row, dict) and row.get("filename"))
    video = [
        row
        for row in candidates
        if Path(str(row.get("filename", ""))).suffix.lower() in {".mp4", ".mov", ".webm", ".mkv"}
    ]
    return video or candidates


class ComfyWanRunner:
    def __init__(
        self,
        *,
        client: ComfyClient,
        workflows: dict[str, Path],
        model_root: Path | None,
        poll_interval: float = 2.0,
        timeout: float = 7200.0,
        retries: int = 1,
        allow_5b_fallback: bool = False,
    ) -> None:
        self.client = client
        self.workflows = workflows
        self.model_root = model_root
        self.poll_interval = poll_interval
        self.timeout = timeout
        self.retries = max(0, retries)
        self.allow_5b_fallback = allow_5b_fallback

    @staticmethod
    def _emit(
        callback: Callable[[dict[str, Any]], None] | None, payload: dict[str, Any]
    ) -> None:
        if callback is not None:
            try:
                callback(dict(payload))
            except Exception:  # progress persistence must never duplicate a landed GPU run
                _LOG.exception("Wan progress callback failed")

    def _telemetry(self, metrics: dict[str, Any]) -> dict[str, Any]:
        telemetry: dict[str, Any] = {}
        try:
            queue = self.client.queue_status()
            running = queue.get("queue_running")
            pending = queue.get("queue_pending")
            telemetry["queue_running"] = len(running) if isinstance(running, list) else 0
            telemetry["queue_pending"] = len(pending) if isinstance(pending, list) else 0
            execution = queue.get("exec_info")
            if isinstance(execution, dict) and isinstance(
                execution.get("queue_remaining"), (int, float)
            ):
                telemetry["queue_remaining"] = int(execution["queue_remaining"])
        except (httpx.HTTPError, ValueError, TypeError):
            pass
        try:
            stats = self.client.system_stats()
            devices = stats.get("devices") if isinstance(stats, dict) else None
            used_values: list[int] = []
            free_values: list[int] = []
            if isinstance(devices, list):
                for device in devices:
                    if not isinstance(device, dict):
                        continue
                    total = device.get("vram_total")
                    free = device.get("vram_free")
                    if isinstance(total, (int, float)) and isinstance(free, (int, float)):
                        used_values.append(max(0, int(total - free)))
                        free_values.append(max(0, int(free)))
            if used_values:
                current = max(used_values)
                metrics["vram_peak_bytes"] = max(
                    int(metrics.get("vram_peak_bytes") or 0), current
                )
                telemetry.update(
                    {
                        "vram_used_bytes": current,
                        "vram_free_bytes": min(free_values),
                        "vram_peak_bytes": metrics["vram_peak_bytes"],
                    }
                )
        except (httpx.HTTPError, ValueError, TypeError):
            pass
        return telemetry

    def _wait(
        self,
        prompt_id: str,
        *,
        status_callback: Callable[[dict[str, Any]], None] | None = None,
        metrics: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        observed = metrics if metrics is not None else {}
        deadline = time.monotonic() + self.timeout
        while time.monotonic() < deadline:
            history = self.client.history(prompt_id)
            if history is not None:
                error = _history_error(history)
                if error:
                    raise ComfyError(error)
                status = history.get("status") if isinstance(history.get("status"), dict) else {}
                if status.get("completed") or _output_candidates(history):
                    return history
            self._emit(
                status_callback,
                {"state": "processing", "prompt_id": prompt_id, **self._telemetry(observed)},
            )
            time.sleep(self.poll_interval)
        raise ComfyError(f"ComfyUI prompt {prompt_id} timed out after {int(self.timeout)} seconds")

    def _preflight(
        self, workflow: dict[str, dict[str, Any]], config: GenerationConfig
    ) -> dict[str, Any]:
        available = None if self.model_root is not None else self.client.available_models()
        return preflight_workflow(
            workflow,
            config,
            model_root=self.model_root,
            available_models=available,
        )

    def run(
        self,
        config: GenerationConfig,
        destination: Path,
        *,
        image: Path | None = None,
        last_image: Path | None = None,
        status_callback: Callable[[dict[str, Any]], None] | None = None,
    ) -> Path:
        modes = [config.mode]
        if self.allow_5b_fallback and config.mode != "ti2v-5B" and "ti2v-5B" in self.workflows:
            modes.append("ti2v-5B")
        destination.parent.mkdir(parents=True, exist_ok=True)
        status_path = destination.with_suffix(".comfy.status.json")
        log_path = destination.with_suffix(".comfy.json")
        client_id = str(uuid.uuid4())
        failures: list[str] = []
        started = time.monotonic()
        metrics: dict[str, Any] = {"vram_peak_bytes": 0}
        log_record: dict[str, Any] = {
            "schema_version": 1,
            "requested_mode": config.mode,
            "request": {**asdict(config), "destination": str(destination)},
            "attempts": [],
            "started_at": _now(),
        }
        _write_json(log_path, log_record)

        for mode in modes:
            current = replace(config, mode=mode)
            workflow_path = self.workflows.get(mode)
            if workflow_path is None:
                failures.append(f"{mode}: no API-format workflow configured")
                continue
            try:
                workflow = load_api_workflow(workflow_path)
                preflight = self._preflight(workflow, current)
                uploaded_first = self.client.upload_image(image) if image else None
                uploaded_last = self.client.upload_image(last_image) if last_image else None
                mutated = mutate_workflow(
                    workflow,
                    current,
                    output_prefix=f"diony/{destination.stem}-{current.seed}",
                    image_name=uploaded_first,
                    last_image_name=uploaded_last,
                )
            except (ComfyError, httpx.HTTPError, OSError) as exc:
                failure = f"{mode} preflight: {exc}"
                failures.append(failure)
                log_record["attempts"].append(
                    {
                        "mode": mode,
                        "stage": "preflight",
                        "state": "failed",
                        "error": str(exc),
                        "at": _now(),
                    }
                )
                _write_json(log_path, log_record)
                _write_json(
                    status_path,
                    {
                        "state": "failed",
                        "stage": "preflight",
                        "mode": mode,
                        "seed": current.seed,
                        "error": str(exc),
                        "updated_at": _now(),
                    },
                )
                self._emit(
                    status_callback,
                    {
                        "state": "failed",
                        "stage": "preflight",
                        "mode": mode,
                        "error": str(exc),
                    },
                )
                continue
            request_record = {
                **asdict(current),
                "workflow": str(workflow_path),
                "destination": str(destination),
                "image": str(image) if image else None,
                "last_image": str(last_image) if last_image else None,
                "preflight": preflight,
            }
            log_record["request"] = request_record
            log_record["workflow_api"] = mutated
            _write_json(log_path, log_record)
            for attempt in range(1, self.retries + 2):
                prompt_id = ""
                _write_json(
                    status_path,
                    {
                        "state": "queued",
                        "mode": mode,
                        "attempt": attempt,
                        "seed": current.seed,
                        "updated_at": _now(),
                    },
                )
                self._emit(
                    status_callback,
                    {"state": "queued", "mode": mode, "attempt": attempt, "seed": current.seed},
                )
                try:
                    queued = self.client.queue(mutated, client_id=client_id)
                    prompt_id = str(queued["prompt_id"])
                    _write_json(
                        status_path,
                        {
                            "state": "processing",
                            "mode": mode,
                            "attempt": attempt,
                            "prompt_id": prompt_id,
                            "seed": current.seed,
                            "updated_at": _now(),
                        },
                    )
                    self._emit(
                        status_callback,
                        {
                            "state": "processing",
                            "mode": mode,
                            "attempt": attempt,
                            "prompt_id": prompt_id,
                            "seed": current.seed,
                        },
                    )
                    history = self._wait(
                        prompt_id, status_callback=status_callback, metrics=metrics
                    )
                    candidates = _output_candidates(history)
                    if not candidates:
                        raise ComfyError(f"ComfyUI prompt {prompt_id} completed with no downloadable output")
                    data = self.client.download(candidates[0])
                    if len(data) < 32:
                        raise ComfyError(f"ComfyUI prompt {prompt_id} returned an empty output")
                    temporary = destination.with_suffix(destination.suffix + ".tmp")
                    temporary.write_bytes(data)
                    temporary.replace(destination)
                    free_warning = ""
                    try:
                        self.client.free_vram()
                    except (httpx.HTTPError, OSError) as exc:
                        free_warning = f"Post-job VRAM free request failed: {exc}"
                    elapsed_ms = round((time.monotonic() - started) * 1000, 2)
                    _write_json(
                        log_path,
                        {
                            **log_record,
                            "queue": queued,
                            "history": history,
                            "completed_at": _now(),
                            "warning": free_warning,
                            "metrics": {**metrics, "elapsed_ms": elapsed_ms},
                            "attempts": [
                                *log_record["attempts"],
                                {
                                    "mode": mode,
                                    "attempt": attempt,
                                    "prompt_id": prompt_id,
                                    "state": "completed",
                                },
                            ],
                        },
                    )
                    _write_json(
                        status_path,
                        {
                            "state": "completed",
                            "mode": mode,
                            "attempt": attempt,
                            "prompt_id": prompt_id,
                            "seed": current.seed,
                            "path": str(destination),
                            "bytes": len(data),
                            "warning": free_warning,
                            "elapsed_ms": elapsed_ms,
                            "vram_peak_bytes": metrics["vram_peak_bytes"],
                            "updated_at": _now(),
                        },
                    )
                    self._emit(
                        status_callback,
                        {
                            "state": "completed",
                            "mode": mode,
                            "attempt": attempt,
                            "prompt_id": prompt_id,
                            "seed": current.seed,
                            "path": str(destination),
                            "bytes": len(data),
                            "elapsed_ms": elapsed_ms,
                            "vram_peak_bytes": metrics["vram_peak_bytes"],
                            "warning": free_warning,
                        },
                    )
                    return destination
                except (ComfyError, httpx.HTTPError, OSError) as exc:
                    failure = f"{mode} attempt {attempt}: {exc}"
                    failures.append(failure)
                    log_record["attempts"].append(
                        {
                            "mode": mode,
                            "attempt": attempt,
                            "prompt_id": prompt_id,
                            "state": "failed",
                            "error": str(exc),
                            "at": _now(),
                        }
                    )
                    _write_json(log_path, log_record)
                    try:
                        self.client.free_vram()
                    except (httpx.HTTPError, OSError):
                        pass
                    _write_json(
                        status_path,
                        {
                            "state": "retrying" if attempt <= self.retries else "failed",
                            "mode": mode,
                            "attempt": attempt,
                            "prompt_id": prompt_id,
                            "seed": current.seed,
                            "error": str(exc),
                            "updated_at": _now(),
                        },
                    )
                    self._emit(
                        status_callback,
                        {
                            "state": "retrying" if attempt <= self.retries else "failed",
                            "mode": mode,
                            "attempt": attempt,
                            "prompt_id": prompt_id,
                            "error": str(exc),
                        },
                    )
        message = " ; ".join(failures) or "ComfyUI generation failed"
        raise ComfyError(message)

    def resume(
        self,
        destination: Path,
        *,
        status_callback: Callable[[dict[str, Any]], None] | None = None,
    ) -> Path | None:
        """Adopt an in-flight Comfy prompt recorded before a Diony restart."""
        if destination.is_file() and destination.stat().st_size > 32:
            return destination
        status_path = destination.with_suffix(".comfy.status.json")
        if not status_path.is_file():
            return None
        try:
            status = json.loads(status_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ComfyError(f"ComfyUI recovery status is unreadable at {status_path}: {exc}") from exc
        prompt_id = str(status.get("prompt_id") or "")
        if not prompt_id:
            return None
        started = time.monotonic()
        metrics: dict[str, Any] = {"vram_peak_bytes": 0}
        self._emit(
            status_callback,
            {"state": "recovering", "prompt_id": prompt_id, "mode": status.get("mode")},
        )
        history = self._wait(prompt_id, status_callback=status_callback, metrics=metrics)
        candidates = _output_candidates(history)
        if not candidates:
            raise ComfyError(f"ComfyUI prompt {prompt_id} completed with no downloadable output")
        data = self.client.download(candidates[0])
        if len(data) < 32:
            raise ComfyError(f"ComfyUI prompt {prompt_id} returned an empty output")
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_suffix(destination.suffix + ".tmp")
        temporary.write_bytes(data)
        temporary.replace(destination)
        warning = ""
        try:
            self.client.free_vram()
        except (httpx.HTTPError, OSError) as exc:
            warning = f"Post-recovery VRAM free request failed: {exc}"
        elapsed_ms = round((time.monotonic() - started) * 1000, 2)
        completed = {
            **status,
            "state": "completed",
            "prompt_id": prompt_id,
            "path": str(destination),
            "bytes": len(data),
            "recovered": True,
            "elapsed_ms": elapsed_ms,
            "vram_peak_bytes": metrics["vram_peak_bytes"],
            "warning": warning,
            "updated_at": _now(),
        }
        _write_json(status_path, completed)
        log_path = destination.with_suffix(".comfy.json")
        log_record: dict[str, Any] = {}
        if log_path.is_file():
            try:
                loaded = json.loads(log_path.read_text(encoding="utf-8"))
                if isinstance(loaded, dict):
                    log_record = loaded
            except (OSError, json.JSONDecodeError):
                pass
        _write_json(
            log_path,
            {
                **log_record,
                "recovered": True,
                "prompt_id": prompt_id,
                "history": history,
                "completed_at": _now(),
                "metrics": {**metrics, "elapsed_ms": elapsed_ms},
                "warning": warning,
            },
        )
        self._emit(status_callback, completed)
        return destination


def workflow_paths_from_env() -> dict[str, Path]:
    root = Path(os.environ.get("DIONY_COMFY_WORKFLOW_DIR", "/diony/workflows/wan2.2"))
    defaults = {
        "ti2v-5B": root / "wan2.2-ti2v-5b.api.json",
        "t2v-A14B": root / "wan2.2-t2v-14b.api.json",
        "i2v-A14B": root / "wan2.2-i2v-14b.api.json",
        "flf2v-A14B": root / "wan2.2-flf2v-14b.api.json",
    }
    result: dict[str, Path] = {}
    for mode, default in defaults.items():
        key = "DIONY_COMFY_WORKFLOW_" + mode.replace("-", "_").upper()
        result[mode] = Path(os.environ.get(key, str(default)))
    return result


def configured_runner() -> ComfyWanRunner:
    base_url = os.environ.get("DIONY_COMFY_URL", "http://127.0.0.1:8188").rstrip("/")
    host = (urlparse(base_url).hostname or "").lower()
    local = host in {"127.0.0.1", "localhost", "::1"}
    allow_remote = os.environ.get("DIONY_ALLOW_REMOTE_COMFY", "").lower() in {"1", "true", "yes"}
    if not local and not allow_remote:
        raise ComfyError(
            "remote ComfyUI is disabled; set DIONY_ALLOW_REMOTE_COMFY=1 only for an explicitly approved endpoint"
        )
    model_root_raw = os.environ.get("DIONY_COMFY_ROOT", "/opt/ComfyUI")
    model_root = Path(model_root_raw) if local and model_root_raw else None
    return ComfyWanRunner(
        client=ComfyClient(
            base_url,
            api_key=os.environ.get("DIONY_COMFY_API_KEY", ""),
            timeout=float(os.environ.get("DIONY_COMFY_HTTP_TIMEOUT", "60")),
        ),
        workflows=workflow_paths_from_env(),
        model_root=model_root,
        poll_interval=float(os.environ.get("DIONY_COMFY_POLL_INTERVAL", "2")),
        timeout=float(os.environ.get("DIONY_WAN_TIMEOUT", "7200")),
        retries=int(os.environ.get("DIONY_COMFY_RETRIES", "1")),
        allow_5b_fallback=os.environ.get("DIONY_COMFY_ALLOW_5B_FALLBACK", "").lower()
        in {"1", "true", "yes"},
    )


def readiness_report() -> dict[str, Any]:
    """Verify ComfyUI, API workflows, graph bindings, and exact model names."""
    report: dict[str, Any] = {
        "schema_version": 1,
        "backend": "comfyui",
        "ok": False,
        "checked_at": _now(),
        "modes": {},
        "errors": [],
    }
    try:
        runner = configured_runner()
        report["system_stats"] = runner.client.system_stats()
        report["comfyui_version"] = validate_comfy_version(report["system_stats"])
        available = None if runner.model_root is not None else runner.client.available_models()
        for mode, path in runner.workflows.items():
            row: dict[str, Any] = {"workflow": str(path), "ok": False}
            try:
                workflow = load_api_workflow(path)
                config = GenerationConfig(mode=mode, prompt="Static shot with controlled natural motion.")
                row.update(
                    preflight_workflow(
                        workflow,
                        config,
                        model_root=runner.model_root,
                        available_models=available,
                    )
                )
            except (ComfyError, httpx.HTTPError, OSError) as exc:
                row["error"] = str(exc)
            report["modes"][mode] = row
        report["ok"] = bool(report["modes"].get("t2v-A14B", {}).get("ok"))
    except (ComfyError, httpx.HTTPError, OSError) as exc:
        report["errors"].append(str(exc))
    return report
