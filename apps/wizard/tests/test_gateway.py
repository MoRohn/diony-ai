import http.client
import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from diony_wizard.gateway import (
    NAMES_HEADER,
    PRODUCER_HOST,
    STUDIO_HOST,
    apply_hosts_text,
    ensure_product_urls,
    gateway_healthy,
    hosts_ready,
    start_product_gateway,
    write_hosts,
)


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def test_apply_hosts_is_idempotent() -> None:
    once = apply_hosts_text("127.0.0.1 localhost\n")
    twice = apply_hosts_text(once)
    assert once == twice
    assert STUDIO_HOST in once
    assert PRODUCER_HOST in once
    assert once.count("127.0.0.1") >= 2
    assert f"::1 {STUDIO_HOST}" in once
    assert f"::1 {PRODUCER_HOST}" in once


def test_write_hosts_uses_override_path(tmp_path, monkeypatch) -> None:
    dest = tmp_path / "hosts"
    dest.write_text("127.0.0.1 localhost\n")
    monkeypatch.setenv("DIONY_HOSTS_PATH", str(dest))
    assert write_hosts()
    assert hosts_ready()
    text = dest.read_text()
    assert "studio.diony.local" in text
    assert "wizard" + ".diony.local" not in text
    assert "producer.diony.local" in text


def test_host_router_sets_names_header() -> None:
    class Echo(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            body = b"ok"
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *args: object) -> None:
            return

    def serve() -> ThreadingHTTPServer:
        server = ThreadingHTTPServer(("127.0.0.1", 0), Echo)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        return server

    wizard = serve()
    producer = serve()
    port = None
    for _ in range(8):
        candidate = _free_port()
        if start_product_gateway(
            candidate,
            wizard=wizard.server_address[1],
            producer=producer.server_address[1],
        ) and gateway_healthy(candidate):
            port = candidate
            break
    assert port is not None

    def hit(host: str) -> tuple[int, str]:
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=2)
        conn.request("GET", "/", headers={"Host": host})
        resp = conn.getresponse()
        label = resp.getheader(NAMES_HEADER) or ""
        resp.read()
        conn.close()
        return resp.status, label

    status, label = hit(STUDIO_HOST)
    assert status == 200
    assert label.startswith("studio")
    status, label = hit(PRODUCER_HOST)
    assert status == 200
    assert label.startswith("producer")


def test_gateway_health_accepts_the_installed_legacy_wizard_label(monkeypatch) -> None:
    class Resp:
        def getheader(self, name: str) -> str:
            return "wizard" if name == NAMES_HEADER else ""

        def read(self) -> bytes:
            return b"{}"

    class Conn:
        def request(self, method: str, path: str, headers: dict[str, str]) -> None:
            assert method == "GET"
            assert path == "/api/state"
            assert headers == {"Host": STUDIO_HOST}

        def getresponse(self) -> Resp:
            return Resp()

        def close(self) -> None:
            return None

    monkeypatch.setattr("diony_wizard.gateway.http.client.HTTPConnection", lambda *_a, **_k: Conn())
    assert gateway_healthy() is True


def test_ensure_lays_hosts_and_router(tmp_path, monkeypatch) -> None:
    dest = tmp_path / "hosts"
    dest.write_text("127.0.0.1 localhost\n")
    monkeypatch.setenv("DIONY_HOSTS_PATH", str(dest))
    monkeypatch.setenv("DIONY_NAMES_PROMPT", "0")
    port = None
    for _ in range(8):
        candidate = _free_port()
        monkeypatch.setenv("DIONY_NAMES_PORT", str(candidate))
        if ensure_product_urls(prompt=False) and gateway_healthy(candidate):
            port = candidate
            break
    assert port is not None
    assert hosts_ready()
    assert "studio.diony.local" in dest.read_text()
    assert "wizard" + ".diony.local" not in dest.read_text()
    assert gateway_healthy(port)


def test_privileged_install_is_foreground_install() -> None:
    from pathlib import Path

    from diony_wizard.gateway import privileged_install_argv

    src = Path(__file__).resolve().parents[1] / "src" / "diony_wizard" / "gateway.py"
    text = src.read_text()
    assert "nohup" not in text
    argv = privileged_install_argv()
    assert argv[-3:] == ["-m", "diony_wizard.gateway", "install"]


def test_install_returns_when_router_already_up(tmp_path, monkeypatch) -> None:
    from diony_wizard.gateway import cmd_install

    dest = tmp_path / "hosts"
    dest.write_text("127.0.0.1 localhost\n")
    monkeypatch.setenv("DIONY_HOSTS_PATH", str(dest))
    port = None
    for _ in range(8):
        candidate = _free_port()
        monkeypatch.setenv("DIONY_NAMES_PORT", str(candidate))
        if start_product_gateway(candidate) and gateway_healthy(candidate):
            port = candidate
            break
    assert port is not None
    assert cmd_install() == 0
    assert hosts_ready()


def test_log_handle_falls_back_when_unwritable(tmp_path, monkeypatch) -> None:
    from diony_wizard.gateway import _log_handle

    blocked = tmp_path / "blocked.log"
    blocked.write_text("no")
    blocked.chmod(0o400)
    monkeypatch.setenv("DIONY_NAMES_LOG", str(blocked))
    monkeypatch.setattr("diony_wizard.gateway.Path.home", lambda: tmp_path / "home")
    handle = _log_handle()
    assert handle is not None
    if handle is not __import__("subprocess").DEVNULL:
        handle.write(b"ok\n")
        handle.close()
