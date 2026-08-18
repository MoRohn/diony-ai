"""Live Dual-Spark / B300 App: pull the pinned pack, clone Wan2.2, start Producer.

Pytest (DIONY_DRY_RUN=1) never SSHes. On spark-01, commands run locally.
From a laptop, SSH BatchMode as DIONY_SSH_USER.
"""

from __future__ import annotations

import json
import os
import shlex
import threading
from pathlib import Path

from diony_contracts.cluster import cluster_root
from diony_contracts.live import wizard_live
from diony_wizard.prep import repo_root
from diony_wizard.remote import apply_local, exec_host, rsync_repo, ssh_user

HF_PACK = [
    ("deepseek-ai/DeepSeek-V4-Flash-0731", "/models/deepseek/DeepSeek-V4-Flash-0731", ""),
    ("Wan-AI/Wan2.2-T2V-A14B", "/models/wan/Wan2.2-T2V-A14B", ""),
    ("Wan-AI/Wan2.2-I2V-A14B", "/models/wan/Wan2.2-I2V-A14B", ""),
    ("Wan-AI/Wan2.2-S2V-14B", "/models/wan/Wan2.2-S2V-14B", ""),
    ("Wan-AI/Wan2.2-Animate-14B", "/models/wan/Wan2.2-Animate-14B", ""),
    ("alibaba-pai/Wan2.2-VACE-Fun-A14B", "/models/wan/Wan2.2-VACE-Fun-A14B", ""),
    ("ai-forever/Real-ESRGAN", "/models/diony/diony_sr_v1", "RealESRGAN_x2.pth"),
]

_lock = threading.Lock()
_job: dict = {
    "running": False,
    "ok": False,
    "error": "",
    "detail": "",
    "log": [],
    "host": "",
}


def apply_path() -> Path:
    return cluster_root() / ".cluster" / "apply.json"


def public_apply() -> dict:
    with _lock:
        log = list(_job["log"])[-40:]
        return {
            "running": bool(_job["running"]),
            "ok": bool(_job["ok"]),
            "error": str(_job["error"]),
            "detail": str(_job["detail"]),
            "log": log,
            "host": str(_job["host"]),
            "live": wizard_live(),
            "user": ssh_user(),
        }


def _persist() -> None:
    dest = apply_path()
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(public_apply(), indent=2) + "\n")


def _log(line: str) -> None:
    with _lock:
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


def remote_setup_script(repo: str) -> str:
    token = os.environ.get("HF_TOKEN") or os.environ.get("HUGGING_FACE_HUB_TOKEN") or ""
    token_line = (
        f"export HUGGING_FACE_HUB_TOKEN={shlex.quote(token)}" if token else "true"
    )
    pack = repr(HF_PACK)
    quoted_repo = shlex.quote(repo)
    # Keep the token out of Wizard logs. It rides SSH stdin only.
    # Install huggingface_hub *before* the pull. Sequential SKU: Producer only.
    return f"""
set -euo pipefail
{token_line}
unset DASH_API_KEY DASHSCOPE_API_KEY
export DIONY_VAULT=/diony/vault
export DIONY_BIND=0.0.0.0
export DIONY_WAN_REPO=/opt/wan22
export PYTHONPATH=/opt/wan22${{PYTHONPATH:+:$PYTHONPATH}}
export DIONY_REPO={quoted_repo}
mkdir -p /models/deepseek /models/wan /models/diony /opt /diony/vault/.cluster
python3 -m pip install -U pip huggingface_hub
python3 - <<'PY'
from huggingface_hub import hf_hub_download, snapshot_download
import os
PACK = {pack}
for item in PACK:
    repo_id, dest, filename = item
    os.makedirs(dest, exist_ok=True)
    if filename:
        hf_hub_download(repo_id=repo_id, filename=filename, local_dir=dest)
    else:
        snapshot_download(repo_id=repo_id, local_dir=dest)
print("HF_OK")
PY
test -d /opt/wan22/.git || git clone --depth 1 https://github.com/Wan-Video/Wan2.2.git /opt/wan22
python3 -m pip install -e "$DIONY_REPO/packages/contracts" -e "$DIONY_REPO/apps/wan" -e "$DIONY_REPO/apps/finish" -e "$DIONY_REPO/apps/producer" -e "$DIONY_REPO/apps/wizard"
python3 -m pip install -r /opt/wan22/requirements.txt || true
test -d /models/wan/Wan2.2-T2V-A14B
test -e /models/diony/diony_sr_v1/RealESRGAN_x2.pth || ls /models/diony/diony_sr_v1/*.pth >/dev/null
if ! pgrep -f "diony-producer" >/dev/null 2>&1; then
  nohup env DIONY_VAULT=/diony/vault DIONY_BIND=0.0.0.0 DIONY_WAN_REPO=/opt/wan22 \\
    DIONY_WAN_URL=http://127.0.0.1:8010 DIONY_FINISH_URL=http://127.0.0.1:8020 \\
    DIONY_DRAIN_BRAIN='pkill -f "vllm serve" || true' \\
    DIONY_DRAIN_WAN='pkill -f diony-wan || true' \\
    diony-producer >/diony/vault/.cluster/producer.log 2>&1 &
fi
echo APPLY_OK
"""


