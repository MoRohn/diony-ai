from __future__ import annotations

import time
from importlib import import_module

from fastapi.testclient import TestClient

from diony_contracts import ProductionPauseReceipt
from diony_contracts.fixture import demo_project
from diony_contracts.studio import blank_studio
from diony_producer.vault import persist_project


def test_pause_after_current_is_durable_and_resume_requeues_the_same_selected_takes(
    monkeypatch,
) -> None:
    app_module = import_module("diony_producer.app")
    farm = import_module("diony_producer.farm")
    project = demo_project()
    project.mark_production_start()
    for shot in project.shots:
        selected = project.takes_for(shot.id)[-1]
        shot.selected_take_id = selected.id
    app_module._project = project
    monkeypatch.setattr(app_module, "maybe_advance_run", lambda _project: None)
    monkeypatch.setattr(app_module, "live_camera", lambda: False)
    client = TestClient(app_module.app)

    paused = client.post(
        "/api/production/pause-after-current",
        json={"reason": "performance_qualification", "after_take_id": "sh-010-t2"},
    )

    assert paused.status_code == 200
    pause = paused.json()["production_pause"]
    assert pause["reason"] == "performance_qualification"
    assert pause["after_take_id"] == "sh-010-t2"
    assert pause["remaining_take_ids"] == [
        shot.selected_take_id for shot in project.shots if shot.selected_take_id != "sh-010-t2"
    ]
    board = client.get("/api/run").json()
    assert board["phase"] == "paused"
    assert board["rolling"] is False
    assert "sh-010-t2" in board["now"]

    resumed = client.post("/api/production/resume")

    assert resumed.status_code == 200
    assert resumed.json()["production_pause"] is None
    assert [shot["selected_take_id"] for shot in resumed.json()["shots"]] == [
        shot.selected_take_id for shot in project.shots
    ]


def test_farm_finishes_current_take_then_holds_pending_selected_takes(monkeypatch) -> None:
    farm = import_module("diony_producer.farm")
    project = demo_project()
    selected = []
    for shot in project.shots[:2]:
        take = project.takes_for(shot.id)[-1]
        shot.selected_take_id = take.id
        selected.append(take)
    completed: list[str] = []
    monkeypatch.setattr("diony_wan.runner.prove_generate", lambda: None)
    monkeypatch.setattr(farm, "list_generate_jobs", lambda: [])
    monkeypatch.setattr(farm, "take_has_live_negative", lambda *_args: False)
    monkeypatch.setattr(farm, "auto_retake_for_qc_failure", lambda *_args: None)
    monkeypatch.setattr(farm, "_take_integrity_error", lambda *_args: "")
    monkeypatch.setattr(farm, "maybe_advance_run", lambda *_args: None, raising=False)

    def finish_first(current_project, take, **_kwargs):
        completed.append(take.id)
        farm.request_pause_after_current(
            current_project,
            reason="performance_qualification",
            after_take_id=take.id,
        )

    monkeypatch.setattr(farm, "run_one", finish_first)

    farm.start_farm(project, selected)
    for _ in range(100):
        if not farm.farm_status()["busy"]:
            break
        time.sleep(0.01)

    assert completed == [selected[0].id]
    assert project.production_pause is not None
    assert project.production_pause.remaining_take_ids == [selected[1].id]
    assert farm.farm_status()["queued"] == 0


def test_project_poll_is_read_only_and_vault_project_can_be_activated(
    monkeypatch,
    tmp_path,
) -> None:
    app_module = import_module("diony_producer.app")
    monkeypatch.setenv("DIONY_VAULT", str(tmp_path))
    target = demo_project()
    target.production_pause = ProductionPauseReceipt(
        reason="b200_attention_qualification",
        after_take_id="sh-010-t1",
        remaining_take_ids=["sh-020-t1"],
    )
    persist_project(target)
    pointer = tmp_path / ".cluster" / "current.json"
    expected_pointer = pointer.read_text()
    app_module._project = blank_studio()
    monkeypatch.setattr(app_module, "maybe_advance_run", lambda _project: None)
    monkeypatch.setattr(app_module, "live_camera", lambda: False)
    client = TestClient(app_module.app)

    polled = client.get("/api/project")

    assert polled.status_code == 200
    assert pointer.read_text() == expected_pointer
    activated = client.post(f"/api/projects/{target.id}/activate")
    assert activated.status_code == 200
    assert activated.json()["id"] == target.id
    assert activated.json()["production_pause"]["reason"] == "b200_attention_qualification"
    assert target.id in pointer.read_text()


def test_infrastructure_recovery_reuses_selected_takes_and_keeps_qualification_hold(
    monkeypatch,
) -> None:
    app_module = import_module("diony_producer.app")
    from diony_contracts.enums import ProductionStopDisposition
    from diony_contracts.models import ProductionStopReceipt

    project = demo_project()
    selected_ids = []
    for shot in project.shots:
        take = project.takes_for(shot.id)[-1]
        shot.selected_take_id = take.id
        selected_ids.append(take.id)
    project.production_stop = ProductionStopReceipt(
        disposition=ProductionStopDisposition.DELETE,
        completed_take_ids=[selected_ids[0]],
    )
    project.production_pause = ProductionPauseReceipt(
        reason="b200_attention_qualification",
        after_take_id=selected_ids[0],
        remaining_take_ids=selected_ids[1:],
    )
    app_module._project = project
    monkeypatch.setattr(app_module, "live_camera", lambda: False)
    client = TestClient(app_module.app)

    recovered = client.post(
        "/api/production/recover-infrastructure",
        json={"reason": "gpu_service_restart"},
    )

    assert recovered.status_code == 200
    body = recovered.json()
    assert body["production_stop"] is None
    assert body["production_pause"] is not None
    assert [shot["selected_take_id"] for shot in body["shots"]] == selected_ids
    assert body["production_recoveries"][-1]["resumed_take_ids"] == selected_ids
