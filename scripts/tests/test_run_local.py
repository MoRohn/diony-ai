from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("diony_run_local", ROOT / "scripts" / "run_local.py")
assert SPEC and SPEC.loader
run_local = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = run_local
SPEC.loader.exec_module(run_local)


def test_child_environment_defaults_to_one_local_vault(monkeypatch, tmp_path):
    monkeypatch.setattr(run_local, "ROOT", tmp_path)
    for key in (
        "DIONY_VAULT",
        "DIONY_PRODUCER_VAULT",
        "DIONY_BIND",
        "DIONY_STUDIO_URL",
        "DIONY_DIRECTOR_URL",
        "DIONY_PRODUCER_URL",
    ):
        monkeypatch.delenv(key, raising=False)

    env = run_local._child_environment()

    assert env["DIONY_VAULT"] == str(tmp_path / ".diony-vault")
    assert env["DIONY_PRODUCER_VAULT"] == env["DIONY_VAULT"]
    assert env["DIONY_BIND"] == "127.0.0.1"
    assert env["DIONY_STUDIO_URL"] == "http://127.0.0.1:8787"
    assert env["DIONY_DIRECTOR_URL"] == "http://127.0.0.1:8790"
    assert env["DIONY_PRODUCER_URL"] == "http://127.0.0.1:8788"


def test_child_environment_preserves_explicit_configuration(monkeypatch, tmp_path):
    monkeypatch.setattr(run_local, "ROOT", tmp_path)
    monkeypatch.setenv("DIONY_VAULT", "/custom/vault")
    monkeypatch.setenv("DIONY_PRODUCER_VAULT", "/custom/producer")
    monkeypatch.setenv("DIONY_WIZARD_PORT", "9787")
    monkeypatch.setenv("DIONY_STUDIO_URL", "https://studio.example.test/path/")

    env = run_local._child_environment()

    assert env["DIONY_VAULT"] == "/custom/vault"
    assert env["DIONY_PRODUCER_VAULT"] == "/custom/producer"
    assert env["DIONY_STUDIO_URL"] == "https://studio.example.test/path/"
    assert env["DIONY_WIZARD_PORT"] == "9787"


def test_services_are_the_three_browser_surfaces():
    assert {service.name for service in run_local.SERVICES} == {"Studio", "Director", "Producer"}
    assert {service.default_port for service in run_local.SERVICES} == {8787, 8788, 8790}
