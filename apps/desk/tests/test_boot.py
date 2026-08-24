import ast
import socket
from pathlib import Path

import pytest

from diony_desk.boot import (
    EDITABLES,
    PRODUCER_URL,
    STATIC,
    STUDIO_URL,
    application_identity,
    claim_desk_port,
    desk_already_installed,
    desk_is_current,
    desk_state_ok,
    ensure_product_names,
    ensure_running,
    find_python,
    finish_open,
    diagnostic_snapshot,
    listener_is_desk,
    listener_is_diony,
    public_state,
    python_help_url,
    repo_root,
    set_state,
    service_command,
    serve,
    should_open_lobby,
    should_open_studio_after_work,
    user_data_dir,
    producer_state_ok,
    producer_html_ok,
    producer_ui_stamp,
    product_names_live,
    wizard_html_ok,
    wizard_state_ok,
    wizard_ui_stamp,
    work,
)

STATIC_DIR = Path(__file__).resolve().parents[1] / "src" / "diony_desk" / "static"


def test_repo_root_is_the_sku() -> None:
    root = repo_root()
    assert (root / "AGENTS.md").is_file()
    assert (root / "Open Diony AI.command").is_file()
    assert (root / "open-diony.sh").is_file()
    assert (root / "Open Diony AI.bat").is_file()
    assert (root / "START HERE.txt").is_file()


def test_editables_cover_the_desk() -> None:
    root = repo_root()
    assert EDITABLES[0] == "packages/contracts"
    assert "apps/wizard" in EDITABLES
    assert "apps/producer" in EDITABLES
    assert "apps/finish" in EDITABLES
    assert "apps/desk" in EDITABLES
    for rel in EDITABLES:
        assert (root / rel / "pyproject.toml").is_file()


def test_python_help_is_a_download_page() -> None:
    assert "python.org" in python_help_url()


def test_skip_install_does_not_pip(monkeypatch) -> None:
    monkeypatch.setenv("DIONY_DESK_SKIP_INSTALL", "1")
    set_state(phase="looking", detail="", error="", log=[])
    work()
    state = public_state()
    assert state["phase"] == "ready"
    assert "skipped" in state["detail"].lower()
    assert state["studio"] == "http://studio.diony.local"
    assert state["producer"] == "http://producer.diony.local"


def test_missing_venv_is_not_installed(tmp_path) -> None:
    assert desk_already_installed(tmp_path) is False


def test_installed_app_uses_normal_platform_data_directories(tmp_path) -> None:
    mac = user_data_dir(
        platform_name="darwin", environ={}, home=tmp_path / "maker"
    )
    windows = user_data_dir(
        platform_name="win32",
        environ={"LOCALAPPDATA": str(tmp_path / "LocalAppData")},
        home=tmp_path / "maker",
    )
    linux = user_data_dir(
        platform_name="linux",
        environ={"XDG_DATA_HOME": str(tmp_path / "xdg")},
        home=tmp_path / "maker",
    )
    assert mac == tmp_path / "maker" / "Library" / "Application Support" / "Diony AI"
    assert windows == tmp_path / "LocalAppData" / "Diony AI"
    assert linux == tmp_path / "xdg" / "diony-ai"


def test_frozen_service_command_reuses_the_signed_application_binary(tmp_path) -> None:
    command = service_command(
        "wizard",
        tmp_path,
        frozen=True,
        executable=tmp_path / "Diony AI.exe",
    )
    assert command == [str(tmp_path / "Diony AI.exe"), "--service", "wizard"]


def test_installed_application_opens_the_named_studio_intro_first() -> None:
    assert should_open_lobby(already_installed=True, frozen=True) is False
    assert should_open_lobby(already_installed=False, frozen=False) is True
    assert should_open_lobby(already_installed=True, frozen=False) is False


def test_ready_studio_reuses_an_open_lobby_instead_of_opening_another_page() -> None:
    assert should_open_studio_after_work(lobby_opened=True, phase="ready") is False
    assert should_open_studio_after_work(lobby_opened=False, phase="ready") is True
    assert should_open_studio_after_work(lobby_opened=False, phase="error") is False


def test_duplicate_launch_brings_studio_back_to_the_user(monkeypatch) -> None:
    opened: list[str] = []
    monkeypatch.setattr("diony_desk.boot.claim_desk_port", lambda root=None: True)
    monkeypatch.setattr("diony_desk.boot.desk_already_installed", lambda root=None: True)
    monkeypatch.setattr("diony_desk.boot.product_names_live", lambda: True)
    monkeypatch.setattr("diony_desk.boot.open_browser", opened.append)

    from diony_desk.boot import main

    assert main([]) == 0
    assert opened == [STUDIO_URL]


