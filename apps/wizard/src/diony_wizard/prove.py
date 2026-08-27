"""SSH + nvidia-smi healthgate for Studio Ready.

Pytest never opens a socket. An operator click on Test GPU does SSH even when
the desk set DIONY_DRY_RUN=1 — that is how a laptop proves a RunPod B200.
"""

from __future__ import annotations

import json
import os
import re
import socket
from typing import Any

from diony_contracts.live import wizard_live
from diony_wizard.apply import _apply_fake
from diony_wizard.prep import SINGLE_GPU, VAULT_MOUNT
from diony_wizard.remote import exec_host, ssh_connect_advice, ssh_port, ssh_user

GPU_EXPECT = {
    "b200": {
        "want": ("B200",),
        "forbid": ("B300", "GB300", "H100", "H200", "RTX", "GB10"),
        "label": "NVIDIA B200",
        "vram": "180 GB",
    },
    "b300": {
        "want": ("B300", "GB300"),
        "forbid": ("B200", "H100", "H200", "RTX", "GB10"),
        "label": "NVIDIA B300",
        "vram": "288 GB class",
    },
    "dual": {
        "want": ("GB10", "Spark", "Grace"),
        "forbid": ("B200", "B300", "H100", "H200", "RTX"),
        "label": "DGX Spark GB10",
        "vram": "256 GB unified",
    },
}

REMOTE_PROBE = r"""
python3 - <<'PY'
import json, os, subprocess
from pathlib import Path

def smi():
    try:
        return subprocess.check_output(
            ["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader"],
            text=True,
            timeout=20,
        ).strip()
    except Exception as exc:
        return f"ERR {exc}"

vault = Path("/diony/vault")
workspace = Path("/workspace")
sr = Path("/models/diony/diony_sr_v1")
print(json.dumps({
    "hostname": os.uname().nodename if hasattr(os, "uname") else "",
    "gpu": smi(),
    "dashscope": bool(os.environ.get("DASH_API_KEY") or os.environ.get("DASHSCOPE_API_KEY")),
    "vault": vault.is_dir(),
    "workspace": workspace.is_dir(),
    "models": Path("/models").is_dir(),
    "deepseek": Path("/models/deepseek/DeepSeek-V4-Flash-0731").is_dir(),
    "wan": Path("/models/wan/Wan2.2-T2V-A14B").is_dir(),
    "sr": sr.is_dir() or bool(list(sr.glob("*.pth"))) if sr.parent.is_dir() else False,
}))
PY
"""


def in_pytest() -> bool:
    return bool(os.environ.get("PYTEST_CURRENT_TEST"))


def prove_clicks_live() -> bool:
    """Operator Test GPU may SSH. Pytest and DIONY_APPLY_FAKE never do."""
    if in_pytest() or _apply_fake():
        return False
    if os.environ.get("DIONY_PROVE_OFF", "").strip().lower() in {"1", "true", "yes"}:
        return False
    return True


def looks_like_ip(host: str) -> bool:
    text = (host or "").strip()
    return bool(re.fullmatch(r"\d{1,3}(?:\.\d{1,3}){3}", text))


def first_host(workbook: dict[str, Any], path: str) -> str:
    connect = workbook.get("connect") or {}
    raw = str(connect.get("hosts") or "").strip()
    if raw:
        return raw.split()[0]
    spec = SINGLE_GPU.get(path) or {}
    return str(spec.get("host") or "spark-01")


def connect_confirm(path: str, connect: dict[str, str] | None) -> dict[str, str]:
    row = connect or {}
    host = str(row.get("hosts") or "").split()[0] if row.get("hosts") else (
        (SINGLE_GPU.get(path) or {}).get("host") or "host"
    )
    user = str(row.get("ssh_user") or ("root" if path in SINGLE_GPU else "spark")).strip()
    port = str(row.get("ssh_port") or "22").strip() or "22"
    vault = str(row.get("nas_ip") or "/workspace").strip()
    spec = SINGLE_GPU.get(path) or {}
    gpu = spec.get("gpu_name") or ("DGX Spark GB10 pair" if path == "dual" else "")
    return {
        "ssh": f"ssh {user}@{host} -p {port}",
        "vault": f"{vault} → {VAULT_MOUNT}",
        "gpu": gpu,
        "note": "Pull pack uses this SSH line. Not Jupyter 8888. Not ssh.runpod.io. RunPod remaps IP and -p after Stop.",
    }


def _check(cid: str, label: str, ok: bool, detail: str, *, dry_run: bool = False) -> dict:
    return {
        "id": cid,
        "label": label,
        "ok": bool(ok),
        "detail": (detail or "")[:240],
        "dry_run": bool(dry_run),
    }


