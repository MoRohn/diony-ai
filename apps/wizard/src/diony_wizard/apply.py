"""Live Dual-Spark / B300 / B200 App: pull the pinned pack, clone Wan2.2, start Producer.

Pytest (DIONY_DRY_RUN=1) never SSHes. On spark-01, commands run locally.
From a laptop, SSH BatchMode as DIONY_SSH_USER on DIONY_SSH_PORT (default 22).
"""

from __future__ import annotations

import json
import os
import shlex
import threading
from datetime import datetime, timezone
from pathlib import Path

from diony_contracts.cluster import cluster_root
from diony_contracts.live import wizard_live
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
    ("deepseek-ai/DeepSeek-V4-Flash-0731", "/models/deepseek/DeepSeek-V4-Flash-0731", ""),
    ("Wan-AI/Wan2.2-T2V-A14B", "/models/wan/Wan2.2-T2V-A14B", ""),
    ("Wan-AI/Wan2.2-I2V-A14B", "/models/wan/Wan2.2-I2V-A14B", ""),
    ("Wan-AI/Wan2.2-S2V-14B", "/models/wan/Wan2.2-S2V-14B", ""),
    ("Wan-AI/Wan2.2-Animate-14B", "/models/wan/Wan2.2-Animate-14B", ""),
    ("alibaba-pai/Wan2.2-VACE-Fun-A14B", "/models/wan/Wan2.2-VACE-Fun-A14B", ""),
    ("ai-forever/Real-ESRGAN", "/models/diony/diony_sr_v1", "RealESRGAN_x2.pth"),
]

COSYVOICE_MODEL = ("iic/CosyVoice2-0.5B", "/models/audio/CosyVoice2-0.5B")

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
    for repo, dest, filename in HF_PACK:
        lines.append(f"mkdir -p {dest}")
        if filename:
            lines.append(
                f"huggingface-cli download {repo} {filename} --local-dir {dest}"
            )
        else:
            lines.append(f"huggingface-cli download {repo} --local-dir {dest}")
    return lines


