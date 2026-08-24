from __future__ import annotations

import hashlib
from importlib import import_module
from pathlib import Path

from fastapi.testclient import TestClient

from diony_contracts.enums import ArtifactKind, ProductionStopDisposition, QCVerdict
from diony_contracts.models import Project
from diony_contracts.wan_prompt import prompt_receipt_sha256


def _opened_project(client: TestClient) -> Project:
    opened = client.post(
        "/api/studio/open",
        json={
            "idea": "A lighthouse keeper paints the fog the color of warnings.",
            "customer_kind": "social",
            "target_minutes": 1,
        },
    )
    assert opened.status_code == 200
    rolled = client.post("/api/studio/roll")
    assert rolled.status_code == 200
    return Project.model_validate(rolled.json())


def _land_completed_negatives(project: Project, root: Path, count: int) -> list[Path]:
    farm = import_module("diony_producer.farm")
    landed: list[Path] = []
    for shot in project.shots[:count]:
        take = project.take(shot.selected_take_id or "")
        path = root / "projects" / project.id / "takes" / take.id / "negative_720.mp4"
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = b"\x00\x00\x00\x18ftypmp42" + take.id.encode() * 80
        path.write_bytes(payload)
        artifact = next(item for item in take.artifacts if item.kind == ArtifactKind.NEGATIVE_720)
        artifact.path = str(path.relative_to(root))
        artifact.project_id = project.id
        artifact.shot_id = shot.id
        artifact.bytes = len(payload)
        artifact.sha256 = hashlib.sha256(payload).hexdigest()
        artifact.prompt_sha256 = prompt_receipt_sha256(shot)
        artifact.reference_sha256 = farm._reference_sha256(project, take)
        artifact.reference_asset_ids = list(shot.keyframe_asset_ids)
        if take.qc:
            take.qc.verdict = QCVerdict.PASS
            take.qc.reference_verified = True
            take.qc.reference_asset_ids = list(shot.keyframe_asset_ids)
        landed.append(path)
    return landed


def test_stop_keep_cancels_future_camera_and_saves_a_stitched_incomplete_result(
    tmp_path, monkeypatch
) -> None:
    app_module = import_module("diony_producer.app")
    stop_module = import_module("diony_producer.production_stop")
    client = TestClient(app_module.app)
    project = _opened_project(client)
    landed = _land_completed_negatives(project, tmp_path, 2)
    app_module._project = project
    cancelled: list[str] = []

    monkeypatch.setattr(app_module, "abort_farm", lambda: cancelled.append("farm"))
    monkeypatch.setattr(app_module, "abort_finish", lambda: cancelled.append("finish"))
    monkeypatch.setattr(app_module, "abort_mix", lambda: cancelled.append("mix"))

    def fake_stitch(clips, destination, fps):
        assert [row[0] for row in clips] == landed
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(b"\x00\x00\x00\x18ftypmp42" + b"incomplete" * 80)
        return destination

    monkeypatch.setattr(stop_module, "stitch_masters", fake_stitch)

    stopped = client.post("/api/production/stop", json={"disposition": "keep"})

    assert stopped.status_code == 200
    board = stopped.json()
    assert cancelled == ["farm", "finish", "mix"]
    assert board["phase"] == "stopped"
    assert board["rolling"] is False
    assert board["can_stop"] is False
    assert board["stop"]["disposition"] == "keep"
    assert board["stop"]["completed_clip_count"] == 2
    assert board["stop"]["result_url"] == "/api/program/file"
    assert board["production_standard"]["status"] == "INCOMPLETE"
    assert board["production_standard"]["master_ready"] is False
    assert "incomplete result" in board["now"].lower()
    result = tmp_path / board["stop"]["result_path"]
    assert result.is_file()
    assert result.stat().st_size > 32

    persisted = client.get("/api/project").json()
    assert persisted["production_stop"]["disposition"] == "keep"
    assert persisted["production_stop"]["completed_take_ids"] == [
        project.shots[0].selected_take_id,
        project.shots[1].selected_take_id,
    ]
    assert client.post("/api/studio/roll").status_code == 409
    assert client.get("/api/run").json()["phase"] == "stopped"
    gallery = client.get("/api/gallery").json()
    kept = next(item for item in gallery["items"] if item["project_id"] == project.id)
    assert kept["id"] == f"program-{project.id}"
    assert kept["playable"] is True
    assert kept["finished"] is False
    assert kept["plate"] == "720 incomplete · not final"
    assert kept["file_url"].startswith(f"/api/projects/{project.id}/program/preview")


def test_stop_keep_cancels_the_active_clip_and_publishes_only_completed_clips(
    tmp_path, monkeypatch
) -> None:
    app_module = import_module("diony_producer.app")
    farm_module = import_module("diony_producer.farm")
    stop_module = import_module("diony_producer.production_stop")
    client = TestClient(app_module.app)
    project = _opened_project(client)
    landed = _land_completed_negatives(project, tmp_path, 1)
    app_module._project = project
    farm_module.reset_farm()
    completed_take_id = project.shots[0].selected_take_id or ""
    active_take_id = project.shots[1].selected_take_id or ""
    cancelled: list[str] = []
    monkeypatch.setattr(app_module, "should_proxy", lambda: False)
    monkeypatch.setattr(app_module, "camera_rolling", lambda: True)
    monkeypatch.setattr(app_module, "abort_farm", lambda: cancelled.append("farm"))
    monkeypatch.setattr(app_module, "abort_finish", lambda: cancelled.append("finish"))
    monkeypatch.setattr(app_module, "abort_mix", lambda: cancelled.append("mix"))

    def fake_stitch(clips, destination, fps):
        assert [row[0] for row in clips] == landed
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(b"\x00\x00\x00\x18ftypmp42" + b"kept" * 160)
        return destination

    monkeypatch.setattr(stop_module, "stitch_masters", fake_stitch)

    stopped = client.post("/api/production/stop", json={"disposition": "keep"})

    assert stopped.status_code == 200
    assert cancelled == ["farm", "finish", "mix"]
    assert project.production_pause is None
    assert project.production_stop is not None
    assert project.production_stop.completed_take_ids == [completed_take_id]
    assert active_take_id not in project.production_stop.completed_take_ids
    assert stopped.json()["phase"] == "stopped"
    assert stopped.json()["can_stop"] is False
    gallery = client.get("/api/gallery").json()
    assert any(item["id"] == f"program-{project.id}" for item in gallery["items"])


