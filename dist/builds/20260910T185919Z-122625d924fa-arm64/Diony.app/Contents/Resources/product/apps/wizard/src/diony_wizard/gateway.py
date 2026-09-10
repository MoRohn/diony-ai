"""Product names: hosts file + :80 Host router.

http://studio.diony.local → 127.0.0.1:8787
http://producer.diony.local → 127.0.0.1:8788
http://director.diony.local → 127.0.0.1:8790
"""

from __future__ import annotations

import http.client
import json
import os
import socket
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

STUDIO_HOST = "studio.diony.local"
PRODUCER_HOST = "producer.diony.local"
DIRECTOR_HOST = "director.diony.local"
WIZARD_PORT = 8787
PRODUCER_PORT = 8788
DIRECTOR_PORT = 8790
GATEWAY_PORT = 80
HOSTS_MARK = "# diony-ai product names"
NAMES_HEADER = "X-Diony-Names"
NAMES_GEN_HEADER = "X-Diony-Names-Gen"
# Bump when router behavior changes (gen 2: 300s forward timeout, director route).
# A router advertising an older generation reads as stale and gets replaced.
NAMES_GEN = "2"


def wizard_port() -> int:
    return int(os.environ.get("DIONY_WIZARD_PORT", str(WIZARD_PORT)))


def producer_port() -> int:
    return int(os.environ.get("DIONY_PRODUCER_PORT", str(PRODUCER_PORT)))


def director_port() -> int:
    return int(os.environ.get("DIONY_DIRECTOR_PORT", str(DIRECTOR_PORT)))


def gateway_port() -> int:
    return int(os.environ.get("DIONY_NAMES_PORT", str(GATEWAY_PORT)))


def hosts_path() -> Path:
    raw = os.environ.get("DIONY_HOSTS_PATH", "").strip()
    if raw:
        return Path(raw)
    if os.name == "nt":
        root = os.environ.get("SystemRoot", r"C:\Windows")
        return Path(root) / "System32" / "drivers" / "etc" / "hosts"
    return Path("/etc/hosts")


def hosts_block() -> str:
    return (
        f"{HOSTS_MARK}\n"
        f"127.0.0.1 {STUDIO_HOST}\n"
        f"127.0.0.1 {PRODUCER_HOST}\n"
        f"127.0.0.1 {DIRECTOR_HOST}\n"
        f"::1 {STUDIO_HOST}\n"
        f"::1 {PRODUCER_HOST}\n"
        f"::1 {DIRECTOR_HOST}\n"
    )


def apply_hosts_text(text: str) -> str:
    """Idempotent hosts file body with both product names on loopback."""
    keep: list[str] = []
    skipping = False
    for line in (text or "").splitlines():
        stripped = line.strip()
        if stripped == HOSTS_MARK:
            skipping = True
            continue
        if skipping:
            if not stripped or "diony.local" in line:
                continue
            skipping = False
        if "diony.local" in line:
            continue
        keep.append(line.rstrip("\n"))
    while keep and not keep[-1].strip():
        keep.pop()
    keep.append("")
    keep.append(hosts_block().rstrip("\n"))
    return "\n".join(keep) + "\n"


def hosts_ready(path: Path | None = None) -> bool:
    dest = path or hosts_path()
    try:
        text = dest.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False
    return STUDIO_HOST in text and PRODUCER_HOST in text and DIRECTOR_HOST in text


def write_hosts(path: Path | None = None) -> bool:
    dest = path or hosts_path()
    try:
        current = dest.read_text(encoding="utf-8", errors="replace") if dest.is_file() else ""
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(apply_hosts_text(current), encoding="utf-8")
        return hosts_ready(dest)
    except OSError:
        return False


def _may_prompt() -> bool:
    if os.environ.get("PYTEST_CURRENT_TEST"):
        return False
    skip = os.environ.get("DIONY_NAMES_SKIP", "").strip().lower()
    if skip in {"1", "true", "yes"}:
        return False
    prompt = os.environ.get("DIONY_NAMES_PROMPT", "1").strip().lower()
    return prompt not in {"0", "false", "no"}


