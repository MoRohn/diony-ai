from __future__ import annotations

import hashlib
import json
import struct
import tomllib
import zlib
from pathlib import Path

import pytest

from diony_desk.release import (
    FEATURE_FILE_SETS,
    build_development_report,
    build_download_manifest,
    stage_product,
)


def test_desktop_release_version_matches_packaged_desk() -> None:
    root = Path(__file__).resolve().parents[3]
    release_version = json.loads(
        (root / "packaging" / "desktop" / "version.json").read_text()
    )["version"]
    desk_version = tomllib.loads(
        (root / "apps" / "desk" / "pyproject.toml").read_text()
    )["project"]["version"]

    assert desk_version == release_version


def test_desktop_and_home_surfaces_use_the_bar_mark_logo_and_app_icon() -> None:
    root = Path(__file__).resolve().parents[3]
    brand_source = root / "Diony-AI-logo.png"
    icons = root / "packaging" / "desktop" / "icons"
    master = icons / "diony-ai-app-icon.png"
    mac_icon = icons / "diony-ai-app-icon.icns"
    windows_icon = icons / "diony-ai-app-icon.ico"
    spec = (root / "packaging" / "desktop" / "diony.spec").read_text()

    logo_png = brand_source.read_bytes()
    assert hashlib.sha256(logo_png).hexdigest() == (
        "3623606e41eec9ead5fafa7e06aca23be9415618e06379864606c8e6e507e1ae"
    )
    assert logo_png.startswith(b"\x89PNG\r\n\x1a\n")
    assert struct.unpack(">II", logo_png[16:24]) == (960, 895)
    assert logo_png[24] == 8
    assert logo_png[25] == 6  # RGBA: the bar mark floats on a transparent canvas.

    png = master.read_bytes()
    assert hashlib.sha256(png).hexdigest() == (
        "d85f8d697148c27c7dfe54bbfc51fd3c833300d967899c599df46fde81d02aa9"
    )
    assert png.startswith(b"\x89PNG\r\n\x1a\n")
    assert struct.unpack(">II", png[16:24]) == (1024, 1024)
    assert png[24] == 8  # stable 8-bit desktop/web master
    assert png[25] == 6  # RGBA: the rounded charcoal tile keeps transparent corners.
    position = 8
    compressed = bytearray()
    while position < len(png):
        length = struct.unpack(">I", png[position : position + 4])[0]
        chunk_type = png[position + 4 : position + 8]
        chunk = png[position + 8 : position + 8 + length]
        if chunk_type == b"IDAT":
            compressed.extend(chunk)
        position += 12 + length
    first_scanline = zlib.decompress(bytes(compressed))[: 1 + (1024 * 4)]
    assert first_scanline[1:5] == bytes((0, 0, 0, 0))  # transparent tile corner
    assert mac_icon.read_bytes().startswith(b"icns")
    assert windows_icon.read_bytes().startswith(b"\x00\x00\x01\x00")
    assert 'name="Diony.app"' in spec
    assert '"CFBundleDisplayName": "Diony"' in spec
    assert '"CFBundleName": "Diony"' in spec
    assert 'name="Diony AI.app"' not in spec
    assert 'MAC_ICON = ICONS / "diony-ai-app-icon.icns"' in spec
    assert 'WINDOWS_ICON = ICONS / "diony-ai-app-icon.ico"' in spec
    assert "icon=str(DESKTOP_ICON)" in spec
    assert "icon=str(MAC_ICON)" in spec

    surfaces = {
        "desk": root / "apps" / "desk" / "src" / "diony_desk" / "static",
        "wizard": root / "apps" / "wizard" / "src" / "diony_wizard" / "static",
        "producer": root / "apps" / "producer" / "src" / "diony_producer" / "static",
    }
    for name, static in surfaces.items():
        html = (static / "index.html").read_text()
        app_icon = static / "diony-ai-app-icon.png"
        assert 'rel="icon"' in html, name
        assert "diony-ai-app-icon.png?v=brand3" in html, name
        assert "diony-ai-optical-mark" not in html, name
        assert 'class="diony-mark"' in html, name
        assert '<span class="diony-word">Diony</span>' in html, name
        assert 'fill="currentColor"' in html, name
        assert not (static / "diony-ai-optical-mark.png").exists(), name
        assert app_icon.read_bytes() == png, name


