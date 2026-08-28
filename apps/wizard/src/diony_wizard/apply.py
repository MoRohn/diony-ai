"""Live Dual-Spark / B300 / B200 App: pull the pinned pack, clone Wan2.2, start Producer.

Pytest (DIONY_DRY_RUN=1) never SSHes. On spark-01, commands run locally.
From a laptop, SSH BatchMode as DIONY_SSH_USER on DIONY_SSH_PORT (default 22).
"""

from __future__ import annotations

import copy
import json
import os
import re
import shlex
import subprocess
import threading
from datetime import datetime, timezone
from pathlib import Path

from diony_contracts.cluster import cluster_root
from diony_contracts.live import wizard_live
from diony_wizard.enterprise import enterprise_hf_token
from diony_wizard.prep import repo_root, vault_mount_lines
from diony_wizard.remote import (
    apply_local,
    exec_host,
    rsync_repo,
    ssh_connect_advice,
    ssh_port,
    ssh_user,
    terminate_streaming,
)

HF_PACK = [
    (
        "deepseek-ai/DeepSeek-V4-Flash-0731",
        "/models/deepseek/DeepSeek-V4-Flash-0731",
        "",
        "7872f01b1d1fe23eabc4c98b48bffcef5a386062",
        74,
        166898661074,
    ),
    (
        "Wan-AI/Wan2.2-T2V-A14B",
        "/models/wan/Wan2.2-T2V-A14B",
        "",
        "c8c270b13ee05bfa474194ac9fb07a5868a97cea",
        32,
        126201624837,
    ),
    (
        "Wan-AI/Wan2.2-I2V-A14B",
        "/models/wan/Wan2.2-I2V-A14B",
        "",
        "206a9ee1b7bfaaf8f7e4d81335650533490646a3",
        33,
        126205359703,
    ),
    (
        "Wan-AI/Wan2.2-S2V-14B",
        "/models/wan/Wan2.2-S2V-14B",
        "",
        "dab4e9c55bbe4c8c4d03db1c2c98c7f0ac9c454b",
        49,
        49148820007,
    ),
    (
        "Wan-AI/Wan2.2-Animate-14B",
        "/models/wan/Wan2.2-Animate-14B",
        "",
        "cb93a225fbaf1ca100f54e79da8f994995b689b3",
        444,
        72376533926,
    ),
    (
        "alibaba-pai/Wan2.2-VACE-Fun-A14B",
        "/models/wan/Wan2.2-VACE-Fun-A14B",
        "",
        "4438caabbfdd7437bbb0283d86cb200d1b7223ba",
        14,
        81241676782,
    ),
    (
        "ai-forever/Real-ESRGAN",
        "/models/diony/diony_sr_v1",
        "RealESRGAN_x2.pth",
        "8110204ebf8d25c031b66c26c2d1098aa831157e",
        1,
        67061725,
    ),
]

# Blueprint 24 optional measured lanes: a local Whisper for transcript QC of the
# speech Wan delivers and a local CLIP for prompt adherence. Pulled best-effort
# after the pack (DIONY_MONTAGE_PACK=0 skips); never part of the pack gate.
# (repo, dest, revision, ignore_patterns, files, bytes)
MONTAGE_PACK = [
    (
        "Systran/faster-whisper-large-v3",
        "/models/audio/faster-whisper-large-v3",
        "edaa852ec7e145841d8ffdb056a99866b5f0a478",
        [],
        7,
        3091841273,
    ),
    (
        "openai/clip-vit-base-patch32",
        "/models/diony/clip-vit-base-patch32",
        "3d74acf9a28c67741b2f4f2ea7635f0aaf6f0268",
        ["*.msgpack", "*.h5"],
        10,
        609872212,
    ),
]

# Blueprint 24 §14 face-embedding identity: OpenCV Zoo YuNet + SFace (Apache-2.0)
# as Git-LFS objects pinned by commit and sha256; best-effort, never a pack gate.
# (url, dest, sha256, bytes)
FACE_PACK = [
    (
        "https://github.com/opencv/opencv_zoo/raw/47534e27c9851bb1128ccc0102f1145e27f23f98/models/face_detection_yunet/face_detection_yunet_2023mar.onnx",
        "/models/diony/opencv-zoo/face_detection_yunet_2023mar.onnx",
        "8f2383e4dd3cfbb4553ea8718107fc0423210dc964f9f4280604804ed2552fa4",
        232589,
    ),
    (
        "https://github.com/opencv/opencv_zoo/raw/47534e27c9851bb1128ccc0102f1145e27f23f98/models/face_recognition_sface/face_recognition_sface_2021dec.onnx",
        "/models/diony/opencv-zoo/face_recognition_sface_2021dec.onnx",
        "0ba9fbfa01b5270c96627c4ef784da859931e02f04419c829e83484087c34e79",
        38696353,
    ),
]

# Wan2.2 speech pack (Blueprint 22): Wan's own S2V TTS lane
# (`generate.py --enable_tts`, `WanS2V.load_tts`) loads a CosyVoice checkout
# and the CosyVoice2-0.5B weights from beside generate.py and would download
# both at render time when absent. Studio pins them on the vault and links
# them where Wan looks, so Production never fetches.
WAN_TTS_RUNTIME_DIR = "/diony/vault/runtime/wan-tts"
WAN_TTS_CHECKOUT_LINK = "/opt/wan22/CosyVoice"
# Pinned CosyVoice revision (Blueprint 22). Wan2.2 targets an older CosyVoice
# API; Diony's `ensure_s2v_tts_prompt_path` overlay bridges the prompt-clip
# argument, and this pin keeps every site on the revision that overlay was
# qualified against instead of whatever `main` is on clone day.
WAN_TTS_CHECKOUT_REV = "074ca6dc9e80a2f424f1f74b48bdd7d3fea531cc"
WAN_TTS_MODEL_LINK = "/opt/wan22/CosyVoice2-0.5B"
WAN_TTS_MODEL = (
    "FunAudioLLM/CosyVoice2-0.5B",
    "/diony/vault/models/audio/CosyVoice2-0.5B",
    "eec1ae6c79877dbd9379285cf8789c9e0879293d",
    19,
    4856505002,
)
# Per-character casting library (Blueprint 22): distinct open-licensed speech
# clips already on the host (Wan2.2 and CosyVoice example assets). Library
# order is cast order; a sibling .txt is the clip's exact transcript so Wan
# clones zero-shot, otherwise it clones cross-lingually from the audio alone.
VOICE_PROMPT_LIBRARY_DIR = "/diony/vault/runtime/voice/prompt-library"
VOICE_PROMPT_LIBRARY: tuple[tuple[str, str, str], ...] = (
    ("$WAN_TTS_ROOT/asset/cross_lingual_prompt.wav", "cast-00-cross_lingual_prompt.wav", ""),
    ("$WAN_TTS_ROOT/asset/zero_shot_prompt.wav", "cast-01-zero_shot_prompt.wav", "希望你以后能够做的比我还好呦。"),
    ("/opt/wan22/examples/talk.wav", "cast-02-talk.wav", ""),
)
# Kept for one release so a Connect on a host prepared before the Wan-native
# lane can move its earlier checkout instead of cloning again.
LEGACY_COSYVOICE_CHECKOUT = "/diony/vault/runtime/cosyvoice/CosyVoice"

_lock = threading.RLock()
_apply_epoch = 0
_job: dict = {
    "running": False,
    "ok": False,
    "error": "",
    "detail": "",
    "log": [],
    "host": "",
    "remote": False,
    "started_at": "",
    "prepared": False,
    "pull_skipped": False,
    "stages": [],
    "percent": None,
    "stage": "",
}

# Weighted setup stages for the Connect page. Weights approximate wall-clock share.
SETUP_STAGES: list[tuple[str, str, int]] = [
    ("site", "Check site", 2),
    ("sync", "Sync Diony repo", 3),
    ("storage", "Model storage on the vault", 3),
    ("python", "Python runtime", 3),
    ("pack", "Pinned model pack", 55),
    ("dedupe", "Shared model assets", 3),
    ("wan", "Wan2.2 runtime", 12),
    ("speech", "Wan2.2 speech pack", 12),
    ("producer", "Producer", 4),
]


def setup_stage_plan() -> list[dict]:
    """Fresh stage list. `pack` carries one item per pinned model."""
    stages: list[dict] = []
    for stage_id, label, weight in SETUP_STAGES:
        stage: dict = {
            "id": stage_id,
            "label": label,
            "weight": weight,
            "status": "pending",
            "percent": None,
            "detail": "",
        }
        if stage_id == "pack":
            stage["items"] = [
                {
                    "id": repo,
                    "label": repo.rsplit("/", 1)[-1],
                    "status": "pending",
                    "percent": None,
                    "done": 0,
                    "total": 1 if filename else int(files or 0),
                    "bytes": int(payload_bytes or 0),
                }
                for repo, _dest, filename, _revision, files, payload_bytes in HF_PACK
            ]
        stages.append(stage)
    return stages


def _stage_fraction(stage: dict) -> float:
    status = stage.get("status")
    if status in {"done", "skipped"}:
        return 1.0
    if stage.get("percent") is not None:
        return max(0.0, min(1.0, float(stage["percent"]) / 100.0))
    return 0.35 if status == "active" else 0.0


def setup_percent(stages: list[dict]) -> int | None:
    """Overall 0-100. Holds at 99 until the last stage is done."""
    if not stages:
        return None
    total = sum(int(stage.get("weight") or 0) for stage in stages) or 1
    if all(stage.get("status") in {"done", "skipped"} for stage in stages):
        return 100
    earned = sum(int(stage.get("weight") or 0) * _stage_fraction(stage) for stage in stages)
    return max(0, min(99, int(round(earned / total * 100))))


def _pack_percent(stage: dict) -> int | None:
    items = stage.get("items") or []
    if not items:
        return stage.get("percent")
    weight = sum(int(item.get("bytes") or 0) for item in items) or len(items)
    earned = 0.0
    for item in items:
        share = int(item.get("bytes") or 0) if any(i.get("bytes") for i in items) else 1
        if item.get("status") == "done":
            earned += share
        elif item.get("total"):
            earned += share * min(1.0, float(item.get("done") or 0) / float(item["total"]))
    return int(round(earned / weight * 100))