def test_duplicate_launch_does_not_repeat_startup_work(monkeypatch) -> None:
    opened: list[str] = []
    monkeypatch.setattr("diony_desk.boot.claim_desk_port", lambda root=None: True)
    monkeypatch.setattr("diony_desk.boot.product_names_live", lambda: False)
    monkeypatch.setattr("diony_desk.boot.desk_state_ok", lambda: True)
    monkeypatch.setattr(
        "diony_desk.boot.ensure_product_names",
        lambda root=None: pytest.fail("a duplicate launch repeated startup work"),
    )
    monkeypatch.setattr("diony_desk.boot.open_browser", opened.append)

    from diony_desk.boot import main

    assert main([]) == 0
    assert opened == ["http://127.0.0.1:8765/"]


def test_launch_in_progress_exits_before_starting_another_desk(
    monkeypatch, tmp_path: Path
) -> None:
    handoffs: list[bool] = []
    monkeypatch.setattr("diony_desk.boot.configure_runtime_environment", lambda root=None: tmp_path)
    monkeypatch.setattr("diony_desk.boot.claim_desk_port", lambda root=None: False)
    monkeypatch.setattr(
        "diony_desk.boot.acquire_instance_lock", lambda **kwargs: None, raising=False
    )
    monkeypatch.setattr(
        "diony_desk.boot.handoff_to_existing_desk",
        lambda: handoffs.append(True),
        raising=False,
    )
    monkeypatch.setattr(
        "diony_desk.boot.serve",
        lambda: pytest.fail("a second Desk server was started"),
    )

    from diony_desk.boot import main

    assert main([]) == 0
    assert handoffs == [True]


def test_instance_lock_allows_only_one_desk_startup(tmp_path: Path) -> None:
    from diony_desk.boot import acquire_instance_lock

    lock_path = tmp_path / "desk.instance.lock"
    first = acquire_instance_lock(lock_path)
    assert first is not None
    try:
        assert acquire_instance_lock(lock_path) is None
    finally:
        first.close()
    replacement = acquire_instance_lock(lock_path)
    assert replacement is not None
    replacement.close()


def test_desk_server_never_falls_back_to_a_second_port() -> None:
    holder = socket.socket()
    holder.bind(("127.0.0.1", 0))
    holder.listen()
    port = holder.getsockname()[1]
    unexpected = None
    try:
        with pytest.raises(OSError):
            unexpected = serve(port)
    finally:
        holder.close()
        if unexpected is not None:
            unexpected.shutdown()
            unexpected.server_close()


def test_diagnostics_use_safe_metadata_and_persistent_log_paths(
    tmp_path: Path, monkeypatch
) -> None:
    root = tmp_path / "product"
    root.mkdir()
    (root / "desktop-release.json").write_text(
        '{"version":"1.2.3","payload_sha256":"abc123"}\n'
    )
    state = tmp_path / "state"
    monkeypatch.setenv("DIONY_DESK_DATA", str(state))
    snapshot = diagnostic_snapshot(root)
    assert snapshot["product"] == "Diony AI"
    assert snapshot["version"] == "1.2.3"
    assert snapshot["payload_sha256"] == "abc123"
    assert snapshot["data_dir"] == str(state)
    assert "environment" not in snapshot
    assert "secrets" not in snapshot


def test_existing_desk_detection_is_semantic() -> None:
    server = serve(0)
    try:
        assert desk_state_ok(port=server.server_address[1]) is True
        assert public_state()["product"] == "Diony AI"
    finally:
        server.shutdown()
        server.server_close()


def test_packaged_desk_identity_comes_from_the_complete_payload(tmp_path: Path) -> None:
    (tmp_path / "desktop-release.json").write_text(
        '{"version":"1.2.3","payload_sha256":"payload-one"}\n'
    )

    assert application_identity(tmp_path) == "payload:payload-one"


def test_running_desk_without_the_current_payload_identity_is_stale(
    monkeypatch, tmp_path: Path
) -> None:
    (tmp_path / "desktop-release.json").write_text(
        '{"version":"1.2.3","payload_sha256":"payload-two"}\n'
    )
    monkeypatch.setattr(
        "diony_desk.boot.desk_status",
        lambda host="127.0.0.1", port=8765: {
            "product": "Diony AI",
            "application_identity": "payload:payload-one",
        },
    )

    assert desk_is_current(tmp_path) is False


def test_renamed_diony_app_is_recognized_as_the_existing_desk(monkeypatch) -> None:
    monkeypatch.setattr("diony_desk.boot.listener_pids", lambda port: [4242])
    monkeypatch.setattr(
        "diony_desk.boot.process_command",
        lambda pid: (
            "/Applications/Diony.app/Contents/MacOS/Diony AI"
            if pid == 4242
            else ""
        ),
    )

    assert listener_is_desk() is True


