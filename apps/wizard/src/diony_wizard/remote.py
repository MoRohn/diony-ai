"""SSH or local shell for Dual-Spark / B300 App apply. Never logs tokens."""

from __future__ import annotations

import os
import shlex
import socket
import subprocess
from dataclasses import dataclass
from pathlib import Path


@dataclass
class RemoteResult:
    host: str
    ok: bool
    code: int
    stdout: str
    stderr: str

    @property
    def text(self) -> str:
        return (self.stdout or self.stderr or "").strip()


def ssh_user() -> str:
    return (
        os.environ.get("DIONY_SSH_USER", "").strip()
        or os.environ.get("USER", "").strip()
        or "spark"
    )


def apply_local(host: str) -> bool:
    if os.environ.get("DIONY_APPLY_LOCAL", "").strip().lower() in {"1", "true", "yes"}:
        return True
    name = (host or "").strip().lower()
    if name in {"localhost", "127.0.0.1", "::1"}:
        return True
    if name == socket.gethostname().lower():
        return True
    if Path("/models").is_dir() and name in {"spark-01", "b300-01"}:
        return True
    return False


def ssh_args() -> list[str]:
    args = [
        "-o",
        "BatchMode=yes",
        "-o",
        "StrictHostKeyChecking=accept-new",
        "-o",
        "ConnectTimeout=20",
    ]
    key = os.environ.get("DIONY_SSH_KEY", "").strip()
    if key:
        args.extend(["-i", key])
    return args


def exec_host(host: str, script: str, *, timeout: int = 3600) -> RemoteResult:
    """Run bash on host. Local when this process is that box; else SSH BatchMode."""
    env = os.environ.copy()
    env.pop("DASH_API_KEY", None)
    env.pop("DASHSCOPE_API_KEY", None)
    if apply_local(host):
        proc = subprocess.run(
            ["bash", "-lc", script],
            capture_output=True,
            text=True,
            timeout=timeout,
            env=env,
            check=False,
        )
        return RemoteResult(host, proc.returncode == 0, proc.returncode, proc.stdout, proc.stderr)
    user = ssh_user()
    cmd = ["ssh", *ssh_args(), f"{user}@{host}", "bash", "-s"]
    proc = subprocess.run(
        cmd,
        input=script,
        capture_output=True,
        text=True,
        timeout=timeout,
        env=env,
        check=False,
    )
    return RemoteResult(host, proc.returncode == 0, proc.returncode, proc.stdout, proc.stderr)


def rsync_repo(host: str, src: str, dest: str, timeout: int = 600) -> RemoteResult:
    if apply_local(host):
        return RemoteResult(host, True, 0, f"local repo {src}", "")
    user = ssh_user()
    cmd = [
        "rsync",
        "-az",
        "-e",
        " ".join(shlex.quote(p) for p in ["ssh", *ssh_args()]),
        "--delete",
        "--exclude",
        ".venv",
        "--exclude",
        "__pycache__",
        "--exclude",
        ".git",
        "--exclude",
        "apps/producer/data",
        src.rstrip("/") + "/",
        f"{user}@{host}:{dest.rstrip('/')}/",
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, check=False)
    return RemoteResult(host, proc.returncode == 0, proc.returncode, proc.stdout, proc.stderr)