_STAGE_MARKERS: dict[str, tuple[str, str, str]] = {
    "PY_RUNTIME_START": ("python", "active", "Creating the Diony venv"),
    "PIP_CHECK": ("python", "active", "Checking huggingface_hub"),
    "PIP_INSTALL": ("python", "active", "Installing huggingface_hub"),
    "PIP_OK": ("python", "done", "Python runtime ready"),
    "MODEL_VOLUME_START": ("storage", "active", "Preparing attached-volume model storage"),
    "MODEL_VOLUME_MIGRATE": ("storage", "active", "Moving existing models to the attached volume"),
    "MODEL_VOLUME_OK": ("storage", "done", "Attached-volume model storage ready"),
    "HF_START": ("pack", "active", "Checking the pinned model pack"),
    "HF_OK": ("pack", "done", "Pinned model pack ready"),
    "PACK_DEDUPE_START": ("dedupe", "active", "Consolidating shared model assets"),
    "PACK_DEDUPE_OK": ("dedupe", "done", "Shared model assets ready"),
    "WAN_RUNTIME_START": ("wan", "active", "Cloning Wan2.2 and installing the Diony apps"),
    "WAN_HOST_OVERLAY_REPAIR": ("wan", "active", "Repairing the Wan host runtime"),
    "WAN_GIT_LOCK_CLEARED": ("wan", "active", "Clearing a stale git lock"),
    "WAN_HOST_PYTHON_OK": ("wan", "done", "Wan host runtime ready"),
    "SPEECH_PACK_START": ("speech", "active", "Preparing Wan2.2's speech pack"),
    "SPEECH_PACK_INSTALL": ("speech", "active", "Installing Wan2.2's speech requirements"),
    "SPEECH_PACK_PULL": ("speech", "active", "Downloading the Wan2.2 speech model"),
    "SPEECH_PACK_OK": ("speech", "done", "Wan2.2 speech model ready"),
    "SPEECH_CAST": ("speech", "done", "Per-character casting library ready"),
    "PRODUCER_START": ("producer", "active", "Starting Producer"),
    "APPLY_OK": ("producer", "done", "Producer is running"),
}


def apply_stage_event(line: str) -> dict | None:
    """Map one streamed setup line to a stage/item transition, or None."""
    clean = str(line or "").strip()
    if not clean:
        return None
    marker, _, rest = clean.partition(" ")
    if marker in {"HF_PULL", "HF_PROGRESS", "HF_DONE"}:
        repo = rest.split(" -> ", 1)[0].split()[0] if rest.strip() else ""
        if not repo:
            return None
        event = {"stage": "pack", "status": "active", "detail": "", "item": repo}
        if marker == "HF_PULL":
            event.update({"item_status": "active", "detail": f"Downloading {repo.rsplit('/', 1)[-1]}"})
        elif marker == "HF_DONE":
            event.update({"item_status": "done", "detail": f"{repo.rsplit('/', 1)[-1]} ready"})
        else:
            counts = rest.split()[1] if len(rest.split()) > 1 else ""
            done_text, _, total_text = counts.partition("/")
            if not done_text.isdigit():
                return None
            event.update(
                {
                    "item_status": "active",
                    "done": int(done_text),
                    "total": int(total_text) if total_text.isdigit() else None,
                    "detail": f"Downloading {repo.rsplit('/', 1)[-1]} · {counts} files",
                }
            )
        return event
    if marker in _STAGE_MARKERS:
        stage_id, status, detail = _STAGE_MARKERS[marker]
        if marker == "MODEL_VOLUME_MIGRATE" and rest.strip():
            detail = f"Moving existing {rest.strip()} models to the attached volume"
        return {"stage": stage_id, "status": status, "detail": detail}
    return None


def _refresh_progress_locked() -> None:
    stages = _job.get("stages") or []
    for stage in stages:
        if stage.get("id") == "pack" and stage.get("status") == "active":
            stage["percent"] = _pack_percent(stage)
    _job["percent"] = setup_percent(stages)
    active = next((stage["id"] for stage in stages if stage.get("status") in {"active", "failed"}), "")
    _job["stage"] = active


def _apply_stage_event_locked(event: dict) -> None:
    stages = _job.get("stages") or []
    index = next((i for i, stage in enumerate(stages) if stage.get("id") == event["stage"]), -1)
    if index < 0:
        return
    stage = stages[index]
    for earlier in stages[:index]:
        if earlier.get("status") not in {"done", "skipped"}:
            earlier["status"] = "done"
            earlier["percent"] = 100
    if event["status"] == "done":
        stage["status"] = "done"
        stage["percent"] = 100
        for item in stage.get("items") or []:
            item["status"] = "done"
            item["percent"] = 100
            item["done"] = item.get("total") or item.get("done") or 0
    elif stage.get("status") != "done":
        stage["status"] = "active"
    if event.get("detail"):
        stage["detail"] = event["detail"]
    item_id = event.get("item")
    if item_id:
        item = next((row for row in stage.get("items") or [] if row.get("id") == item_id), None)
        if item is None:
            item = {"id": item_id, "label": item_id.rsplit("/", 1)[-1], "status": "pending", "percent": None, "done": 0, "total": 0, "bytes": 0}
            stage.setdefault("items", []).append(item)
        item["status"] = event.get("item_status") or item["status"]
        if event.get("done") is not None:
            item["done"] = int(event["done"])
            if event.get("total"):
                item["total"] = int(event["total"])
        if item["status"] == "done":
            item["done"] = item.get("total") or item.get("done") or 0
            item["percent"] = 100
        elif item.get("total"):
            item["percent"] = int(round(min(1.0, item["done"] / item["total"]) * 100))
    _refresh_progress_locked()


def _stage(epoch: int, stage_id: str, status: str, detail: str = "") -> None:
    """Local-side stage transition (readiness, rsync, prepared-site shortcuts)."""
    with _lock:
        if epoch != _apply_epoch:
            return
        _apply_stage_event_locked({"stage": stage_id, "status": "active" if status == "active" else "done", "detail": detail})
        if status == "skipped":
            for stage in _job.get("stages") or []:
                if stage.get("id") == stage_id:
                    stage["status"] = "skipped"
                    stage["percent"] = 100
                    for item in stage.get("items") or []:
                        item["status"] = "skipped"
                        item["percent"] = 100
            _refresh_progress_locked()
    _persist()


def _stage_line(line: str, epoch: int) -> None:
    event = apply_stage_event(line)
    if not event:
        return
    with _lock:
        if epoch != _apply_epoch:
            return
        _apply_stage_event_locked(event)
    _persist()


def _finish_stages(epoch: int) -> None:
    with _lock:
        if epoch != _apply_epoch:
            return
        for stage in _job.get("stages") or []:
            if stage.get("status") not in {"done", "skipped"}:
                stage["status"] = "done"
                stage["percent"] = 100
        _refresh_progress_locked()


def _fail_stages(epoch: int, error: str) -> None:
    with _lock:
        if epoch != _apply_epoch:
            return
        stages = _job.get("stages") or []
        active = next((stage for stage in stages if stage.get("status") == "active"), None)
        if active is None:
            active = next((stage for stage in stages if stage.get("status") == "pending"), None)
        if active is not None:
            active["status"] = "failed"
            active["detail"] = str(error or "")[:160]
        _refresh_progress_locked()


def public_progress(snapshot: dict | None = None) -> dict:
    """The Connect page contract: stages, overall percent, active stage, start time."""
    data = snapshot if snapshot is not None else public_apply()
    return {
        "stages": data.get("stages") or [],
        "percent": data.get("percent"),
        "stage": data.get("stage") or "",
        "started_at": data.get("started_at") or "",
        "running": bool(data.get("running")),
    }


def apply_path() -> Path:
    return cluster_root() / ".cluster" / "apply.json"


def _snapshot_locked() -> dict:
    log = list(_job["log"])[-40:]
    return {
        "running": bool(_job["running"]),
        "ok": bool(_job["ok"]),
        "error": str(_job["error"]),
        "detail": str(_job["detail"]),
        "log": log,
        "host": str(_job["host"]),
        "live": wizard_live() or bool(_job.get("remote")),
        "user": ssh_user(),
        "port": ssh_port(),
        "started_at": str(_job.get("started_at") or ""),
        "prepared": bool(_job.get("prepared")),
        "pull_skipped": bool(_job.get("pull_skipped")),
        "stages": copy.deepcopy(_job.get("stages") or []),
        "percent": _job.get("percent"),
        "stage": str(_job.get("stage") or ""),
    }


def public_apply() -> dict:
    with _lock:
        return _snapshot_locked()


def _persist() -> None:
    with _lock:
        payload = _snapshot_locked()
    dest = apply_path()
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(payload, indent=2) + "\n")


def _still(epoch: int) -> bool:
    with _lock:
        return epoch == _apply_epoch


def _commit_job(epoch: int, **fields) -> bool:
    with _lock:
        if epoch != _apply_epoch:
            return False
        _job.update(fields)
    _persist()
    return True


def _log(line: str, epoch: int | None = None) -> None:
    with _lock:
        if epoch is not None and epoch != _apply_epoch:
            return
        _job["log"].append(line[:500])
        _job["detail"] = line[:200]
    _persist()


def hf_download_lines() -> list[str]:
    lines = []
    for repo, dest, filename, revision, _files, _bytes in HF_PACK:
        lines.append(f"mkdir -p {dest}")
        if filename:
            lines.append(
                f"huggingface-cli download {repo} {filename} --revision {revision} --local-dir {dest}"
            )
        else:
            lines.append(f"huggingface-cli download {repo} --revision {revision} --local-dir {dest}")
    return lines


