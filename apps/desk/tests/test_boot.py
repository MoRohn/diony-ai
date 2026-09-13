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
    diagnostic_snapshot,
    director_is_current,
    director_state_ok,
    director_vault_ok,
    ensure_product_names,
    ensure_running,
    expected_vault,
    find_python,
    finish_open,
    listener_is_desk,
    producer_html_ok,
    producer_state_ok,
    producer_ui_stamp,
    product_names_live,
    public_state,
    python_help_url,
    repo_root,
    serve,
    service_command,
    set_state,
    should_open_lobby,
    should_open_studio_after_work,
    user_data_dir,
    wizard_state_ok,
    work,
)

STATIC_DIR = Path(__file__).resolve().parents[1] / "src" / "diony_desk" / "static"


def test_repo_root_is_the_sku() -> None:
    root = repo_root()
    assert (root / "AGENTS.md").is_file()
    assert (root / "Open Diony AI.command").is_file()
    assert (root / "open-diony.sh").is_file()
    assert (root / "Open Diony AI.bat").is_file()
    assert (root / "Build Diony AI.command").is_file()
    assert (root / "Build Diony AI.bat").is_file()
    assert (root / "scripts" / "release" / "build-local-windows.ps1").is_file()
    assert (root / "START HERE.txt").is_file()


def test_editables_cover_the_desk() -> None:
    root = repo_root()
    assert EDITABLES[0] == "packages/contracts"
    assert "apps/wizard" in EDITABLES
    assert "apps/producer" in EDITABLES
    assert "apps/finish" not in EDITABLES and "apps/wan" not in EDITABLES
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
    mac = user_data_dir(platform_name="darwin", environ={}, home=tmp_path / "maker")
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


def test_duplicate_launch_lands_on_the_studio_opening(monkeypatch) -> None:
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


def test_launch_in_progress_exits_before_starting_another_desk(monkeypatch, tmp_path: Path) -> None:
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
    (root / "desktop-release.json").write_text('{"version":"1.2.3","payload_sha256":"abc123"}\n')
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
        lambda pid: "/Applications/Diony.app/Contents/MacOS/Diony AI" if pid == 4242 else "",
    )

    assert listener_is_desk() is True


