"""Desk Producer talks to the signed GPU. Wan lives on that box, not on this laptop."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shlex
import signal
import socket
import subprocess
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx

from diony_contracts.cluster import WIZARD_APP_PATHS, cluster_root, read_site_lock, write_site_lock
from diony_contracts.live import generate_py, inference_live

TUNNEL_PORT = int(os.environ.get("DIONY_GPU_PRODUCER_PORT", "18788") or "18788")
PLACEHOLDER_HOSTS = {
    "spark-01",
    "spark-02",
    "spark-03",
    "spark-04",
    "b200-01",
    "b300-01",
    "node-01",
    "localhost",
    "127.0.0.1",
}

GPU_STAMP_FILES = (
    "apps/producer/src/diony_producer/gpu.py",
    "apps/producer/src/diony_producer/farm.py",
    "apps/producer/src/diony_producer/app.py",
    "apps/producer/src/diony_producer/run.py",
    "apps/producer/src/diony_producer/finish.py",
    "apps/producer/src/diony_producer/mix.py",
    "apps/producer/src/diony_producer/voice.py",
    "apps/producer/src/diony_producer/speech_audio.py",
    "apps/finish/src/diony_finish/rrdb.py",
    "apps/finish/src/diony_finish/pipeline.py",
    "apps/wan/src/diony_wan/runner.py",
    "apps/wan/src/diony_wan/attention_qualification.py",
    "apps/wan/src/diony_wan/resident.py",
    "apps/wan/src/diony_wan/resident_worker.py",
    "apps/wan/src/diony_wan/runtime.py",
    "apps/wan/src/diony_wan/storage.py",
    "apps/wan/src/diony_wan/validation.py",
    "packages/contracts/src/diony_contracts/models.py",
    "packages/contracts/src/diony_contracts/studio.py",
    "packages/contracts/src/diony_contracts/voice.py",
    "scripts/benchmark/measure_wan_attention.py",
)

GPU_SYNC_TREES = (
    ("apps/producer/src/diony_producer", "/opt/diony-ai/apps/producer/src/diony_producer"),
    ("apps/finish/src/diony_finish", "/opt/diony-ai/apps/finish/src/diony_finish"),
    ("apps/wan/src/diony_wan", "/opt/diony-ai/apps/wan/src/diony_wan"),
    ("packages/contracts/src/diony_contracts", "/opt/diony-ai/packages/contracts/src/diony_contracts"),
    ("infra/runtime", "/opt/diony-ai/infra/runtime"),
    ("scripts", "/opt/diony-ai/scripts"),
)

_lock = threading.Lock()
_tunnel: subprocess.Popen[bytes] | None = None
_tunnel_key = ""
_last_error = ""
_synced_stamp = ""
_sync_deferred_at = 0.0
# A GPU Producer takes ~2 minutes to answer after launch (its lifespan resumes
# the camera before uvicorn binds :8788). Within this window a silent :8788 is
# "starting", not "down": the desk must not drop the tunnel and re-run the
# start script, which would kill the starting process and relaunch it — the
# 2026-08-27 loop that answered every mutation with "control plane not ready".
_PRODUCER_START_GRACE_SECONDS = 300.0
_last_producer_start_at = 0.0
_ensure_lock = threading.Lock()
_SYNC_DEFER_SECONDS = 60.0
_ssh_fail_at = 0.0
_ssh_fail_key = ""


def _dry() -> bool:
    return os.environ.get("DIONY_DRY_RUN", "").strip().lower() in {"1", "true", "yes", "on"}


def in_pytest() -> bool:
    return bool(os.environ.get("PYTEST_CURRENT_TEST"))


def local_wan() -> bool:
    """True only when THIS process can run generate.py or hit local diony-wan."""
    if _dry():
        return False
    if generate_py().is_file() and inference_live():
        return True
    try:
        res = httpx.get(os.environ.get("DIONY_WAN_URL", "http://127.0.0.1:8010").rstrip("/") + "/health", timeout=1.5)
        return res.status_code == 200
    except httpx.HTTPError:
        return False


def routable_host(host: str) -> bool:
    name = (host or "").strip().lower()
    if not name or name in PLACEHOLDER_HOSTS:
        return False
    return True


_wizard_connect_at = 0.0
_wizard_connect_row: dict = {}


def _wizard_connect() -> dict:
    """Live Studio Connect. Never cache a TCP line across a pod restart."""
    global _wizard_connect_at, _wizard_connect_row
    if in_pytest():
        return {}
    now = time.time()
    if _wizard_connect_row and now - _wizard_connect_at < 1.5:
        return dict(_wizard_connect_row)
    try:
        state = httpx.get("http://127.0.0.1:8787/api/state", timeout=1.5).json()
    except (httpx.HTTPError, ValueError, OSError):
        return dict(_wizard_connect_row)
    row = dict((state.get("workbook") or {}).get("connect") or {})
    if not row:
        row = dict(((state.get("report") or {}).get("workbook") or {}).get("connect") or {})
    _wizard_connect_at = now
    if row:
        _wizard_connect_row = row
    return dict(row)


def merge_connect(lock: dict | None, wizard: dict | None) -> dict:
    """Studio Connect host/port win over a stale site.json line (pod restart).

    Empty Studio (Site with no Connect yet) does not wipe a signed lock.
    """
    data = dict(lock or {})
    row = dict(data.get("connect") or {})
    wiz = dict(wizard or {})
    parts = str(wiz.get("hosts") or "").split()
    wiz_host = parts[0].strip() if parts else str(wiz.get("host") or "").strip()
    wiz_port = str(wiz.get("ssh_port") or "").strip()
    wiz_user = str(wiz.get("ssh_user") or "").strip()
    lock_host = str(row.get("host") or "").strip()
    if not lock_host:
        hosts = data.get("nodes") or []
        lock_host = str(hosts[0]).strip() if hosts else ""
    lock_port = str(row.get("ssh_port") or os.environ.get("DIONY_SSH_PORT") or "").strip()
    lock_user = str(row.get("ssh_user") or os.environ.get("DIONY_SSH_USER") or "").strip()
    if routable_host(wiz_host) and wiz_port:
        host, port, user = wiz_host, wiz_port, wiz_user
    else:
        host, port, user = lock_host, lock_port, lock_user
    if not port:
        port = "22"
    if not user:
        path = str(data.get("compute_path") or "")
        user = "root" if path in {"b200", "b300"} else "spark"
    if not host:
        return {}
    return {
        "host": host,
        "ssh_user": user,
        "ssh_port": str(port),
        "vault": str(row.get("vault") or wiz.get("nas_ip") or data.get("vault_export") or ""),
    }


def connect_from_lock(lock: dict | None = None) -> dict:
    data = lock if lock is not None else read_site_lock() or {}
    wiz = {} if in_pytest() else _wizard_connect()
    return merge_connect(data, wiz)


def persist_connect(conn: dict) -> None:
    """Write the live SSH line into site.json so a Studio restart does not strand Producer."""
    if in_pytest() or not conn:
        return
    lock = read_site_lock() or {}
    prev = dict(lock.get("connect") or {})
    host = str(conn.get("host") or "").strip()
    port = str(conn.get("ssh_port") or "").strip()
    user = str(conn.get("ssh_user") or "").strip()
    if not host or not port:
        return
    same = (
        str(prev.get("host") or "") == host
        and str(prev.get("ssh_port") or "") == port
        and str(prev.get("ssh_user") or "") == user
    )
    if same and lock.get("nodes") == [host]:
        return
    lock["connect"] = {
        "host": host,
        "ssh_user": user,
        "ssh_port": port,
        "vault": str(conn.get("vault") or prev.get("vault") or lock.get("vault_export") or ""),
    }
    lock["nodes"] = [host]
    write_site_lock(lock)


def signed_gpu_site(lock: dict | None = None) -> bool:
    data = lock if lock is not None else read_site_lock() or {}
    path = str(data.get("compute_path") or "")
    if path not in WIZARD_APP_PATHS:
        return False
    if data.get("gpu_ready"):
        return True
    conn = connect_from_lock(data)
    return bool(conn and routable_host(conn["host"]))


def wizard_pack_on_gpu(lock: dict | None = None) -> bool:
    """Studio App + Test GPU: Wan2.2 and diony_sr_v1 are on that GPU, not this desk."""
    data = lock if lock is not None else read_site_lock() or {}
    return signed_gpu_site(data) and bool(data.get("gpu_ready"))


def gpu_finish_live() -> bool:
    """GPU Producer /api/finish.live, or Studio App pack on that GPU. Desk DRY_RUN does not apply."""
    if not should_proxy():
        return False
    packed = wizard_pack_on_gpu()
    try:
        res = httpx.get(gpu_producer_base() + "/api/finish", timeout=2.0)
        if res.status_code == 200 and bool(res.json().get("live")):
            return True
    except (httpx.HTTPError, ValueError):
        pass
    return packed


def apply_ssh_env(connect: dict | None = None) -> None:
    row = connect or connect_from_lock()
    if not row:
        return
    os.environ["DIONY_SSH_USER"] = row["ssh_user"]
    os.environ["DIONY_SSH_PORT"] = str(row["ssh_port"])
    if not os.environ.get("DIONY_SSH_KEY", "").strip():
        key = Path.home() / ".ssh" / "id_ed25519"
        if key.is_file():
            os.environ["DIONY_SSH_KEY"] = str(key)


def should_proxy() -> bool:
    if in_pytest():
        return False
    if os.environ.get("DIONY_APPLY_OFF", "").strip().lower() in {"1", "true", "yes"}:
        return False
    if os.environ.get("DIONY_GPU_PROXY", "1").strip().lower() in {"0", "false", "no", "off"}:
        return False
    if local_wan():
        return False
    conn = connect_from_lock()
    if not conn or not routable_host(conn["host"]):
        return False
    return signed_gpu_site()


def abort_gpu_camera() -> None:
    """SIGTERM generate.py --task on the signed GPU. Never diony-producer."""
    if in_pytest():
        return
    conn = connect_from_lock()
    if not conn or not routable_host(conn["host"]):
        return
    apply_ssh_env(conn)
    script = r"""
