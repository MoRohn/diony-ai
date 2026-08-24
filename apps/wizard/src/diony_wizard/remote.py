"""SSH or local shell for Dual-Spark / B300 / B200 App apply. Never logs tokens."""

from __future__ import annotations

import os
import shlex
import socket
import subprocess
import threading
from collections.abc import Callable
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


def ssh_port() -> int:
    raw = os.environ.get("DIONY_SSH_PORT", "").strip()
    if not raw:
        return 22
    try:
        port = int(raw)
    except ValueError:
        return 22
    if port < 1 or port > 65535:
        return 22
    return port


def apply_local(host: str) -> bool:
    if os.environ.get("DIONY_APPLY_LOCAL", "").strip().lower() in {"1", "true", "yes"}:
        return True
    name = (host or "").strip().lower()
    if name in {"localhost", "127.0.0.1", "::1"}:
        return True
    if name == socket.gethostname().lower():
        return True
    if Path("/models").is_dir() and name in {"spark-01", "b300-01", "b200-01"}:
        return True
    return False


def ssh_connect_advice(host: str, port: int, text: str = "") -> str | None:
    """Operator copy when the TCP map is dead. RunPod remaps IP/-p on Stop/Resume."""
    blob = f"{text or ''}".lower()
    dead = (
        "connection refused" in blob
        or "connection timed out" in blob
        or "timed out" in blob
        or "no route to host" in blob
        or "network is unreachable" in blob
        or "operation timed out" in blob
    )
    if not dead:
        return None
    dest = f"{host}:{port}"
    return (
        f"{dest} is not accepting SSH. Resume the pod on RunPod. "
        "Open Connect. Copy the new SSH over exposed TCP IP and -p "
        "(not Jupyter 8888, not ssh.runpod.io). Then Pull pack."
    )


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
        args.extend(["-i", str(Path(key).expanduser())])
    port = ssh_port()
    if port != 22:
        args.extend(["-p", str(port)])
    return args


def _exec_streaming(
    host: str,
    cmd: list[str],
    script: str,
    *,
    timeout: int,
    env: dict[str, str],
    remote: bool,
    on_output: Callable[[str], None],
) -> RemoteResult:
    proc = subprocess.Popen(
        cmd,
        stdin=subprocess.PIPE if remote else subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
        env=env,
    )
    if remote and proc.stdin is not None:
        proc.stdin.write(script)
        proc.stdin.close()

    stdout: list[str] = []
    stderr: list[str] = []

    def drain(pipe, bucket: list[str]) -> None:
        if pipe is None:
            return
        for line in iter(pipe.readline, ""):
            bucket.append(line)
            try:
                on_output(line.rstrip("\r\n"))
            except Exception:
                continue
        pipe.close()

    readers = [
        threading.Thread(target=drain, args=(proc.stdout, stdout), daemon=True),
        threading.Thread(target=drain, args=(proc.stderr, stderr), daemon=True),
    ]
    for reader in readers:
        reader.start()
    try:
        code = proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()
        for reader in readers:
            reader.join(timeout=2)
        raise subprocess.TimeoutExpired(
            cmd,
            timeout,
            output="".join(stdout),
            stderr="".join(stderr),
        )
    for reader in readers:
        reader.join(timeout=2)
    return RemoteResult(host, code == 0, code, "".join(stdout), "".join(stderr))


def exec_host(
    host: str,
    script: str,
    *,
    timeout: int = 3600,
    on_output: Callable[[str], None] | None = None,
) -> RemoteResult:
    """Run bash on host. Local when this process is that box; else SSH BatchMode."""
    env = os.environ.copy()
    env.pop("DASH_API_KEY", None)
    env.pop("DASHSCOPE_API_KEY", None)
    local = apply_local(host)
    if on_output is not None:
        command = ["bash", "-lc", script] if local else [
            "ssh",
            *ssh_args(),
            f"{ssh_user()}@{host}",
            "bash",
            "-s",
        ]
        return _exec_streaming(
            host,
            command,
            script,
            timeout=timeout,
            env=env,
            remote=not local,
            on_output=on_output,
        )
    if local:
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