def test_release_environment_has_exact_direct_dependency_pins() -> None:
    root = Path(__file__).resolve().parents[3]
    requirements = [
        line.strip()
        for line in (root / "packaging" / "desktop" / "requirements-release.txt")
        .read_text()
        .splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]

    assert requirements
    assert all("==" in line for line in requirements)
    assert not any(
        operator in line
        for line in requirements
        for operator in (">=", "<=", "~=", "<", ">")
    )


def test_stage_product_is_complete_but_excludes_machine_state(tmp_path: Path) -> None:
    source = tmp_path / "source"
    (source / "apps" / "wizard" / "src").mkdir(parents=True)
    (source / "apps" / "wizard" / "tests").mkdir(parents=True)
    (source / "apps" / "wizard" / "src" / "diony_wizard.egg-info").mkdir()
    (source / "apps" / "wizard" / "build" / "lib").mkdir(parents=True)
    (source / "apps" / "producer" / "data").mkdir(parents=True)
    (source / "packages" / "contracts").mkdir(parents=True)
    (source / "infra" / "profiles").mkdir(parents=True)
    (source / "blueprints").mkdir(parents=True)
    for name in ("LICENSE", "NOTICE", "README.md", "CHANGELOG.md", "START HERE.txt"):
        (source / name).write_text(f"{name}\n")
    (source / "apps" / "wizard" / "src" / "app.py").write_text("PRODUCT = True\n")
    (source / "apps" / "wizard" / "tests" / "test_app.py").write_text("secret test\n")
    (source / "apps" / "wizard" / "src" / "diony_wizard.egg-info" / "PKG-INFO").write_text(
        "generated metadata\n"
    )
    (source / "apps" / "wizard" / "build" / "lib" / "stale.py").write_text(
        "generated wheel copy\n"
    )
    (source / "apps" / "producer" / "data" / "project.json").write_text("private\n")
    (source / "packages" / "contracts" / "pyproject.toml").write_text("[project]\n")
    (source / "infra" / "profiles" / "spark-4x-v1.yaml").write_text("sku: spark\n")
    (source / "blueprints" / "00-product-constitution.md").write_text("truth\n")
    for relatives in FEATURE_FILE_SETS.values():
        for relative in relatives:
            feature_file = source / relative
            feature_file.parent.mkdir(parents=True, exist_ok=True)
            feature_file.write_text(f"packaged feature: {relative.as_posix()}\n")
    (source / ".env").write_text("TOKEN=must-not-ship\n")

    target = tmp_path / "payload"
    report = stage_product(source, target, version="1.2.3")

    assert (target / "apps" / "wizard" / "src" / "app.py").is_file()
    assert (target / "infra" / "profiles" / "spark-4x-v1.yaml").is_file()
    assert not (target / "apps" / "wizard" / "tests").exists()
    assert not (
        target / "apps" / "wizard" / "src" / "diony_wizard.egg-info"
    ).exists()
    assert not (target / "apps" / "wizard" / "build").exists()
    assert not (target / "apps" / "producer" / "data").exists()
    assert not (target / ".env").exists()
    metadata = json.loads((target / "desktop-release.json").read_text())
    assert metadata["version"] == "1.2.3"
    assert metadata["payload_sha256"] == report["payload_sha256"]
    assert metadata["files"] == report["files"]


def test_stage_product_preserves_wan_prompt_intelligence_exactly(tmp_path: Path) -> None:
    """Future desktop builds must ship the reviewed prompt system, byte for byte."""
    root = Path(__file__).resolve().parents[3]
    target = tmp_path / "payload"

    stage_product(root, target, version="test-wan-prompt")

    required = (
        Path("blueprints/20-wan22-prompt-intelligence.md"),
        Path("packages/contracts/jsonschema/wan_prompt_plan.schema.json"),
        Path("packages/contracts/src/diony_contracts/wan_prompt.py"),
    )
    for relative in required:
        assert (target / relative).read_bytes() == (root / relative).read_bytes()