python3 - <<'PY'
import os, signal, subprocess
try:
    blob = subprocess.check_output(["ps", "-eo", "pid,args"], text=True)
except Exception:
    raise SystemExit(0)
for line in blob.splitlines():
    if "generate.py" not in line or "--task" not in line:
        continue
    if "diony-producer" in line:
        continue
    try:
        os.kill(int(line.split(None, 1)[0]), signal.SIGTERM)
    except (ValueError, ProcessLookupError, PermissionError):
        pass
PY
"""
    cmd = ["ssh", *_ssh_args(conn["ssh_port"]), f"{conn['ssh_user']}@{conn['host']}", "bash", "-s"]
    try:
        subprocess.run(cmd, input=script, capture_output=True, text=True, timeout=20, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return


def last_gpu_error() -> str:
    return _last_error


def gpu_producer_base() -> str:
    return f"http://127.0.0.1:{TUNNEL_PORT}"


def gpu_producer_ready_base() -> str | None:
    """Return the existing tunnel without starting SSH or blocking an API request."""
    if not should_proxy() or not _port_open(TUNNEL_PORT):
        return None
    return gpu_producer_base()


def _ssh_args(port: str, *, connect_timeout: int = 12) -> list[str]:
    args = [
        "-o",
        "BatchMode=yes",
        "-o",
        "StrictHostKeyChecking=accept-new",
        "-o",
        f"ConnectTimeout={int(connect_timeout)}",
        "-o",
        "ExitOnForwardFailure=yes",
        "-o",
        "ServerAliveInterval=30",
    ]
    key = os.environ.get("DIONY_SSH_KEY", "").strip()
    if key:
        args.extend(["-i", key])
    if str(port) != "22":
        args.extend(["-p", str(port)])
    return args


def _port_open(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.4):
            return True
    except OSError:
        return False


def _set_error(msg: str) -> None:
    global _last_error
    _last_error = msg[:800]


def _ensure_tunnel(conn: dict) -> bool:
    global _tunnel, _tunnel_key
    key = f"{conn.get('host')}:{conn.get('ssh_port')}"
    if _tunnel_key and _tunnel_key != key:
        _drop_tunnel()
        _free_local_tunnel_port()
    if _port_open(TUNNEL_PORT) and _tunnel_key == key:
        return True
    with _lock:
        if _port_open(TUNNEL_PORT) and _tunnel_key == key:
            return True
        _drop_tunnel()
        if _port_open(TUNNEL_PORT):
            _free_local_tunnel_port()
        cmd = [
            "ssh",
            *_ssh_args(conn["ssh_port"]),
            "-N",
            "-L",
            f"127.0.0.1:{TUNNEL_PORT}:127.0.0.1:8788",
            f"{conn['ssh_user']}@{conn['host']}",
        ]
        _tunnel = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        deadline = time.time() + 14
        while time.time() < deadline:
            if _port_open(TUNNEL_PORT):
                _tunnel_key = key
                return True
            if _tunnel.poll() is not None:
                err = (_tunnel.stderr.read() if _tunnel.stderr else b"") or b""
                _set_error(err.decode("utf-8", "replace") or f"SSH tunnel to {conn['host']}:{conn['ssh_port']} closed")
                _tunnel = None
                _tunnel_key = ""
                return False
            time.sleep(0.2)
        _set_error(f"SSH tunnel to {conn['host']}:{conn['ssh_port']} did not open Producer :8788")
        return False


def _drop_tunnel() -> None:
    global _tunnel, _tunnel_key
    proc = _tunnel
    _tunnel = None
    _tunnel_key = ""
    if proc is None:
        return
    if proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            proc.kill()


def _free_local_tunnel_port() -> None:
    """Desk :18788 only. Never GPU :8788. Never diony-producer by name."""
    _drop_tunnel()
    try:
        out = subprocess.check_output(
            ["lsof", "-nP", f"-iTCP:{TUNNEL_PORT}", "-sTCP:LISTEN", "-t"],
            text=True,
            timeout=3,
            stderr=subprocess.DEVNULL,
        )
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return
    for line in out.split():
        try:
            os.kill(int(line), signal.SIGTERM)
        except (ValueError, OSError, ProcessLookupError):
            continue
    deadline = time.time() + 2
    while time.time() < deadline and _port_open(TUNNEL_PORT):
        time.sleep(0.1)


def desk_repo() -> Path | None:
    explicit = os.environ.get("DIONY_APP_ROOT", "").strip()
    if explicit:
        root = Path(explicit).expanduser().resolve()
        if (root / "apps" / "producer").is_dir() and (root / "infra" / "profiles").is_dir():
            return root
        return None
    here = Path(__file__).resolve()
    for path in [here, *here.parents]:
        if (path / "AGENTS.md").is_file() and (path / "apps" / "producer").is_dir():
            return path
    return None


def _code_stamp() -> str:
    root = desk_repo()
    if root is None:
        return ""
    digest = hashlib.sha256()
    found = False
    for rel in GPU_STAMP_FILES:
        dest = root / rel
        if dest.is_file():
            try:
                payload = dest.read_bytes()
            except OSError:
                continue
            found = True
            digest.update(rel.encode("utf-8"))
            digest.update(b"\0")
            digest.update(payload)
            digest.update(b"\0")
    return digest.hexdigest() if found else ""


def _remote_code_matches(conn: dict, root: Path) -> bool:
    """Prove every runtime-critical source file matches before trusting a sync stamp."""
    manifest: list[str] = []
    for rel in GPU_STAMP_FILES:
        source = root / rel
        if not source.is_file():
            return False
        try:
            digest = hashlib.sha256(source.read_bytes()).hexdigest()
        except OSError:
            return False
        manifest.append(f"{digest}  /opt/diony-ai/{rel}")
    if not manifest:
        return False
    command = [
        "ssh",
        *_ssh_args(conn["ssh_port"]),
        f"{conn['ssh_user']}@{conn['host']}",
        "sha256sum",
        "-c",
        "-",
    ]
    proc = subprocess.run(
        command,
        input="\n".join(manifest) + "\n",
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    return proc.returncode == 0


def _remote_producer_busy(conn: dict) -> bool:
    """Fail closed when remote FARM/Finish state cannot prove an idle boundary."""
    script = r'''
project="$(curl -sf --max-time 4 http://127.0.0.1:8788/api/project 2>/dev/null || true)"
if [ -n "$project" ]; then
  stop_state="$(PROJECT_PAYLOAD="$project" python3 - <<'PY'
import json, os
try:
    data=json.loads(os.environ.get('PROJECT_PAYLOAD') or '{}')
except (TypeError, json.JSONDecodeError):
    print('unknown')
    raise SystemExit
pause=data.get('production_pause') if isinstance(data.get('production_pause'),dict) else {}
print('stop_keep' if pause.get('reason') == 'stop_keep' else 'normal')
PY
)"
  if [ "$stop_state" = stop_keep ]; then
    echo idle
    exit 0
  fi
fi
if pgrep -f '[g]enerate.py.*--task' >/dev/null 2>&1; then
  echo busy
  exit 0
fi
payload="$(curl -sf --max-time 4 http://127.0.0.1:8788/api/run 2>/dev/null || true)"
if [ -z "$payload" ]; then
  echo busy
  exit 0
fi
RUN_PAYLOAD="$payload" python3 - <<'PY'
import json, os
try:
    data=json.loads(os.environ.get('RUN_PAYLOAD') or '{}')
except (TypeError, json.JSONDecodeError):
    print('busy')
    raise SystemExit
farm=data.get('farm') if isinstance(data.get('farm'),dict) else {}
finish=data.get('finish') if isinstance(data.get('finish'),dict) else {}
mix=data.get('mix') if isinstance(data.get('mix'),dict) else {}
print('busy' if data.get('rolling') or farm.get('busy') or finish.get('busy') or mix.get('busy') else 'idle')
PY
'''
    cmd = ["ssh", *_ssh_args(conn["ssh_port"]), f"{conn['ssh_user']}@{conn['host']}", "bash", "-s"]
    try:
        proc = subprocess.run(cmd, input=script, capture_output=True, text=True, timeout=12, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return True
    return proc.returncode != 0 or (proc.stdout or "").strip().splitlines()[-1:] != ["idle"]


def _probe_ssh(conn: dict) -> bool:
    global _ssh_fail_at, _ssh_fail_key
    key = f"{conn.get('host')}:{conn.get('ssh_port')}"
    if key == _ssh_fail_key and time.time() - _ssh_fail_at < 8:
        return False
    cmd = [
        "ssh",
        *_ssh_args(conn["ssh_port"], connect_timeout=5),
        f"{conn['ssh_user']}@{conn['host']}",
        "echo",
        "GPU_SSH_OK",
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=10, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        _ssh_fail_at = time.time()
        _ssh_fail_key = key
        _set_error(
            f"SSH {conn['host']}:{conn['ssh_port']} failed ({exc}). "
            "After a pod restart the exposed TCP IP and -p change. "
            "Save the new line on Studio Connect, then Produce."
        )
        return False
    if proc.returncode == 0 and "GPU_SSH_OK" in (proc.stdout or ""):
        _ssh_fail_key = ""
        return True
    blob = (proc.stderr or proc.stdout or "").strip()
    _ssh_fail_at = time.time()
    _ssh_fail_key = key
    if "Connection refused" in blob or "timed out" in blob.lower() or "Operation timed out" in blob:
        _set_error(
            f"SSH {conn['host']}:{conn['ssh_port']} refused. "
            "After a pod restart the SSH over exposed TCP IP and -p change. "
            "Save the new line on Studio Connect, then Produce."
        )
    else:
        _set_error(blob[:800] or f"SSH {conn['host']}:{conn['ssh_port']} failed")
    return False


def _sync_gpu_apps(conn: dict) -> bool:
    """Push Producer + Wan Python onto /opt/diony-ai. Never --delete the GPU venv.

    Returns True when the GPU now carries the desk's code (freshly pushed or
    already current). A deferred push during live Production returns False and
    stamps ``_sync_deferred_at`` so the caller does not hammer the box with a
    restart attempt on every controller tick.
    """
    global _synced_stamp, _sync_deferred_at
    if in_pytest():
        return False
    stamp = _code_stamp()
    if not stamp:
        return False
    if stamp == _synced_stamp:
        return True
    root = desk_repo()
    if root is None:
        return False
    seen = [
        "ssh",
        *_ssh_args(conn["ssh_port"]),
        f"{conn['ssh_user']}@{conn['host']}",
        "cat",
        "/diony/vault/.cluster/desk.stamp",
    ]
    marked = subprocess.run(seen, capture_output=True, text=True, timeout=12, check=False)
    if marked.returncode == 0 and (marked.stdout or "").strip() == stamp:
        if _remote_code_matches(conn, root):
            _synced_stamp = stamp
            return True
    if _remote_producer_busy(conn):
        _sync_deferred_at = time.time()
        return False
    remote = [
        "ssh",
        *_ssh_args(conn["ssh_port"]),
        f"{conn['ssh_user']}@{conn['host']}",
        "test",
        "-d",
        "/opt/diony-ai/apps/producer/src/diony_producer",
    ]
    probe = subprocess.run(remote, capture_output=True, text=True, timeout=20, check=False)
    if probe.returncode != 0:
        _set_error(
            "Studio App did not leave /opt/diony-ai on this GPU. "
            "App Continue, then Ready Test GPU. Produce farms that box, not this desk."
        )
        return False
    ssh_e = " ".join(shlex.quote(p) for p in ["ssh", *_ssh_args(conn["ssh_port"])])
    for rel, dest in GPU_SYNC_TREES:
        src = root / rel
        if not src.is_dir():
            continue
        cmd = [
            "rsync",
            "-az",
            "-e",
            ssh_e,
            "--exclude",
            "__pycache__",
            "--exclude",
            "data",
            str(src).rstrip("/") + "/",
            f"{conn['ssh_user']}@{conn['host']}:{dest.rstrip('/')}/",
        ]
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=90, check=False)
        if proc.returncode != 0:
            _set_error((proc.stderr or proc.stdout or "rsync failed")[:800])
            return False
    if not _remote_code_matches(conn, root):
        _set_error(
            "GPU sync integrity check failed: one or more runtime source files "
            "are missing or do not match their local SHA-256 digest."
        )
        return False
    mark = [
        "ssh",
        *_ssh_args(conn["ssh_port"]),
        f"{conn['ssh_user']}@{conn['host']}",
        "bash",
        "-s",
    ]
    mark_script = (
        "set -e\n"
        "mkdir -p /diony/vault/.cluster\n"
        f"printf '%s\\n' {shlex.quote(stamp)} > /diony/vault/.cluster/desk.stamp\n"
    )
    marked = subprocess.run(
        mark,
        input=mark_script,
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    if marked.returncode != 0:
        _set_error((marked.stderr or marked.stdout or "GPU sync stamp write failed")[:800])
        return False
    _synced_stamp = stamp
    _restart_gpu_producer_bind(conn)
    return True


def _restart_gpu_producer_bind(conn: dict) -> None:
    """Free GPU :8788 so new farm.py loads. generate.py --task keeps running; boot adopts it."""
    script = r"""
