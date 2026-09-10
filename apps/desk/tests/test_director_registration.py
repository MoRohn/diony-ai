"""The desk knows how to launch the Director service (Blueprint 25 M1)."""

from pathlib import Path

from diony_desk import boot


def test_director_is_an_editable_install() -> None:
    assert "apps/director" in boot.EDITABLES


def test_service_command_accepts_director_source_checkout() -> None:
    command = boot.service_command("director", Path("/repo"), frozen=False)
    assert command[-1].endswith("diony-director")


def test_service_command_accepts_director_frozen() -> None:
    command = boot.service_command(
        "director", frozen=True, executable=Path("/Applications/Diony.app/x")
    )
    assert command[-2:] == ["--service", "director"]


def test_director_port_is_reserved() -> None:
    assert boot.DIRECTOR_PORT == 8790


def test_director_url_is_a_product_name() -> None:
    assert boot.DIRECTOR_URL == "http://director.diony.local"


def test_listener_signatures_recognize_the_director(monkeypatch) -> None:
    monkeypatch.setattr(boot, "listener_pids", lambda port: [4242])
    monkeypatch.setattr(boot, "process_command", lambda pid: "/venv/bin/diony-director")
    assert boot.listener_is_diony(boot.DIRECTOR_PORT) is True
    monkeypatch.setattr(
        boot, "process_command", lambda pid: "/Applications/Diony.app/x --service director"
    )
    assert boot.listener_is_diony(boot.DIRECTOR_PORT) is True
    monkeypatch.setattr(boot, "process_command", lambda pid: "some-other-app")
    assert boot.listener_is_diony(boot.DIRECTOR_PORT) is False


def test_director_ui_stamp_reads_the_cache_stamp() -> None:
    from pathlib import Path as _P

    repo = _P(boot.__file__).resolve().parents[3].parent
    stamp = boot.director_ui_stamp(repo)
    assert stamp, "director index.html must carry an app.js?v= stamp"


def test_director_startup_is_best_effort_never_raises(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(boot, "is_frozen", lambda: False)
    monkeypatch.setattr(boot, "venv_script", lambda name, root=None: tmp_path / "missing")
    monkeypatch.setattr(
        boot,
        "subprocess",
        type(
            "S", (), {"run": staticmethod(lambda *a, **k: (_ for _ in ()).throw(OSError("no pip")))}
        ),
    )
    boot.ensure_director_running(tmp_path)  # must not raise


def test_diony_spec_packages_the_director() -> None:
    from pathlib import Path as _P

    repo = _P(boot.__file__).resolve().parents[3].parent
    spec = (repo / "packaging" / "desktop" / "diony.spec").read_text()
    assert '"diony_director"' in spec
    assert '"director"' in spec


def test_release_build_scripts_install_the_director() -> None:
    from pathlib import Path as _P

    repo = _P(boot.__file__).resolve().parents[3].parent
    for script in ("build-local-macos.sh", "build-macos.sh"):
        text = (repo / "scripts" / "release" / script).read_text()
        assert "apps/director" in text, (
            f"{script} must pip-install apps/director into the build venv"
        )
    windows = (repo / "scripts" / "release" / "build-windows.ps1").read_text()
    assert "apps\\director" in windows


def test_cloud_engine_lands_on_the_studio_opening(monkeypatch) -> None:
    """Flow: Studio (configure + choose engine) -> Director -> Producer."""
    monkeypatch.delenv("DIONY_ENGINE", raising=False)
    assert boot.landing_url(wait=0.0) == boot.STUDIO_URL


def test_product_names_need_the_director_route_and_generation(monkeypatch) -> None:
    labels = {"studio.diony.local": "wizard", "director.diony.local": "wizard"}
    gen = {"value": boot.NAMES_GEN_EXPECTED}

    def fake_probe(url, path="/"):
        from urllib.parse import urlparse

        return labels.get(urlparse(url).hostname or "", ""), gen["value"]

    monkeypatch.setattr(boot, "_name_route_probe", fake_probe)
    monkeypatch.setattr(boot, "_name_route_label", lambda url, path="/": fake_probe(url, path)[0])
    assert boot.product_names_live() is False  # stale pre-Director router

    labels["director.diony.local"] = "director"
    assert boot.product_names_live() is True

    gen["value"] = ""  # right routes, old generation: still stale
    assert boot.product_names_live() is False
    gen["value"] = boot.NAMES_GEN_EXPECTED

    labels["studio.diony.local"] = ""
    assert boot.product_names_live() is False


def _serving(body: bytes):
    class Resp:
        status = 200

        def read(self):
            return body

    class Conn:
        def __init__(self, *a, **k):
            pass

        def request(self, *a, **k):
            pass

        def getresponse(self):
            return Resp()

        def close(self):
            pass

    return lambda *a, **k: Conn()


def test_wizard_html_ok_accepts_only_the_studio_opening(monkeypatch) -> None:
    monkeypatch.setattr(
        boot.http.client, "HTTPConnection", _serving(b'<html data-diony="studio-opening">')
    )
    assert boot.wizard_html_ok() is True
    # a stale classic rail (or any other page) is not this desk's Studio
    monkeypatch.setattr(
        boot.http.client, "HTTPConnection", _serving(b'<script src="app.js?v=brand17">')
    )
    assert boot.wizard_html_ok() is False


def test_producer_html_ok_never_accepts_the_studio_opening(monkeypatch) -> None:
    monkeypatch.setattr(boot, "producer_ui_stamp", lambda root=None: "gpuline1")
    monkeypatch.setattr(
        boot.http.client, "HTTPConnection", _serving(b'<html data-diony="studio-opening">')
    )
    assert boot.producer_html_ok() is False


def test_names_gen_stays_in_lockstep_with_the_gateway() -> None:
    """boot.NAMES_GEN_EXPECTED was comment-enforced only; drift makes
    product_names_live() permanently False and re-runs the privileged names
    refresh on every launch. Now it is test-enforced."""
    import sys
    from pathlib import Path

    wizard_src = Path(__file__).resolve().parents[2] / "wizard" / "src"
    if str(wizard_src) not in sys.path:
        sys.path.insert(0, str(wizard_src))
    from diony_desk import boot
    from diony_wizard import gateway

    assert boot.NAMES_GEN_EXPECTED == gateway.NAMES_GEN


def test_engine_is_cloud_works_without_the_venv() -> None:
    """boot.py runs under the SYSTEM interpreter in source mode, where
    diony_contracts is not importable — the cloud-engine rule must not crash
    the launch (it took down every source-mode returning-user launch once)."""
    import subprocess
    import sys
    from pathlib import Path

    boot_src = Path(__file__).resolve().parents[1] / "src"
    code = (
        "import sys\n"
        # simulate the system interpreter: only the desk's own src on the path
        f"sys.path = [p for p in sys.path if 'contracts' not in p and 'site-packages' not in p]\n"
        f"sys.path.insert(0, {str(boot_src)!r})\n"
        "try:\n"
        "    import diony_contracts\n"
        "    raise SystemExit('scrub failed: diony_contracts importable, test proves nothing')\n"
        "except ImportError:\n"
        "    pass\n"
        "import diony_desk.boot as boot\n"
        "assert boot.engine_is_cloud() is True\n"
        "import os\n"
        "os.environ['DIONY_ENGINE'] = 'local_legacy'\n"
        "assert boot.engine_is_cloud() is False\n"
        "print('OK')\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        timeout=60,
        env={"PATH": "/usr/bin:/bin", "PYTHONPATH": ""},
    )
    assert result.returncode == 0, result.stderr
    assert "OK" in result.stdout
