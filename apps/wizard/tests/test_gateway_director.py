"""director.diony.local is a first-class product name beside studio and producer."""

from diony_wizard.gateway import (
    DIRECTOR_HOST,
    apply_hosts_text,
    director_port,
    hosts_ready,
    route_label,
    write_hosts,
)


def test_hosts_block_lays_the_director_name_idempotently() -> None:
    once = apply_hosts_text("127.0.0.1 localhost\n")
    twice = apply_hosts_text(once)
    assert once == twice
    assert f"127.0.0.1 {DIRECTOR_HOST}" in once
    assert f"::1 {DIRECTOR_HOST}" in once


def test_hosts_ready_requires_the_director_name(tmp_path, monkeypatch) -> None:
    dest = tmp_path / "hosts"
    dest.write_text("127.0.0.1 studio.diony.local\n127.0.0.1 producer.diony.local\n")
    monkeypatch.setenv("DIONY_HOSTS_PATH", str(dest))
    assert hosts_ready() is False  # pre-Director installs must re-lay the names
    assert write_hosts()
    assert hosts_ready() is True
    assert DIRECTOR_HOST in dest.read_text()


def test_route_label_maps_all_three_product_names() -> None:
    assert route_label("studio.diony.local") == "studio"
    assert route_label("producer.diony.local:80") == "producer"
    assert route_label("director.diony.local") == "director"
    assert route_label("DIRECTOR.DIONY.LOCAL") == "director"
    assert route_label("") == "studio"
    assert route_label("unknown.example.com") == "studio"


def test_director_port_env_override(monkeypatch) -> None:
    assert director_port() == 8790
    monkeypatch.setenv("DIONY_DIRECTOR_PORT", "9999")
    assert director_port() == 9999


def test_a_pre_director_router_is_healthy_but_not_current(monkeypatch) -> None:
    """The installed legacy router answers for Studio but cannot route the Director."""
    import diony_wizard.gateway as gw

    class Resp:
        def __init__(self, label):
            self._label = label

        def getheader(self, name):
            return self._label if name == gw.NAMES_HEADER else ""

        def read(self):
            return b""

    class Conn:
        def __init__(self, *a, **k):
            self._label = "wizard"

        def request(self, method, path, headers=None):
            host = (headers or {}).get("Host", "")
            # a stale router labels every host with its legacy single label
            self._label = "wizard"

        def getresponse(self):
            return Resp(self._label)

        def close(self):
            return None

    monkeypatch.setattr(gw.http.client, "HTTPConnection", Conn)
    assert gw.gateway_healthy(80) is True
    assert gw.gateway_current(80) is False


def test_a_current_router_labels_the_director_route(monkeypatch) -> None:
    import diony_wizard.gateway as gw

    class Resp:
        def __init__(self, label):
            self._label = label

        def getheader(self, name):
            if name == gw.NAMES_HEADER:
                return self._label
            if name == gw.NAMES_GEN_HEADER:
                return gw.NAMES_GEN
            return ""

        def read(self):
            return b""

    class Conn:
        def __init__(self, *a, **k):
            self._label = "studio"

        def request(self, method, path, headers=None):
            self._label = gw.route_label((headers or {}).get("Host", ""))

        def getresponse(self):
            return Resp(self._label)

        def close(self):
            return None

    monkeypatch.setattr(gw.http.client, "HTTPConnection", Conn)
    assert gw.gateway_current(80) is True


def test_a_previous_generation_router_reads_as_stale(monkeypatch) -> None:
    """A router that routes all three names but predates the current generation
    (e.g. the 60s-forward-timeout build) must be replaceable, not accepted."""
    import diony_wizard.gateway as gw

    class Resp:
        def __init__(self, label):
            self._label = label

        def getheader(self, name):
            if name == gw.NAMES_HEADER:
                return self._label
            return ""  # no generation header: an old build

        def read(self):
            return b""

    class Conn:
        def __init__(self, *a, **k):
            self._label = "studio"

        def request(self, method, path, headers=None):
            self._label = gw.route_label((headers or {}).get("Host", ""))

        def getresponse(self):
            return Resp(self._label)

        def close(self):
            return None

    monkeypatch.setattr(gw.http.client, "HTTPConnection", Conn)
    assert gw.gateway_healthy(80) is True
    assert gw.gateway_current(80) is False


def test_stop_stale_gateway_never_touches_a_foreign_server(monkeypatch) -> None:
    import diony_wizard.gateway as gw

    monkeypatch.setattr(gw, "_route_label_at", lambda *a, **k: "")  # no Diony header
    monkeypatch.setattr(gw, "_gateway_listener_pids", lambda port: [4242])
    killed = []
    monkeypatch.setattr(gw.os, "kill", lambda pid, sig: killed.append(pid))
    assert gw.stop_stale_gateway(80) is False  # listener remains, untouched
    assert killed == []


def test_stop_stale_gateway_terminates_only_diony_processes(monkeypatch) -> None:
    import diony_wizard.gateway as gw

    monkeypatch.setattr(gw, "_route_label_at", lambda *a, **k: "wizard")
    pids = {"alive": [111, 222]}
    monkeypatch.setattr(gw, "_gateway_listener_pids", lambda port: list(pids["alive"]))
    commands = {111: "/usr/bin/python -m diony_wizard.gateway serve", 222: "nginx: master"}
    monkeypatch.setattr(gw, "_process_command", lambda pid: commands[pid])
    killed = []

    def fake_kill(pid, sig):
        killed.append(pid)
        pids["alive"] = [222]  # the Diony one dies; nginx (never signalled) remains

    monkeypatch.setattr(gw.os, "kill", fake_kill)
    gw.stop_stale_gateway(80)
    assert killed and all(pid == 111 for pid in killed)