if command -v fuser >/dev/null 2>&1; then
  fuser -k 8788/tcp >/dev/null 2>&1 || true
  sleep 1
elif command -v ss >/dev/null 2>&1; then
  pids="$(ss -ltnp 'sport = :8788' 2>/dev/null | sed -n 's/.*pid=\([0-9][0-9]*\).*/\1/p' | sort -u)"
  for pid in $pids; do
    case "$pid" in
      *[!0-9]*|'') continue ;;
    esac
    kill "$pid" >/dev/null 2>&1 || true
  done
  sleep 1
fi
"""
    cmd = ["ssh", *_ssh_args(conn["ssh_port"]), f"{conn['ssh_user']}@{conn['host']}", "bash", "-s"]
    subprocess.run(cmd, input=script, capture_output=True, text=True, timeout=20, check=False)


def _gpu_producer_start_script(expected_stamp: str | None = None) -> str:
    """Repair and attest the Wan host runtime before accepting Production."""
    code_stamp = (expected_stamp or _code_stamp()).strip()
    script = r"""
set -euo pipefail
if [ ! -d /models/wan/Wan2.2-T2V-A14B ] || [ ! -f /opt/wan22/generate.py ]; then
  echo GPU_PACK_MISSING
  exit 12
fi
export DIONY_VAULT=/diony/vault
export DIONY_BIND=0.0.0.0
export DIONY_WAN_REPO=/opt/wan22
export DIONY_VOLUME_CAPACITY_GB="${DIONY_VOLUME_CAPACITY_GB:-1200}"
export DIONY_VOLUME_MIN_FREE_GB="${DIONY_VOLUME_MIN_FREE_GB:-100}"
export DIONY_MODEL_HOT_CACHE="${DIONY_MODEL_HOT_CACHE:-auto}"
export DIONY_MODEL_HOT_CACHE_GB="${DIONY_MODEL_HOT_CACHE_GB:-320}"
export DIONY_MODEL_HOT_CACHE_MIN_FREE_GB="${DIONY_MODEL_HOT_CACHE_MIN_FREE_GB:-64}"
unset DIONY_DRY_RUN
unset DIONY_WAN_RESIDENT DIONY_WAN_RESIDENT_RECEIPT DIONY_WAN_KERNEL_MODE DIONY_WAN_RESIDENT_CANARY_PERCENT DIONY_WAN_ATTENTION_BACKEND
REGIONAL_RECEIPT=/diony/vault/qualification/wan-regional-compile-validation.json
EAGER_RECEIPT=/diony/vault/qualification/wan-resident-validation.json
if [ -f "$REGIONAL_RECEIPT" ]; then
  export DIONY_WAN_RESIDENT=validated
  export DIONY_WAN_RESIDENT_RECEIPT="$REGIONAL_RECEIPT"
  export DIONY_WAN_KERNEL_MODE=regional_compile
  export DIONY_WAN_RESIDENT_CANARY_PERCENT=10