def site_readiness_script(repo: str) -> str:
    """Fast read-only remote probe used before setup or any model download.

    GPU runtime imports are deliberately excluded here. Setup writes a versioned
    receipt after performing that expensive validation under the exact Wan
    interpreter; Connect verifies the receipt and filesystem contract instead.
    """
    quoted_repo = repr(repo)
    expected_snapshots = {
        dest: (revision, files, payload_bytes)
        for _repo, dest, _filename, revision, files, payload_bytes in HF_PACK
    }
    expected_snapshots[WAN_TTS_MODEL[1]] = WAN_TTS_MODEL[2:]
    quoted_snapshots = repr(expected_snapshots)
    return f"""
python3 - <<'PY'
import json
import os
import shutil
import subprocess
from pathlib import Path

repo = Path({quoted_repo})
expected_snapshots = {quoted_snapshots}

def populated(raw):
    path = Path(raw)
    try:
        return path.is_dir() and next(path.iterdir(), None) is not None
    except OSError:
        return False

def snapshot_state(raw, expected):
    expected_revision, expected_files, expected_bytes = expected
    root = Path(raw)
    if not root.is_dir():
        return {{"complete": False, "pinned": False, "revision": ""}}
    if any(root.rglob("*.incomplete")):
        return {{"complete": False, "pinned": False, "revision": ""}}
    metadata = sorted(root.rglob("*.metadata"))
    revisions = set()
    complete = bool(metadata)
    payload_bytes = 0
    payload_files = 0
    for meta in metadata:
        try:
            lines = meta.read_text(errors="replace").splitlines()
            revision = lines[0].strip() if lines else ""
            revisions.add(revision)
            download = meta.parent
            while download.name != "download" and download != root:
                download = download.parent
            relative = meta.relative_to(download)
            target = root / str(relative).removesuffix(".metadata")
            complete = complete and target.is_file() and target.stat().st_size > 0
            if target.is_file():
                payload_files += 1
                payload_bytes += target.stat().st_size
        except (OSError, ValueError):
            complete = False
    revision = next(iter(revisions)) if len(revisions) == 1 else ""
    return {{
        "complete": (
            complete
            and bool(revision)
            and payload_files == expected_files
            and payload_bytes == expected_bytes
        ),
        "pinned": complete and revision == expected_revision,
        "revision": revision,
    }}

snapshot_states = {{
    path: snapshot_state(path, expected)
    for path, expected in expected_snapshots.items()
}}
snapshot_complete = all(row["complete"] for row in snapshot_states.values())
snapshot_pinned = all(row["pinned"] for row in snapshot_states.values())
sr = Path("/models/diony/diony_sr_v1")
tts_model = Path("/diony/vault/models/audio/CosyVoice2-0.5B")
tts_required = (
    "cosyvoice2.yaml",
    "llm.pt",
    "flow.pt",
    "hift.pt",
    "speech_tokenizer_v2.onnx",
)
tts_model_ready = all((tts_model / name).is_file() for name in tts_required)
volume_root = Path("/diony/vault/models").resolve()
model_volume = all(
    (Path("/models") / kind).resolve() == volume_root / kind
    for kind in ("deepseek", "wan", "diony", "audio")
)
pack = snapshot_complete and snapshot_pinned and (
    (sr / "RealESRGAN_x2.pth").is_file() or bool(list(sr.glob("*.pth")))
) and tts_model_ready
wan_repo = Path("/opt/wan22/.git").is_dir()
# Wan's own TTS lane loads both from beside generate.py (WanS2V.load_tts).
tts_checkout = Path("/opt/wan22/CosyVoice")
tts_checkout_ready = all((tts_checkout / relative).exists() for relative in (
    "cosyvoice/cli/cosyvoice.py",
    "third_party/Matcha-TTS",
))
tts_link = Path("/opt/wan22/CosyVoice2-0.5B")
speech_pack = tts_checkout_ready and all((tts_link / name).is_file() for name in tts_required)
casting_library = any(Path("/diony/vault/runtime/voice/prompt-library").glob("*.wav")) if Path("/diony/vault/runtime/voice/prompt-library").is_dir() else False
runtime_receipt_path = Path("/diony/vault/.cluster/wan-host-runtime.json")
try:
    runtime_report = json.loads(runtime_receipt_path.read_text(encoding="utf-8"))
except (OSError, json.JSONDecodeError, TypeError):
    runtime_report = {{}}
optional_engines = {{
    name: bool(runtime_report.get("capabilities", {{}}).get(name, {{}}).get("available"))
    for name in ("s2v", "animate")
}}
runtime_python = Path(str(runtime_report.get("python") or ""))
runtime_roots = str(runtime_report.get("wan_repo") or "").split(os.pathsep)
wan_runtime = bool(
    runtime_report.get("schema_version") == 1
    and runtime_report.get("ok") is True
    and runtime_report.get("required_capabilities_ready") is True
    and str(runtime_report.get("checked_at") or "").strip()
    and "/opt/wan22" in runtime_roots
    and runtime_python.is_file()
    and all(optional_engines.values())
)
producer_installed = bool(shutil.which("diony-producer")) or (
    repo / ".venv" / "bin" / "diony-producer"
).is_file()
producer_running = subprocess.run(
    ["pgrep", "-f", "diony-producer"],
    stdout=subprocess.DEVNULL,
    stderr=subprocess.DEVNULL,
    check=False,
).returncode == 0
prepared = pack and model_volume and wan_repo and speech_pack
print("DIONY_SITE_STATE " + json.dumps({{
    "prepared": prepared,
    "ready": prepared and wan_runtime and producer_installed and producer_running,
    "pack": pack,
    "snapshot_complete": snapshot_complete,
    "snapshot_pinned": snapshot_pinned,
    "optional_engines": optional_engines,
    "model_volume": model_volume,
    "wan_repo": wan_repo,
    "speech_pack": speech_pack,
    "casting_library": casting_library,
    "wan_runtime": wan_runtime,
    "producer_installed": producer_installed,
    "producer_running": producer_running,
}}, separators=(",", ":")))
PY
"""


def parse_site_readiness(result) -> dict:
    if not getattr(result, "ok", False):
        return {}
    for line in reversed((getattr(result, "stdout", "") or "").splitlines()):
        if not line.startswith("DIONY_SITE_STATE "):
            continue
        try:
            payload = json.loads(line.removeprefix("DIONY_SITE_STATE "))
        except json.JSONDecodeError:
            return {}
        return payload if isinstance(payload, dict) else {}
    return {}


