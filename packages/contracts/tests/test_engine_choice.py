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


def test_the_three_cloud_era_choices_are_legal(tmp_path, monkeypatch) -> None:
    _isolate(tmp_path, monkeypatch)
    assert set(ENGINE_CHOICES) == {"auto", "grok", "google-flow"}
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