elif [ -f "$EAGER_RECEIPT" ]; then
  export DIONY_WAN_RESIDENT=validated
  export DIONY_WAN_RESIDENT_RECEIPT="$EAGER_RECEIPT"
  export DIONY_WAN_KERNEL_MODE=eager
  export DIONY_WAN_RESIDENT_CANARY_PERCENT=10
fi
SYSTEM_PY=""
for CANDIDATE in "${DIONY_PYTHON:-}" /usr/bin/python3 /usr/local/bin/python "$(command -v python3 2>/dev/null || true)"; do
  [ -n "$CANDIDATE" ] || continue
  if "$CANDIDATE" -c "import torch" >/dev/null 2>&1; then
    SYSTEM_PY="$CANDIDATE"
    break
  fi
done
[ -n "$SYSTEM_PY" ] || { echo GPU_HOST_PYTHON_WITH_TORCH_MISSING; exit 13; }
WAN_PY=/diony/vault/runtime/wan/venv/bin/python
WAN_VENV="${WAN_PY%/bin/python}"
if [ ! -x "$WAN_VENV/bin/python" ]; then
  mkdir -p "$(dirname "$WAN_VENV")"
  "$SYSTEM_PY" -m venv --system-site-packages "$WAN_VENV"
fi
HOST_PY="$WAN_VENV/bin/python"
"$HOST_PY" -c "import torch" >/dev/null 2>&1 || { echo GPU_HOST_PYTHON_WITH_TORCH_MISSING; exit 13; }
export DIONY_PYTHON="$HOST_PY"
_diony_wan_speech_pack_ready() {
  # Same completeness bar as diony_producer.voice.speech_pack_missing: Wan2.2's
  # own TTS lane (generate.py --enable_tts) loads CosyVoice and CosyVoice2-0.5B
  # from beside generate.py; a directory Studio App is still filling is not a
  # speech pack yet.
  [ -f /opt/wan22/CosyVoice/cosyvoice/cli/cosyvoice.py ] || return 1
  [ -e /opt/wan22/CosyVoice/third_party/Matcha-TTS ] || return 1
  for _diony_tts_file in cosyvoice2.yaml llm.pt flow.pt hift.pt speech_tokenizer_v2.onnx; do
    [ -f "/opt/wan22/CosyVoice2-0.5B/$_diony_tts_file" ] || return 1
  done
  return 0
}
if _diony_wan_speech_pack_ready; then
  if [ -d /diony/vault/runtime/voice/prompt-library ]; then
    export DIONY_WAN_TTS_PROMPT_LIBRARY=/diony/vault/runtime/voice/prompt-library
  fi
  if [ -f /opt/wan22/examples/zero_shot_prompt.wav ]; then
    export DIONY_WAN_TTS_PROMPT_AUDIO=/opt/wan22/examples/zero_shot_prompt.wav
    export DIONY_WAN_TTS_PROMPT_TEXT='希望你以后能够做的比我还好呦。'
  fi
fi
if [ -d /diony/vault/runtime/audio/library ]; then
  export DIONY_SOUND_LIBRARY=/diony/vault/runtime/audio/library
fi
# Blueprint 24 optional measured lanes: the env is exported only when the
# staged weights are complete, so an absent pack measures nothing and gates
# nothing. The Producer interpreter receives the overlay packages once.
_diony_whisper_ready() {
  [ -f /models/audio/faster-whisper-large-v3/model.bin ] || return 1
  [ -f /models/audio/faster-whisper-large-v3/config.json ] || return 1
  [ -f /models/audio/faster-whisper-large-v3/tokenizer.json ] || return 1
  return 0
}
_diony_clip_ready() {
  [ -f /models/diony/clip-vit-base-patch32/config.json ] || return 1
  [ -f /models/diony/clip-vit-base-patch32/preprocessor_config.json ] || return 1
  [ -f /models/diony/clip-vit-base-patch32/model.safetensors ] || [ -f /models/diony/clip-vit-base-patch32/pytorch_model.bin ] || return 1
  return 0
}
if _diony_whisper_ready; then
  export DIONY_WHISPER_MODEL=/models/audio/faster-whisper-large-v3
fi
if _diony_clip_ready; then
  export DIONY_CLIP_MODEL=/models/diony/clip-vit-base-patch32
fi
# Blueprint 24 §14: face-embedding identity (OpenCV Zoo YuNet + SFace) measures
# only when both models are staged and the Producer interpreter has cv2.
if [ -f /models/diony/opencv-zoo/face_detection_yunet_2023mar.onnx ] && \
   [ -f /models/diony/opencv-zoo/face_recognition_sface_2021dec.onnx ] && \
   /opt/diony-ai/.venv/bin/python -c "import cv2; cv2.FaceDetectorYN; cv2.FaceRecognizerSF" >/dev/null 2>&1; then
  export DIONY_FACE_ID_DETECTOR_MODEL=/models/diony/opencv-zoo/face_detection_yunet_2023mar.onnx
  export DIONY_FACE_ID_RECOGNIZER_MODEL=/models/diony/opencv-zoo/face_recognition_sface_2021dec.onnx
fi
if [ -f /opt/diony-ai/apps/producer/src/diony_producer/audiogen_backend.py ]; then
  export DIONY_AUDIOGEN_COMMAND="/opt/diony-ai/.venv/bin/python /opt/diony-ai/apps/producer/src/diony_producer/audiogen_backend.py"
fi
RUNTIME_PROBE=/opt/diony-ai/apps/wan/src/diony_wan/runtime.py
RUNTIME_OVERLAY=/opt/diony-ai/infra/runtime/wan-host-overlay.txt
# Wan's S2V speech lane imports (cosyvoice.llm / cosyvoice.flow) that Wan's own
# requirements_s2v.txt does not cover (Blueprint 22); applied with the host overlay.
TTS_OVERLAY=/opt/diony-ai/infra/runtime/wan-tts-overlay.txt
MONTAGE_OVERLAY=/opt/diony-ai/infra/runtime/montage-overlay.txt
RUNTIME_REPORT=/diony/vault/.cluster/wan-host-runtime.json
VPY=/opt/diony-ai/.venv/bin/python
PRODUCER_BIN=/opt/diony-ai/.venv/bin/diony-producer
ATTENTION_RECEIPT=/diony/vault/qualification/wan-cudnn-sdpa-validation.json
if [ -x "$VPY" ] && [ -f "$ATTENTION_RECEIPT" ]; then
  if DIONY_ATTENTION_RECEIPT="$ATTENTION_RECEIPT" "$VPY" -c "import json, os; from pathlib import Path; from diony_wan.attention_qualification import attention_decision; pointer=json.loads(Path('/diony/vault/.cluster/current.json').read_text()); decision=attention_decision(Path(os.environ['DIONY_ATTENTION_RECEIPT']), project_id=str(pointer.get('project_id') or '')); raise SystemExit(0 if decision.enabled else 1)"; then
    export DIONY_WAN_ATTENTION_BACKEND=cudnn_sdpa
  fi