def _python_bootstrap(repo: str) -> str:
    """Venv first. PEP 668 images (RunPod Ubuntu) cannot pip into system Python.

    The venv does not get CUDA torch. generate.py and diony-finish use the host
    interpreter that already `import torch` (RunPod PyTorch image: /usr/bin/python3).
    """
    quoted = shlex.quote(repo)
    return f"""
_diony_repo={quoted}
_diony_venv="$_diony_repo/.venv"
_DIONY_HOST_PYTHON=""
for _diony_candidate in "${{DIONY_PYTHON:-}}" /usr/bin/python3 /usr/local/bin/python "$(command -v python3 2>/dev/null || true)"; do
  [ -n "$_diony_candidate" ] || continue
  if "$_diony_candidate" -c "import torch" >/dev/null 2>&1; then
    _DIONY_HOST_PYTHON="$_diony_candidate"
    break
  fi
done
[ -n "$_DIONY_HOST_PYTHON" ] || {{ echo WAN_HOST_PYTHON_WITH_TORCH_MISSING >&2; exit 13; }}
_DIONY_WAN_RUNTIME=/diony/vault/runtime/wan
_DIONY_WAN_PYTHON="$_DIONY_WAN_RUNTIME/venv/bin/python"
mkdir -p "$_DIONY_WAN_RUNTIME"
if [ ! -x "$_DIONY_WAN_PYTHON" ]; then
  "$_DIONY_HOST_PYTHON" -m venv --system-site-packages "$_DIONY_WAN_RUNTIME/venv"
fi
[ -x "$_DIONY_WAN_PYTHON" ] || {{ echo WAN_PERSISTENT_RUNTIME_MISSING >&2; exit 13; }}
mkdir -p "$_diony_repo"
if [ ! -x "$_diony_venv/bin/python" ]; then
  python3 -m venv "$_diony_venv" || python3 -m venv "$_diony_venv" --without-pip || true
fi
if [ -x "$_diony_venv/bin/python" ] && [ ! -x "$_diony_venv/bin/pip" ]; then
  "$_diony_venv/bin/python" -m ensurepip --upgrade || true
fi
if [ ! -x "$_diony_venv/bin/pip" ]; then
  rm -rf "$_diony_venv"
fi
_diony_pip() {{
  if [ -x "$_diony_venv/bin/pip" ]; then
    "$_diony_venv/bin/pip" "$@"
  else
    python3 -m pip --break-system-packages "$@"
  fi
}}
_diony_python() {{
  if [ -x "$_diony_venv/bin/pip" ] && [ -x "$_diony_venv/bin/python" ]; then
    "$_diony_venv/bin/python" "$@"
  else
    python3 "$@"
  fi
}}
if [ -x "$_diony_venv/bin/pip" ]; then
  export PATH="$_diony_venv/bin:$PATH"
fi
export PIP_DISABLE_PIP_VERSION_CHECK=1
export PIP_ROOT_USER_ACTION=ignore
export DIONY_PYTHON="${{DIONY_PYTHON:-$_DIONY_WAN_PYTHON}}"
export DIONY_VOLUME_CAPACITY_GB="${{DIONY_VOLUME_CAPACITY_GB:-1200}}"
export DIONY_VOLUME_MIN_FREE_GB="${{DIONY_VOLUME_MIN_FREE_GB:-100}}"
export DIONY_MODEL_HOT_CACHE="${{DIONY_MODEL_HOT_CACHE:-auto}}"
export DIONY_MODEL_HOT_CACHE_GB="${{DIONY_MODEL_HOT_CACHE_GB:-320}}"
export DIONY_MODEL_HOT_CACHE_MIN_FREE_GB="${{DIONY_MODEL_HOT_CACHE_MIN_FREE_GB:-64}}"
_diony_clear_stale_git_locks() {{
  # pip checks VCS requirements out under <venv>/src. An interrupted run leaves
  # .git/index.lock behind and every later pip install fails with
  # "Another git process seems to be running". Clear it when no git is live.
  local root lock
  for root in "$@"; do
    [ -d "$root" ] || continue
    for lock in "$root"/*/.git/*.lock "$root"/.git/*.lock; do
      [ -e "$lock" ] || continue
      if pgrep -x git >/dev/null 2>&1; then
        echo "WAN_GIT_LOCK_BUSY $lock" >&2
        continue
      fi
      echo "WAN_GIT_LOCK_CLEARED $lock"
      rm -f -- "$lock"
    done
  done
  return 0
}}
_diony_host_wan_pip() {{
  [ -f /opt/wan22/requirements.txt ] || return 0
  local probe="$_diony_repo/apps/wan/src/diony_wan/runtime.py"
  local overlay="$_diony_repo/infra/runtime/wan-host-overlay.txt"
  local report="/diony/vault/.cluster/wan-host-runtime.json"
  local wan_py="$DIONY_PYTHON"
  local -a required=(--require-capability s2v --require-capability animate)
  [ -f "$probe" ] || {{ echo "WAN_RUNTIME_PROBE_MISSING $probe" >&2; return 14; }}
  [ -f "$overlay" ] || {{ echo "WAN_RUNTIME_OVERLAY_MISSING $overlay" >&2; return 14; }}
  if PYTHONPATH=/opt/wan22 "$wan_py" "$probe" "${{required[@]}}" --write "$report" >/dev/null 2>&1; then
    echo WAN_HOST_PYTHON_OK
    return 0
  fi
  echo WAN_HOST_OVERLAY_REPAIR
  _diony_clear_stale_git_locks "$(cd "$(dirname "$wan_py")/.." && pwd)/src" /opt/wan22
  PIP_ROOT_USER_ACTION=ignore \\
    "$wan_py" -m pip install --disable-pip-version-check \\
      --upgrade-strategy only-if-needed -r "$overlay"
  if PYTHONPATH=/opt/wan22 "$wan_py" "$probe" "${{required[@]}}" --write "$report" >/dev/null 2>&1; then
    echo WAN_HOST_PYTHON_OK
    return 0
  fi
  grep -viE '^[[:space:]]*(flash_attn|numpy)|^[[:space:]]*#' /opt/wan22/requirements.txt > /tmp/diony-wan-req.txt || true
  PIP_ROOT_USER_ACTION=ignore \\
    "$wan_py" -m pip install --disable-pip-version-check \\
      --upgrade-strategy only-if-needed -r /tmp/diony-wan-req.txt -r "$overlay"
  if [ -f /opt/wan22/requirements_s2v.txt ]; then
    PIP_ROOT_USER_ACTION=ignore "$wan_py" -m pip install --disable-pip-version-check \\
      --upgrade-strategy only-if-needed -r /opt/wan22/requirements_s2v.txt
  fi
  if [ -f /opt/wan22/requirements_animate.txt ]; then
    grep -viE '^[[:space:]]*-e[[:space:]]+git\\+https://github.com/facebookresearch/sam2' \\
      /opt/wan22/requirements_animate.txt > /tmp/diony-wan-animate-req.txt || true
    PIP_ROOT_USER_ACTION=ignore "$wan_py" -m pip install --disable-pip-version-check \\
      --upgrade-strategy only-if-needed ninja -r /tmp/diony-wan-animate-req.txt
    PIP_ROOT_USER_ACTION=ignore "$wan_py" -m pip install --disable-pip-version-check \\
      --no-build-isolation --upgrade-strategy only-if-needed \\
      -e 'git+https://github.com/facebookresearch/sam2.git@0e78a118995e66bb27d78518c4bd9a3e95b4e266#egg=SAM-2'
  fi
  PYTHONPATH=/opt/wan22 "$wan_py" "$probe" "${{required[@]}}" --write "$report"
  echo WAN_HOST_PYTHON_OK
}}
_diony_link_system_torch() {{
  local vpy="$_diony_venv/bin/python"
  [ -x "$vpy" ] || return 0
  if "$vpy" -c "import torch" >/dev/null 2>&1; then
    return 0
  fi
  local root sp
  root=$("$_DIONY_HOST_PYTHON" -c "import torch, os; print(os.path.dirname(os.path.dirname(torch.__file__)))" 2>/dev/null) || return 0
  [ -n "$root" ] || return 0
  sp=$("$vpy" -c "import site; print(site.getsitepackages()[0])") || return 0
  printf '%s\\n' "$root" > "$sp/diony-system-torch.pth"
}}
_diony_link_system_torch
"""


def _ensure_huggingface_hub() -> str:
    """Install huggingface_hub only when import fails. Never `pip install -U pip`."""
    return """
echo PIP_CHECK
if _diony_python -c "from huggingface_hub import snapshot_download, hf_hub_download" >/dev/null 2>&1; then
  echo PIP_OK huggingface_hub already present
else
  echo PIP_INSTALL huggingface_hub
  _diony_pip install -q huggingface_hub
  echo PIP_OK huggingface_hub installed
fi
"""


def _persistent_model_storage() -> str:
    """Keep reusable model weights on the attached vault, preserving /models APIs."""
    return r'''
echo MODEL_VOLUME_START
_diony_persist_model_dir() {
  local kind="$1"
  local volume="/diony/vault/models/$kind"
  local legacy="/models/$kind"
  local backup="/models/.${kind}.diony-overlay"
  local current pending volume_real
  local -a sync_filters=()
  mkdir -p "$volume" /models
  volume_real=$(readlink -f "$volume")
  if [ "$kind" = audio ]; then
    sync_filters+=(--exclude '*.incomplete' --exclude '.cache')
  fi
  if [ -L "$legacy" ]; then
    current=$(readlink -f "$legacy" || true)
    [ "$current" = "$volume_real" ] || {
      echo "MODEL_VOLUME_ERROR $kind points to $current" >&2
      return 1
    }
    return 0
  fi
  if [ -e "$legacy" ] && [ ! -d "$legacy" ]; then
    echo "MODEL_VOLUME_ERROR $legacy is not a directory" >&2
    return 1
  fi
  if [ -d "$legacy" ] && [ -n "$(find "$legacy" -mindepth 1 -maxdepth 1 -print -quit)" ]; then
    echo "MODEL_VOLUME_MIGRATE $kind"
    [ ! -e "$backup" ] || {
      echo "MODEL_VOLUME_ERROR stale migration backup $backup" >&2
      return 1
    }
    rsync -a "${sync_filters[@]}" -- "$legacy/" "$volume/"
    pending=$(rsync -rlni --size-only "${sync_filters[@]}" -- "$legacy/" "$volume/")
    [ -z "$pending" ] || {
      echo "MODEL_VOLUME_ERROR verification failed for $kind" >&2
      return 1
    }
    mv "$legacy" "$backup"
    ln -s "$volume" "$legacy"
    [ "$(readlink -f "$legacy")" = "$volume_real" ] || return 1
    rm -rf -- "$backup"
  else
    [ ! -d "$legacy" ] || rmdir "$legacy"
    ln -s "$volume" "$legacy"
  fi
  echo "MODEL_VOLUME_READY $kind"
}
_diony_persist_model_dir deepseek
_diony_persist_model_dir wan
_diony_persist_model_dir diony
_diony_persist_model_dir audio
echo MODEL_VOLUME_OK
'''


def _model_pack_dedupe() -> str:
    """Hard-link byte-identical shared Wan assets without changing model paths."""
    return """
echo PACK_DEDUPE_START
_diony_hardlink_identical() {
  local canonical="$1"
  shift
  [ -f "$canonical" ] || return 0
  local candidate pending basename canonical_meta candidate_meta canonical_etag candidate_etag
  basename=$(basename "$canonical")
  canonical_meta="$(dirname "$canonical")/.cache/huggingface/download/${basename}.metadata"
  canonical_etag=$(sed -n '2p' "$canonical_meta" 2>/dev/null || true)
  for candidate in "$@"; do
    [ -f "$candidate" ] || continue
    [ "$canonical" -ef "$candidate" ] && continue
    candidate_meta="$(dirname "$candidate")/.cache/huggingface/download/${basename}.metadata"
    candidate_etag=$(sed -n '2p' "$candidate_meta" 2>/dev/null || true)
    if [ -n "$canonical_etag" ] && [ -n "$candidate_etag" ]; then
      [ "$canonical_etag" = "$candidate_etag" ] || continue
      [ "$(stat -c '%s' "$canonical")" = "$(stat -c '%s' "$candidate")" ] || continue
    else
      cmp -s "$canonical" "$candidate" || continue
    fi
    pending="${candidate}.diony-link"
    ln -f "$canonical" "$pending"
    mv -f "$pending" "$candidate"
  done
}
_diony_t5=/models/wan/Wan2.2-I2V-A14B/models_t5_umt5-xxl-enc-bf16.pth
_diony_hardlink_identical "$_diony_t5" \
  /models/wan/Wan2.2-T2V-A14B/models_t5_umt5-xxl-enc-bf16.pth \
  /models/wan/Wan2.2-S2V-14B/models_t5_umt5-xxl-enc-bf16.pth \
  /models/wan/Wan2.2-Animate-14B/models_t5_umt5-xxl-enc-bf16.pth \
  /models/wan/Wan2.2-VACE-Fun-A14B/models_t5_umt5-xxl-enc-bf16.pth
_diony_vae=/models/wan/Wan2.2-I2V-A14B/Wan2.1_VAE.pth
_diony_hardlink_identical "$_diony_vae" \
  /models/wan/Wan2.2-T2V-A14B/Wan2.1_VAE.pth \
  /models/wan/Wan2.2-S2V-14B/Wan2.1_VAE.pth \
  /models/wan/Wan2.2-VACE-Fun-A14B/Wan2.1_VAE.pth
echo PACK_DEDUPE_OK
"""


_PROGRESS_NOISE = re.compile(
    r"(\d{1,3}%\|"                # tqdm percentage + bar
    r"|[█-▏]+\|"        # bare block-character bar fragment
    r"|\d+/\d+ \[\d{1,2}:\d{2}<"  # n/total [mm:ss<
    r"|\d+(\.\d+)?(it/s|s/it)\]"  # rate suffix
    r"|^\s*\[A\s*$)"              # stripped tqdm cursor-up escape
)
_ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
_CAUSE_LINE = re.compile(
    r"^\s*(?:×\s*)?(fatal:|error:|ERROR:|Error:|OSError:|PermissionError:|"
    r"FileNotFoundError:|RuntimeError:|HF_FAIL |No space left on device|"
    r"Killed|Connection (?:closed|reset)|client_loop: send disconnect)",
)