def dry_run_checks(path: str, workbook: dict[str, Any]) -> list[dict]:
    spec = SINGLE_GPU.get(path) or {}
    host = first_host(workbook, path)
    gpu = spec.get("gpu_name") or ("DGX Spark GB10" if path == "dual" else path)
    vram = spec.get("vram") or ""
    return [
        _check("ssh", "SSH", True, f"dry-run recorded · {host}", dry_run=True),
        _check("gpu", "GPU", True, f"dry-run recorded · expect {gpu} {vram}".strip(), dry_run=True),
        _check("vault", "Vault", True, f"dry-run recorded · {VAULT_MOUNT}", dry_run=True),
        _check("dashscope", "No Dashscope", True, "dry-run recorded · keys unset on apply", dry_run=True),
        _check("pack", "Pack", True, "dry-run recorded · App Continue did not SSH", dry_run=True),
    ]


def match_gpu(path: str, smi: str) -> tuple[bool, str]:
    expect = GPU_EXPECT.get(path)
    text = smi or ""
    if text.startswith("ERR") or not text.strip():
        return False, text or "nvidia-smi did not run."
    if expect is None:
        return True, text.split("\n")[0][:120]
    upper = text.upper()
    for bad in expect["forbid"]:
        if bad.upper() in upper:
            return False, f"Wrong GPU for this Site. nvidia-smi: {text.splitlines()[0][:160]}"
    if not any(token.upper() in upper for token in expect["want"]):
        return False, (
            f"Need {expect['label']}. nvidia-smi: {text.splitlines()[0][:160]}"
        )
    return True, text.splitlines()[0][:160]


def _tcp_ok(host: str, port: int, timeout: float = 5.0) -> tuple[bool, str]:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True, f"{host}:{port} accepted TCP"
    except OSError as exc:
        return False, f"{host}:{port} {exc}"


def run_probe(path: str, workbook: dict[str, Any], *, scope: str = "gpu") -> dict:
    """Run connect TCP/SSH and/or GPU checks. Never used by pytest."""
    host = first_host(workbook, path)
    port = ssh_port()
    user = ssh_user()
    checks: list[dict] = []

    if scope in {"connect", "gpu"}:
        if looks_like_ip(host) or host not in {"b200-01", "b300-01", "spark-01", "localhost"}:
            ok, detail = _tcp_ok(host, port)
            if not ok:
                detail = ssh_connect_advice(host, port, detail) or detail
            checks.append(_check("tcp", "TCP port", ok, detail))
        else:
            checks.append(
                _check(
                    "tcp",
                    "TCP port",
                    True,
                    f"{host} is a Diony name. Type the RunPod IP to probe the pod.",
                    dry_run=True,
                )
            )

    ssh = exec_host(host, "echo SSH_OK && hostname", timeout=25)
    ssh_ok = ssh.ok and "SSH_OK" in (ssh.stdout or "")
    ssh_detail = (
        ssh_connect_advice(host, port, ssh.text)
        or (ssh.text[:400] if ssh.text else f"{user}@{host}:{port}")
    )
    checks.append(
        _check(
            "ssh",
            "SSH",
            ssh_ok,
            ssh_detail,
        )
    )
    if scope == "connect" or not ssh_ok:
        ok = all(c["ok"] for c in checks)
        return {
            "ok": ok,
            "live": True,
            "host": host,
            "user": user,
            "port": str(port),
            "scope": scope,
            "checks": checks,
        }

    remote = exec_host(host, REMOTE_PROBE, timeout=40)
    payload: dict[str, Any] = {}
    if remote.ok:
        try:
            payload = json.loads((remote.stdout or "").strip().splitlines()[-1])
        except (json.JSONDecodeError, IndexError):
            payload = {}
    if not payload:
        checks.append(
            _check(
                "gpu",
                "GPU",
                False,
                (remote.text or "nvidia-smi probe failed")[:200],
            )
        )
    else:
        gpu_ok, gpu_detail = match_gpu(path, str(payload.get("gpu") or ""))
        checks.append(_check("gpu", "GPU", gpu_ok, gpu_detail))
        vault_ok = bool(payload.get("vault") or payload.get("workspace"))
        vault_detail = (
            f"{VAULT_MOUNT} present"
            if payload.get("vault")
            else "/workspace present"
            if payload.get("workspace")
            else "Need /diony/vault or /workspace on this GPU"
        )
        checks.append(_check("vault", "Vault", vault_ok, vault_detail))
        dash = bool(payload.get("dashscope"))
        checks.append(
            _check(
                "dashscope",
                "No Dashscope",
                not dash,
                "DASH_API_KEY is set on the GPU" if dash else "No Dashscope keys on this GPU",
            )
        )
        pack_ok = bool(payload.get("deepseek") and payload.get("wan") and payload.get("sr"))
        missing = [
            name
            for name, key in (
                ("DeepSeek 0731", "deepseek"),
                ("Wan2.2 A14B", "wan"),
                ("diony_sr_v1", "sr"),
            )
            if not payload.get(key)
        ]
        checks.append(
            _check(
                "pack",
                "Pack",
                pack_ok,
                "DeepSeek + Wan 720p + diony_sr_v1 on /models"
                if pack_ok
                else ("Missing: " + ", ".join(missing) + ". Pull pack on this line."),
            )
        )

    ok = all(c["ok"] for c in checks)
    return {
        "ok": ok,
        "live": True,
        "host": host,
        "user": user,
        "port": str(port),
        "scope": scope,
        "checks": checks,
        "raw": payload,
    }


