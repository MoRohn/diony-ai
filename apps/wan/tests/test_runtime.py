import importlib

from diony_wan import runtime


def test_runtime_probe_reports_every_missing_import(monkeypatch) -> None:
    def fake_import(name: str):
        if name in {"einops", "wan"}:
            raise ModuleNotFoundError(f"No module named '{name}'")
        return type("Module", (), {"__version__": "1.0"})()

    monkeypatch.setattr(importlib, "import_module", fake_import)
    report = runtime.probe_runtime()

    assert report["schema_version"] == 1
    assert report["ok"] is False
    assert [row["module"] for row in report["failures"]] == ["einops", "wan"]
    assert report["capabilities"]["t2v"]["missing"] == ["einops", "Wan2.2"]
    assert report["capabilities"]["i2v"]["missing"] == ["einops", "Wan2.2"]
    assert report["python"]


def test_runtime_contract_includes_upstream_omission() -> None:
    assert ("einops", "einops") in runtime.REQUIRED_IMPORTS
    assert ("decord", "decord") in runtime.REQUIRED_IMPORTS
    assert ("openai-whisper", "whisper") in runtime.OPTIONAL_ENGINE_IMPORTS["s2v"]
    assert ("SAM-2", "sam2") in runtime.OPTIONAL_ENGINE_IMPORTS["animate"]
    assert ("Wan2.2", "wan") in runtime.REQUIRED_IMPORTS