def test_claim_desk_port_replaces_a_stale_diony_desk(monkeypatch, tmp_path: Path) -> None:
    stopped: list[int] = []
    monkeypatch.setattr("diony_desk.boot.desk_state_ok", lambda: True)
    monkeypatch.setattr("diony_desk.boot.desk_is_current", lambda root=None: False)
    monkeypatch.setattr("diony_desk.boot.listener_is_desk", lambda port=8765: True)
    monkeypatch.setattr("diony_desk.boot.stop_listener", lambda port: stopped.append(port))
    monkeypatch.setattr(
        "diony_desk.boot.port_open", lambda port, host="127.0.0.1": False
    )

    assert claim_desk_port(tmp_path) is False
    assert stopped == [8765]


def test_claim_desk_port_stops_old_bundle_services(monkeypatch, tmp_path: Path) -> None:
    stopped: list[int] = []
    listening = {8765, 8787, 8788}
    monkeypatch.setattr("diony_desk.boot.desk_state_ok", lambda: True)
    monkeypatch.setattr("diony_desk.boot.desk_is_current", lambda root=None: False)
    monkeypatch.setattr("diony_desk.boot.listener_is_desk", lambda port=8765: True)
    monkeypatch.setattr("diony_desk.boot.listener_is_diony", lambda port: True)
    monkeypatch.setattr(
        "diony_desk.boot.port_open",
        lambda port, host="127.0.0.1": port in listening,
    )

    def fake_stop(port: int) -> None:
        stopped.append(port)
        listening.discard(port)

    monkeypatch.setattr("diony_desk.boot.stop_listener", fake_stop)

    assert claim_desk_port(tmp_path) is False
    assert stopped == [8765, 8787, 8788]


def test_claim_current_desk_stops_legacy_duplicate_desk_listeners(
    monkeypatch, tmp_path: Path
) -> None:
    stopped: list[int] = []
    monkeypatch.setattr(
        "diony_desk.boot.desk_state_ok",
        lambda host="127.0.0.1", port=8765: port in {8765, 8766, 8768},
    )
    monkeypatch.setattr("diony_desk.boot.desk_is_current", lambda root=None: True)
    monkeypatch.setattr(
        "diony_desk.boot.listener_is_desk", lambda port=8765: port in {8765, 8766}
    )
    monkeypatch.setattr(
        "diony_desk.boot.port_open",
        lambda port, host="127.0.0.1": port in {8765, 8766, 8768},
    )
    monkeypatch.setattr("diony_desk.boot.stop_listener", stopped.append)

    assert claim_desk_port(tmp_path) is True
    assert stopped == [8766]


def test_find_python_accepts_this_interpreter() -> None:
    found = find_python()
    if found is None:
        return
    binary, ver = found
    assert ver >= (3, 11)
    assert binary


def test_static_is_shipped() -> None:
    assert (STATIC / "index.html").is_file()
    assert STATIC == STATIC_DIR