def replicate_script() -> str:
    user = ssh_user()
    return f"""
set -euo pipefail
if [ -d /models/wan ]; then
  rsync -a /models/ {user}@spark-02:/models/ || rsync -a /models/ spark-02:/models/ || true
fi
echo REPLICATE_DONE
"""


def reset_apply() -> None:
    with _lock:
        _job.update(
            {
                "running": False,
                "ok": False,
                "error": "",
                "detail": "",
                "host": "",
            }
        )
        _job["log"] = []
    _persist()


def should_apply(path: str | None) -> bool:
    return wizard_live() and path in {"dual", "b300"}


def ensure_apply(path: str, hosts: list[str]) -> dict:
    """Start pack apply once. Safe to call on every App Continue."""
    if os.environ.get("DIONY_APPLY_FAKE", "").strip() in {"1", "true", "yes"}:
        with _lock:
            _job.update(
                {
                    "running": False,
                    "ok": True,
                    "error": "",
                    "detail": "fake apply",
                    "host": hosts[0] if hosts else "",
                }
            )
            _job["log"] = ["fake apply ok"]
        _persist()
        return public_apply()
    with _lock:
        if _job["ok"]:
            return public_apply()
        if _job["running"]:
            return public_apply()
        _job["running"] = True
        _job["ok"] = False
        _job["error"] = ""
        _job["host"] = hosts[0] if hosts else "spark-01"
        _job["log"] = []
    thread = threading.Thread(
        target=_run_apply,
        args=(path, hosts),
        name="diony-apply",
        daemon=True,
    )
    thread.start()
    return public_apply()


def _run_apply(path: str, hosts: list[str]) -> None:
    primary = hosts[0] if hosts else "spark-01"
    repo = os.environ.get("DIONY_REPO", "").strip() or (
        str(repo_root()) if apply_local(primary) else "/opt/diony-ai"
    )
    try:
        _log(f"Apply on {primary} as {ssh_user()}")
        if not apply_local(primary):
            _log(f"Sync repo → {primary}:{repo}")
            synced = rsync_repo(primary, str(repo_root()), repo)
            if not synced.ok:
                raise RuntimeError(
                    f"rsync to {primary} failed ({synced.code}). "
                    f"Set DIONY_SSH_KEY and DIONY_SSH_USER. {synced.text[-800:]}"
                )
        _log("Pull pack + Wan2.2 + diony_sr_v1, install, start Producer")
        timeout = int(os.environ.get("DIONY_APPLY_TIMEOUT", "21600"))
        result = exec_host(primary, remote_setup_script(repo), timeout=timeout)
        if not result.ok or "APPLY_OK" not in (result.stdout or ""):
            raise RuntimeError(
                result.text[-2000:] or f"apply exited {result.code}"
            )
        if path == "dual" and len(hosts) > 1:
            _log(f"Replicate /models to {hosts[1]}")
            exec_host(primary, replicate_script(), timeout=7200)
        _log("Pack applied. Producer is up on this pair.")
        with _lock:
            _job["ok"] = True
            _job["running"] = False
            _job["error"] = ""
        _persist()
    except Exception as exc:  # noqa: BLE001 — apply must surface the miss
        with _lock:
            _job["ok"] = False
            _job["running"] = False
            _job["error"] = str(exc)[:2000]
            _job["detail"] = _job["error"]
        _log(f"FAIL {_job['error']}")
        _persist()
