"""ffmpeg comes with Diony: the desk finds the packaged copy, installs the
pinned release when the bundle lacks it, and refuses anything unverified."""

from __future__ import annotations

import hashlib
import json
import os
import re
import zipfile
from pathlib import Path

from diony_desk import boot, media


def _fake_tool(folder: Path, name: str, version: str = "0.test") -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / name
    path.write_text(f'#!/bin/sh\necho "{name} version {version}-fake"\n')
    path.chmod(0o755)
    return path


def _isolate(monkeypatch, tmp_path: Path) -> Path:
    for key in ("DIONY_FFMPEG", "DIONY_FFPROBE"):
        # set (not deleted) so monkeypatch restores whatever the desk exports
        monkeypatch.setenv(key, "")
    monkeypatch.setenv("DIONY_FFMPEG_BUNDLE_DIR", str(tmp_path / "bundle"))
    monkeypatch.setenv("PATH", str(tmp_path / "nothing"))
    monkeypatch.setattr(media, "_known_dirs", lambda platform_name=None: ())
    return tmp_path / "data"


def test_the_lock_pins_every_desktop_platform_by_url_and_sha256() -> None:
    lock = media.read_lock()
    for key in media.PLATFORM_KEYS:
        entry = media.pinned(key)
        assert entry and entry["version"] and entry["license"].startswith("GPL")
        names = set()
        for artifact in entry["artifacts"]:
            assert artifact["url"].startswith("https://")
            assert re.fullmatch(r"[0-9a-f]{64}", artifact["sha256"])
            names.update(artifact["members"].values())
        suffix = ".exe" if key.startswith("windows") else ""
        assert {f"ffmpeg{suffix}", f"ffprobe{suffix}"} <= names
    assert "linux-amd64" not in lock["platforms"]  # no pinned static build: system only
    assert media.pinned("linux-amd64") is None


def test_platform_keys_follow_the_desk() -> None:
    assert media.platform_key("darwin", "arm64") == "macos-arm64"
    assert media.platform_key("darwin", "x86_64") == "macos-amd64"
    assert media.platform_key("win32", "AMD64") == "windows-amd64"
    assert media.platform_key("linux", "x86_64") == "linux-amd64"
    assert media.tool_filename("ffmpeg", "win32") == "ffmpeg.exe"
    assert media.tool_filename("ffprobe", "darwin") == "ffprobe"


def test_the_packaged_copy_wins_then_the_installed_copy_then_the_system(
    monkeypatch, tmp_path
) -> None:
    data = _isolate(monkeypatch, tmp_path)
    assert media.find_tool("ffmpeg", data_dir=data, platform_name="darwin") == ("", "missing")
    installed = _fake_tool(media.tools_dir(data), "ffmpeg")
    assert media.find_tool("ffmpeg", data_dir=data, platform_name="darwin") == (
        str(installed),
        "installed",
    )
    bundled = _fake_tool(tmp_path / "bundle", "ffmpeg")
    assert media.find_tool("ffmpeg", data_dir=data, platform_name="darwin") == (
        str(bundled),
        "bundled",
    )
    explicit = _fake_tool(tmp_path / "mine", "ffmpeg")
    monkeypatch.setenv("DIONY_FFMPEG", str(explicit))
    assert media.find_tool("ffmpeg", data_dir=data, platform_name="darwin") == (
        str(explicit),
        "configured",
    )


def _synthetic_lock(tmp_path: Path, *, corrupt: bool = False) -> dict:
    archive = tmp_path / "ffmpeg-tools.zip"
    with zipfile.ZipFile(archive, "w") as bundle:
        for name in ("ffmpeg", "ffprobe"):
            bundle.writestr(f"tools/bin/{name}", f'#!/bin/sh\necho "{name} version 7.7.7-fake"\n')
        bundle.writestr("tools/LICENSE", "GPL")
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    if corrupt:
        digest = "0" * 64
    return {
        "platforms": {
            "macos-arm64": {
                "version": "7.7.7",
                "source": "https://example.invalid/",
                "license": "GPL-3.0-or-later",
                "artifacts": [
                    {
                        "url": f"https://example.invalid/{archive.name}",
                        "sha256": digest,
                        "members": {
                            "tools/bin/ffmpeg": "ffmpeg",
                            "tools/bin/ffprobe": "ffprobe",
                            "tools/LICENSE": "FFMPEG-LICENSE.txt",
                        },
                    }
                ],
            }
        }
    }