def test_stage_product_preserves_voice_performance_system_exactly(tmp_path: Path) -> None:
    """Desktop payloads must carry the complete reviewed voice system and receipt."""
    root = Path(__file__).resolve().parents[3]
    target = tmp_path / "payload"
    required = (
        Path("apps/desk/src/diony_desk/boot.py"),
        Path("apps/producer/src/diony_producer/app.py"),
        Path("apps/producer/src/diony_producer/farm.py"),
        Path("apps/producer/src/diony_producer/gpu.py"),
        Path("apps/producer/src/diony_producer/mix.py"),
        Path("apps/producer/src/diony_producer/voice.py"),
        Path("packages/contracts/src/diony_contracts/__init__.py"),
        Path("packages/contracts/src/diony_contracts/enums.py"),
        Path("packages/contracts/src/diony_contracts/export_schema.py"),
        Path("packages/contracts/src/diony_contracts/models.py"),
        Path("packages/contracts/src/diony_contracts/voice.py"),
        Path("packages/contracts/jsonschema/bible.schema.json"),
        Path("packages/contracts/jsonschema/performance_script.schema.json"),
        Path("packages/contracts/jsonschema/project.schema.json"),
        Path("packages/contracts/jsonschema/qc_report.schema.json"),
        Path("packages/contracts/jsonschema/shot.schema.json"),
        Path("packages/contracts/jsonschema/take.schema.json"),
        Path("packages/contracts/jsonschema/voice_clip_package.schema.json"),
        Path("packages/contracts/jsonschema/voice_profile.schema.json"),
        Path("packages/contracts/jsonschema/voice_qc_receipt.schema.json"),
        Path("blueprints/22-voice-performance-system.md"),
    )

    stage_product(root, target, version="test-voice-performance")

    for relative in required:
        assert (target / relative).read_bytes() == (root / relative).read_bytes()
    metadata = json.loads((target / "desktop-release.json").read_text())
    receipt = metadata["features"]["voice_performance"]
    assert receipt["paths"] == [path.as_posix() for path in required]
    assert receipt["files"] == len(required)
    assert len(receipt["sha256"]) == 64