fi
if [ -x "$VPY" ] && [ -f "$MONTAGE_OVERLAY" ] && { [ -n "${DIONY_WHISPER_MODEL:-}" ] || [ -n "${DIONY_CLIP_MODEL:-}" ]; }; then
  _diony_montage_ready() {
    if [ -n "${DIONY_WHISPER_MODEL:-}" ]; then
      "$VPY" -c "import faster_whisper" >/dev/null 2>&1 || return 1
    fi
    if [ -n "${DIONY_CLIP_MODEL:-}" ]; then
      "$VPY" -c "import transformers, PIL" >/dev/null 2>&1 || return 1
    fi
    return 0
  }
  if ! _diony_montage_ready; then
    echo GPU_MONTAGE_OVERLAY_REPAIR
    PIP_BREAK_SYSTEM_PACKAGES=1 PIP_ROOT_USER_ACTION=ignore \
      "$VPY" -m pip install --disable-pip-version-check \
        --upgrade-strategy only-if-needed -r "$MONTAGE_OVERLAY" || true
  fi
  if ! _diony_montage_ready; then
    # A lane whose packages did not land measures nothing; Production keeps
    # running without it and the receipt records the fields as unmeasured.
    echo GPU_MONTAGE_OVERLAY_UNAVAILABLE
    unset DIONY_WHISPER_MODEL DIONY_CLIP_MODEL
  fi
fi
if [ -x "$VPY" ]; then
  "$VPY" -c "from pathlib import Path; from diony_wan.runner import ensure_attention_fallback, ensure_negative_prompt_input, ensure_optional_engine_lazy_imports; ensure_attention_fallback(Path('/opt/wan22')); ensure_negative_prompt_input(Path('/opt/wan22')); ensure_optional_engine_lazy_imports(Path('/opt/wan22'))"
fi
_diony_wan_host_ready() {
  [ -f "$RUNTIME_PROBE" ] && [ -f "$RUNTIME_OVERLAY" ] && \
    PYTHONPATH=/opt/wan22 "$HOST_PY" "$RUNTIME_PROBE" \
      --require-capability s2v --require-capability animate \
      --write "$RUNTIME_REPORT" >/dev/null 2>&1
}
if ! _diony_wan_host_ready; then
  [ -f "$RUNTIME_PROBE" ] || { echo "GPU_RUNTIME_PROBE_MISSING"; exit 13; }
  [ -f "$RUNTIME_OVERLAY" ] || { echo "GPU_RUNTIME_OVERLAY_MISSING"; exit 13; }
  echo GPU_RUNTIME_OVERLAY_REPAIR
  PIP_BREAK_SYSTEM_PACKAGES=1 PIP_ROOT_USER_ACTION=ignore \
    "$HOST_PY" -m pip install --disable-pip-version-check \
      --upgrade-strategy only-if-needed -r "$RUNTIME_OVERLAY" \
      $( [ -f "$TTS_OVERLAY" ] && printf -- '-r %s' "$TTS_OVERLAY" )
fi
if ! _diony_wan_host_ready; then
  grep -viE '^[[:space:]]*(flash_attn|numpy)|^[[:space:]]*#' /opt/wan22/requirements.txt > /tmp/diony-wan-req.txt || true
  PIP_BREAK_SYSTEM_PACKAGES=1 PIP_ROOT_USER_ACTION=ignore \
    "$HOST_PY" -m pip install --disable-pip-version-check \
      --upgrade-strategy only-if-needed -r /tmp/diony-wan-req.txt -r "$RUNTIME_OVERLAY"
fi
if ! _diony_wan_host_ready; then
  if [ -f /opt/wan22/requirements_s2v.txt ]; then
    PIP_BREAK_SYSTEM_PACKAGES=1 PIP_ROOT_USER_ACTION=ignore \
      "$HOST_PY" -m pip install --disable-pip-version-check \
        --upgrade-strategy only-if-needed -r /opt/wan22/requirements_s2v.txt
  fi
  if [ -f /opt/wan22/requirements_animate.txt ]; then
    grep -viE '^[[:space:]]*-e[[:space:]]+git\+https://github.com/facebookresearch/sam2' \
      /opt/wan22/requirements_animate.txt > /tmp/diony-wan-animate-req.txt || true
    PIP_BREAK_SYSTEM_PACKAGES=1 PIP_ROOT_USER_ACTION=ignore \
      "$HOST_PY" -m pip install --disable-pip-version-check \
        --upgrade-strategy only-if-needed ninja -r /tmp/diony-wan-animate-req.txt
    PIP_BREAK_SYSTEM_PACKAGES=1 PIP_ROOT_USER_ACTION=ignore \
      "$HOST_PY" -m pip install --disable-pip-version-check \
        --no-build-isolation --upgrade-strategy only-if-needed \
        -e 'git+https://github.com/facebookresearch/sam2.git@0e78a118995e66bb27d78518c4bd9a3e95b4e266#egg=SAM-2'
  fi
fi
if ! _diony_wan_host_ready; then
  echo GPU_RUNTIME_MISSING
  exit 13
fi
if [ -x "$VPY" ] && [ -n "${DIONY_PYTHON:-}" ] && ! "$VPY" -c "import torch" >/dev/null 2>&1; then
  ROOT=$("$DIONY_PYTHON" -c "import torch, os; print(os.path.dirname(os.path.dirname(torch.__file__)))" 2>/dev/null || true)
  SP=$("$VPY" -c "import site; print(site.getsitepackages()[0])" 2>/dev/null || true)
  if [ -n "$ROOT" ] && [ -n "$SP" ]; then
    printf '%s\n' "$ROOT" > "$SP/diony-system-torch.pth"
  fi