def test_pending_stop_keep_from_an_older_service_is_cancelled_immediately(
    tmp_path, monkeypatch
) -> None:
    app_module = import_module("diony_producer.app")
    farm_module = import_module("diony_producer.farm")
    stop_module = import_module("diony_producer.production_stop")
    client = TestClient(app_module.app)
    project = _opened_project(client)
    landed = _land_completed_negatives(project, tmp_path, 1)
    completed_take_id = project.shots[0].selected_take_id or ""
    active_take_id = project.shots[1].selected_take_id or ""
    farm_module.request_pause_after_current(
        project,
        reason="stop_keep",
        after_take_id=active_take_id,
    )
    app_module._project = project
    cancelled: list[str] = []
    monkeypatch.setattr(app_module, "should_proxy", lambda: False)
    monkeypatch.setattr(app_module, "camera_rolling", lambda: True)
    monkeypatch.setattr(app_module, "farm_status", lambda: {"busy": True})
    monkeypatch.setattr(app_module, "abort_farm", lambda: cancelled.append("farm"))
    monkeypatch.setattr(app_module, "abort_finish", lambda: cancelled.append("finish"))
    monkeypatch.setattr(app_module, "abort_mix", lambda: cancelled.append("mix"))

    def fake_stitch(clips, destination, fps):
        assert [row[0] for row in clips] == landed
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(b"\x00\x00\x00\x18ftypmp42" + b"kept" * 160)
        return destination

    monkeypatch.setattr(stop_module, "stitch_masters", fake_stitch)

    app_module._advance_legacy_once()

    assert cancelled == ["farm", "finish", "mix"]
    assert project.production_pause is None
    assert project.production_stop is not None
    assert project.production_stop.completed_take_ids == [completed_take_id]
    assert active_take_id not in project.production_stop.completed_take_ids


def test_interrupted_stop_keep_finalization_is_completed_after_restart(
    tmp_path, monkeypatch
) -> None:
    app_module = import_module("diony_producer.app")
    stop_module = import_module("diony_producer.production_stop")
    client = TestClient(app_module.app)
    project = _opened_project(client)
    landed = _land_completed_negatives(project, tmp_path, 2)
    receipt = stop_module.record_production_stop(project, ProductionStopDisposition.KEEP)
    app_module._project = project
    cancelled: list[str] = []
    monkeypatch.setattr(app_module, "should_proxy", lambda: False)
    monkeypatch.setattr(app_module, "abort_farm", lambda: cancelled.append("farm"))
    monkeypatch.setattr(app_module, "abort_finish", lambda: cancelled.append("finish"))
    monkeypatch.setattr(app_module, "abort_mix", lambda: cancelled.append("mix"))

    def fake_stitch(clips, destination, fps):
        assert [row[0] for row in clips] == landed
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(b"\x00\x00\x00\x18ftypmp42" + b"recovered" * 160)
        return destination

    monkeypatch.setattr(stop_module, "stitch_masters", fake_stitch)

    assert receipt.completed_take_ids
    assert receipt.result_path == ""
    app_module._advance_legacy_once()

    assert cancelled == ["farm", "finish", "mix"]
    assert receipt.result_path.endswith("/incomplete_720.mov")
    assert receipt.result_sha256
    assert receipt.result_error == ""


def test_stop_delete_removes_generated_project_media_and_is_idempotent(tmp_path) -> None:
    app_module = import_module("diony_producer.app")
    client = TestClient(app_module.app)
    project = _opened_project(client)
    landed = _land_completed_negatives(project, tmp_path, 2)
    app_module._project = project

    stopped = client.post("/api/production/stop", json={"disposition": "delete"})

    assert stopped.status_code == 200
    board = stopped.json()
    assert board["phase"] == "stopped"
    assert board["stop"]["disposition"] == "delete"
    assert board["stop"]["completed_clip_count"] == 2
    assert board["stop"]["result_path"] == ""
    assert board["stop"]["deleted_file_count"] >= 2
    assert all(not path.exists() for path in landed)
    assert all(
        not any(
            artifact.kind in {ArtifactKind.NEGATIVE_720, ArtifactKind.FINISH_1080}
            and artifact.path.startswith(f"projects/{project.id}/")
            for artifact in take.artifacts
        )
        for take in app_module._project.takes
    )

    repeated = client.post("/api/production/stop", json={"disposition": "delete"})
    assert repeated.status_code == 200
    assert repeated.json()["stop"] == board["stop"]


def test_stop_cannot_change_the_retention_choice_after_it_is_recorded(tmp_path) -> None:
    app_module = import_module("diony_producer.app")
    client = TestClient(app_module.app)
    project = _opened_project(client)
    app_module._project = project

    assert client.post("/api/production/stop", json={"disposition": "delete"}).status_code == 200

    changed = client.post("/api/production/stop", json={"disposition": "keep"})
    assert changed.status_code == 409
    assert "already stopped" in changed.json()["detail"].lower()