def strip_progress_noise(text: str) -> str:
    """Drop tqdm/progress-bar refreshes so an excerpt shows only real output."""
    kept: list[str] = []
    for raw in _ANSI.sub("", str(text or "")).replace("\r", "\n").splitlines():
        line = raw.rstrip()
        if not line.strip():
            continue
        if _PROGRESS_NOISE.search(line):
            continue
        kept.append(line)
    return "\n".join(kept)


def apply_stage_text(stdout: str) -> str:
    """Name the last Studio stage the remote setup reported on stdout."""
    for line in reversed(str(stdout or "").splitlines()):
        label = apply_progress_text(line)
        if label:
            return label
    return ""


def _stage_prefix(stdout: str) -> str:
    stage = apply_stage_text(stdout)
    return f"Setup stopped at: {stage}. " if stage else ""


def summarize_traceback(text: str) -> str:
    """Exception line first, then the innermost frame. Fits Studio's 300 chars."""
    lines = [line.rstrip() for line in str(text or "").splitlines() if line.strip()]
    if not lines:
        return ""
    last_frame = max(
        (index for index, line in enumerate(lines) if re.match(r'\s*File "', line)),
        default=-1,
    )
    exc_line = next(
        (line.strip() for line in lines[last_frame + 1 :] if not line.startswith(" ")),
        lines[-1].strip(),
    )
    frame = ""
    for line in reversed(lines):
        match = re.match(r'\s*File "([^"]+)", line (\d+)', line)
        if match and match.group(1) not in {"<string>", "<stdin>"}:
            parts = match.group(1).replace("\\", "/").split("/")
            short = "/".join(parts[-3:]) if len(parts) > 3 else match.group(1)
            frame = f" — raised in {short} line {match.group(2)}"
            break
    return f"{exc_line[:400]}{frame}."


def apply_fail_text(result) -> str:
    """Operator excerpt. Lead with the cause; never with download-bar noise."""
    out = getattr(result, "stdout", "") or ""
    err = getattr(result, "stderr", "") or ""
    code = getattr(result, "code", None)
    raw = f"{out}\n{err}".strip()
    blob = strip_progress_noise(raw)
    prefix = _stage_prefix(out)

    if "diony_wan/runtime.py" in blob and "_write_report" in blob:
        cause = next(
            (
                line.strip()
                for line in reversed(blob.splitlines())
                if line.strip() and not line.strip().startswith("^")
            ),
            "Runtime report write failed.",
        )
        return (
            "Wan runtime readiness report could not be finalized. "
            f"Retry validation. {cause[-600:]}"
        )
    if "DIONY_APPLY_BUSY" in blob:
        return (
            "Another Diony setup is still running on this host. Wait for it to finish "
            "or fail, then retry validation. Diony did not start a second install."
        )
    lock_match = re.search(r"Unable to create '([^']+\.git/[^']*\.lock)': File exists", blob)
    if lock_match:
        return (
            f"{prefix}A previous install was interrupted and left a git lock at "
            f"{lock_match.group(1)}. Retry validation: Diony now clears stale git locks "
            "before repairing the Wan host runtime."
        )
    hf_fail = next(
        (line.strip() for line in reversed(blob.splitlines()) if line.strip().startswith("HF_FAIL ")),
        "",
    )
    if hf_fail:
        repo, _, reason = hf_fail.removeprefix("HF_FAIL ").partition(":")
        name = repo.strip().rsplit("/", 1)[-1] or repo.strip()
        hint = ""
        if re.search(r"\b(401|403)\b|gated|GatedRepo|authenticat", reason, re.IGNORECASE):
            hint = " Check the Diony-Enterprise HF Token and the model's access grant."
        elif re.search(r"No space left|ENOSPC|disk quota", reason, re.IGNORECASE):
            hint = " The attached volume is full. Free space, then retry validation."
        return f"Model download failed for {name}: {reason.strip()[-500:] or 'unknown error'}.{hint}"
    for marker, text in (
        ("WAN_RUNTIME_PROBE_MISSING", "the Wan runtime probe"),
        ("WAN_RUNTIME_OVERLAY_MISSING", "the Wan host overlay requirements"),
    ):
        if marker in blob:
            return (
                f"{prefix}The repo sync to this host is incomplete: {text} is missing. "
                "Retry validation to re-sync the Diony repo."
            )
    pip_noise = "Requirement already satisfied" in blob or (
        "huggingface_hub" in blob and "site-packages" in blob and "Traceback" not in blob
    )
    if pip_noise and "APPLY_OK" not in out and "HF_OK" not in out:
        return (
            "Pack pull stopped at pip. huggingface_hub is already on this GPU. "
            "Retry Pull pack. Studio now skips that pip step and starts Hugging Face."
        )
    if "SPEECH_OVERLAY_MISSING" in blob:
        return (
            f"{prefix}The repo sync to this host is incomplete: infra/runtime/wan-tts-overlay.txt "
            "is missing. Retry validation to re-sync the Diony repo."
        )
    idx = blob.rfind("Traceback (most recent call last):")
    if idx >= 0:
        summary = summarize_traceback(blob[idx:])
        missing = re.search(r"ModuleNotFoundError: No module named '([^']+)'", summary)
        if missing and "SPEECH_IMPORT_FAILED" in blob:
            return (
                f"{prefix}The Wan host interpreter is missing the Python package "
                f"'{missing.group(1)}' for Wan2.2's speech lane. Retry validation: Diony installs "
                "wan-tts-overlay.txt (transformers, diffusers, x-transformers) "
                "alongside Wan's requirements_s2v.txt."
            )
        if missing:
            return f"{prefix}Python package '{missing.group(1)}' is missing on the host. {summary}"
        return f"{prefix}{summary}"
    lines = blob.splitlines()
    for line_no in range(len(lines) - 1, -1, -1):
        if _CAUSE_LINE.search(lines[line_no]):
            excerpt = "\n".join(lines[line_no : line_no + 4]).strip()
            return f"{prefix}{excerpt[:1200]}"
    tail = "\n".join(lines[-12:])[-1200:]
    if not tail:
        tail = f"apply exited {code}" if code is not None else "apply failed"
    return f"{prefix}{tail}"


def apply_progress_text(line: str) -> str:
    """Translate streamed setup markers into concise, non-secret Studio status."""
    clean = str(line or "").strip()
    if not clean:
        return ""
    if clean.startswith("HF_PULL "):
        repo = clean.removeprefix("HF_PULL ").split(" -> ", 1)[0]
        return f"Downloading {repo.rsplit('/', 1)[-1]}"
    if clean.startswith("HF_DONE "):
        repo = clean.removeprefix("HF_DONE ").strip()
        return f"{repo.rsplit('/', 1)[-1]} ready"
    if clean.startswith("HF_PROGRESS "):
        parts = clean.removeprefix("HF_PROGRESS ").split()
        if len(parts) >= 2 and "/" in parts[1]:
            return f"Downloading {parts[0].rsplit('/', 1)[-1]} · {parts[1]} files"
        return ""
    if clean.startswith("WAN_GIT_LOCK_CLEARED "):
        return "Clearing a stale git lock left by an interrupted install"
    stages = {
        "PY_RUNTIME_START": "Preparing the Python runtime",
        "WAN_RUNTIME_START": "Installing the Wan2.2 runtime and Diony apps",
        "SPEECH_PACK_START": "Preparing Wan2.2's speech pack",
        "PRODUCER_START": "Starting Producer",
        "HF_START": "Checking the pinned model pack",
        "HF_OK": "Pinned model pack ready",
        "MODEL_VOLUME_START": "Preparing attached-volume model storage",
        "MODEL_VOLUME_OK": "Attached-volume model storage ready",
        "PACK_DEDUPE_START": "Consolidating shared model assets",
        "PACK_DEDUPE_OK": "Shared model assets ready",
        "SPEECH_PACK_INSTALL": "Installing Wan2.2's speech requirements",
        "SPEECH_PACK_OK": "Wan2.2 speech model ready",
        "SPEECH_CAST": "Per-character casting library ready",
        "WAN_HOST_OVERLAY_REPAIR": "Repairing the Wan host runtime",
        "WAN_HOST_PYTHON_OK": "Wan host runtime ready",
        "APPLY_OK": "Compute setup complete",
    }
    if clean.startswith("SPEECH_PACK_PULL "):
        return "Downloading Wan2.2 speech model"
    if clean.startswith("SPEECH_CAST "):
        return "Per-character casting library ready"
    if clean.startswith("MODEL_VOLUME_MIGRATE "):
        kind = clean.removeprefix("MODEL_VOLUME_MIGRATE ").strip()
        return f"Moving existing {kind} models to the attached volume"
    return stages.get(clean, "")