def test_claim_desk_port_replaces_a_stale_diony_desk(monkeypatch, tmp_path: Path) -> None:
    stopped: list[int] = []
    monkeypatch.setattr("diony_desk.boot.desk_state_ok", lambda: True)
    monkeypatch.setattr("diony_desk.boot.desk_is_current", lambda root=None: False)
    monkeypatch.setattr("diony_desk.boot.listener_is_desk", lambda port=8765: True)
    monkeypatch.setattr("diony_desk.boot.stop_listener", lambda port: stopped.append(port))
    monkeypatch.setattr("diony_desk.boot.port_open", lambda port, host="127.0.0.1": False)

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
    monkeypatch.setattr("diony_desk.boot.listener_is_desk", lambda port=8765: port in {8765, 8766})
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
    assert "<i>/</i><span>Diony</span><strong>AI</strong>" not in html
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
    assert "<span>OWN YOUR STUDIO</span>" not in html
    assert "<strong>PRODUCE AT COST</strong>" not in html
    assert 'class="motto-rule"' not in html
    assert ".header-motto strong" in css
    assert ".motto-rule" not in css
    assert ".header-motto::after" not in css
    assert "Desk / First open" not in css
    assert ".sheet::before" not in css
    assert "left: 45%" not in css
    motto_action_start = css.index(".header-motto strong {")
    motto_action_rule = css[motto_action_start : css.index(".bays .on", motto_action_start)]
    assert "linear-gradient" in motto_action_rule
    assert "var(--signal)" in motto_action_rule
    assert "var(--ember)" in motto_action_rule
    assert "translateY" not in motto_action_rule
    boot = (Path(__file__).resolve().parents[1] / "src/diony_desk/boot.py").read_text()
    assert (
        "Preparing the Diony AI Studio & Producer. This computer may ask for a password once."
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
    assert (
        "Password for the product names"
        not in (Path(__file__).resolve().parents[1] / "src/diony_desk/boot.py").read_text()
    )
    assert "window.location.href = state.wizard" not in js
    assert "http://studio.diony.local" in js
    assert "wizard" + ".diony.local" not in js
    assert "127.0.0.1:8787" not in js
    assert "127.0.0.1:8788" not in js
    assert "python3.11" in sh.read_text()
    assert "Double-click" in sh.read_text() or "Finder" in sh.read_text()
    text = start.read_text()
    # The first-open guide is the cloud product: keys, ffmpeg, the three
    # surfaces, and the Director flow. The hardware rail is a footnote.
    assert "Open Diony AI.command" in text
    assert "Open Diony AI.bat" in text
    assert "open-diony.sh" in text
    assert "python.org" in text
    assert "keys.env" in text
    assert "XAI_API_KEY" in text
    assert "USEAPI_TOKEN" in text
    assert "DIONY_FLOW_PLAN" in text
    assert "https://console.x.ai/" in text
    assert "https://useapi.net/" in text
    assert "ffmpeg" in text
    assert "http://studio.diony.local" in text
    assert "Continue to the Director" in text
    assert "Run & Assemble All" in text
    assert "diony-director-check" in text
    assert "/classic" not in text
    assert "Runpod" not in text and "Enverge" not in text
    assert "Quad-Spark" not in text


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
    assert (
        "webbrowser.open(STUDIO_URL)" in text
        or "webbrowser.open(url)" in text
        or "open_browser(" in text
    )


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

    def fake_run(cmd, **kwargs):
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


def test_product_names_reject_the_stale_pre_director_gateway(monkeypatch) -> None:
    """A legacy router labels every host "wizard": Studio answers but the
    Director cannot be routed. That router must read as NOT live so the
    one-time privileged refresh replaces it with the three-name router."""

    labels = {"studio.diony.local": "wizard", "director.diony.local": "wizard"}

    def fake_probe(url, path="/"):
        from urllib.parse import urlparse as _parse

        from diony_desk import boot as _boot

        return labels.get(_parse(url).hostname or "", ""), _boot.NAMES_GEN_EXPECTED

    monkeypatch.setattr("diony_desk.boot._name_route_probe", fake_probe)
    monkeypatch.setattr(
        "diony_desk.boot._name_route_label", lambda url, path="/": fake_probe(url, path)[0]
    )
    assert product_names_live() is False

    # A refreshed install may keep the legacy "wizard" label on the Studio
    # route — that is still live once the Director route answers as itself
    # on the current router generation.
    labels["director.diony.local"] = "director"
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


def test_launchers_open_the_built_app_first_and_the_build_launchers_pair_up() -> None:
    root = repo_root()
    mac = (root / "Open Diony AI.command").read_text()
    win = (root / "Open Diony AI.bat").read_text()
    # the built app wins when it is there; the source path stays as the fallback
    assert 'APP="$ROOT/dist/Diony.app"' in mac and 'exec open -n "$APP"' in mac
    assert mac.index("Diony.app") < mac.index("pick_python()")
    assert "development" not in mac  # the app is dist/Diony.app, not a channel folder
    assert r"dist\builds\%BUILD_ID%\Diony AI\Diony AI.exe" in win
    assert r'set /p BUILD_ID=<"dist\current-build.txt"' in win
    assert 'start "" "%APP%"' in win and win.index("Diony AI.exe") < win.index("where py")
    assert "packaging/desktop/version.json" in mac and r"packaging\desktop\version.json" in win
    # each desk has a Build launcher that drives its local build script
    build_mac = (root / "Build Diony AI.command").read_text()
    build_win = (root / "Build Diony AI.bat").read_text()
    assert "scripts/release/build-local-macos.sh" in build_mac
    assert (
        r"scripts\release\build-local-windows.ps1" in build_win
        and "ExecutionPolicy Bypass" in build_win
    )
    ps1 = (root / "scripts" / "release" / "build-local-windows.ps1").read_text()
    assert '"windows-development-v1"' in ps1
    assert "diony_desk.media" in ps1 and "windows-amd64" in ps1 and "DIONY_FFMPEG_BUNDLE_DIR" in ps1
    assert "development-report" in ps1 and "ItemType Junction" in ps1
    assert "signtool" not in ps1.lower() and "ISCC" not in ps1


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
    monkeypatch.setattr(
        "diony_desk.boot.wizard_html_ok", lambda root=None, host="127.0.0.1", port=None: True
    )
    monkeypatch.setattr(
        "diony_desk.boot.producer_state_ok", lambda host="127.0.0.1", port=None: True
    )
    monkeypatch.setattr(
        "diony_desk.boot.producer_html_ok", lambda root=None, host="127.0.0.1", port=None: True
    )
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


@pytest.mark.parametrize(("check", "stamp"), ((producer_html_ok, "brand7"),))
def test_ui_health_check_reads_version_marker_after_20kb(monkeypatch, check, stamp: str) -> None:
    payload = b"x" * 30000 + f"app.js?v={stamp}".encode()

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
    monkeypatch.setattr("diony_desk.boot.producer_ui_stamp", lambda root=None: stamp)

    assert check() is True


def test_producer_state_ok_requires_the_engine_payload(monkeypatch) -> None:
    class Resp:
        status = 200

        def read(self, _n: int = 0) -> bytes:
            return b'{"cloud":true,"engine_choice":"grok"}'

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


def _director_conn(monkeypatch, status_body: bytes, html: bytes = b"<html></html>") -> None:
    class Resp:
        def __init__(self, body: bytes) -> None:
            self.status = 200
            self._body = body

        def read(self, _n: int = 0) -> bytes:
            return self._body

    class Conn:
        def __init__(self) -> None:
            self.requested = ""

        def request(self, _method: str, path: str) -> None:
            self.requested = path

        def getresponse(self) -> Resp:
            return Resp(status_body if self.requested.startswith("/api/") else html)

        def close(self) -> None:
            return None

    monkeypatch.setattr("diony_desk.boot.http.client.HTTPConnection", lambda *_a, **_k: Conn())


def test_director_on_another_vault_is_stale(monkeypatch, tmp_path: Path) -> None:
    """A checkout venv Director left on :8790 beside the packaged app reads
    the checkout vault; Studio's engine choice lands in the desk's vault and
    the console opened on Grok with the Flow pills dimmed. The desk must not
    adopt it (2026-09-10)."""
    desk_vault = tmp_path / "Application Support" / "vault"
    desk_vault.mkdir(parents=True)
    monkeypatch.setenv("DIONY_VAULT", str(desk_vault))
    other = tmp_path / "checkout" / "apps" / "producer" / "data"
    other.mkdir(parents=True)
    _director_conn(monkeypatch, b'{"engine":"cloud","vault":"%s"}' % str(other).encode())
    assert director_state_ok() is True
    assert director_vault_ok(tmp_path) is False
    assert director_is_current(tmp_path) is False


def test_director_on_the_desk_vault_is_current(monkeypatch, tmp_path: Path) -> None:
    desk_vault = tmp_path / "vault"
    desk_vault.mkdir()
    monkeypatch.setenv("DIONY_VAULT", str(desk_vault))
    _director_conn(monkeypatch, b'{"engine":"cloud","vault":"%s"}' % str(desk_vault).encode())
    assert director_vault_ok(tmp_path) is True
    assert director_is_current(tmp_path) is True  # no index.html under tmp_path: stamp check passes


def test_director_without_a_vault_in_its_status_is_stale(monkeypatch, tmp_path: Path) -> None:
    """An older build's status has no vault field; the desk restarts it
    rather than trusting a Director it cannot place."""
    monkeypatch.setenv("DIONY_VAULT", str(tmp_path))
    _director_conn(monkeypatch, b'{"engine":"cloud"}')
    assert director_vault_ok(tmp_path) is False


def test_expected_vault_follows_the_cluster_rule(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.delenv("DIONY_VAULT", raising=False)
    monkeypatch.delenv("DIONY_PRODUCER_VAULT", raising=False)
    monkeypatch.setattr("diony_desk.boot.Path.is_dir", lambda self: False)
    assert expected_vault(tmp_path) == tmp_path / "apps" / "producer" / "data"
    monkeypatch.setenv("DIONY_PRODUCER_VAULT", str(tmp_path / "p"))
    assert expected_vault(tmp_path) == tmp_path / "p"
    monkeypatch.setenv("DIONY_VAULT", str(tmp_path / "v"))
    assert expected_vault(tmp_path) == tmp_path / "v"


def test_producer_state_ok_accepts_a_large_payload(monkeypatch) -> None:
    payload = ('{"cloud":true,"note":"' + ("x" * 20000) + '"}').encode()

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
    assert stamp == "p18"
    html = repo_root() / "apps" / "producer" / "src" / "diony_producer" / "static" / "index.html"
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

    def fake_script(name: str, root=None):
        return wizard if "wizard" in name else producer

    monkeypatch.setattr("diony_desk.boot.venv_script", fake_script)
    monkeypatch.setattr(
        "diony_desk.boot.port_open", lambda port, host="127.0.0.1": bool(listening.get(port))
    )
    monkeypatch.setattr(
        "diony_desk.boot.wizard_state_ok", lambda host="127.0.0.1", port=None: health["ok"]
    )
    monkeypatch.setattr("diony_desk.boot.wait_port", lambda port, timeout=40.0: True)
    monkeypatch.setattr("diony_desk.boot.ensure_product_names", lambda root=None: True)
    monkeypatch.setattr("diony_desk.boot.finish_open", lambda names_ok=False: None)
    monkeypatch.setattr("diony_desk.boot.listener_is_diony", lambda port: True)
    monkeypatch.setattr("diony_desk.boot.producer_is_current", lambda root=None: True)
    # the Director probe must not reach a live :8790 on the developer's desk
    monkeypatch.setattr("diony_desk.boot.director_is_current", lambda root=None: True)

    def fake_stop(port: int) -> None:
        stopped.append(port)
        listening[port] = False
        health["ok"] = False

    def fake_start(script, log_name, root) -> None:
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

    def fake_script(name: str, root=None):
        return wizard if "wizard" in name else producer

    monkeypatch.setattr("diony_desk.boot.venv_script", fake_script)
    monkeypatch.setattr(
        "diony_desk.boot.port_open", lambda port, host="127.0.0.1": bool(listening.get(port))
    )
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
    # the Director probe must not reach a live :8790 on the developer's desk
    monkeypatch.setattr("diony_desk.boot.director_is_current", lambda root=None: True)

    def fake_stop(port: int) -> None:
        stopped.append(port)
        listening[port] = False

    def fake_start(script, log_name, root) -> None:
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

    monkeypatch.setattr("diony_desk.boot.venv_script", lambda name, root=None: tmp_path / name)
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
    monkeypatch.setattr("diony_desk.boot.venv_script", lambda name, root=None: tmp_path / name)
    monkeypatch.setattr("diony_desk.boot.port_open", lambda port, host="127.0.0.1": port == 8787)
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
    monkeypatch.setattr(
        "diony_desk.boot.wizard_state_ok", lambda host="127.0.0.1", port=None: False
    )
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


def test_agent_scratch_never_ships_in_a_release_payload() -> None:
    """A local .claude directory (agent session scratch) leaked into the
    staged payload, changing the payload sha on every rebuild of an unchanged
    tree — the build script's 'already current' short-circuit never fired."""
    from pathlib import Path as _P

    from diony_desk.release import _ship

    assert not _ship(_P("apps/director/.claude/confidenz-latest.json"))
    assert not _ship(_P(".claude/settings.json"))
    assert _ship(_P("apps/director/src/diony_director/app.py"))


# --- startup visibility: the Dock, the lobby on a slow start, the desk log ---------


def test_a_slow_start_shows_the_lobby_and_a_settled_one_never_opens_a_second_page() -> None:
    from diony_desk import boot

    # still working after the grace, no page open yet → the lobby shows progress
    assert boot.should_open_lobby_while_slow(lobby_opened=False, phase="starting") is True
    assert boot.should_open_lobby_while_slow(lobby_opened=False, phase="looking") is True
    # one page is enough; a settled phase opens its own page from _after_work
    assert boot.should_open_lobby_while_slow(lobby_opened=True, phase="starting") is False
    for phase in ("ready", "need_names", "need_python", "error"):
        assert boot.should_open_lobby_while_slow(lobby_opened=False, phase=phase) is False
    assert 0 < boot.LOBBY_GRACE_S <= 5, "the grace is a few seconds, not a startup"
    # the installed app still opens straight onto Studio when the start is quick
    assert boot.should_open_lobby(already_installed=True, frozen=True) is False


def test_the_dock_is_registered_only_by_the_packaged_mac_app(monkeypatch) -> None:
    from diony_desk import boot

    monkeypatch.setattr(boot, "is_frozen", lambda: False)
    assert boot.dock_finish_launching() is None
    monkeypatch.setattr(boot, "is_frozen", lambda: True)
    monkeypatch.setenv("DIONY_DESK_NO_DOCK", "1")
    assert boot.dock_finish_launching() is None
    monkeypatch.delenv("DIONY_DESK_NO_DOCK", raising=False)
    monkeypatch.setattr(boot.sys, "platform", "linux")
    assert boot.dock_finish_launching() is None


def test_main_finishes_launching_before_any_startup_probe() -> None:
    from diony_desk import boot

    source = Path(boot.__file__).read_text()
    body = source[source.index("def main(") :]
    assert body.index("dock_finish_launching()") < body.index("claim_desk_port()")
    assert "worker.join(LOBBY_GRACE_S)" in body
    assert "dock.run()" in body


def test_startup_logs_roll_instead_of_growing_forever(tmp_path: Path, monkeypatch) -> None:
    from diony_desk import boot

    log = tmp_path / "producer.log"
    log.write_bytes(b"x" * 100)
    assert boot.rotate_log(log, limit=1000) is False and log.is_file()
    assert boot.rotate_log(log, limit=10) is True
    assert not log.exists() and (tmp_path / "producer.previous.log").read_bytes() == b"x" * 100
    assert boot.rotate_log(tmp_path / "absent.log", limit=1) is False
    # the desk writes its own record of every launch beside the service logs
    monkeypatch.setenv("DIONY_DESK_DATA", str(tmp_path / "state"))
    monkeypatch.setattr(boot, "is_frozen", lambda: True)
    boot.say("Preparing ffmpeg.")
    text = (tmp_path / "state" / "logs" / "desk.log").read_text()
    assert "Preparing ffmpeg." in text and f"[{boot.os.getpid()}]" in text
    assert boot.SERVICE_LOG_LIMIT >= 8 * 1024 * 1024 > boot.DESK_LOG_LIMIT // 2