def _python() -> str:
    return sys.executable


def _gateway_argv(command: str) -> list[str]:
    if bool(getattr(sys, "frozen", False)):
        return [_python(), "--service", "gateway", command]
    return [_python(), "-m", "diony_wizard.gateway", command]


def _cmdline(args: list[str]) -> str:
    if os.name == "nt":
        return subprocess.list2cmdline(args)
    import shlex

    return shlex.join(args)


def _log_path() -> Path:
    raw = os.environ.get("DIONY_NAMES_LOG", "").strip()
    if raw:
        return Path(raw)
    if os.name == "nt":
        return Path(os.environ.get("TEMP", ".")) / "diony-names.log"
    return Path("/tmp/diony-names.log")


def _log_handle():
    """Prefer /tmp, then ~/.diony. Never fail install because a root-owned log is leftover."""
    tried = [_log_path()]
    if os.name != "nt":
        tried.append(Path.home() / ".diony" / "names.log")
    for path in tried:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            return path.open("ab")
        except OSError:
            continue
    return subprocess.DEVNULL


def privileged_install_argv() -> list[str]:
    """Foreground admin command. install writes hosts, detaches serve, then exits."""
    return _gateway_argv("install")


def flush_name_cache() -> None:
    """Drop stale NXDOMAIN for studio.diony.local after /etc/hosts changes."""
    try:
        if sys.platform == "darwin":
            subprocess.run(["dscacheutil", "-flushcache"], capture_output=True, timeout=8)
            subprocess.run(["killall", "-HUP", "mDNSResponder"], capture_output=True, timeout=8)
        elif os.name == "nt":
            subprocess.run(["ipconfig", "/flushdns"], capture_output=True, timeout=8)
    except (OSError, subprocess.SubprocessError):
        return


def _note_privilege_error(proc: subprocess.CompletedProcess[str]) -> None:
    text = ((proc.stderr or "") + "\n" + (proc.stdout or "")).strip()[-2000:]
    if not text:
        return
    try:
        dest = _log_path()
        dest.write_text(text + "\n", encoding="utf-8")
    except OSError:
        return


def route_label(host: str) -> str:
    """Host header → service label. Unknown names fall back to Studio."""
    name = (host or "").split(":")[0].lower()
    if name.startswith("producer."):
        return "producer"
    if name.startswith("director."):
        return "director"
    return "studio"


def handler_class(
    wizard: int, producer: int, director: int | None = None
) -> type[BaseHTTPRequestHandler]:
    director_backend = director if director is not None else director_port()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: object) -> None:
            return

        def do_GET(self) -> None:
            self._forward()

        def do_HEAD(self) -> None:
            self._forward()

        def do_POST(self) -> None:
            self._forward()

        def do_PUT(self) -> None:
            self._forward()

        def do_PATCH(self) -> None:
            self._forward()

        def do_DELETE(self) -> None:
            self._forward()

        def do_OPTIONS(self) -> None:
            self._forward()

        def _label(self) -> str:
            return route_label(self.headers.get("Host") or "")

        def _backend(self) -> int:
            label = self._label()
            if label == "producer":
                return producer
            if label == "director":
                return director_backend
            return wizard

        def _forward(self) -> None:
            label = self._label()
            port = self._backend()
            length = int(self.headers.get("Content-Length") or 0)
            body = self.rfile.read(length) if length else None
            headers = {
                k: v for k, v in self.headers.items() if k.lower() not in {"host", "connection"}
            }
            conn = http.client.HTTPConnection("127.0.0.1", port, timeout=300)
            try:
                conn.request(self.command, self.path, body=body, headers=headers)
                resp = conn.getresponse()
                data = resp.read()
            except OSError:
                msg = b"Studio, Producer, or Director is not up.\n"
                self.send_response(502)
                self.send_header("Content-Type", "text/plain; charset=utf-8")
                self.send_header(NAMES_HEADER, label)
                self.send_header(NAMES_GEN_HEADER, NAMES_GEN)
                self.send_header("Content-Length", str(len(msg)))
                self.end_headers()
                if self.command != "HEAD":
                    self.wfile.write(msg)
                return
            finally:
                conn.close()
            self.send_response(resp.status)
            self.send_header(NAMES_HEADER, label)
            self.send_header(NAMES_GEN_HEADER, NAMES_GEN)
            for key, value in resp.getheaders():
                if key.lower() not in {"transfer-encoding", "connection", NAMES_HEADER.lower()}:
                    self.send_header(key, value)
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(data)

    return Handler