def remote_setup_script(repo: str, nas: str = "") -> str:
    token = hf_token()
    if token:
        quoted = shlex.quote(token)
        token_line = (
            f"export HF_TOKEN={quoted}\nexport HUGGING_FACE_HUB_TOKEN={quoted}"
        )
    else:
        token_line = "true"
    pack = repr(HF_PACK)
    montage_pack = repr(MONTAGE_PACK)
    face_pulls = "\n".join(
        f"_diony_face_pull {shlex.quote(url)} {shlex.quote(dest)} {sha}" for url, dest, sha, _bytes in FACE_PACK
    )
    tts_model = repr(WAN_TTS_MODEL)
    prompt_library_dir = VOICE_PROMPT_LIBRARY_DIR
    prompt_library_lines = "\n".join(
        f'if [ -f "{source}" ]; then [ -f "{VOICE_PROMPT_LIBRARY_DIR}/{name}" ] || cp "{source}" "{VOICE_PROMPT_LIBRARY_DIR}/{name}"; '
        + (
            f"printf '%s\\n' {shlex.quote(transcript)} > \"{VOICE_PROMPT_LIBRARY_DIR}/{Path(name).stem}.txt\"; "
            if transcript
            else ""
        )
        + "_diony_cast_count=$((_diony_cast_count+1)); fi"
        for source, name, transcript in VOICE_PROMPT_LIBRARY
    )
    quoted_repo = shlex.quote(repo)
    mount = "\n".join(vault_mount_lines(nas))
    py = _python_bootstrap(repo)
    hub = _ensure_huggingface_hub()
    model_storage = _persistent_model_storage()
    dedupe = _model_pack_dedupe()
    # Keep the token out of Studio logs. It rides SSH stdin only.
    # Vault before .cluster. Venv before pip. Sequential SKU: Producer only.
    return f"""
set -euo pipefail
{token_line}
unset DASH_API_KEY DASHSCOPE_API_KEY
export DIONY_VAULT=/diony/vault
export DIONY_BIND=0.0.0.0
export DIONY_WAN_REPO=/opt/wan22
export PYTHONPATH=/opt/wan22${{PYTHONPATH:+:$PYTHONPATH}}
export DIONY_REPO={quoted_repo}
# Studio reads stage markers, not progress bars. Bars only flood the SSH stream.
export HF_HUB_DISABLE_PROGRESS_BARS=1
export TQDM_DISABLE=1
export PIP_PROGRESS_BAR=off
export GIT_TERMINAL_PROMPT=0
mkdir -p /models/deepseek /models/wan /models/diony /opt
{mount}
mkdir -p /diony/vault/.cluster
# One setup per host. A second run would collide with the first on pip/git.
exec 9>/diony/vault/.cluster/apply.lock
if ! flock -n 9; then
  echo "DIONY_APPLY_BUSY another Diony setup still holds /diony/vault/.cluster/apply.lock" >&2
  exit 75
fi
{model_storage}
echo PY_RUNTIME_START
{py}
{hub}
echo HF_START
_diony_python - <<'PY'
from concurrent.futures import ThreadPoolExecutor, as_completed
from huggingface_hub import hf_hub_download, snapshot_download
import os
import sys
PACK = {pack}
try:
    pull_workers = min(4, max(1, int(os.environ.get("DIONY_MODEL_PULL_WORKERS", "2"))))
    file_workers = min(8, max(1, int(os.environ.get("DIONY_HF_FILE_WORKERS", "4"))))
except ValueError:
    pull_workers, file_workers = 2, 4

import threading
import time

def _completed_files(dest):
    count = 0
    for root, dirs, files in os.walk(dest):
        dirs[:] = [d for d in dirs if d != ".cache"]
        count += sum(1 for name in files if not name.endswith(".incomplete"))
    return count

_active = {{}}
_active_lock = threading.Lock()

def _monitor():
    last = {{}}
    while True:
        time.sleep(float(os.environ.get("DIONY_HF_PROGRESS_SECONDS", "20")))
        with _active_lock:
            snapshot = dict(_active)
        if not snapshot:
            return
        for repo_id, (dest, expected) in snapshot.items():
            done = min(_completed_files(dest), expected) if expected else _completed_files(dest)
            if last.get(repo_id) != done:
                last[repo_id] = done
                total = expected or "?"
                print(f"HF_PROGRESS {{repo_id}} {{done}}/{{total}}", flush=True)

def pull(item):
    repo_id, dest, filename, revision, expected_files, _expected_bytes = item
    print(f"HF_PULL {{repo_id}} -> {{dest}}", flush=True)
    with _active_lock:
        _active[repo_id] = (dest, 1 if filename else int(expected_files or 0))
    os.makedirs(dest, exist_ok=True)
    try:
        if filename:
            hf_hub_download(
                repo_id=repo_id,
                filename=filename,
                revision=revision,
                local_dir=dest,
            )
        else:
            snapshot_download(
                repo_id=repo_id,
                revision=revision,
                local_dir=dest,
                max_workers=file_workers,
            )
    except Exception as exc:
        print(f"HF_FAIL {{repo_id}}: {{exc}}", file=sys.stderr, flush=True)
        raise
    finally:
        with _active_lock:
            _active.pop(repo_id, None)
    return repo_id

threading.Thread(target=_monitor, name="diony-hf-progress", daemon=True).start()
with ThreadPoolExecutor(max_workers=min(pull_workers, len(PACK))) as pool:
    futures = [pool.submit(pull, item) for item in PACK]
    for future in as_completed(futures):
        repo_id = future.result()
        print(f"HF_DONE {{repo_id}}", flush=True)
print("HF_OK", flush=True)
PY
echo MONTAGE_PACK_START
if [ "${{DIONY_MONTAGE_PACK:-1}}" != "0" ]; then
_diony_python - <<'PY' || echo MONTAGE_PACK_SKIPPED
# Blueprint 24 optional lanes: best-effort, never a pack failure. A lane whose
# weights did not land is simply unmeasured on this site.
from huggingface_hub import snapshot_download
import os
import sys
PACK = {montage_pack}
for repo_id, dest, revision, ignore, _files, _bytes in PACK:
    print(f"HF_PULL {{repo_id}} -> {{dest}}", flush=True)
    os.makedirs(dest, exist_ok=True)
    try:
        snapshot_download(repo_id=repo_id, revision=revision, local_dir=dest, ignore_patterns=ignore or None, max_workers=4)
        print(f"HF_DONE {{repo_id}}", flush=True)
    except Exception as exc:
        print(f"HF_OPTIONAL_FAIL {{repo_id}}: {{exc}}", file=sys.stderr, flush=True)
print("MONTAGE_PACK_OK", flush=True)
PY
echo FACE_PACK_START
_diony_face_pull() {{
  local url="$1" dest="$2" sha="$3"
  mkdir -p "$(dirname "$dest")"
  if [ -f "$dest" ] && [ "$(sha256sum "$dest" | cut -d' ' -f1)" = "$sha" ]; then
    echo "FACE_PACK_PRESENT $dest"; return 0
  fi
  if curl -fsSL --retry 3 -o "$dest.part" "$url" && [ "$(sha256sum "$dest.part" | cut -d' ' -f1)" = "$sha" ]; then
    mv -f "$dest.part" "$dest"; echo "FACE_PACK_DONE $dest"; return 0
  fi
  rm -f "$dest.part"; echo "FACE_PACK_OPTIONAL_FAIL $dest" >&2; return 0
}}
{face_pulls}
fi
{dedupe}
echo WAN_RUNTIME_START
test -d /opt/wan22/.git || git clone --depth 1 https://github.com/Wan-Video/Wan2.2.git /opt/wan22
_diony_pip install -e "$DIONY_REPO/packages/contracts" -e "$DIONY_REPO/apps/wan" -e "$DIONY_REPO/apps/finish" -e "$DIONY_REPO/apps/producer" -e "$DIONY_REPO/apps/wizard"
_diony_python -c "from pathlib import Path; from diony_wan.runner import ensure_attention_fallback, ensure_optional_engine_lazy_imports; ensure_attention_fallback(Path('/opt/wan22')); ensure_optional_engine_lazy_imports(Path('/opt/wan22'))"
_diony_host_wan_pip
echo SPEECH_PACK_START
# Wan2.2's own S2V speech lane (generate.py --enable_tts): WanS2V.load_tts loads
# ./CosyVoice and ./CosyVoice2-0.5B relative to generate.py and downloads both
# when absent. Pin them on the vault and link them where Wan looks.
WAN_TTS_RUNTIME={WAN_TTS_RUNTIME_DIR}
WAN_TTS_ROOT="$WAN_TTS_RUNTIME/CosyVoice"
mkdir -p "$WAN_TTS_RUNTIME"
if [ ! -d "$WAN_TTS_ROOT/.git" ]; then
  if [ -d {LEGACY_COSYVOICE_CHECKOUT}/.git ]; then
    rsync -a -- {LEGACY_COSYVOICE_CHECKOUT}/ "$WAN_TTS_ROOT/"
  elif [ -d {WAN_TTS_CHECKOUT_LINK}/.git ] && [ ! -L {WAN_TTS_CHECKOUT_LINK} ]; then
    rsync -a -- {WAN_TTS_CHECKOUT_LINK}/ "$WAN_TTS_ROOT/"
  else
    git clone --recursive https://github.com/FunAudioLLM/CosyVoice.git "$WAN_TTS_ROOT"
  fi
fi
test -d "$WAN_TTS_ROOT/.git"
# Pin the checkout: a clone-day `main` drifted the prompt-clip API under Wan2.2.
if [ "$(git -C "$WAN_TTS_ROOT" rev-parse HEAD 2>/dev/null)" != "{WAN_TTS_CHECKOUT_REV}" ]; then
  git -C "$WAN_TTS_ROOT" fetch --depth 1 origin {WAN_TTS_CHECKOUT_REV} 2>/dev/null || git -C "$WAN_TTS_ROOT" fetch origin
  git -C "$WAN_TTS_ROOT" checkout --quiet {WAN_TTS_CHECKOUT_REV} && git -C "$WAN_TTS_ROOT" submodule update --init --recursive --depth 1 2>/dev/null || true
fi
if [ -d {WAN_TTS_CHECKOUT_LINK} ] && [ ! -L {WAN_TTS_CHECKOUT_LINK} ]; then
  rm -rf -- {WAN_TTS_CHECKOUT_LINK}
fi
ln -sfn "$WAN_TTS_ROOT" {WAN_TTS_CHECKOUT_LINK}
WAN_TTS_OVERLAY="$_diony_repo/infra/runtime/wan-tts-overlay.txt"
[ -f "$WAN_TTS_OVERLAY" ] || {{ echo "SPEECH_OVERLAY_MISSING $WAN_TTS_OVERLAY" >&2; exit 14; }}
_diony_wan_tts_ready() {{
  # The interpreter that runs generate.py must import Wan's TTS stack itself.
  PYTHONPATH="/opt/wan22:$WAN_TTS_ROOT:$WAN_TTS_ROOT/third_party/Matcha-TTS" "$DIONY_PYTHON" - <<'PY' 2>&1
import importlib.util
import sys
missing = [name for name in ("transformers", "diffusers", "x_transformers", "hyperpyyaml", "onnxruntime", "modelscope", "librosa", "soundfile") if importlib.util.find_spec(name) is None]
if missing:
    print("SPEECH_MISSING " + " ".join(missing))
    sys.exit(1)
from cosyvoice.cli.cosyvoice import CosyVoice2  # noqa: F401
PY
}}
if ! _diony_wan_tts_ready >/dev/null 2>&1; then
  echo SPEECH_PACK_INSTALL
  PIP_BREAK_SYSTEM_PACKAGES=1 PIP_ROOT_USER_ACTION=ignore \
    "$DIONY_PYTHON" -m pip install --disable-pip-version-check \
      --upgrade-strategy only-if-needed -r /opt/wan22/requirements_s2v.txt -r "$WAN_TTS_OVERLAY"
fi
if ! _diony_wan_tts_ready; then
  echo "SPEECH_IMPORT_FAILED the Wan host interpreter cannot import cosyvoice after installing requirements_s2v.txt + wan-tts-overlay.txt" >&2
  exit 15
fi
_diony_python - <<'PY'
import os
from huggingface_hub import snapshot_download
repo_id, dest, revision, _expected_files, _expected_bytes = {tts_model}
os.makedirs(dest, exist_ok=True)
required = ("cosyvoice2.yaml", "llm.pt", "flow.pt", "hift.pt", "speech_tokenizer_v2.onnx")
if not all(os.path.isfile(os.path.join(dest, name)) for name in required):
    print(f"SPEECH_PACK_PULL {{repo_id}} -> {{dest}}", flush=True)
    snapshot_download(repo_id=repo_id, revision=revision, local_dir=dest, max_workers=4)
missing = [name for name in required if not os.path.isfile(os.path.join(dest, name))]
if missing:
    raise RuntimeError(f"Wan speech model snapshot incomplete: {{', '.join(missing)}}")
print("SPEECH_PACK_OK", flush=True)
PY
if [ -d {WAN_TTS_MODEL_LINK} ] && [ ! -L {WAN_TTS_MODEL_LINK} ]; then
  rm -rf -- {WAN_TTS_MODEL_LINK}
fi
ln -sfn {WAN_TTS_MODEL[1]} {WAN_TTS_MODEL_LINK}
mkdir -p {prompt_library_dir}
_diony_cast_count=0
{prompt_library_lines}
echo "SPEECH_CAST $_diony_cast_count"
test -d /models/wan/Wan2.2-T2V-A14B
test -e /models/diony/diony_sr_v1/RealESRGAN_x2.pth || ls /models/diony/diony_sr_v1/*.pth >/dev/null
exec 9>&-
echo PRODUCER_START
if ! pgrep -f "diony-producer" >/dev/null 2>&1; then
  nohup env PATH="$PATH" DIONY_VAULT=/diony/vault DIONY_BIND=0.0.0.0 DIONY_WAN_REPO=/opt/wan22 \\
    DIONY_PYTHON="${{DIONY_PYTHON:-/usr/bin/python3}}" \\
    DIONY_WAN_URL=http://127.0.0.1:8010 DIONY_FINISH_URL=http://127.0.0.1:8020 \\
    DIONY_DRAIN_BRAIN='pkill -f "vllm serve" || true' \\
    DIONY_DRAIN_WAN='pkill -f diony-wan || true' \\
    diony-producer >/diony/vault/.cluster/producer.log 2>&1 &
fi
echo APPLY_OK
"""


