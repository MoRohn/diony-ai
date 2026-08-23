import threading
import time
from importlib import import_module

from fastapi.testclient import TestClient


def test_signed_gpu_reconcile_does_not_block_local_producer_port(monkeypatch) -> None:
    import uvicorn

    from diony_contracts.fixture import demo_project
    app_module = import_module("diony_producer.app")

    entered = threading.Event()
    release = threading.Event()
    served = threading.Event()
    resumed = threading.Event()

    def slow_gpu_reconcile():
        entered.set()
        release.wait(timeout=2)

    monkeypatch.setattr(app_module, "backfill_connect_from_wizard", lambda: None)
    monkeypatch.setattr(app_module, "apply_ssh_env", lambda: None)
    monkeypatch.setattr(app_module, "should_proxy", lambda: True)
    monkeypatch.setattr(app_module, "ensure_gpu_producer", slow_gpu_reconcile)
    monkeypatch.setattr(app_module, "gpu_producer_ready_base", lambda: None)
    monkeypatch.setattr(app_module, "_gpu_reconcile_thread", None)
    monkeypatch.setattr(app_module, "_boot_project", demo_project)
    monkeypatch.setattr(app_module, "resume_camera", lambda project: project)
    monkeypatch.setattr(app_module, "_apply_site", lambda project: project)
    monkeypatch.setattr(app_module, "maybe_advance_run", lambda project: resumed.set())
    monkeypatch.setattr(uvicorn, "run", lambda *args, **kwargs: served.set())

    started = time.monotonic()
    try:
        app_module.main()
    finally:
        release.set()

    assert time.monotonic() - started < 0.5
    assert entered.wait(timeout=0.5)
    assert served.is_set()
    assert resumed.is_set()


def test_signed_gpu_reconcile_does_not_block_application_lifespan(monkeypatch) -> None:
    app_module = import_module("diony_producer.app")
    entered = threading.Event()
    release = threading.Event()

    def slow_gpu_reconcile():
        entered.set()
        release.wait(timeout=2)

    monkeypatch.setattr(app_module, "backfill_connect_from_wizard", lambda: None)
    monkeypatch.setattr(app_module, "apply_ssh_env", lambda: None)
    monkeypatch.setattr(app_module, "should_proxy", lambda: True)
    monkeypatch.setattr(app_module, "ensure_gpu_producer", slow_gpu_reconcile)
    monkeypatch.setattr(app_module, "gpu_producer_ready_base", lambda: None)
    monkeypatch.setattr(app_module, "_gpu_reconcile_thread", None)

    started = time.monotonic()
    try:
        with TestClient(app_module.app) as client:
            assert client.get("/api/project").status_code == 200
    finally:
        release.set()

    assert time.monotonic() - started < 0.5
    assert entered.wait(timeout=0.5)


def test_gpu_sync_contract_includes_finish_runtime_and_stamp() -> None:
    gpu_module = import_module("diony_producer.gpu")

    assert (
        "apps/finish/src/diony_finish",
        "/opt/diony-ai/apps/finish/src/diony_finish",
    ) in gpu_module.GPU_SYNC_TREES
    assert "apps/finish/src/diony_finish/rrdb.py" in gpu_module.GPU_STAMP_FILES
    assert "apps/finish/src/diony_finish/pipeline.py" in gpu_module.GPU_STAMP_FILES
