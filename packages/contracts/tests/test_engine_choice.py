"""The production-engine choice: written by Studio's opening, read by the Director."""

import pytest

from diony_contracts.cluster import (
    ENGINE_CHOICES,
    engine_choice_path,
    read_engine_choice,
    write_engine_choice,
)


def _isolate(tmp_path, monkeypatch):
    monkeypatch.setenv("DIONY_VAULT", str(tmp_path))
    monkeypatch.setenv("DIONY_PRODUCER_VAULT", str(tmp_path))


def test_no_choice_reads_empty(tmp_path, monkeypatch) -> None:
    _isolate(tmp_path, monkeypatch)
    assert read_engine_choice() == ""


def test_choice_roundtrips(tmp_path, monkeypatch) -> None:
    _isolate(tmp_path, monkeypatch)
    path = write_engine_choice("grok")
    assert path == engine_choice_path()
    assert read_engine_choice() == "grok"
    write_engine_choice("google-flow")
    assert read_engine_choice() == "google-flow"


def test_unknown_engines_are_refused(tmp_path, monkeypatch) -> None:
    _isolate(tmp_path, monkeypatch)
    with pytest.raises(ValueError):
        write_engine_choice("wan-local")
    assert read_engine_choice() == ""


def test_garbage_on_disk_reads_empty(tmp_path, monkeypatch) -> None:
    _isolate(tmp_path, monkeypatch)
    engine_choice_path().parent.mkdir(parents=True, exist_ok=True)
    engine_choice_path().write_text("not json")
    assert read_engine_choice() == ""


def test_the_two_cameras_are_the_only_legal_choices(tmp_path, monkeypatch) -> None:
    _isolate(tmp_path, monkeypatch)
    assert set(ENGINE_CHOICES) == {"grok", "google-flow"}
    for choice in ENGINE_CHOICES:
        write_engine_choice(choice)
        assert read_engine_choice() == choice


def test_local_legacy_is_retired(tmp_path, monkeypatch) -> None:
    _isolate(tmp_path, monkeypatch)
    with pytest.raises(ValueError):
        write_engine_choice("local_legacy")
    # a choice written before the retirement reads as no choice at all
    engine_choice_path().parent.mkdir(parents=True, exist_ok=True)
    engine_choice_path().write_text('{"engine": "local_legacy"}')
    assert read_engine_choice() == ""


def test_the_platform_choice_defaults_to_the_detected_desk_and_persists(
    tmp_path, monkeypatch
) -> None:
    from diony_contracts import cluster

    _isolate(tmp_path, monkeypatch)
    detected = cluster.detected_platform()
    assert detected in {"mac", "windows", "linux"}
    assert cluster.read_platform_choice() == ("windows" if detected == "windows" else "mac")
    cluster.write_platform_choice("windows")
    assert cluster.read_platform_choice() == "windows"
    hints = cluster.platform_hints()
    assert hints["platform"] == "windows" and hints["launcher"] == "Open Diony AI.bat"
    assert (
        "winget" in cluster.ffmpeg_install_hint() and "ffmpeg.exe" in cluster.ffmpeg_install_hint()
    )
    cluster.write_platform_choice("mac")
    assert cluster.platform_hints()["launcher"] == "Open Diony AI.command"
    assert "brew install ffmpeg" in cluster.ffmpeg_install_hint()
    with pytest.raises(ValueError):
        cluster.write_platform_choice("beos")
    # a stale or foreign value on disk reads as the detected desk
    cluster.platform_choice_path().write_text('{"platform": "amiga"}')
    assert cluster.read_platform_choice() == ("windows" if detected == "windows" else "mac")


def test_a_windows_desk_is_detected_from_the_interpreter(monkeypatch) -> None:
    from diony_contracts import cluster

    monkeypatch.setattr(cluster.sys, "platform", "win32")
    assert cluster.detected_platform() == "windows"
    monkeypatch.setattr(cluster.sys, "platform", "darwin")
    assert cluster.detected_platform() == "mac"
    monkeypatch.setattr(cluster.sys, "platform", "linux")
    assert cluster.detected_platform() == "linux"