def producer_only_script(repo: str, nas: str = "") -> str:
    """Desk Producer line: install + start. Pack pull stays in How / App."""
    quoted_repo = shlex.quote(repo)
    mount = "\n".join(vault_mount_lines(nas))
    py = _python_bootstrap(repo)
    model_storage = _persistent_model_storage()
    return f"""
set -euo pipefail
unset DASH_API_KEY DASHSCOPE_API_KEY
export DIONY_VAULT=/diony/vault
export DIONY_BIND=0.0.0.0
export DIONY_WAN_REPO=/opt/wan22
export PYTHONPATH=/opt/wan22${{PYTHONPATH:+:$PYTHONPATH}}
export DIONY_REPO={quoted_repo}
{mount}
mkdir -p /diony/vault/.cluster
{model_storage}
{py}
_diony_pip install -e "$DIONY_REPO/packages/contracts" -e "$DIONY_REPO/apps/wan" -e "$DIONY_REPO/apps/finish" -e "$DIONY_REPO/apps/producer"
_diony_python -c "from pathlib import Path; from diony_wan.runner import ensure_attention_fallback, ensure_optional_engine_lazy_imports; ensure_attention_fallback(Path('/opt/wan22')); ensure_optional_engine_lazy_imports(Path('/opt/wan22'))"
_diony_host_wan_pip
if ! pgrep -f "diony-producer" >/dev/null 2>&1; then
  nohup env PATH="$PATH" DIONY_VAULT=/diony/vault DIONY_BIND=0.0.0.0 DIONY_WAN_REPO=/opt/wan22 \\
    DIONY_PYTHON="${{DIONY_PYTHON:-/usr/bin/python3}}" \\
    DIONY_WAN_URL=http://127.0.0.1:8010 DIONY_FINISH_URL=http://127.0.0.1:8020 \\
    DIONY_DRAIN_BRAIN='pkill -f "vllm serve" || true' \\
    DIONY_DRAIN_WAN='pkill -f diony-wan || true' \\
    diony-producer >/diony/vault/.cluster/producer.log 2>&1 &
fi
echo APPLY_OK
"""


def site_lock_script(payload: dict) -> str:
    body = json.dumps(payload, indent=2)
    return f"""
set -euo pipefail
mkdir -p /diony/vault/.cluster
cat > /diony/vault/.cluster/site.json <<'JSON'
{body}
JSON
echo LOCK_OK
"""


def replicate_script(host: str = "spark-02") -> str:
    user = ssh_user()
    return f"""
set -euo pipefail
if [ -d /models/wan ]; then
  rsync -a /models/ {user}@{host}:/models/ || rsync -a /models/ {host}:/models/ || true
fi
echo REPLICATE_DONE
"""


def reset_apply() -> None:
    global _apply_epoch
    with _lock:
        _apply_epoch += 1
    # Stop the previous streamed setup before a new one can start on the host.
    terminate_streaming()
    with _lock:
        _job.update(
            {
                "running": False,
                "ok": False,
                "error": "",
                "detail": "",
                "host": "",
                "remote": False,
                "started_at": "",
                "prepared": False,
                "pull_skipped": False,
                "stages": [],
                "percent": None,
                "stage": "",
            }
        )
        _job["log"] = []
    _persist()


def _fake_ok(hosts: list[str], detail: str) -> dict:
    with _lock:
        stages = setup_stage_plan()
        for stage in stages:
            stage["status"] = "done"
            stage["percent"] = 100
            for item in stage.get("items") or []:
                item["status"] = "done"
                item["percent"] = 100
                item["done"] = item.get("total") or 0
        _job.update(
            {
                "running": False,
                "ok": True,
                "error": "",
                "detail": detail,
                "host": hosts[0] if hosts else "",
                "prepared": True,
                "pull_skipped": False,
                "stages": stages,
                "percent": 100,
                "stage": "",
            }
        )
        _job["log"] = [detail]
    _persist()
    return public_apply()


def _apply_fake() -> bool:
    return os.environ.get("DIONY_APPLY_FAKE", "").strip().lower() in {"1", "true", "yes"}


def operator_ssh(path: str | None, workbook: dict | None = None) -> bool:
    """Laptop with a real GPU IP may SSH even when desk set DIONY_DRY_RUN=1.

    Pytest never does. Placeholder names (b200-01) never do.
    """
    from diony_wizard.prove import first_host, in_pytest, looks_like_ip

    if in_pytest() or _apply_fake():
        return False
    if os.environ.get("DIONY_APPLY_OFF", "").strip().lower() in {"1", "true", "yes"}:
        return False
    host = first_host(workbook or {}, path or "")
    return looks_like_ip(host)


def apply_allowed(
    path: str | None,
    workbook: dict | None = None,
    hosts: list[str] | None = None,
) -> bool:
    if path not in {"dual", "b300", "b200"}:
        return False
    if _apply_fake() or wizard_live():
        return True
    book = dict(workbook or {})
    if hosts and not str((book.get("connect") or {}).get("hosts") or "").strip():
        connect = dict(book.get("connect") or {})
        connect["hosts"] = hosts[0]
        book["connect"] = connect
    return operator_ssh(path, book)


def should_apply(path: str | None, workbook: dict | None = None) -> bool:
    return apply_allowed(path, workbook)


def hf_token() -> str:
    """The Diony-Enterprise HF Token. Always the backend's own credential."""
    return enterprise_hf_token()


def hf_token_set() -> bool:
    return bool(hf_token())


def load_hf_token() -> None:
    """Export the Diony-Enterprise HF Token for in-process huggingface_hub calls. Never logs it."""
    token = hf_token()
    if not token:
        return
    os.environ["HF_TOKEN"] = token
    os.environ["HUGGING_FACE_HUB_TOKEN"] = token


def bind_hf_from_answers(answers: dict | None) -> dict:
    """Strip any pasted hf_token so workbook / site.json never store one. The backend token is used regardless."""
    data = dict(answers or {})
    data.pop("hf_token", None)
    return data


def should_start_producer(path: str | None) -> bool:
    """Desk crate: Sign starts Producer when this Studio is live on spark-01."""
    return wizard_live() and path == "local"


def write_remote_site_lock(host: str, payload: dict) -> None:
    """Copy Sign onto the GPU vault so Producer on that box reads the same lock."""
    from diony_wizard.prove import looks_like_ip

    if _apply_fake() or not host:
        return
    if not wizard_live() and not looks_like_ip(host):
        return
    result = exec_host(host, site_lock_script(payload), timeout=60)
    if not result.ok or "LOCK_OK" not in (result.stdout or ""):
        _log(f"site.json on {host} failed: {(result.text or '')[-400:]}")
        return
    _log(f"Wrote site.json on {host}:/diony/vault/.cluster/site.json")


def retry_apply(path: str, hosts: list[str], *, nas: str = "", workbook: dict | None = None) -> dict:
    with _lock:
        if _job["running"]:
            return _snapshot_locked()
        _job["ok"] = False
        _job["error"] = ""
        _job["remote"] = False
    return ensure_apply(path, hosts, nas=nas, workbook=workbook)