fi
_diony_8788_pid() {
  local pid=""
  if command -v fuser >/dev/null 2>&1; then
    pid=$(fuser 8788/tcp 2>/dev/null | awk '{print $1}')
  elif command -v ss >/dev/null 2>&1; then
    pid=$(ss -ltnp 'sport = :8788' 2>/dev/null | sed -n 's/.*pid=\([0-9][0-9]*\).*/\1/p' | head -1)
  fi
  case "$pid" in
    *[!0-9]*|'') return 1 ;;
  esac
  printf '%s\n' "$pid"
}
_diony_existing_producer_ready() {
  curl -sf --max-time 2 http://127.0.0.1:8788/api/site >/dev/null || return 1
  PRODUCER_PID=$(_diony_8788_pid) || return 1
  [ -r "/proc/$PRODUCER_PID/environ" ] || return 1
  local active_env
  active_env=$(tr '\000' '\n' < "/proc/$PRODUCER_PID/environ")
  printf '%s\n' "$active_env" | grep -Fqx "DIONY_PYTHON=$DIONY_PYTHON" || return 1
  printf '%s\n' "$active_env" | grep -Fqx "DIONY_VOLUME_CAPACITY_GB=$DIONY_VOLUME_CAPACITY_GB" || return 1
  printf '%s\n' "$active_env" | grep -Fqx "DIONY_VOLUME_MIN_FREE_GB=$DIONY_VOLUME_MIN_FREE_GB" || return 1
  printf '%s\n' "$active_env" | grep -Fqx "DIONY_MODEL_HOT_CACHE=$DIONY_MODEL_HOT_CACHE" || return 1
  printf '%s\n' "$active_env" | grep -Fqx "DIONY_MODEL_HOT_CACHE_GB=$DIONY_MODEL_HOT_CACHE_GB" || return 1
  printf '%s\n' "$active_env" | grep -Fqx "DIONY_MODEL_HOT_CACHE_MIN_FREE_GB=$DIONY_MODEL_HOT_CACHE_MIN_FREE_GB" || return 1
  printf '%s\n' "$active_env" | grep -Fqx "DIONY_CODE_STAMP=$DIONY_CODE_STAMP" || return 1
  if [ -n "${DIONY_WAN_TTS_PROMPT_LIBRARY:-}" ]; then
    printf '%s\n' "$active_env" | grep -Fqx "DIONY_WAN_TTS_PROMPT_LIBRARY=$DIONY_WAN_TTS_PROMPT_LIBRARY" || return 1
  fi
  if [ -n "${DIONY_WAN_TTS_PROMPT_AUDIO:-}" ]; then
    printf '%s\n' "$active_env" | grep -Fqx "DIONY_WAN_TTS_PROMPT_AUDIO=$DIONY_WAN_TTS_PROMPT_AUDIO" || return 1
  fi
  if [ -n "${DIONY_WHISPER_MODEL:-}" ]; then
    printf '%s\n' "$active_env" | grep -Fqx "DIONY_WHISPER_MODEL=$DIONY_WHISPER_MODEL" || return 1
  fi
  if [ -n "${DIONY_CLIP_MODEL:-}" ]; then
    printf '%s\n' "$active_env" | grep -Fqx "DIONY_CLIP_MODEL=$DIONY_CLIP_MODEL" || return 1
  fi
  if [ -n "${DIONY_FACE_ID_RECOGNIZER_MODEL:-}" ]; then
    printf '%s\n' "$active_env" | grep -Fqx "DIONY_FACE_ID_RECOGNIZER_MODEL=$DIONY_FACE_ID_RECOGNIZER_MODEL" || return 1
  fi
}
_diony_production_busy() {
  # A code/env refresh may never kill the owner of a live camera, finish, or
  # mix. The next idle health check will apply the synchronized build.
  if pgrep -af '[g]enerate.py' >/dev/null 2>&1; then
    return 0
  fi
  local endpoint payload
  for endpoint in farm finish mix; do
    # Fail closed: a Producer that is alive but too busy to answer in time
    # (Wan loading its TTS models for the first spoken Shot) is
    # busy, not idle. An empty answer must never earn a kill.
    payload=$(curl -sf --max-time 6 "http://127.0.0.1:8788/api/$endpoint" 2>/dev/null) || return 0
    [ -n "$payload" ] || return 0
    if printf '%s' "$payload" | grep -Eq '"busy"[[:space:]]*:[[:space:]]*true'; then
      return 0
    fi
  done
  payload=$(curl -sf --max-time 6 http://127.0.0.1:8788/api/project 2>/dev/null) || return 0
  if printf '%s' "$payload" | grep -Eq '"status"[[:space:]]*:[[:space:]]*"shooting"'; then
    return 0
  fi
  return 1
}
_diony_starting_producer() {
  # uvicorn binds :8788 only after the lifespan resumes the camera (~2 min).
  # A young diony-producer carrying this exact code stamp is starting, and
  # killing it would only start the clock again.
  local pid age
  for pid in $(pgrep -f 'bin/diony-producer' 2>/dev/null); do
    [ -r "/proc/$pid/environ" ] || continue
    tr '\000' '\n' < "/proc/$pid/environ" | grep -Fqx "DIONY_CODE_STAMP=$DIONY_CODE_STAMP" || continue
    age=$(ps -o etimes= -p "$pid" 2>/dev/null | tr -d ' ')
    case "$age" in ''|*[!0-9]*) continue ;; esac
    if [ "$age" -lt 300 ]; then
      return 0
    fi
  done
  return 1
}
if _diony_existing_producer_ready; then
  echo GPU_PRODUCER_OK
  exit 0
fi
if _diony_starting_producer; then
  echo GPU_PRODUCER_STARTING
  exit 0
fi
if OLD_PRODUCER_PID=$(_diony_8788_pid); then
  if _diony_production_busy; then
    echo GPU_PRODUCER_BUSY_DEFERRED
    exit 0
  fi
  kill "$OLD_PRODUCER_PID" >/dev/null 2>&1 || true
  for _diony_stop_wait in 1 2 3 4 5; do
    kill -0 "$OLD_PRODUCER_PID" >/dev/null 2>&1 || break
    sleep 1
  done
  if kill -0 "$OLD_PRODUCER_PID" >/dev/null 2>&1; then
    echo GPU_PRODUCER_STOP_FAILED
    exit 14
  fi
fi
nohup env -u DIONY_DRY_RUN PATH="$PATH" DIONY_VAULT=/diony/vault DIONY_BIND=0.0.0.0 DIONY_WAN_REPO=/opt/wan22 \
  DIONY_PYTHON="$DIONY_PYTHON" \
  DIONY_VOLUME_CAPACITY_GB="$DIONY_VOLUME_CAPACITY_GB" \
  DIONY_VOLUME_MIN_FREE_GB="$DIONY_VOLUME_MIN_FREE_GB" \
  DIONY_MODEL_HOT_CACHE="$DIONY_MODEL_HOT_CACHE" \
  DIONY_MODEL_HOT_CACHE_GB="$DIONY_MODEL_HOT_CACHE_GB" \
  DIONY_MODEL_HOT_CACHE_MIN_FREE_GB="$DIONY_MODEL_HOT_CACHE_MIN_FREE_GB" \
  DIONY_CODE_STAMP="$DIONY_CODE_STAMP" \
  DIONY_WAN_TTS_PROMPT_LIBRARY="${DIONY_WAN_TTS_PROMPT_LIBRARY:-}" \
  DIONY_WAN_TTS_PROMPT_AUDIO="${DIONY_WAN_TTS_PROMPT_AUDIO:-}" \
  DIONY_WAN_TTS_PROMPT_TEXT="${DIONY_WAN_TTS_PROMPT_TEXT:-}" \
  DIONY_SOUND_LIBRARY="${DIONY_SOUND_LIBRARY:-}" \
  DIONY_WHISPER_MODEL="${DIONY_WHISPER_MODEL:-}" \
  DIONY_CLIP_MODEL="${DIONY_CLIP_MODEL:-}" \
  DIONY_FACE_ID_DETECTOR_MODEL="${DIONY_FACE_ID_DETECTOR_MODEL:-}" \
  DIONY_FACE_ID_RECOGNIZER_MODEL="${DIONY_FACE_ID_RECOGNIZER_MODEL:-}" \
  DIONY_AUDIOGEN="${DIONY_AUDIOGEN:-}" \
  DIONY_AUDIOGEN_COMMAND="${DIONY_AUDIOGEN_COMMAND:-}" \
  DIONY_AUDIOGEN_CLI_THINKSOUND="${DIONY_AUDIOGEN_CLI_THINKSOUND:-}" \
  DIONY_AUDIOGEN_CLI_ACE_STEP="${DIONY_AUDIOGEN_CLI_ACE_STEP:-}" \
  DIONY_AUDIOGEN_CLI_STABLE_AUDIO_OPEN="${DIONY_AUDIOGEN_CLI_STABLE_AUDIO_OPEN:-}" \
  "$PRODUCER_BIN" >/diony/vault/.cluster/producer.log 2>&1 &
echo GPU_PRODUCER_LAUNCHED
for _diony_start_wait in 1 2 3 4 5 6 7 8 9 10; do
  sleep 1
  if _diony_existing_producer_ready; then
    echo GPU_PRODUCER_OK
    exit 0
  fi
done
# The Producer resumes the camera before it binds :8788 (about two minutes
# on a signed lease). A launched process on this stamp is starting, not a
# mismatch; reporting failure here made the desk relaunch it in a loop.
if _diony_starting_producer; then
  echo GPU_PRODUCER_STARTING
  exit 0
