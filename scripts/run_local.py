#!/usr/bin/env python3
"""Run Diony's three browser services as one local process group."""

from __future__ import annotations

import os
import signal
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
import webbrowser
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


@dataclass(frozen=True)
class Service:
    name: str
    module: str
    port_env: str
    default_port: int
    health_path: str

    @property
    def port(self) -> int:
        return int(os.environ.get(self.port_env, str(self.default_port)))

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}"


SERVICES = (
    Service("Producer", "diony_producer.app:app", "DIONY_PRODUCER_PORT", 8788, "/api/engine"),
    Service(
        "Director", "diony_director.app:app", "DIONY_DIRECTOR_PORT", 8790, "/api/director/status"
    ),
    Service("Studio", "diony_wizard.app:app", "DIONY_WIZARD_PORT", 8787, "/api/health"),
)


def _port_available(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind(("127.0.0.1", port))
        except OSError:
            return False
    return True


def _wait_ready(service: Service, process: subprocess.Popen, timeout: float = 30.0) -> None:
    deadline = time.monotonic() + timeout
    url = service.url + service.health_path
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(
                f"{service.name} exited during startup with code {process.returncode}."
            )
        try:
            with urllib.request.urlopen(url, timeout=1) as response:
                if response.status == 200:
                    return
        except (OSError, urllib.error.URLError):
            time.sleep(0.15)
    raise RuntimeError(f"{service.name} did not become ready at {url}.")


def _child_environment() -> dict[str, str]:
    env = os.environ.copy()
    vault = env.get("DIONY_VAULT", "").strip() or str(ROOT / ".diony-vault")
    env.setdefault("DIONY_VAULT", vault)
    env.setdefault("DIONY_PRODUCER_VAULT", vault)
    env.setdefault("DIONY_BIND", "127.0.0.1")
    for service in SERVICES:
        env.setdefault(service.port_env, str(service.default_port))
    env.setdefault("DIONY_STUDIO_URL", f"http://127.0.0.1:{env['DIONY_WIZARD_PORT']}")
    env.setdefault("DIONY_DIRECTOR_URL", f"http://127.0.0.1:{env['DIONY_DIRECTOR_PORT']}")
    env.setdefault("DIONY_PRODUCER_URL", f"http://127.0.0.1:{env['DIONY_PRODUCER_PORT']}")
    return env


def _stop(processes: list[subprocess.Popen]) -> None:
    for process in reversed(processes):
        if process.poll() is None:
            process.terminate()
    deadline = time.monotonic() + 5
    for process in reversed(processes):
        remaining = max(0.0, deadline - time.monotonic())
        try:
            process.wait(timeout=remaining)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()


def main() -> int:
    env = _child_environment()
    occupied = [
        f"{service.name} ({service.port})"
        for service in SERVICES
        if not _port_available(service.port)
    ]
    if occupied:
        print(
            "Diony cannot start because these localhost ports are already in use:", file=sys.stderr
        )
        for item in occupied:
            print(f"  - {item}", file=sys.stderr)
        return 1

    processes: list[subprocess.Popen] = []
    stopping = False

    def request_stop(_signum=None, _frame=None) -> None:
        nonlocal stopping
        stopping = True

    signal.signal(signal.SIGINT, request_stop)
    signal.signal(signal.SIGTERM, request_stop)

    try:
        for service in SERVICES:
            process = subprocess.Popen(
                [
                    sys.executable,
                    "-m",
                    "uvicorn",
                    service.module,
                    "--host",
                    env["DIONY_BIND"],
                    "--port",
                    str(service.port),
                ],
                cwd=ROOT,
                env=env,
            )
            processes.append(process)
            _wait_ready(service, process)
            print(f"{service.name}: {service.url}", flush=True)

        studio_url = env["DIONY_STUDIO_URL"]
        print("\nDiony is ready. Leave this terminal open; press Ctrl-C to stop.\n", flush=True)
        if env.get("DIONY_NO_BROWSER", "").strip().lower() not in {"1", "true", "yes"}:
            webbrowser.open(studio_url)

        while not stopping:
            failed = next((p for p in processes if p.poll() is not None), None)
            if failed is not None:
                raise RuntimeError(
                    f"A Diony service exited unexpectedly with code {failed.returncode}."
                )
            time.sleep(0.25)
    except KeyboardInterrupt:
        pass
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    finally:
        _stop(processes)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