def site_readiness_script(repo: str) -> str:
    """Read-only remote probe used before setup or any model download."""
    quoted_repo = repr(repo)
    return f"""
python3 - <<'PY'
import json
import os
import shutil
import subprocess
from pathlib import Path

repo = Path({quoted_repo})

def populated(raw):
    path = Path(raw)
    try:
        return path.is_dir() and next(path.iterdir(), None) is not None
    except OSError:
        return False

required = [
    "/models/deepseek/DeepSeek-V4-Flash-0731",
    "/models/wan/Wan2.2-T2V-A14B",
    "/models/wan/Wan2.2-I2V-A14B",
    "/models/wan/Wan2.2-S2V-14B",
    "/models/wan/Wan2.2-Animate-14B",
    "/models/wan/Wan2.2-VACE-Fun-A14B",
]
sr = Path("/models/diony/diony_sr_v1")
cosy_model = Path("/models/audio/CosyVoice2-0.5B")
pack = all(populated(path) for path in required) and (
    (sr / "RealESRGAN_x2.pth").is_file() or bool(list(sr.glob("*.pth")))
) and populated(cosy_model)
wan_repo = Path("/opt/wan22/.git").is_dir()
cosy_repo = Path("/opt/wan22/CosyVoice/.git").is_dir()
runtime_probe = repo / "apps" / "wan" / "src" / "diony_wan" / "runtime.py"
host_python = Path("/usr/bin/python3")
runtime_env = os.environ.copy()
runtime_env["PYTHONPATH"] = "/opt/wan22"
wan_runtime = wan_repo and runtime_probe.is_file() and host_python.is_file() and subprocess.run(
    [str(host_python), str(runtime_probe)],
    stdout=subprocess.DEVNULL,
    stderr=subprocess.DEVNULL,
    env=runtime_env,
    check=False,
).returncode == 0
producer_installed = bool(shutil.which("diony-producer")) or (
    repo / ".venv" / "bin" / "diony-producer"
).is_file()
producer_running = subprocess.run(
    ["pgrep", "-f", "diony-producer"],
    stdout=subprocess.DEVNULL,
    stderr=subprocess.DEVNULL,
    check=False,
).returncode == 0
prepared = pack and wan_repo and cosy_repo
print("DIONY_SITE_STATE " + json.dumps({{
    "prepared": prepared,
    "ready": prepared and wan_runtime and producer_installed and producer_running,
    "pack": pack,
    "wan_repo": wan_repo,
    "cosy_repo": cosy_repo,
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
export DIONY_PYTHON="${{DIONY_PYTHON:-$_DIONY_HOST_PYTHON}}"
_diony_host_wan_pip() {{
  [ -f /opt/wan22/requirements.txt ] || return 0
  local probe="$_diony_repo/apps/wan/src/diony_wan/runtime.py"
  local overlay="$_diony_repo/infra/runtime/wan-host-overlay.txt"
  local report="/diony/vault/.cluster/wan-host-runtime.json"
  [ -f "$probe" ] || {{ echo "WAN_RUNTIME_PROBE_MISSING $probe" >&2; return 14; }}
  [ -f "$overlay" ] || {{ echo "WAN_RUNTIME_OVERLAY_MISSING $overlay" >&2; return 14; }}
  if PYTHONPATH=/opt/wan22 "$_DIONY_HOST_PYTHON" "$probe" --write "$report" >/dev/null 2>&1; then
    echo WAN_HOST_PYTHON_OK
    return 0
  fi
  echo WAN_HOST_OVERLAY_REPAIR
  PIP_BREAK_SYSTEM_PACKAGES=1 PIP_ROOT_USER_ACTION=ignore \\
    "$_DIONY_HOST_PYTHON" -m pip install --disable-pip-version-check \\
      --upgrade-strategy only-if-needed -r "$overlay"
  if PYTHONPATH=/opt/wan22 "$_DIONY_HOST_PYTHON" "$probe" --write "$report" >/dev/null 2>&1; then
    echo WAN_HOST_PYTHON_OK
    return 0
  fi
  grep -viE '^[[:space:]]*(flash_attn|numpy)|^[[:space:]]*#' /opt/wan22/requirements.txt > /tmp/diony-wan-req.txt || true
  PIP_BREAK_SYSTEM_PACKAGES=1 PIP_ROOT_USER_ACTION=ignore \\
    "$_DIONY_HOST_PYTHON" -m pip install --disable-pip-version-check \\
      --upgrade-strategy only-if-needed -r /tmp/diony-wan-req.txt -r "$overlay"
  PYTHONPATH=/opt/wan22 "$_DIONY_HOST_PYTHON" "$probe" --write "$report"
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


def _model_pack_dedupe() -> str:
    """Hard-link byte-identical shared Wan assets without changing model paths."""
    return """