def ensure_apply(
    path: str,
    hosts: list[str],
    *,
    nas: str = "",
    workbook: dict | None = None,
    existing: bool = False,
) -> dict:
    """Start pack apply once. Safe to call on App Continue or Ready Pull pack."""
    existing_local = existing and path == "local" and bool(hosts)
    if not apply_allowed(path, workbook, hosts) and not _apply_fake() and not existing_local:
        return public_apply()
    if _apply_fake():
        return _fake_ok(hosts, "fake apply")
    with _lock:
        if _job["ok"] or _job["running"]:
            return _snapshot_locked()
        epoch = _apply_epoch
        _job["running"] = True
        _job["ok"] = False
        _job["error"] = ""
        _job["host"] = hosts[0] if hosts else "spark-01"
        _job["log"] = []
        _job["remote"] = not _apply_fake()
        _job["started_at"] = datetime.now(timezone.utc).isoformat()
        _job["prepared"] = False
        _job["pull_skipped"] = False
        _job["stages"] = setup_stage_plan()
        _job["percent"] = 0
        _job["stage"] = ""
    thread = threading.Thread(
        target=_run_apply,
        args=(path, hosts, nas, epoch),
        name="diony-apply",
        daemon=True,
    )
    thread.start()
    return public_apply()


def ensure_producer(path: str, hosts: list[str], *, nas: str = "") -> dict:
    """Local Producer line: start diony-producer without re-pulling the pack."""
    if not wizard_live() and not _apply_fake():
        return public_apply()
    if _apply_fake():
        return _fake_ok(hosts, "fake producer start")
    with _lock:
        if _job["running"]:
            return _snapshot_locked()
        if _job["ok"] and "producer" in str(_job.get("detail") or ""):
            return _snapshot_locked()
        epoch = _apply_epoch
        _job["running"] = True
        _job["ok"] = False
        _job["error"] = ""
        _job["host"] = hosts[0] if hosts else "spark-01"
        _job["log"] = []
        _job["started_at"] = datetime.now(timezone.utc).isoformat()
        _job["stages"] = []
        _job["percent"] = None
        _job["stage"] = ""
    thread = threading.Thread(
        target=_run_producer,
        args=(path, hosts, nas, epoch),
        name="diony-producer-start",
        daemon=True,
    )
    thread.start()
    return public_apply()


def apply_exception_text(exc: Exception) -> str:
    """Return an actionable public error without exposing an SSH argv or key path."""
    if isinstance(exc, subprocess.TimeoutExpired):
        seconds = int(float(exc.timeout))
        return (
            "The compute connection was recorded, but the read-only readiness check did not "
            f"finish within {seconds} seconds. Diony did not stop or restart Producer. "
            "Retry validation when the active workload has settled."
        )
    return str(exc)[:2000]


def _run_producer(path: str, hosts: list[str], nas: str, epoch: int) -> None:
    primary = hosts[0] if hosts else "spark-01"
    repo = os.environ.get("DIONY_REPO", "").strip() or (
        str(repo_root()) if apply_local(primary) else "/opt/diony-ai"
    )
    try:
        if not _still(epoch):
            return
        _log(f"Start Producer on {primary}", epoch)
        timeout = int(os.environ.get("DIONY_APPLY_TIMEOUT", "600"))
        result = exec_host(primary, producer_only_script(repo, nas), timeout=timeout)
        if not _still(epoch):
            return
        if not result.ok or "APPLY_OK" not in (result.stdout or ""):
            raise RuntimeError(result.text[-2000:] or f"producer start exited {result.code}")
        _log("Producer is up. Sign writes site.json, then open it.", epoch)
        _commit_job(epoch, ok=True, running=False, error="", detail="producer started")
    except Exception as exc:  # noqa: BLE001
        err = apply_exception_text(exc)
        if not _commit_job(epoch, ok=False, running=False, error=err, detail=err):
            return
        _log(f"FAIL {err}", epoch)


def _run_apply(path: str, hosts: list[str], nas: str, epoch: int) -> None:
    primary = hosts[0] if hosts else "spark-01"
    repo = os.environ.get("DIONY_REPO", "").strip() or (
        str(repo_root()) if apply_local(primary) else "/opt/diony-ai"
    )
    try:
        if not _still(epoch):
            return
        _log("Check Site before any model pull", epoch)
        _stage(epoch, "site", "active", "Read-only readiness probe over SSH")
        readiness: list[dict] = []
        for host in hosts or [primary]:
            result = exec_host(host, site_readiness_script(repo), timeout=90)
            readiness.append(parse_site_readiness(result))
        primary_state = readiness[0] if readiness else {}
        _stage(epoch, "site", "done", "Site inspected")
        if primary_state.get("prepared"):
            _log("Site already prepared. Model pull skipped.", epoch)
            for stage_id, detail in (
                ("storage", "Already on the vault"),
                ("python", "Already installed"),
                ("pack", "Already on the vault"),
                ("dedupe", "Already consolidated"),
                ("wan", "Already installed"),
                ("cosyvoice", "Already installed"),
            ):
                _stage(epoch, stage_id, "skipped", detail)
            if primary_state.get("producer_running") and not primary_state.get("wan_runtime"):
                raise RuntimeError(
                    "Producer is active, but its last verified Wan runtime receipt is missing or invalid. "
                    "Finish or stop the active production, then retry validation. Diony did not interrupt Producer."
                )
            if not primary_state.get("producer_running"):
                if not apply_local(primary):
                    _stage(epoch, "sync", "active", f"rsync → {primary}")
                    primed = exec_host(primary, f"mkdir -p {shlex.quote(repo)}", timeout=30)
                    if not primed.ok:
                        raise RuntimeError(
                            ssh_connect_advice(primary, ssh_port(), primed.text)
                            or f"SSH to {primary}:{ssh_port()} failed. {primed.text[-800:]}"
                        )
                    synced = rsync_repo(primary, str(repo_root()), repo)
                    if not synced.ok:
                        raise RuntimeError(
                            ssh_connect_advice(primary, ssh_port(), synced.text)
                            or f"rsync to {primary}:{ssh_port()} failed. {synced.text[-800:]}"
                        )
                _log("Repair host runtime and start Producer without pulling models", epoch)
                _stage(epoch, "producer", "active", "Starting Producer")
                started = exec_host(primary, producer_only_script(repo, nas), timeout=600)
                if not started.ok or "APPLY_OK" not in (started.stdout or ""):
                    raise RuntimeError(started.text[-2000:] or "Could not start Producer")
            if path in {"dual", "local"}:
                for host, row in zip((hosts or [])[1:], readiness[1:]):
                    if row.get("prepared"):
                        continue
                    _log(f"Replicate the existing pack to {host}", epoch)
                    replicated = exec_host(primary, replicate_script(host), timeout=7200)
                    if not replicated.ok:
                        raise RuntimeError(replicated.text[-1200:] or f"Could not replicate models to {host}")
            _finish_stages(epoch)
            _commit_job(
                epoch,
                ok=True,
                running=False,
                error="",
                detail="Site ready. Model pull skipped.",
                prepared=True,
                pull_skipped=True,
            )
            return
        _log(f"Apply on {primary} as {ssh_user()} port {ssh_port()}", epoch)
        if not hf_token_set():
            _log("Diony-Enterprise HF Token is empty. Gated Hugging Face pulls will fail.", epoch)
        if not apply_local(primary):
            _log(f"Sync repo → {primary}:{repo}", epoch)
            _stage(epoch, "sync", "active", f"rsync → {primary}:{repo}")
            primed = exec_host(primary, f"mkdir -p {shlex.quote(repo)} /models /workspace", timeout=30)
            if not _still(epoch):
                return
            if not primed.ok:
                raise RuntimeError(
                    ssh_connect_advice(primary, ssh_port(), primed.text)
                    or f"SSH to {primary}:{ssh_port()} failed. {primed.text[-800:]}"
                )
            synced = rsync_repo(primary, str(repo_root()), repo)
            if not _still(epoch):
                return
            if not synced.ok:
                raise RuntimeError(
                    ssh_connect_advice(primary, ssh_port(), synced.text)
                    or (
                        f"rsync to {primary}:{ssh_port()} failed ({synced.code}). "
                        f"Set DIONY_SSH_KEY, DIONY_SSH_USER, and DIONY_SSH_PORT "
                        f"(RunPod TCP -p). {synced.text[-800:]}"
                    )
                )
        if not _still(epoch):
            return
        _log("Mount vault, pull pack + Wan2.2 + diony_sr_v1, install, start Producer", epoch)
        _stage(epoch, "sync", "done", "Repo on the host")
        timeout = int(os.environ.get("DIONY_APPLY_TIMEOUT", "21600"))
        def progress(line: str) -> None:
            _stage_line(line, epoch)
            detail = apply_progress_text(line)
            if detail:
                _log(detail, epoch)

        result = exec_host(
            primary,
            remote_setup_script(repo, nas),
            timeout=timeout,
            on_output=progress,
        )
        if not _still(epoch):
            return
        if not result.ok or "APPLY_OK" not in (result.stdout or ""):
            raise RuntimeError(
                ssh_connect_advice(primary, ssh_port(), result.text)
                or apply_fail_text(result)
            )
        if path in {"dual", "local"} and len(hosts) > 1:
            for host in hosts[1:]:
                _log(f"Replicate /models to {host}", epoch)
                replicated = exec_host(primary, replicate_script(host), timeout=7200)
                if not replicated.ok:
                    raise RuntimeError(replicated.text[-1200:] or f"Could not replicate models to {host}")
            if not _still(epoch):
                return
        _log("Pack applied. Test GPU on Ready.", epoch)
        _finish_stages(epoch)
        _commit_job(
            epoch,
            ok=True,
            running=False,
            error="",
            prepared=True,
            pull_skipped=False,
        )
    except Exception as exc:  # noqa: BLE001 — apply must surface the miss
        err = apply_exception_text(exc)
        _fail_stages(epoch, err)
        if not _commit_job(epoch, ok=False, running=False, error=err, detail=err):
            return
        _log(f"FAIL {err}", epoch)
        _persist()