def gateway_healthy(port: int | None = None, timeout: float = 0.6) -> bool:
    dest = gateway_port() if port is None else port
    try:
        conn = http.client.HTTPConnection("127.0.0.1", dest, timeout=timeout)
        conn.request("GET", "/api/state", headers={"Host": STUDIO_HOST})
        resp = conn.getresponse()
        resp.read()
        names = (resp.getheader(NAMES_HEADER) or "").lower()
        conn.close()
    except OSError:
        return False
    # `wizard` is the label emitted by the first installed Diony gateway.
    # Accept it so an existing privileged router does not trigger a password loop.
    return names.startswith(("studio", "wizard"))


def _route_label_at(port: int, host: str, timeout: float = 0.6) -> str:
    label, _gen = _route_probe_at(port, host, timeout)
    return label


def _route_probe_at(port: int, host: str, timeout: float = 0.6) -> tuple[str, str]:
    try:
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=timeout)
        conn.request("GET", "/", headers={"Host": host})
        resp = conn.getresponse()
        resp.read()
        label = (resp.getheader(NAMES_HEADER) or "").lower()
        gen = (resp.getheader(NAMES_GEN_HEADER) or "").strip()
        conn.close()
    except OSError:
        return "", ""
    return label, gen


def gateway_current(port: int | None = None, timeout: float = 0.6) -> bool:
    """A Diony router answers AND it routes all three product names.

    A pre-Director router labels director.diony.local as studio/wizard — that
    router is healthy but stale, and stale must be replaceable, not accepted.
    """
    dest = gateway_port() if port is None else port
    if not gateway_healthy(dest, timeout):
        return False
    label, gen = _route_probe_at(dest, DIRECTOR_HOST, timeout)
    return label.startswith("director") and gen == NAMES_GEN


def _gateway_listener_pids(port: int) -> list[int]:
    if os.name == "nt":
        try:
            out = subprocess.check_output(
                ["netstat", "-ano", "-p", "TCP"],
                text=True,
                timeout=8,
                stderr=subprocess.DEVNULL,
            )
        except (OSError, subprocess.SubprocessError):
            return []
        pids: list[int] = []
        for line in out.splitlines():
            if "LISTEN" not in line.upper() or f":{port}" not in line:
                continue
            try:
                pids.append(int(line.split()[-1]))
            except ValueError:
                continue
        return list(dict.fromkeys(pids))
    try:
        out = subprocess.check_output(
            ["lsof", "-nP", f"-iTCP:{port}", "-sTCP:LISTEN", "-t"],
            text=True,
            timeout=5,
            stderr=subprocess.DEVNULL,
        )
    except (OSError, subprocess.SubprocessError):
        return []
    pids = []
    for token in out.split():
        try:
            pids.append(int(token))
        except ValueError:
            continue
    return list(dict.fromkeys(pids))


def _process_command(pid: int) -> str:
    try:
        if os.name == "nt":
            query = (
                '$p=Get-CimInstance Win32_Process -Filter "ProcessId = '
                + str(int(pid))
                + '"; if ($p) {$p.CommandLine}'
            )
            return subprocess.check_output(
                ["powershell", "-NoProfile", "-Command", query],
                text=True,
                timeout=8,
                stderr=subprocess.DEVNULL,
            ).strip()
        return subprocess.check_output(
            ["ps", "-p", str(int(pid)), "-o", "command="],
            text=True,
            timeout=5,
            stderr=subprocess.DEVNULL,
        ).strip()
    except (OSError, subprocess.SubprocessError, ValueError):
        return ""