fi
echo GPU_PRODUCER_ENV_MISMATCH
exit 14
"""
    return script.replace(
        "set -euo pipefail\n",
        "set -euo pipefail\nexport DIONY_CODE_STAMP="
        + shlex.quote(code_stamp)
        + "\n",
        1,
    )


def _mark_producer_started() -> None:
    global _last_producer_start_at
    _last_producer_start_at = time.time()


def _producer_starting() -> bool:
    """Did this desk (re)start the GPU Producer within the grace window?"""
    return 0 <= time.time() - _last_producer_start_at < _PRODUCER_START_GRACE_SECONDS


_ALIVE_PROBE_CACHE_SECONDS = 15.0
_alive_probe: tuple[float, bool] = (0.0, False)


def _remote_producer_alive(conn: dict) -> bool:
    """Is a diony-producer process present on the box right now (cached 15 s)?

    Grace means "give a starting process time", never "leave a dead one for
    five minutes": measured 2026-08-27, a killed Producer sat unrelaunched for
    the whole window while a mutation waited on it.
    """
    global _alive_probe
    at, alive = _alive_probe
    if time.time() - at < _ALIVE_PROBE_CACHE_SECONDS:
        return alive
    cmd = [
        "ssh",
        *_ssh_args(conn["ssh_port"], connect_timeout=6),
        f"{conn['ssh_user']}@{conn['host']}",
        "pgrep", "-f", "bin/diony-producer",
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=12, check=False)
        alive = proc.returncode == 0 and bool((proc.stdout or "").strip())
    except (OSError, subprocess.SubprocessError):
        alive = True  # the line, not the Producer, is in doubt: do not relaunch on a guess
    _alive_probe = (time.time(), alive)
    return alive


def _producer_coming_up(conn: dict) -> bool:
    """Within the grace window *and* a Producer process exists to wait for."""
    return _producer_starting() and _remote_producer_alive(conn)


def _start_gpu_producer(conn: dict) -> None:
    script = _gpu_producer_start_script()
    cmd = ["ssh", *_ssh_args(conn["ssh_port"]), f"{conn['ssh_user']}@{conn['host']}", "bash", "-s"]
    try:
        proc = subprocess.run(
            cmd,
            input=script,
            capture_output=True,
            text=True,
            timeout=900,
            check=False,
        )
    except subprocess.TimeoutExpired:
        _set_error(
            f"GPU Producer startup timed out on {conn['host']} after 15 minutes. "
            "The local Producer remains open; check Studio Connect and retry Produce."
        )
        return
    blob = (proc.stderr or proc.stdout or "").strip()
    if "GPU_PACK_MISSING" in (proc.stdout or "") or proc.returncode == 12:
        _set_error(
            "Studio App did not leave Wan2.2 on this GPU. "
            "App Continue, then Ready Test GPU. Produce farms that box, not this desk."
        )
        return
    if "GPU_RUNTIME_" in (proc.stdout or "") or proc.returncode == 13:
        _set_error(
            "Wan2.2 host Python dependencies could not be repaired. "
            "Studio requires a passing runtime report before Production."
        )
        return
    if proc.returncode == 0 and (
        "GPU_PRODUCER_OK" in (proc.stdout or "")
        or "GPU_PRODUCER_BUSY_DEFERRED" in (proc.stdout or "")
        or "GPU_PRODUCER_STARTING" in (proc.stdout or "")
    ):
        _set_error("")
        if "GPU_PRODUCER_LAUNCHED" in (proc.stdout or "") or "GPU_PRODUCER_STARTING" in (proc.stdout or ""):
            # We just launched it, or it is still coming up: give it the
            # grace window before anyone may restart it again.
            _mark_producer_started()
        return
    if proc.returncode != 0 or "GPU_PRODUCER_OK" not in (proc.stdout or ""):
        _set_error(blob[:800] or f"GPU Producer start failed on {conn['host']}")


def gpu_producer_up() -> bool:
    if not should_proxy():
        return False
    try:
        res = httpx.get(gpu_producer_base() + "/api/site", timeout=2.0)
        return res.status_code == 200
    except httpx.HTTPError:
        return False


def ensure_gpu_producer() -> str | None:
    """Open the Connect SSH line to GPU Producer. Never from pytest."""
    if not should_proxy():
        return None
    sync_connect_from_wizard()
    conn = connect_from_lock()
    if not conn:
        _set_error("Signed lease has no Connect host. Save Studio Connect, then Sign.")
        return None
    apply_ssh_env(conn)
    key = f"{conn['host']}:{conn['ssh_port']}"
    if _tunnel_key and _tunnel_key != key:
        _free_local_tunnel_port()
    if _port_open(TUNNEL_PORT) and (_tunnel_key in {"", key}):
        try:
            res = httpx.get(gpu_producer_base() + "/api/site", timeout=2.0)
            if res.status_code == 200:
                _set_error("")
                if (
                    _code_stamp()
                    and _code_stamp() != _synced_stamp
                    and time.time() - _sync_deferred_at >= _SYNC_DEFER_SECONDS
                    and not _producer_starting()
                    and _probe_ssh(conn)
                ):
                    persist_connect(conn)
                    if _sync_gpu_apps(conn):
                        # Only a landed push earns a restart; a deferred one
                        # would just run the start script to be told "busy".
                        _start_gpu_producer(conn)
                return gpu_producer_base()
        except httpx.HTTPError:
            pass
        if _producer_coming_up(conn) and (_tunnel is None or _tunnel.poll() is None):
            # The line is up and the Producer is still coming up behind it.
            _set_error("GPU Producer is starting; the desk will connect when it answers.")
            return None
        _free_local_tunnel_port()
    if not _ensure_lock.acquire(blocking=False):
        # Another request or the reconcile thread is already on the line;
        # a second start script would only fight the first for the Producer.
        return None
    try:
        if not _probe_ssh(conn):
            return None
        persist_connect(conn)
        if not _producer_coming_up(conn):
            _sync_gpu_apps(conn)
            _start_gpu_producer(conn)
        if not _ensure_tunnel(conn):
            return None
        try:
            res = httpx.get(gpu_producer_base() + "/api/site", timeout=4.0)
            if res.status_code == 200:
                _set_error("")
                return gpu_producer_base()
            _set_error(f"GPU Producer HTTP {res.status_code}")
        except httpx.HTTPError as exc:
            _set_error(str(exc)[:800] if not _producer_coming_up(conn) else "GPU Producer is starting; the desk will connect when it answers.")
        return None
    finally:
        _ensure_lock.release()


def sync_connect_from_wizard() -> None:
    """Keep site.json on the live Studio TCP line. Empty Studio does not wipe a signed lock."""
    if in_pytest():
        return
    lock = read_site_lock() or {}
    if not signed_gpu_site(lock):
        apply_ssh_env(connect_from_lock(lock))
        return
    conn = merge_connect(lock, _wizard_connect())
    if not conn:
        apply_ssh_env(connect_from_lock(lock))
        return
    persist_connect(conn)
    apply_ssh_env(conn)


def backfill_connect_from_wizard() -> None:
    sync_connect_from_wizard()


def gpu_block_copy() -> str:
    conn = connect_from_lock()
    host = conn.get("host") or "the leased GPU"
    port = conn.get("ssh_port") or "22"
    err = last_gpu_error()
    site = (read_site_lock() or {}).get("sku") or "lease"
    base = (
        f"{site} is signed. Wan2.2 is on that GPU, not on this desk. "
        f"Cannot reach Producer on {host}:{port}."
    )
    hint = (
        " After a pod restart the SSH over exposed TCP IP and -p change. "
        "Save the new line on Studio Connect, then Produce."
    )
    if err:
        if "Studio Connect" in err or "pod restart" in err:
            return f"{base} {err}"
        return f"{base} {err}{hint}"
    return f"{base}{hint}"


def drive_remote_advance(base: str) -> None:
    """Desk drives picture then mix on GPU Producer so Production does not wait for a click."""
    if in_pytest():
        return
    try:
        farm = httpx.get(base + "/api/farm", timeout=8.0).json()
        fin = httpx.get(base + "/api/finish", timeout=8.0).json()
        proj = httpx.get(base + "/api/project", timeout=8.0).json()
    except (httpx.HTTPError, ValueError):
        return
    if farm.get("busy") or fin.get("busy"):
        return
    shots = proj.get("shots") or []
    if not shots:
        return
    done = int(farm.get("done") or 0)
    total = int(farm.get("total") or len(shots) or 0)
    if not total or done < total:
        return
    takes = {item.get("id"): item for item in proj.get("takes") or []}
    for shot in shots:
        take = takes.get(shot.get("selected_take_id"))
        if not take:
            return
        if (take.get("qc") or {}).get("verdict") == "fail":
            return
    gates = {item.get("name"): item for item in proj.get("gates") or []}
    try:
        if gates.get("picture", {}).get("open"):
            httpx.post(
                base + "/api/gates/picture/lock",
                json={"locked_by": "producer"},
                timeout=30.0,
            )
            return
        if gates.get("mix", {}).get("open"):
            evidence = proj.get("mix") or {}
            if not evidence.get("stem_asset_ids") or evidence.get("measured_lufs") is None or evidence.get("true_peak_dbtp") is None:
                httpx.post(base + "/api/mix/start", timeout=30.0)
                return
            httpx.post(
                base + "/api/gates/mix/lock",
                json={"locked_by": "producer"},
                timeout=30.0,
            )
    except httpx.HTTPError:
        return


_MONITOR_CACHE: dict = {"at": 0.0, "row": None}

_ACCESS_MARK = ("HTTP/1.1", "GET /api/", "POST /api/", "127.0.0.1:")
_NOISE_MARK = (
    "Started server process",
    "Waiting for application",
    "Application startup",
    "Application shutdown",
    "Uvicorn running",
    "error while attempting to bind",
)


def parse_nvidia(raw: str) -> dict:
    """Turn nvidia-smi csv into a showrunner heartbeat."""
    line = ""
    for item in reversed((raw or "").strip().splitlines()):
        if item.strip():
            line = item.strip()
            break
    if not line:
        return {"name": "", "util": None, "used_mib": None, "total_mib": None, "heartbeat": ""}
    parts = [p.strip() for p in line.split(",")]
    name = parts[0].replace("NVIDIA", "").strip() if parts else ""

    def digits(value: str) -> int | None:
        num = "".join(ch for ch in value if ch.isdigit())
        return int(num) if num else None

    util = digits(parts[1]) if len(parts) > 1 else None
    used = digits(parts[2]) if len(parts) > 2 else None
    total = digits(parts[3]) if len(parts) > 3 else None
    bits = [name or "GPU"]
    if util is not None:
        bits.append(f"{util}%")
    if used is not None and total:
        bits.append(f"{used} / {total} MiB")
    elif used is not None:
        bits.append(f"{used} MiB")
    return {
        "name": name,
        "util": util,
        "used_mib": used,
        "total_mib": total,
        "heartbeat": " · ".join(bits),
    }


def parse_generate_ps(blob: str) -> str:
    """One line: generate.py · take · elapsed."""
    line = ""
    for raw in (blob or "").splitlines():
        if "generate.py" in raw and "grep" not in raw:
            line = raw.strip()
            break
    if not line:
        return ""
    etime = line.split()[0] if line.split() else ""
    take = ""
    hit = re.search(r"takes/([^/]+)/", line)
    if hit:
        take = hit.group(1)
    return " · ".join(part for part in ("generate.py", take, etime) if part)


def useful_producer_lines(blob: str) -> list[str]:
    """Drop uvicorn access polls. Keep tracebacks and generate.py noise."""
    out: list[str] = []
    for raw in (blob or "").splitlines():
        line = raw.strip().strip("\x00")
        if not line:
            continue
        if any(mark in line for mark in _ACCESS_MARK):
            continue
        if any(mark in line for mark in _NOISE_MARK):
            continue
        if line.startswith("INFO:") and "api/" in line.lower():
            continue
        out.append(line[-400:])
    return out[-16:]


def parse_activity_journal(blob: str) -> list[dict]:
    rows: list[dict] = []
    for raw in (blob or "").splitlines():
        line = raw.strip()
        if not line.startswith("{"):
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(row, dict) and str(row.get("text") or "").strip():
            rows.append(row)
    return rows[-24:]


def gpu_monitor() -> dict:
    """nvidia-smi, generate.py, activity journal. Never dump access logs. Pytest never SSH."""
    empty = {
        "heartbeat": "",
        "nvidia": "",
        "log": "",
        "generate": "",
        "journal": [],
        "observed_at": "",
        "gpu": {"name": "", "util": None, "used_mib": None, "total_mib": None},
    }
    if in_pytest():
        return empty
    if not should_proxy():
        try:
            journal = cluster_root() / ".cluster" / "activity.jsonl"
            if journal.is_file():
                empty["journal"] = parse_activity_journal(journal.read_text(encoding="utf-8"))
        except OSError:
            pass
        return empty
    err = last_gpu_error()
    if err and ("refused" in err.lower() or "Studio Connect" in err or "timed out" in err.lower()):
        now = time.time()
        cached = _MONITOR_CACHE.get("row")
        if cached and now - float(_MONITOR_CACHE.get("at") or 0) < 8:
            return cached
        _MONITOR_CACHE["at"] = now
        _MONITOR_CACHE["row"] = empty
        return empty
    now = time.time()
    cached = _MONITOR_CACHE.get("row")
    if cached and now - float(_MONITOR_CACHE.get("at") or 0) < 2.5:
        return cached
    conn = connect_from_lock()
    if not conn or not routable_host(conn["host"]):
        return empty
    script = r"""
