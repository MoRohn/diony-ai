from __future__ import annotations

from importlib import import_module

from fastapi.testclient import TestClient

from diony_contracts.enums import (
    AssetKind,
    ContinuityMode,
    CustomerKind,
    Engine,
    GateName,
    ProductionStopDisposition,
    ProjectStatus,
    ReferenceLock,
)
from diony_contracts.models import Asset, Brief, ProductionStopReceipt
from diony_contracts.studio import expand_idea
from diony_producer.farm import new_generation_take


def _stopped_reference_project():
    screens = [
        Asset(
            id="ast-studio1",
            kind=AssetKind.IMAGE,
            filename="Studio1.png",
            mime="image/png",
            bytes=12,
            sha256="1" * 64,
            path="assets/ast-studio1/Studio1.png",
        ),
        Asset(
            id="ast-dionysius",
            kind=AssetKind.IMAGE,
            filename="Dionysius.png",
            mime="image/png",
            bytes=12,
            sha256="2" * 64,
            path="assets/ast-dionysius/Dionysius.png",
        ),
    ]
    project = expand_idea(
        Brief(
            idea=(
                "Create a Diony AI demo guided by the exact character illustrated in the last "
                "attached image. Scene 1 — Cold Open (0–8s) Dionysius walks into frame. "
                "Narration: ‘Idea in. Show out.’ Scene 2 — Choice (8–22s) Dionysius gestures. "
                "Narration: ‘Two doors.’"
            ),
            customer_kind=CustomerKind.SERIES,
            target_minutes=1,
            attachment_ids=[item.id for item in screens],
        ),
        screens,
    )
    project.id = "studio-recalibration"
    project.bible.locked = True
    bible_gate = next(gate for gate in project.gates if gate.name == GateName.BIBLE)
    bible_gate.open = False
    bible_gate.locked_by = "showrunner"
    old_take_ids: list[str] = []
    for shot in project.shots:
        take = new_generation_take(project, shot, live=True)
        project.takes.append(take)
        shot.selected_take_id = take.id
        old_take_ids.append(take.id)
    first_artifact = project.takes[0].artifacts[0]
    first_artifact.path = "projects/studio-recalibration/takes/sh-010-t1/negative_720.mp4"
    first_artifact.sha256 = "a" * 64
    project.production_started_at = "2026-08-22T11:58:51Z"
    project.production_ended_at = "2026-08-22T12:43:57Z"
    project.production_stop = ProductionStopReceipt(
        disposition=ProductionStopDisposition.KEEP,
        completed_take_ids=old_take_ids[:2],
        result_path=(
            "projects/studio-recalibration/program/"
            "tl-studio-recalibration/incomplete_720.mov"
        ),
        result_sha256="f" * 64,
        result_width=1280,
        result_height=720,
    )
    project.status = ProjectStatus.STOPPED
    return project, old_take_ids


def test_recalibrated_restart_preserves_old_takes_and_rolls_new_reference_locked_takes(
    monkeypatch,
) -> None:
    app_module = import_module("diony_producer.app")
    project, old_take_ids = _stopped_reference_project()
    app_module._project = project
    monkeypatch.setattr(app_module, "maybe_advance_run", lambda _project: None)
    client = TestClient(app_module.app)

    restarted = client.post(
        "/api/production/restart",
        json={"reason": "reference_and_performance_recalibration"},
    )

    assert restarted.status_code == 200
    body = restarted.json()
    assert body["id"] == "studio-recalibration"
    assert body["version"] == 2
    assert body["status"] == ProjectStatus.SHOOTING
    assert body["production_stop"] is None
    assert body["production_started_at"] is not None
    assert body["production_ended_at"] is None
    assert len(body["takes"]) == len(project.shots) * 2
    assert {take["id"] for take in body["takes"]}.issuperset(old_take_ids)
    assert next(take for take in body["takes"] if take["id"] == old_take_ids[0])["artifacts"][0][
        "sha256"
    ] == "a" * 64

    restart = body["production_restarts"][-1]
    assert restart["previous_stop"]["completed_take_ids"] == old_take_ids[:2]
    assert restart["preserved_take_ids"] == old_take_ids
    assert restart["replacement_take_ids"] == [
        f"{shot['id']}-t2" for shot in body["shots"]
    ]
    assert restart["prompt_strategy_version"] == "wan22-five-layer-v3"
    assert restart["reference_asset_ids"] == ["ast-dionysius"]

    for index, shot in enumerate(body["shots"]):
        assert shot["engine"] == Engine.I2V_A14B
        assert shot["selected_take_id"] == f"{shot['id']}-t2"
        assert shot["keyframe_asset_ids"] == ["ast-dionysius"]
        assert shot["reference_lock"] == ReferenceLock.CHARACTER
        expected = ContinuityMode.REFERENCE if index == 0 else ContinuityMode.PREVIOUS_FRAME
        assert shot["continuity_mode"] == expected
        assert shot["prompt_plan"]["strategy_version"] == "wan22-five-layer-v3"

    assert body["timeline"]["preview_path"].endswith("stitch.txt")
    assert body["timeline"]["picture_sha256"] == ""
    assert body["timeline"]["program_sha256"] == ""
    assert [event["take_id"] for event in body["timeline"]["events"]] == [
        shot["selected_take_id"] for shot in body["shots"]
    ]
    assert body["timeline"]["events"][1]["keyframe_from_take_id"] == "sh-010-t2"
    gates = {gate["name"]: gate for gate in body["gates"]}
    assert gates["bible"]["open"] is False
    assert gates["picture"]["open"] is True
    assert gates["mix"]["open"] is True


def test_recalibrated_restart_requires_a_durably_stopped_production() -> None:
    app_module = import_module("diony_producer.app")
    project, _ = _stopped_reference_project()
    project.production_stop = None
    app_module._project = project
    client = TestClient(app_module.app)

    response = client.post(
        "/api/production/restart",
        json={"reason": "reference_and_performance_recalibration"},
    )

    assert response.status_code == 409
    assert "stopped" in response.json()["detail"].lower()