GATEWAY_PROCESS_MARKS = ("diony_wizard.gateway", "--service gateway", "diony-names", "diony")


def stop_stale_gateway(port: int | None = None) -> bool:
    """Stop a stale Diony router so a current one can bind. Never touches
    a foreign server: the listener must answer with our names header AND the
    process command must look like a Diony gateway."""
    dest = gateway_port() if port is None else port
    if not _route_label_at(dest, STUDIO_HOST):
        return not _gateway_listener_pids(dest)
    victims = [
        pid
        for pid in _gateway_listener_pids(dest)
        if any(mark in _process_command(pid).lower() for mark in GATEWAY_PROCESS_MARKS)
    ]
    if not victims:
        return not _gateway_listener_pids(dest)
    import signal as _signal
    import time as _time

    for pid in victims:
        try:
            os.kill(pid, _signal.SIGTERM)
        except (OSError, PermissionError):
            pass
    deadline = _time.monotonic() + 4.0
    while _time.monotonic() < deadline:
        if not _gateway_listener_pids(dest):
            return True
        _time.sleep(0.2)
    for pid in victims:
        try:
            os.kill(pid, _signal.SIGKILL)
        except (OSError, PermissionError, AttributeError):
            pass
    _time.sleep(0.3)
    return not _gateway_listener_pids(dest)


def _bind_server(
    host: str, port: int, family: int, handler: type[BaseHTTPRequestHandler]
) -> ThreadingHTTPServer | None:
    class Server(ThreadingHTTPServer):
        address_family = family
        allow_reuse_address = True

        def server_bind(self) -> None:
            if family == socket.AF_INET6:
                try:
                    self.socket.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 1)
                except OSError:
                    pass
            super().server_bind()

    try:
        return Server((host, port), handler)
    except OSError:
        return None


def start_product_gateway(
    port: int | None = None,
    *,
    wizard: int | None = None,
    producer: int | None = None,
    director: int | None = None,
    background: bool = True,
) -> bool:
    """Bind the Host router: studio/producer/director.diony.local → their ports."""
    dest = gateway_port() if port is None else port
    if gateway_current(dest):
        return True
    handler = handler_class(
        wizard if wizard is not None else wizard_port(),
        producer if producer is not None else producer_port(),
        director if director is not None else director_port(),
    )
    ThreadingHTTPServer.allow_reuse_address = True
    servers: list[ThreadingHTTPServer] = []
    for host, family in (("127.0.0.1", socket.AF_INET), ("::1", socket.AF_INET6)):
        srv = _bind_server(host, dest, family, handler)
        if srv:
            servers.append(srv)
    if not servers:
        return gateway_current(dest)
    for srv in servers:
        thread = threading.Thread(
            target=srv.serve_forever,
            name="diony-names",
            daemon=background,
        )
        thread.start()
    if not background:
        print(f"Diony names on 127.0.0.1:{dest}", flush=True)
        threading.Event().wait()
    return True


def _wait_healthy(port: int, tries: int = 80) -> bool:
    for _ in range(tries):
        if gateway_current(port):
            return True
        threading.Event().wait(0.15)
    return gateway_current(port)


def _detach_serve() -> None:
    """Start `gateway serve` in a new session so the password helper can exit."""
    handle = _log_handle()
    env = os.environ.copy()
    env["PYTHONUNBUFFERED"] = "1"
    env.setdefault("DIONY_NAMES_PORT", str(gateway_port()))
    kwargs: dict = {
        "args": _gateway_argv("serve"),
        "stdin": subprocess.DEVNULL,
        "stdout": handle,
        "stderr": subprocess.STDOUT,
        "close_fds": True,
        "env": env,
    }
    if os.name == "nt":
        kwargs["creationflags"] = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        kwargs["start_new_session"] = True
    subprocess.Popen(**kwargs)