def test_stage_product_preserves_optimal_clip_flow_and_gpu_qualifier(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[3]
    target = tmp_path / "payload"
    required = (
        Path("apps/producer/src/diony_producer/farm.py"),
        Path("apps/producer/src/diony_producer/gpu.py"),
        Path("apps/producer/src/diony_producer/run.py"),
        Path("apps/producer/src/diony_producer/static/app.js"),
        Path("apps/producer/src/diony_producer/static/index.html"),
        Path("apps/wan/src/diony_wan/attention_qualification.py"),
        Path("apps/wan/src/diony_wan/runner.py"),
        Path("packages/contracts/src/diony_contracts/models.py"),
        Path("packages/contracts/src/diony_contracts/studio.py"),
        Path("packages/contracts/jsonschema/generation_execution_receipt.schema.json"),
        Path("scripts/benchmark/measure_wan_attention.py"),
        Path("blueprints/23-optimal-clip-production-flow.md"),
    )

    stage_product(root, target, version="test-optimal-clip-flow")

    for relative in required:
        assert (target / relative).read_bytes() == (root / relative).read_bytes()
    receipt = json.loads((target / "desktop-release.json").read_text())["features"][
        "optimal_clip_flow"
    ]
    assert receipt["paths"] == [path.as_posix() for path in required]
    assert receipt["files"] == len(required)
    assert len(receipt["sha256"]) == 64


def test_stage_product_rejects_an_incomplete_voice_performance_feature(tmp_path: Path) -> None:
    source = tmp_path / "source"
    product = source / "apps" / "wizard" / "src" / "wizard.py"
    product.parent.mkdir(parents=True)
    product.write_text("PRODUCT = True\n")

    with pytest.raises(ValueError, match="voice_performance"):
        stage_product(source, tmp_path / "payload", version="1.2.3")


def test_development_report_marks_local_app_as_non_publishable(tmp_path: Path) -> None:
    app = tmp_path / "Diony AI.app"
    executable = app / "Contents" / "MacOS" / "Diony AI"
    executable.parent.mkdir(parents=True)
    executable.write_bytes(b"frozen local app")
    payload = tmp_path / "payload" / "desktop-release.json"
    payload.parent.mkdir()
    payload.write_text(
        '{"version":"1.2.3","files":42,"payload_sha256":"payload-hash",'
        '"features":{"voice_performance":{"files":21,"sha256":"voice-hash"}}}\n'
    )

    report = build_development_report(
        app=app,
        payload_metadata=payload,
        version="1.2.3",
        architecture="arm64",
        built_at="2026-08-20T12:00:00Z",
    )

    assert report["channel"] == "development"
    assert report["publishable"] is False
    assert report["trust"] == "ad-hoc-local-only"
    assert report["build_recipe"] == "macos-development-v3"
    assert report["version"] == "1.2.3"
    assert report["architecture"] == "arm64"
    assert report["payload_sha256"] == "payload-hash"
    assert report["features"]["voice_performance"]["sha256"] == "voice-hash"
    assert report["bundle_bytes"] == len(b"frozen local app")
    assert len(report["bundle_sha256"]) == 64


def test_stable_download_manifest_requires_verified_mac_and_windows_artifacts(
    tmp_path: Path,
) -> None:
    mac = tmp_path / "DionyAI-1.2.3-macOS-universal.dmg"
    windows = tmp_path / "DionyAI-Setup-1.2.3-windows-x64.exe"
    mac.write_bytes(b"mac artifact")
    windows.write_bytes(b"windows artifact")

    with pytest.raises(ValueError, match="verified release proof"):
        build_download_manifest(
            version="1.2.3",
            artifacts={"mac": mac, "windows": windows},
            proofs={"mac": {}, "windows": {}},
            channel="stable",
            published_at="2026-08-20T00:00:00Z",
        )

    manifest = build_download_manifest(
        version="1.2.3",
        artifacts={"mac": mac, "windows": windows},
        proofs={
            "mac": {
                "signed": True,
                "notarized": True,
                "stapled": True,
                "gatekeeper_verified": True,
            },
            "windows": {
                "signed": True,
                "timestamped": True,
                "signature_verified": True,
            },
        },
        channel="stable",
        published_at="2026-08-20T00:00:00Z",
    )

    assert set(manifest["downloads"]) == {"mac", "windows"}
    assert manifest["downloads"]["mac"]["label"] == "Download for Mac"
    assert manifest["downloads"]["windows"]["label"] == "Download for Windows"
    assert manifest["downloads"]["mac"]["url"].endswith(mac.name)
    assert manifest["downloads"]["windows"]["url"].endswith(windows.name)
    assert len(manifest["downloads"]["mac"]["sha256"]) == 64
    assert manifest["downloads"]["windows"]["bytes"] == len(b"windows artifact")


def test_stable_download_manifest_rejects_unversioned_artifact_names(tmp_path: Path) -> None:
    mac = tmp_path / "DionyAI-latest.dmg"
    windows = tmp_path / "DionyAI-Setup.exe"
    mac.write_bytes(b"mac")
    windows.write_bytes(b"windows")

    with pytest.raises(ValueError, match="artifact filename"):
        build_download_manifest(
            version="1.2.3",
            artifacts={"mac": mac, "windows": windows},
            proofs={"mac": {}, "windows": {}},
        )


def test_desktop_release_scripts_enforce_platform_trust_checks() -> None:
    root = Path(__file__).resolve().parents[3]
    mac = (root / "scripts" / "release" / "build-macos.sh").read_text()
    windows = (root / "scripts" / "release" / "build-windows.ps1").read_text()
    installer = (root / "packaging" / "windows" / "DionyAI.iss").read_text()

    assert "notarytool submit" in mac
    assert "stapler staple" in mac
    assert "spctl" in mac
    assert "codesign --verify" in mac
    assert "codesign --deep" not in mac
    assert 'APP="$BUILD_ROOT/dist/Diony.app"' in mac
    assert '"$DMG_STAGE/Diony.app"' in mac
    assert "Diony AI.app" not in mac
    assert 'Invoke-Checked $SignTool @("sign"' in windows
    assert '"/fd", "SHA256"' in windows
    assert '"/tr"' in windows and '"/td", "SHA256"' in windows
    assert 'Invoke-Checked $SignTool @("verify", "/pa"' in windows
    assert "PrivilegesRequired=admin" in installer
    assert "UninstallDisplayName=Diony AI" in installer


def test_desktop_release_scripts_require_supported_build_python() -> None:
    root = Path(__file__).resolve().parents[3]
    mac = (root / "scripts" / "release" / "build-macos.sh").read_text()
    windows = (root / "scripts" / "release" / "build-windows.ps1").read_text()

    assert "DIONY_RELEASE_PYTHON" in mac
    assert "sys.version_info >= (3, 11)" in mac
    assert '"$RELEASE_PYTHON" -m venv' in mac
    assert '/usr/bin/python3 -m venv' not in mac
    assert "DIONY_RELEASE_PYTHON" in windows
    assert "sys.version_info >= (3, 11)" in windows
    assert "py -3.11 -m venv" not in windows


def test_local_mac_builder_creates_a_stable_in_repo_app_without_release_claims() -> None:
    root = Path(__file__).resolve().parents[3]
    command = root / "Build Diony AI.command"
    builder = root / "scripts" / "release" / "build-local-macos.sh"

    assert command.is_file()
    assert command.stat().st_mode & 0o111
    assert builder.is_file()
    assert builder.stat().st_mode & 0o111
    command_text = command.read_text()
    builder_text = builder.read_text()
    assert "build-local-macos.sh" in command_text
    assert 'open -n "$APP"' in command_text
    assert 'open -R "$APP"' not in command_text
    assert "dist/desktop" in builder_text
    assert "development" in builder_text
    assert "diony_desk.release" in builder_text
    assert "stage" in builder_text
    assert "development-report" in builder_text
    assert "DIONY_MAC_ARCH" in builder_text
    assert "PyInstaller" in builder_text
    assert 'INSTALL_PAYLOAD="$BUILD_ROOT/install-payload"' in builder_text
    assert '"$INSTALL_PAYLOAD/apps/wizard"' in builder_text
    assert '"$PAYLOAD/apps/wizard"' not in builder_text
    assert '"$ROOT/apps/wizard"' not in builder_text
    assert "build-macos.sh" not in builder_text
    assert "notarytool submit" not in builder_text
    assert "gatekeeper_verified" not in builder_text
    assert "/bin/ln -sfn" in builder_text
    assert 'STABLE_APP="$OUTPUT/Diony.app"' in builder_text
    assert 'BUILT_APP="$BUILD_ROOT/dist/Diony.app"' in builder_text
    assert 'LEGACY_STABLE_APP="$OUTPUT/Diony AI.app"' in builder_text
    assert '[[ -L "$LEGACY_STABLE_APP" ]]' in builder_text
    assert '/bin/unlink "$LEGACY_STABLE_APP"' in builder_text
    assert 'APP="$ROOT/dist/desktop/$VERSION/development/Diony.app"' in command_text
    assert "Diony AI.app was not created" not in command_text


def test_release_builders_do_not_mutate_the_hashed_payload_during_wheel_builds() -> None:
    root = Path(__file__).resolve().parents[3]
    mac = (root / "scripts" / "release" / "build-macos.sh").read_text()
    windows = (root / "scripts" / "release" / "build-windows.ps1").read_text()
    assert 'INSTALL_PAYLOAD="$BUILD_ROOT/install-payload"' in mac
    assert '"$INSTALL_PAYLOAD/apps/wizard"' in mac
    assert "$InstallPayload = Join-Path $BuildRoot \"install-payload\"" in windows
    assert '(Join-Path $InstallPayload "apps\\wizard")' in windows