def _fetch_from(tmp_path: Path):
    calls: list[str] = []

    def fetch(url: str, dest: Path, progress) -> None:
        calls.append(url)
        data = (tmp_path / Path(url).name).read_bytes()
        dest.write_bytes(data)
        progress(len(data), len(data))

    fetch.calls = calls  # type: ignore[attr-defined]
    return fetch


def test_a_bare_desk_installs_the_pinned_release_and_exports_it(monkeypatch, tmp_path) -> None:
    data = _isolate(monkeypatch, tmp_path)
    lock = _synthetic_lock(tmp_path)
    monkeypatch.setattr(media, "read_lock", lambda path=None: lock)
    said: list[str] = []
    fetch = _fetch_from(tmp_path)
    result = media.ensure_ffmpeg(
        data_dir=data, say=said.append, fetch=fetch, platform_name="darwin", machine="arm64"
    )
    assert result["ready"] and result["source"] == "installed" and result["version"] == "7.7.7"
    tools = media.tools_dir(data)
    assert result["ffmpeg"] == str(tools / "ffmpeg") and os.access(tools / "ffprobe", os.X_OK)
    assert (tools / "FFMPEG-LICENSE.txt").read_text() == "GPL"
    assert json.loads((tools / "PINNED.json").read_text())["version"] == "7.7.7"
    assert os.environ["DIONY_FFMPEG"] == result["ffmpeg"]
    assert os.environ["DIONY_FFPROBE"] == result["ffprobe"]
    assert fetch.calls == [lock["platforms"]["macos-arm64"]["artifacts"][0]["url"]]
    assert any("Installing ffmpeg 7.7.7" in line for line in said)
    assert said[-1].startswith("ffmpeg 7.7.7 ready — installed by Diony")
    # the next open finds the copy without fetching again: the exported path
    # answers first, and with the export gone the installed copy does
    again = media.ensure_ffmpeg(
        data_dir=data, say=said.append, fetch=fetch, platform_name="darwin", machine="arm64"
    )
    assert again["source"] == "configured" and again["ffmpeg"] == result["ffmpeg"]
    monkeypatch.setenv("DIONY_FFMPEG", "")
    monkeypatch.setenv("DIONY_FFPROBE", "")
    again = media.ensure_ffmpeg(
        data_dir=data, say=said.append, fetch=fetch, platform_name="darwin", machine="arm64"
    )
    assert again["source"] == "installed" and len(fetch.calls) == 1


def test_an_archive_that_fails_its_sha256_installs_nothing(monkeypatch, tmp_path) -> None:
    data = _isolate(monkeypatch, tmp_path)
    lock = _synthetic_lock(tmp_path, corrupt=True)
    monkeypatch.setattr(media, "read_lock", lambda path=None: lock)
    said: list[str] = []
    result = media.ensure_ffmpeg(
        data_dir=data,
        say=said.append,
        fetch=_fetch_from(tmp_path),
        platform_name="darwin",
        machine="arm64",
    )
    assert not result["ready"] and result["source"] == "missing"
    assert (
        "did not match its pinned SHA-256" in result["note"]
        and "brew install ffmpeg" in result["note"]
    )
    assert not (media.tools_dir(data) / "ffmpeg").exists()
    assert not os.environ.get("DIONY_FFMPEG")


def test_a_platform_without_a_pinned_build_reports_the_manual_step(monkeypatch, tmp_path) -> None:
    data = _isolate(monkeypatch, tmp_path)
    result = media.ensure_ffmpeg(
        data_dir=data, say=lambda _: None, platform_name="linux", machine="x86_64"
    )
    assert not result["ready"] and "apt install ffmpeg" in result["note"]
    bundled = _fake_tool(tmp_path / "bundle", "ffmpeg")
    _fake_tool(tmp_path / "bundle", "ffprobe")
    result = media.ensure_ffmpeg(
        data_dir=data, say=lambda _: None, platform_name="darwin", machine="arm64"
    )
    assert result["ready"] and result["source"] == "bundled" and result["ffmpeg"] == str(bundled)


def test_the_desk_prepares_ffmpeg_before_the_services_and_reports_it(monkeypatch, tmp_path) -> None:
    data = _isolate(monkeypatch, tmp_path)
    monkeypatch.setattr(boot, "data_dir", lambda root=None: data)
    _fake_tool(tmp_path / "bundle", "ffmpeg")
    _fake_tool(tmp_path / "bundle", "ffprobe")
    result = boot.ensure_media_tools(tmp_path)
    assert result["ready"] and boot.public_state()["ffmpeg"]["source"] == "bundled"
    src = (Path(boot.__file__)).read_text()
    assert src.index("ensure_media_tools(root)") < src.index(
        "if is_frozen():\n            ensure_running(root)"
    )