def _spawn_privileged_install() -> bool:
    """One-time administrator prompt. install itself returns; serve stays up."""
    cmd = privileged_install_argv()
    if sys.platform == "darwin":
        script = f"do shell script {json.dumps(_cmdline(cmd))} with administrator privileges"
        proc = subprocess.run(
            ["osascript", "-e", script], capture_output=True, text=True, timeout=180
        )
        if proc.returncode != 0:
            _note_privilege_error(proc)
        return proc.returncode == 0
    if os.name == "nt":
        gateway_args = _gateway_argv("install")[1:]
        ps = (
            "Start-Process -FilePath "
            + json.dumps(_python())
            + " -ArgumentList "
            + json.dumps(subprocess.list2cmdline(gateway_args))
            + " -Verb RunAs -Wait"
        )
        proc = subprocess.run(
            ["powershell", "-Command", ps], capture_output=True, text=True, timeout=180
        )
        if proc.returncode != 0:
            _note_privilege_error(proc)
        return proc.returncode == 0
    for tool in ("pkexec", "sudo"):
        proc = subprocess.run([tool, *cmd], capture_output=True, text=True, timeout=180)
        if proc.returncode == 0:
            return True
        _note_privilege_error(proc)
    return False


def ensure_product_urls(*, prompt: bool | None = None) -> bool:
    """Hosts file + :80 router. Password prompt is once, only if this machine still lacks names."""
    if prompt is False:
        os.environ["DIONY_NAMES_PROMPT"] = "0"
    dest = gateway_port()
    hosts_ok = hosts_ready() or write_hosts()
    start_product_gateway(dest)
    if hosts_ok and _wait_healthy(dest, tries=10):
        return True
    if not _may_prompt():
        return hosts_ready() and gateway_current(dest)
    if _spawn_privileged_install():
        return hosts_ready() and _wait_healthy(dest, tries=80)
    return hosts_ready() and gateway_current(dest)


def cmd_write_hosts() -> int:
    return 0 if write_hosts() else 1


def cmd_serve() -> int:
    dest = gateway_port()
    if gateway_current(dest):
        threading.Event().wait()
        return 0
    stop_stale_gateway(dest)
    start_product_gateway(dest, background=False)
    return 1


def cmd_install() -> int:
    if not write_hosts() and not hosts_ready():
        return 1
    flush_name_cache()
    dest = gateway_port()
    if gateway_current(dest):
        return 0
    # A pre-Director router may hold :80; install runs privileged, so it can
    # retire the stale Diony router before serving the current one.
    stop_stale_gateway(dest)
    try:
        _detach_serve()
    except OSError as exc:
        print(f"Could not start Diony names: {exc}", file=sys.stderr)
        return 1
    return 0 if _wait_healthy(dest, tries=80) else 1


def cmd_ensure() -> int:
    ok = ensure_product_urls()
    if ok:
        print(f"http://{STUDIO_HOST}")
        print(f"http://{PRODUCER_HOST}")
        print(f"http://{DIRECTOR_HOST}")
        return 0
    print(
        "Could not lay studio.diony.local. Approve the administrator prompt, "
        "or run: python -m diony_wizard.gateway ensure",
        file=sys.stderr,
    )
    return 1


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    cmd = args[0] if args else "ensure"
    if cmd in {"-h", "--help"}:
        print("diony names: write-hosts | serve | install | ensure")
        print("Lays studio, producer, and director .diony.local names on this machine.")
        return 0
    if cmd == "write-hosts":
        return cmd_write_hosts()
    if cmd == "serve":
        return cmd_serve()
    if cmd == "install":
        return cmd_install()
    if cmd == "ensure":
        return cmd_ensure()
    print(f"unknown names command: {cmd}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