nvidia-smi --query-gpu=name,utilization.gpu,memory.used,memory.total --format=csv,noheader 2>/dev/null || true
echo ---PS---
ps -eo etime,args 2>/dev/null | grep '[g]enerate.py' || true
echo ---JOURNAL---
tail -n 40 /diony/vault/.cluster/activity.jsonl 2>/dev/null || tail -n 40 /workspace/.cluster/activity.jsonl 2>/dev/null || true
echo ---LOG---
tail -n 80 /diony/vault/.cluster/producer.log 2>/dev/null || tail -n 80 /workspace/.cluster/producer.log 2>/dev/null || true
"""
    cmd = ["ssh", *_ssh_args(conn["ssh_port"]), f"{conn['ssh_user']}@{conn['host']}", "bash", "-s"]
    try:
        proc = subprocess.run(cmd, input=script, capture_output=True, text=True, timeout=12, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return empty
    blob = proc.stdout or ""
    nvi, _, rest = blob.partition("---PS---")
    ps_blob, _, rest = rest.partition("---JOURNAL---")
    journal_blob, _, log_blob = rest.partition("---LOG---")
    parsed = parse_nvidia(nvi)
    generate = parse_generate_ps(ps_blob)
    useful = useful_producer_lines(log_blob)
    heartbeat = parsed["heartbeat"]
    if generate and (parsed["util"] == 0 or (parsed["used_mib"] or 0) < 1024):
        heartbeat = f"{heartbeat} · loading weights" if heartbeat else "loading weights"
    elif generate and heartbeat:
        heartbeat = f"{heartbeat} · {generate}"
    row = {
        "heartbeat": heartbeat or ("GPU line is up" if generate else ""),
        "nvidia": nvi.strip().splitlines()[-1].strip() if nvi.strip() else "",
        "generate": generate,
        "journal": parse_activity_journal(journal_blob),
        "log": "\n".join(useful),
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "gpu": {
            "name": parsed["name"],
            "util": parsed["util"],
            "used_mib": parsed["used_mib"],
            "total_mib": parsed["total_mib"],
        },
    }
    _MONITOR_CACHE["at"] = now
    _MONITOR_CACHE["row"] = row
    return row


def cached_gpu_monitor(*, max_age_seconds: float = 12.0) -> dict:
    """Return the last background sample without opening SSH on a GET request."""
    row = _MONITOR_CACHE.get("row")
    age = time.time() - float(_MONITOR_CACHE.get("at") or 0)
    if isinstance(row, dict) and age <= max(0.0, max_age_seconds):
        return row
    return {
        "heartbeat": "",
        "nvidia": "",
        "log": "",
        "generate": "",
        "journal": [],
        "observed_at": "",
        "gpu": {"name": "", "util": None, "used_mib": None, "total_mib": None},
    }