def identify_gpu(path: str, workbook: dict[str, Any]) -> dict:
    """Is the signed GPU still on its Connect line? TCP, SSH, then nvidia-smi.

    Lighter than ``run_probe``: it answers "which GPU is at this line", not
    "is the pack complete". Worst case is one TCP timeout plus one SSH
    timeout. Never used by pytest.
    """
    host = first_host(workbook, path)
    port = ssh_port()
    user = ssh_user()
    checks: list[dict] = []
    base = {"host": host, "port": str(port), "user": user, "gpu": ""}
    if looks_like_ip(host):
        ok, detail = _tcp_ok(host, port)
        if not ok:
            detail = ssh_connect_advice(host, port, detail) or detail
        checks.append(_check("tcp", "TCP port", ok, detail))
        if not ok:
            return {**base, "ok": False, "detail": detail, "checks": checks}
    ssh = exec_host(
        host,
        "nvidia-smi --query-gpu=name,memory.total --format=csv,noheader",
        timeout=25,
    )
    smi = (ssh.stdout or "").strip()
    if not ssh.ok or not smi:
        detail = ssh_connect_advice(host, port, ssh.text) or (
            ssh.text[:240] if ssh.text else f"{user}@{host}:{port} did not answer"
        )
        checks.append(_check("ssh", "SSH", False, detail))
        return {**base, "ok": False, "detail": detail, "checks": checks}
    checks.append(_check("ssh", "SSH", True, f"{user}@{host}:{port}"))
    gpu_ok, gpu_detail = match_gpu(path, smi)
    checks.append(_check("gpu", "GPU", gpu_ok, gpu_detail))
    return {
        **base,
        "ok": gpu_ok,
        "gpu": smi.splitlines()[0][:160],
        "detail": gpu_detail,
        "checks": checks,
    }


def record_dry_run(path: str, workbook: dict[str, Any], *, scope: str = "gpu") -> dict:
    checks = dry_run_checks(path, workbook)
    if scope == "connect":
        checks = [c for c in checks if c["id"] in {"ssh", "tcp"}] or checks[:1]
    return {
        "ok": True,
        "live": False,
        "host": first_host(workbook, path),
        "user": ssh_user(),
        "port": str(ssh_port()),
        "scope": scope,
        "checks": checks,
        "raw": {},
    }


def prove_must_pass(path: str, workbook: dict[str, Any]) -> bool:
    """Real IP or live Studio: Ready Continue requires a green Test GPU."""
    if in_pytest() or _apply_fake():
        return False
    host = first_host(workbook, path)
    return looks_like_ip(host) or wizard_live()


def pack_missing(row: dict | None) -> bool:
    for item in (row or {}).get("checks") or []:
        if item.get("id") == "pack" and not item.get("ok"):
            return True
    return False


def prove_block_reason(row: dict | None, *, applying: bool = False) -> str | None:
    if applying:
        return "Pack is still pulling."
    fails = [item for item in (row or {}).get("checks") or [] if not item.get("ok")]
    if not fails:
        return None
    if any(item.get("id") == "pack" for item in fails):
        return "Pack is missing. Pull pack, then Test GPU."
    detail = str(fails[0].get("detail") or "").strip()
    return detail or "Fix the red row, then Test GPU."


def public_prove(row: dict | None) -> dict:
    data = row or {}
    checks = list(data.get("checks") or [])
    return {
        "ok": bool(data.get("ok")),
        "live": bool(data.get("live")),
        "host": str(data.get("host") or ""),
        "user": str(data.get("user") or ""),
        "port": str(data.get("port") or ""),
        "scope": str(data.get("scope") or "gpu"),
        "checks": checks,
        "pack_ok": not pack_missing(data),
    }