def test_boot_is_stdlib() -> None:
    src = Path(__file__).resolve().parents[1] / "src" / "diony_desk" / "boot.py"
    tree = ast.parse(src.read_text())
    mods: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            mods.extend(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            mods.append(node.module.split(".")[0])
    banned = {"fastapi", "pydantic", "uvicorn", "httpx", "starlette"}
    assert not banned.intersection(mods)


def test_lobby_is_startup_only_and_hands_off_in_the_same_page() -> None:
    html = (STATIC_DIR / "index.html").read_text()
    css = (STATIC_DIR / "app.css").read_text()
    js = (STATIC_DIR / "app.js").read_text()
    sh = Path(__file__).resolve().parents[3] / "Open Diony AI.command"
    start = Path(__file__).resolve().parents[3] / "START HERE.txt"
    assert "Connect Studio" not in html
    assert "Enter to Start New or Join Studio" not in html
    assert "Own the source. Be creative." not in html
    assert "Opening Studio" in html
    assert 'href="/app.css?v=brand3"' in html
    assert 'src="/app.js?v=brand3"' in html
    assert "<title>Diony AI Studio</title>" in html
    assert 'class="brand diony-brand"' in html
    assert 'class="diony-mark"' in html
    assert '<span class="diony-word">Diony</span>' in html
    assert "diony-ai-optical-mark" not in html
    assert '<i>/</i><span>Diony</span><strong>AI</strong>' not in html
    assert 'src="/dionysus-sketch.png?v=desk15"' in html
    sketch = (STATIC_DIR / "dionysus-sketch.png").read_bytes()
    assert sketch.startswith(b"\x89PNG\r\n\x1a\n")
    assert sketch[25] in {4, 6}
    stage_rule = css[css.index(".stage-copy {") : css.index(".stage-copy strong")]
    assert "letter-spacing: -0.04em" in stage_rule
    assert ".diony-brand::after" in css
    brand_line_start = css.index(".diony-brand::after {")
    brand_line_rule = css[brand_line_start : css.index(".site-line", brand_line_start)]
    assert "content: none;" in brand_line_rule
    brand_start = css.index(".brand.diony-brand {")
    brand_rule = css[brand_start : css.index(".diony-mark", brand_start)]
    assert "background: transparent;" in brand_rule
    assert "overflow: visible;" in brand_rule
    assert "color: var(--light);" in brand_rule
    assert "clip-path" not in brand_rule
    mark_start = css.index(".diony-mark {")
    mark_rule = css[mark_start : css.index(".diony-word", mark_start)]
    assert "height: clamp(" in mark_rule
    word_start = css.index(".diony-word {")
    word_rule = css[word_start : css.index("}", word_start)]
    assert "var(--display)" in word_rule
    assert '<span>OWN YOUR STUDIO</span>' not in html
    assert '<strong>PRODUCE AT COST</strong>' not in html
    assert 'class="motto-rule"' not in html
    assert ".header-motto strong" in css
    assert ".motto-rule" not in css
    assert ".header-motto::after" not in css
    assert "Desk / First open" not in css
    assert ".sheet::before" not in css
    assert "left: 45%" not in css
    motto_action_start = css.index(".header-motto strong {")
    motto_action_rule = css[
        motto_action_start : css.index(".bays .on", motto_action_start)
    ]
    assert "linear-gradient" in motto_action_rule
    assert "var(--signal)" in motto_action_rule
    assert "var(--ember)" in motto_action_rule
    assert "translateY" not in motto_action_rule
    boot = (Path(__file__).resolve().parents[1] / "src/diony_desk/boot.py").read_text()
    assert (
        "Preparing the Diony AI Studio & Producer. "
        "This computer may ask for a password once."
    ) in boot
    assert "Laying http://studio.diony.local" not in boot
    assert "Enter to Start New or Join Studio" not in boot
    assert "Own the source. Be creative." not in js
    assert 'slate.textContent = "ENTER"' not in js
    assert 'if (document.body.dataset.phase !== "need_names") return;' in js
    assert "Approve once" not in js
    assert "One approval. No cloud connection." not in js
    assert "<summary>Details</summary>" in html
    assert "What happened" not in html
    assert "friendlyStatus" in js
    assert 'lines.join("\\n")' not in js
    ready_branch = js[
        js.index('if (state.phase === "ready")') : js.index(
            '} else if (state.phase === "need_python")'
        )
    ]
    assert "window.location.replace(state.studio" in ready_branch
    assert 'target = "_blank"' not in ready_branch
    assert "Continue to Studio." not in js
    assert "Checking your studio." in js
    assert "Get Python" in js
    assert "family=Inter" in html
    assert "Inter" in css
    assert "--baize" in css
    assert "Newsreader" not in html
    assert "/api/status" in js
    assert "/api/ensure-names" in js
    assert "/api/diagnostics" in html
    assert "Download diagnostics" in html
    assert "state.runtime_mode" in js
    assert "need_names" in js
    assert 'body[data-phase="need_names"] h1' in css
    assert "Connect Studio" not in boot
    assert "Password for the product names" not in (Path(__file__).resolve().parents[1] / "src/diony_desk/boot.py").read_text()
    assert "window.location.href = state.wizard" not in js
    assert "http://studio.diony.local" in js
    assert "wizard" + ".diony.local" not in js
    assert "127.0.0.1:8787" not in js
    assert "127.0.0.1:8788" not in js
    assert "python3.11" in sh.read_text()
    assert "Double-click" in sh.read_text() or "Finder" in sh.read_text()
    assert "Open Diony AI.command" in start.read_text()
    assert "python.org" in start.read_text()
    assert "https://www.runpod.io/" in start.read_text()
    assert "https://enverge.ai/" in start.read_text()
    assert start.read_text().find("https://www.runpod.io/") < start.read_text().find("https://enverge.ai/")
    assert "Local Quad-Spark Cluster" in start.read_text()
    assert "2× DGX Spark GB10" in start.read_text() or "2x DGX Spark GB10" in start.read_text()
    assert "NVIDIA B300" in start.read_text()
    assert "Leased Dual-Spark Cluster" in start.read_text()
    assert "Leased B300" in start.read_text()
    assert "Leased B200" in start.read_text()
    assert "NVIDIA B200" in start.read_text()
    assert "Blueprint 14" in start.read_text()
    assert "Container disk" in start.read_text()
    assert "Volume disk" in start.read_text()
    assert "Network volume" in start.read_text() or "Network Volume" in start.read_text()
    assert "Deploy Pod" in start.read_text()
    assert "800 GB" in start.read_text()
    assert "10 GB" in start.read_text()
    assert "1000 GB" in start.read_text()
    assert "recommended" in start.read_text().lower()
    assert "/workspace" in start.read_text()
    assert "SSH over exposed TCP" in start.read_text()
    assert "SSH port" in start.read_text()
    assert "28231" in start.read_text()
    assert "Jupyter" in start.read_text()
    assert "ssh.runpod.io" in start.read_text()


def test_product_urls_are_named() -> None:
    src = Path(__file__).resolve().parents[1] / "src" / "diony_desk" / "boot.py"
    text = src.read_text()
    assert STUDIO_URL == "http://studio.diony.local"
    assert PRODUCER_URL == "http://producer.diony.local"
    assert 'STUDIO_URL = "http://studio.diony.local"' in text
    assert 'PRODUCER_URL = "http://producer.diony.local"' in text
    assert "diony_wizard.gateway" in text
    assert '"ensure"' in text
    assert "127.0.0.1:8787" not in text
    assert "127.0.0.1:8788" not in text
    assert "webbrowser.open(STUDIO_URL)" in text or 'webbrowser.open(url)' in text or "open_browser(" in text


def test_lobby_uses_the_diony_brand_design_system() -> None:
    html = (STATIC_DIR / "index.html").read_text()
    css = (STATIC_DIR / "app.css").read_text()
    assert "family=Archivo:wdth,wght@125,850" in html
    assert "family=Inter" in html
    assert 'data-product-surface="desk"' in html
    assert 'class="diony-spirit' in html
    assert "--charcoal: #0d0d0d" in css.lower()
    assert "--light: #f6f6f6" in css.lower()
    assert "--signal: #ff402f" in css.lower()
    assert "--ember: #ffa23a" in css.lower()
    assert "--flare: #ff7a2f" in css.lower()
    assert "--garnet: #e43b4b" in css.lower()
    assert "--brand-gradient" in css
    assert ":focus-visible" in css
    assert "prefers-reduced-motion: reduce" in css


def test_ensure_product_names_runs_gateway(monkeypatch, tmp_path) -> None:
    calls: list[list[str]] = []

    class Proc:
        returncode = 0

    def fake_run(cmd, **kwargs):  # noqa: ANN001
        calls.append(list(cmd))
        return Proc()

    py = tmp_path / "python"
    py.write_text("#!/bin/sh\n")
    monkeypatch.setattr("diony_desk.boot.subprocess.run", fake_run)
    monkeypatch.setattr("diony_desk.boot.venv_python", lambda root=None: py)
    monkeypatch.setattr("diony_desk.boot.product_names_live", lambda: False)
    assert ensure_product_names(tmp_path) is True
    assert calls
    assert calls[0][-3:] == ["-m", "diony_wizard.gateway", "ensure"]


def test_product_names_live_accepts_the_installed_legacy_gateway(monkeypatch) -> None:
    class Resp:
        def getheader(self, name: str) -> str:
            return "wizard" if name == "X-Diony-Names" else ""

        def read(self) -> bytes:
            return b"{}"

    class Conn:
        def request(self, method: str, path: str) -> None:
            assert method == "GET"
            assert path == "/api/state"

        def getresponse(self) -> Resp:
            return Resp()

        def close(self) -> None:
            return None

    monkeypatch.setattr(
        "diony_desk.boot.http.client.HTTPConnection", lambda *_a, **_k: Conn()
    )
    assert product_names_live() is True


def test_launchers_open_named_studio() -> None:
    root = repo_root()
    mac = (root / "Open Diony AI.command").read_text()
    win = (root / "Open Diony AI.bat").read_text()
    sh = (root / "open-diony.sh").read_text()
    for text in (mac, win, sh):
        assert "http://studio.diony.local" in text
        assert "wizard" + ".diony.local" not in text
        assert "127.0.0.1:8787" not in text
        assert "127.0.0.1:8788" not in text
        assert "boot.py" in text
    assert "for %%V in (3.14 3.13 3.12 3.11)" in win
    assert 'py -%%V "%BOOT%"' in win
    assert 'py -3 "%BOOT%"' not in win
    assert "version_info >= (3, 11)" in win


def test_failed_names_stay_on_lobby(monkeypatch) -> None:
    monkeypatch.delenv("DIONY_DESK_SKIP_INSTALL", raising=False)
    monkeypatch.setattr("diony_desk.boot.desk_already_installed", lambda root=None: True)
    monkeypatch.setattr("diony_desk.boot.find_python", lambda: ("/usr/bin/python3", (3, 11)))
    monkeypatch.setattr(
        "diony_desk.boot.port_open",
        lambda port, host="127.0.0.1": port in {8787, 8788},
    )
    monkeypatch.setattr("diony_desk.boot.ensure_product_names", lambda root=None: False)
    monkeypatch.setattr("diony_desk.boot.product_names_live", lambda: False)
    monkeypatch.setattr("diony_desk.boot.wizard_state_ok", lambda host="127.0.0.1", port=None: True)
    monkeypatch.setattr("diony_desk.boot.wizard_html_ok", lambda root=None, host="127.0.0.1", port=None: True)
    monkeypatch.setattr("diony_desk.boot.producer_state_ok", lambda host="127.0.0.1", port=None: True)
    monkeypatch.setattr("diony_desk.boot.producer_html_ok", lambda root=None, host="127.0.0.1", port=None: True)
    set_state(phase="looking", detail="", error="", log=[])
    work()
    state = public_state()
    assert state["phase"] == "need_names"
    assert state["detail"] == "Studio needs permission to use its local address."


def test_wizard_state_ok_rejects_500(monkeypatch) -> None:
    class Resp:
        status = 500

        def read(self, _n: int = 0) -> bytes:
            return b"Internal Server Error"

    class Conn:
        def request(self, *_a, **_k) -> None:
            return None

        def getresponse(self) -> Resp:
            return Resp()

        def close(self) -> None:
            return None

    monkeypatch.setattr("diony_desk.boot.http.client.HTTPConnection", lambda *_a, **_k: Conn())
    assert wizard_state_ok() is False


@pytest.mark.parametrize(
    ("check", "stamp"),
    ((wizard_html_ok, "brand11"), (producer_html_ok, "brand7")),
)
def test_ui_health_check_reads_version_marker_after_20kb(
    monkeypatch, check, stamp: str
) -> None:
    payload = b"x" * 30000 + f'app.js?v={stamp}'.encode()

    class Resp:
        status = 200

        def read(self, amount: int | None = None) -> bytes:
            return payload if amount is None else payload[:amount]

    class Conn:
        def request(self, *_a, **_k) -> None:
            return None

        def getresponse(self) -> Resp:
            return Resp()

        def close(self) -> None:
            return None

    monkeypatch.setattr("diony_desk.boot.http.client.HTTPConnection", lambda *_a, **_k: Conn())
    monkeypatch.setattr(
        "diony_desk.boot.wizard_ui_stamp"
        if check is wizard_html_ok
        else "diony_desk.boot.producer_ui_stamp",
        lambda root=None: stamp,
    )

    assert check() is True


def test_producer_state_ok_requires_a_real_project_payload(monkeypatch) -> None:
    class Resp:
        status = 200

        def read(self, _n: int = 0) -> bytes:
            return b'{"id":"project-one"}'

    class Conn:
        requested = ""

        def request(self, _method: str, path: str) -> None:
            self.requested = path

        def getresponse(self) -> Resp:
            return Resp()

        def close(self) -> None:
            return None

    monkeypatch.setattr("diony_desk.boot.http.client.HTTPConnection", lambda *_a, **_k: Conn())
    assert producer_state_ok() is True


def test_producer_state_ok_accepts_a_large_project_payload(monkeypatch) -> None:
    payload = ('{"id":"project-large","brief":"' + ("x" * 20000) + '"}').encode()

    class Resp:
        status = 200

        def read(self, amount: int | None = None) -> bytes:
            return payload if amount is None else payload[:amount]

    class Conn:
        def request(self, *_a, **_k) -> None:
            return None

        def getresponse(self) -> Resp:
            return Resp()

        def close(self) -> None:
            return None

    monkeypatch.setattr("diony_desk.boot.http.client.HTTPConnection", lambda *_a, **_k: Conn())
    assert producer_state_ok() is True


def test_producer_ui_stamp_matches_html() -> None:
    stamp = producer_ui_stamp()
    assert stamp == "brand10"
    html = repo_root() / "apps" / "producer" / "src" / "diony_producer" / "static" / "index.html"
    assert f"app.js?v={stamp}" in html.read_text(encoding="utf-8")
    assert f"app.css?v={stamp}" in html.read_text(encoding="utf-8")


def test_wizard_ui_stamp_matches_html() -> None:
    stamp = wizard_ui_stamp()
    assert stamp == "brand12"
    html = repo_root() / "apps" / "wizard" / "src" / "diony_wizard" / "static" / "index.html"
    assert f"app.js?v={stamp}" in html.read_text(encoding="utf-8")
    assert f"app.css?v={stamp}" in html.read_text(encoding="utf-8")


def test_finish_open_ready_when_names_work() -> None:
    set_state(phase="looking", detail="", error="", log=[])
    finish_open(names_ok=True)
    state = public_state()
    assert state["phase"] == "ready"
    assert state["title"] == "Opening Studio"
    assert state["detail"] == "Studio is ready."


def test_ensure_running_restarts_unhealthy_wizard(monkeypatch, tmp_path) -> None:
    started: list[str] = []
    stopped: list[int] = []
    listening = {8787: True, 8788: True}
    health = {"ok": False}

    wizard = tmp_path / "diony-wizard"
    producer = tmp_path / "diony-producer"
    wizard.write_text("ok\n")
    producer.write_text("ok\n")

    def fake_script(name: str, root=None):  # noqa: ANN001
        return wizard if "wizard" in name else producer

    monkeypatch.setattr("diony_desk.boot.venv_script", fake_script)
    monkeypatch.setattr("diony_desk.boot.port_open", lambda port, host="127.0.0.1": bool(listening.get(port)))
    monkeypatch.setattr("diony_desk.boot.wizard_state_ok", lambda host="127.0.0.1", port=None: health["ok"])
    monkeypatch.setattr("diony_desk.boot.wait_port", lambda port, timeout=40.0: True)
    monkeypatch.setattr("diony_desk.boot.ensure_product_names", lambda root=None: True)
    monkeypatch.setattr("diony_desk.boot.finish_open", lambda names_ok=False: None)
    monkeypatch.setattr("diony_desk.boot.listener_is_diony", lambda port: True)
    monkeypatch.setattr("diony_desk.boot.producer_is_current", lambda root=None: True)

    def fake_stop(port: int) -> None:
        stopped.append(port)
        listening[port] = False
        health["ok"] = False

    def fake_start(script, log_name, root) -> None:  # noqa: ANN001
        started.append(log_name)
        if log_name == "wizard.log":
            listening[8787] = True
            health["ok"] = True
        if log_name == "producer.log":
            listening[8788] = True

    monkeypatch.setattr("diony_desk.boot.stop_listener", fake_stop)
    monkeypatch.setattr("diony_desk.boot.start_app", fake_start)
    set_state(phase="looking", detail="", error="", log=[])
    ensure_running(tmp_path)
    assert stopped == [8787]
    assert "wizard.log" in started
    assert "producer.log" not in started


def test_ensure_running_restarts_stale_ui(monkeypatch, tmp_path) -> None:
    started: list[str] = []
    stopped: list[int] = []
    listening = {8787: True, 8788: True}
    html_ok = {"ok": False}

    wizard = tmp_path / "diony-wizard"
    producer = tmp_path / "diony-producer"
    wizard.write_text("ok\n")
    producer.write_text("ok\n")

    def fake_script(name: str, root=None):  # noqa: ANN001
        return wizard if "wizard" in name else producer

    monkeypatch.setattr("diony_desk.boot.venv_script", fake_script)
    monkeypatch.setattr("diony_desk.boot.port_open", lambda port, host="127.0.0.1": bool(listening.get(port)))
    monkeypatch.setattr("diony_desk.boot.wizard_state_ok", lambda host="127.0.0.1", port=None: True)
    monkeypatch.setattr(
        "diony_desk.boot.wizard_html_ok",
        lambda root=None, host="127.0.0.1", port=None: html_ok["ok"],
    )
    monkeypatch.setattr("diony_desk.boot.wait_port", lambda port, timeout=40.0: True)
    monkeypatch.setattr("diony_desk.boot.ensure_product_names", lambda root=None: True)
    monkeypatch.setattr("diony_desk.boot.finish_open", lambda names_ok=False: None)
    monkeypatch.setattr("diony_desk.boot.listener_is_diony", lambda port: True)
    monkeypatch.setattr("diony_desk.boot.producer_is_current", lambda root=None: True)

    def fake_stop(port: int) -> None:
        stopped.append(port)
        listening[port] = False

    def fake_start(script, log_name, root) -> None:  # noqa: ANN001
        started.append(log_name)
        if log_name == "wizard.log":
            listening[8787] = True
            html_ok["ok"] = True
        if log_name == "producer.log":
            listening[8788] = True

    monkeypatch.setattr("diony_desk.boot.stop_listener", fake_stop)
    monkeypatch.setattr("diony_desk.boot.start_app", fake_start)
    set_state(phase="looking", detail="", error="", log=[])
    ensure_running(tmp_path)
    assert stopped == [8787]
    assert "wizard.log" in started
    assert "producer.log" not in started


def test_ensure_running_restarts_stale_producer(monkeypatch, tmp_path) -> None:
    started: list[str] = []
    stopped: list[int] = []
    listening = {8787: True, 8788: True}
    producer_current = {"ok": False}
    for name in ("diony-wizard", "diony-producer"):
        (tmp_path / name).write_text("ok\n")

    monkeypatch.setattr(
        "diony_desk.boot.venv_script", lambda name, root=None: tmp_path / name
    )
    monkeypatch.setattr(
        "diony_desk.boot.port_open",
        lambda port, host="127.0.0.1": bool(listening.get(port)),
    )
    monkeypatch.setattr("diony_desk.boot.wizard_is_current", lambda root=None: True)
    monkeypatch.setattr(
        "diony_desk.boot.producer_is_current", lambda root=None: producer_current["ok"]
    )
    monkeypatch.setattr("diony_desk.boot.listener_is_diony", lambda port: True)
    monkeypatch.setattr("diony_desk.boot.wait_port", lambda port, timeout=40.0: True)
    monkeypatch.setattr("diony_desk.boot.ensure_product_names", lambda root=None: True)
    monkeypatch.setattr("diony_desk.boot.finish_open", lambda names_ok=False: None)

    def fake_stop(port: int) -> None:
        stopped.append(port)
        listening[port] = False

    def fake_start(_command, log_name: str, _root: Path) -> None:
        started.append(log_name)
        if log_name == "producer.log":
            listening[8788] = True
            producer_current["ok"] = True

    monkeypatch.setattr("diony_desk.boot.stop_listener", fake_stop)
    monkeypatch.setattr("diony_desk.boot.start_app", fake_start)
    ensure_running(tmp_path)
    assert stopped == [8788]
    assert started == ["producer.log"]


def test_unknown_process_on_product_port_is_never_killed(monkeypatch, tmp_path) -> None:
    for name in ("diony-wizard", "diony-producer"):
        (tmp_path / name).write_text("ok\n")
    monkeypatch.setattr(
        "diony_desk.boot.venv_script", lambda name, root=None: tmp_path / name
    )
    monkeypatch.setattr(
        "diony_desk.boot.port_open", lambda port, host="127.0.0.1": port == 8787
    )
    monkeypatch.setattr("diony_desk.boot.wizard_is_current", lambda root=None: False)
    monkeypatch.setattr("diony_desk.boot.listener_is_diony", lambda port: False)
    stopped: list[int] = []
    monkeypatch.setattr("diony_desk.boot.stop_listener", lambda port: stopped.append(port))
    with pytest.raises(RuntimeError, match="another application"):
        ensure_running(tmp_path)
    assert stopped == []


def test_work_restarts_when_state_is_dead(monkeypatch) -> None:
    called: list[object] = []
    monkeypatch.delenv("DIONY_DESK_SKIP_INSTALL", raising=False)
    monkeypatch.setattr("diony_desk.boot.desk_already_installed", lambda root=None: True)
    monkeypatch.setattr("diony_desk.boot.find_python", lambda: ("/usr/bin/python3", (3, 11)))
    monkeypatch.setattr(
        "diony_desk.boot.port_open",
        lambda port, host="127.0.0.1": port in {8787, 8788},
    )
    monkeypatch.setattr("diony_desk.boot.wizard_state_ok", lambda host="127.0.0.1", port=None: False)
    monkeypatch.setattr(
        "diony_desk.boot.ensure_running",
        lambda root: called.append(root) or finish_open(names_ok=True),
    )
    set_state(phase="looking", detail="", error="", log=[])
    work()
    assert called
    assert public_state()["phase"] == "ready"


def test_work_checks_each_runtime_once_and_repairs_stale_producer(monkeypatch) -> None:
    find_calls: list[bool] = []
    repaired: list[Path] = []
    monkeypatch.delenv("DIONY_DESK_SKIP_INSTALL", raising=False)
    monkeypatch.setattr("diony_desk.boot.desk_already_installed", lambda root=None: True)
    monkeypatch.setattr(
        "diony_desk.boot.find_python",
        lambda: find_calls.append(True) or ("/usr/bin/python3", (3, 11)),
    )
    monkeypatch.setattr(
        "diony_desk.boot.port_open",
        lambda port, host="127.0.0.1": port in {8787, 8788},
    )
    monkeypatch.setattr("diony_desk.boot.wizard_is_current", lambda root=None: True)
    monkeypatch.setattr("diony_desk.boot.producer_is_current", lambda root=None: False)
    monkeypatch.setattr(
        "diony_desk.boot.ensure_running",
        lambda root: repaired.append(root) or finish_open(names_ok=True),
    )
    monkeypatch.setattr("diony_desk.boot.ensure_product_names", lambda root=None: True)
    set_state(phase="looking", detail="", error="", log=[])

    work()

    assert len(find_calls) == 1
    assert repaired
    assert public_state()["phase"] == "ready"
