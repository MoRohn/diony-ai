"""Live Dual-Spark / B300 / B200 App: pull the pinned pack, clone Wan2.2, start Producer.

Pytest (DIONY_DRY_RUN=1) never SSHes. On spark-01, commands run locally.
From a laptop, SSH BatchMode as DIONY_SSH_USER on DIONY_SSH_PORT (default 22).
"""

from __future__ import annotations

import json
import os
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

COSYVOICE_MODEL = (
    "FunAudioLLM/CosyVoice2-0.5B",
    "/diony/vault/models/audio/CosyVoice2-0.5B",
    "eec1ae6c79877dbd9379285cf8789c9e0879293d",
    19,
    4856505002,
)

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
    expected_snapshots[COSYVOICE_MODEL[1]] = COSYVOICE_MODEL[2:]
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
cosy_model = Path("/diony/vault/models/audio/CosyVoice2-0.5B")
cosy_required = (
    "cosyvoice2.yaml",
    "llm.pt",
    "flow.pt",
    "hift.pt",
    "speech_tokenizer_v2.onnx",
)
cosy_ready = all((cosy_model / name).is_file() for name in cosy_required)
volume_root = Path("/diony/vault/models").resolve()
model_volume = all(
    (Path("/models") / kind).resolve() == volume_root / kind
    for kind in ("deepseek", "wan", "diony", "audio")
)
pack = snapshot_complete and snapshot_pinned and (
    (sr / "RealESRGAN_x2.pth").is_file() or bool(list(sr.glob("*.pth")))
) and cosy_ready
wan_repo = Path("/opt/wan22/.git").is_dir()
cosy_root = Path("/diony/vault/runtime/cosyvoice/CosyVoice")
cosy_python = Path("/diony/vault/runtime/cosyvoice/venv/bin/python")
cosy_repo = (cosy_root / ".git").is_dir()
cosy_runtime = all((cosy_root / relative).exists() for relative in (
    "cosyvoice/cli/cosyvoice.py",
    "third_party/Matcha-TTS",
)) and cosy_repo and cosy_python.is_file()
voice_backend = (
    repo / "apps" / "producer" / "src" / "diony_producer" / "cosyvoice_backend.py"
).is_file()
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
prepared = pack and model_volume and wan_repo and cosy_runtime and voice_backend
print("DIONY_SITE_STATE " + json.dumps({{
    "prepared": prepared,
    "ready": prepared and wan_runtime and producer_installed and producer_running,
    "pack": pack,
    "snapshot_complete": snapshot_complete,
    "snapshot_pinned": snapshot_pinned,
    "optional_engines": optional_engines,
    "model_volume": model_volume,
    "wan_repo": wan_repo,
    "cosy_repo": cosy_repo,
    "cosy_runtime": cosy_runtime,
    "voice_backend": voice_backend,
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


def apply_fail_text(result) -> str:
    """Operator excerpt. Do not treat pip 'already satisfied' as the miss."""
    out = getattr(result, "stdout", "") or ""
    err = getattr(result, "stderr", "") or ""
    code = getattr(result, "code", None)
    blob = f"{out}\n{err}".strip()
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
    pip_noise = "Requirement already satisfied" in blob or (
        "huggingface_hub" in blob and "site-packages" in blob and "Traceback" not in blob
    )
    if pip_noise and "APPLY_OK" not in out and "HF_OK" not in out:
        return (
            "Pack pull stopped at pip. huggingface_hub is already on this GPU. "
            "Retry Pull pack. Studio now skips that pip step and starts Hugging Face."
        )
    for marker in ("Traceback (most recent call last):", "\nERROR:", "\nError:"):
        idx = blob.rfind(marker)
        if idx >= 0:
            return blob[idx:][-2000:]
    tail = blob[-2000:] or (f"apply exited {code}" if code is not None else "apply failed")
    return tail


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
    stages = {
        "HF_START": "Checking the pinned model pack",
        "HF_OK": "Pinned model pack ready",
        "MODEL_VOLUME_START": "Preparing attached-volume model storage",
        "MODEL_VOLUME_OK": "Attached-volume model storage ready",
        "PACK_DEDUPE_START": "Consolidating shared model assets",
        "PACK_DEDUPE_OK": "Shared model assets ready",
        "COSYVOICE_RUNTIME_INSTALL": "Installing the CosyVoice2 runtime",
        "COSYVOICE_OK": "CosyVoice2 voice model ready",
        "WAN_HOST_OVERLAY_REPAIR": "Repairing the Wan host runtime",
        "WAN_HOST_PYTHON_OK": "Wan host runtime ready",
        "APPLY_OK": "Compute setup complete",
    }
    if clean.startswith("COSYVOICE_PULL "):
        return "Downloading CosyVoice2 voice model"
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
    cosy_model = repr(COSYVOICE_MODEL)
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
mkdir -p /models/deepseek /models/wan /models/diony /opt
{mount}
mkdir -p /diony/vault/.cluster
{model_storage}
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

def pull(item):
    repo_id, dest, filename, revision, _expected_files, _expected_bytes = item
    print(f"HF_PULL {{repo_id}} -> {{dest}}", flush=True)
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
    return repo_id

with ThreadPoolExecutor(max_workers=min(pull_workers, len(PACK))) as pool:
    futures = [pool.submit(pull, item) for item in PACK]
    for future in as_completed(futures):
        repo_id = future.result()
        print(f"HF_DONE {{repo_id}}", flush=True)
print("HF_OK", flush=True)
PY
{dedupe}
test -d /opt/wan22/.git || git clone --depth 1 https://github.com/Wan-Video/Wan2.2.git /opt/wan22
_diony_pip install -e "$DIONY_REPO/packages/contracts" -e "$DIONY_REPO/apps/wan" -e "$DIONY_REPO/apps/finish" -e "$DIONY_REPO/apps/producer" -e "$DIONY_REPO/apps/wizard"
_diony_python -c "from pathlib import Path; from diony_wan.runner import ensure_attention_fallback, ensure_optional_engine_lazy_imports; ensure_attention_fallback(Path('/opt/wan22')); ensure_optional_engine_lazy_imports(Path('/opt/wan22'))"
_diony_host_wan_pip
COSY_RUNTIME=/diony/vault/runtime/cosyvoice
COSY_ROOT="$COSY_RUNTIME/CosyVoice"
COSY_PY="$COSY_RUNTIME/venv/bin/python"
mkdir -p "$COSY_RUNTIME"
if [ ! -d "$COSY_ROOT/.git" ]; then
  if [ -d /opt/wan22/CosyVoice/.git ] && [ ! -L /opt/wan22/CosyVoice ]; then
    rsync -a -- /opt/wan22/CosyVoice/ "$COSY_ROOT/"
  else
    git clone --depth 1 --recursive https://github.com/FunAudioLLM/CosyVoice.git "$COSY_ROOT"
  fi
fi
test -d "$COSY_ROOT/.git"
if [ -d /opt/wan22/CosyVoice ] && [ ! -L /opt/wan22/CosyVoice ]; then
  rm -rf -- /opt/wan22/CosyVoice
fi
ln -sfn "$COSY_ROOT" /opt/wan22/CosyVoice
if [ ! -x "$COSY_PY" ]; then
  "$_DIONY_HOST_PYTHON" -m venv --system-site-packages "$COSY_RUNTIME/venv"
fi
if ! PYTHONPATH="$COSY_ROOT:$COSY_ROOT/third_party/Matcha-TTS" "$COSY_PY" -c "from cosyvoice.cli.cosyvoice import CosyVoice2; import modelscope" >/dev/null 2>&1; then
  echo COSYVOICE_RUNTIME_INSTALL
  PIP_BREAK_SYSTEM_PACKAGES=1 PIP_ROOT_USER_ACTION=ignore \
    "$COSY_PY" -m pip install --disable-pip-version-check \
      --upgrade-strategy only-if-needed -r /opt/wan22/requirements_s2v.txt
fi
PYTHONPATH="$COSY_ROOT:$COSY_ROOT/third_party/Matcha-TTS" "$COSY_PY" -c "from cosyvoice.cli.cosyvoice import CosyVoice2; import modelscope"
export DIONY_COSYVOICE_PYTHON="$COSY_PY"
export DIONY_COSYVOICE_ROOT="$COSY_ROOT"
export DIONY_COSYVOICE_MODEL=/diony/vault/models/audio/CosyVoice2-0.5B
export DIONY_COSYVOICE_PROMPT_AUDIO=/opt/wan22/examples/zero_shot_prompt.wav
export DIONY_VOICE_BACKEND_COMMAND="$_diony_venv/bin/python $_diony_repo/apps/producer/src/diony_producer/cosyvoice_backend.py"
export DIONY_SPEAKER_EMBEDDING_MODEL=cosyvoice-campplus-v1
_diony_python - <<'PY'
import os
from huggingface_hub import snapshot_download
repo_id, dest, revision, _expected_files, _expected_bytes = {cosy_model}
os.makedirs(dest, exist_ok=True)
required = ("cosyvoice2.yaml", "llm.pt", "flow.pt", "hift.pt", "speech_tokenizer_v2.onnx")
if not all(os.path.isfile(os.path.join(dest, name)) for name in required):
    print(f"COSYVOICE_PULL {{repo_id}} -> {{dest}}", flush=True)
    snapshot_download(repo_id=repo_id, revision=revision, local_dir=dest, max_workers=4)
missing = [name for name in required if not os.path.isfile(os.path.join(dest, name))]
if missing:
    raise RuntimeError(f"CosyVoice snapshot incomplete: {{', '.join(missing)}}")
print("COSYVOICE_OK", flush=True)
PY
test -d /models/wan/Wan2.2-T2V-A14B
test -e /models/diony/diony_sr_v1/RealESRGAN_x2.pth || ls /models/diony/diony_sr_v1/*.pth >/dev/null
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
            }
        )
        _job["log"] = []
    _persist()


def _fake_ok(hosts: list[str], detail: str) -> dict:
    with _lock:
        _job.update(
            {
                "running": False,
                "ok": True,
                "error": "",
                "detail": detail,
                "host": hosts[0] if hosts else "",
                "prepared": True,
                "pull_skipped": False,
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
        readiness: list[dict] = []
        for host in hosts or [primary]:
            result = exec_host(host, site_readiness_script(repo), timeout=90)
            readiness.append(parse_site_readiness(result))
        primary_state = readiness[0] if readiness else {}
        if primary_state.get("prepared"):
            _log("Site already prepared. Model pull skipped.", epoch)
            if primary_state.get("producer_running") and not primary_state.get("wan_runtime"):
                raise RuntimeError(
                    "Producer is active, but its last verified Wan runtime receipt is missing or invalid. "
                    "Finish or stop the active production, then retry validation. Diony did not interrupt Producer."
                )
            if not primary_state.get("producer_running"):
                if not apply_local(primary):
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
        timeout = int(os.environ.get("DIONY_APPLY_TIMEOUT", "21600"))
        def progress(line: str) -> None:
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
        if not _commit_job(epoch, ok=False, running=False, error=err, detail=err):
            return
        _log(f"FAIL {err}", epoch)
        _persist()
