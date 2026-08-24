import importlib
import json
import threading
from pathlib import Path

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


def test_runtime_report_writes_are_isolated_when_validations_overlap(
    tmp_path: Path, monkeypatch
) -> None:
    destination = tmp_path / "wan-host-runtime.json"
    both_writers_ready = threading.Barrier(2)
    original_write_text = Path.write_text

    def synchronized_write_text(path: Path, *args, **kwargs):
        written = original_write_text(path, *args, **kwargs)
        if path.name.startswith("wan-host-runtime.json") and path.suffix == ".tmp":
            both_writers_ready.wait(timeout=2)
        return written

    monkeypatch.setattr(Path, "write_text", synchronized_write_text)
    failures: list[BaseException] = []

    def write_report(writer: int) -> None:
        try:
            runtime._write_report(str(destination), {"writer": writer})
        except BaseException as exc:  # noqa: BLE001 - the test retains thread failures
            failures.append(exc)

    threads = [threading.Thread(target=write_report, args=(writer,)) for writer in (1, 2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=3)

    assert failures == []
    assert json.loads(destination.read_text())["writer"] in {1, 2}
    assert list(tmp_path.glob("wan-host-runtime.json.*.tmp")) == []