echo PACK_DEDUPE_START
_diony_hardlink_identical() {
  local canonical="$1"
  shift
  [ -f "$canonical" ] || return 0
  local candidate pending
  for candidate in "$@"; do
    [ -f "$candidate" ] || continue
    [ "$canonical" -ef "$candidate" ] && continue
    cmp -s "$canonical" "$candidate" || continue
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


def remote_setup_script(repo: str, nas: str = "") -> str:
    token = os.environ.get("HF_TOKEN") or os.environ.get("HUGGING_FACE_HUB_TOKEN") or ""
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
{py}
{hub}
echo HF_START
_diony_python - <<'PY'
from huggingface_hub import hf_hub_download, snapshot_download
import os
import sys
PACK = {pack}
for item in PACK:
    repo_id, dest, filename = item
    print(f"HF_PULL {{repo_id}} -> {{dest}}", flush=True)
    os.makedirs(dest, exist_ok=True)
    try:
        if filename:
            hf_hub_download(repo_id=repo_id, filename=filename, local_dir=dest)
        else:
            snapshot_download(repo_id=repo_id, local_dir=dest)
    except Exception as exc:
        print(f"HF_FAIL {{repo_id}}: {{exc}}", file=sys.stderr, flush=True)
        raise
    print(f"HF_DONE {{repo_id}}", flush=True)
print("HF_OK", flush=True)
PY
{dedupe}
test -d /opt/wan22/.git || git clone --depth 1 https://github.com/Wan-Video/Wan2.2.git /opt/wan22
_diony_pip install -e "$DIONY_REPO/packages/contracts" -e "$DIONY_REPO/apps/wan" -e "$DIONY_REPO/apps/finish" -e "$DIONY_REPO/apps/producer" -e "$DIONY_REPO/apps/wizard"
_diony_python -c "from pathlib import Path; from diony_wan.runner import ensure_attention_fallback, ensure_optional_engine_lazy_imports; ensure_attention_fallback(Path('/opt/wan22')); ensure_optional_engine_lazy_imports(Path('/opt/wan22'))"
_diony_host_wan_pip
test -d /opt/wan22/CosyVoice/.git || git clone --depth 1 --recursive https://github.com/FunAudioLLM/CosyVoice.git /opt/wan22/CosyVoice
if ! PYTHONPATH=/opt/wan22/CosyVoice:/opt/wan22/CosyVoice/third_party/Matcha-TTS "$_DIONY_HOST_PYTHON" -c "from cosyvoice.cli.cosyvoice import CosyVoice2; import modelscope" >/dev/null 2>&1; then
  echo COSYVOICE_RUNTIME_INSTALL
  PIP_BREAK_SYSTEM_PACKAGES=1 PIP_ROOT_USER_ACTION=ignore \
    "$_DIONY_HOST_PYTHON" -m pip install --disable-pip-version-check \
      --upgrade-strategy only-if-needed -r /opt/wan22/requirements_s2v.txt
fi
"$_DIONY_HOST_PYTHON" - <<'PY'
import os
from modelscope import snapshot_download
repo_id, dest = {cosy_model}
os.makedirs(dest, exist_ok=True)
if not any(os.scandir(dest)):
    print(f"COSYVOICE_PULL {{repo_id}} -> {{dest}}", flush=True)
    snapshot_download(repo_id, local_dir=dest)
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


def hf_token_set() -> bool:
    return bool(
        os.environ.get("HF_TOKEN", "").strip()
        or os.environ.get("HUGGING_FACE_HUB_TOKEN", "").strip()
    )


def hf_token_path() -> Path:
    return cluster_root() / ".cluster" / "hf.token"


def load_hf_token() -> None:
    """Load a previously saved token. Never logs it."""
    if hf_token_set():
        return
    try:
        token = hf_token_path().read_text(encoding="utf-8").strip()
    except OSError:
        return
    if token:
        os.environ["HF_TOKEN"] = token
        os.environ.setdefault("HUGGING_FACE_HUB_TOKEN", token)


def bind_hf_token(raw: str) -> None:
    token = (raw or "").strip()
    if not token or token in {"set", "••••"}:
        return
    os.environ["HF_TOKEN"] = token
    os.environ["HUGGING_FACE_HUB_TOKEN"] = token
    dest = hf_token_path()
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(token + "\n", encoding="utf-8")
    try:
        dest.chmod(0o600)
    except OSError:
        pass


def bind_hf_from_answers(answers: dict | None) -> dict:
    """Set HF_TOKEN from the form. Strip it so workbook / site.json never store it."""
    data = dict(answers or {})
    bind_hf_token(str(data.pop("hf_token", "") or ""))
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
        err = str(exc)[:2000]
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
            result = exec_host(host, site_readiness_script(repo), timeout=45)
            readiness.append(parse_site_readiness(result))
        primary_state = readiness[0] if readiness else {}
        if primary_state.get("prepared"):
            _log("Site already prepared. Model pull skipped.", epoch)
            if not primary_state.get("producer_running") or not primary_state.get("wan_runtime"):
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
            _log("HF_TOKEN is not set on this Studio. Gated Hugging Face pulls will fail.", epoch)
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
        result = exec_host(primary, remote_setup_script(repo, nas), timeout=timeout)
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
        err = str(exc)[:2000]
        if not _commit_job(epoch, ok=False, running=False, error=err, detail=err):
            return
        _log(f"FAIL {err}", epoch)
        _persist()
