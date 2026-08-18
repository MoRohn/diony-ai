from pathlib import Path

from fastapi.testclient import TestClient

from diony_producer.app import app


def _client(tmp_path: Path, monkeypatch) -> TestClient:
    monkeypatch.setenv("DIONY_PRODUCER_VAULT", str(tmp_path))
    client = TestClient(app)
    client.post("/api/reset")
    return client


def test_upload_still_and_cast_from_refs(tmp_path, monkeypatch) -> None:
    client = _client(tmp_path, monkeypatch)
    still = client.post(
        "/api/assets",
        files={"file": ("lead.png", b"\x89PNG\r\n\x1a\n" + b"ref", "image/png")},
    )
    assert still.status_code == 200, still.text
    asset = still.json()
    assert asset["kind"] == "image"
    assert asset["filename"] == "lead.png"
    pages = client.post(
        "/api/assets",
        files={"file": ("notes.md", b"# Cold open\nA baker times bread to a siren.", "text/markdown")},
    )
    assert pages.status_code == 200
    doc = pages.json()
    assert "baker" in doc["extracted_text"]
    opened = client.post(
        "/api/studio/open",
        json={
            "idea": "",
            "customer_kind": "education",
            "target_minutes": 3,
            "attachment_ids": [asset["id"], doc["id"]],
        },
    )
    assert opened.status_code == 200, opened.text
    project = opened.json()
    ids = {a["id"] for a in project["assets"]}
    assert asset["id"] in ids
    assert doc["id"] in ids
    assert any(shot["keyframe_asset_ids"] for shot in project["shots"])


def test_reject_unsupported_upload(tmp_path, monkeypatch) -> None:
    client = _client(tmp_path, monkeypatch)
    res = client.post(
        "/api/assets",
        files={"file": ("payload.exe", b"MZ", "application/octet-stream")},
    )
    assert res.status_code == 400


def test_mark_print_creates_child_version(tmp_path, monkeypatch) -> None:
    client = _client(tmp_path, monkeypatch)
    rolled = client.post("/api/studio/roll")
    assert rolled.status_code == 200
    parent = next(t for t in rolled.json()["takes"] if t["shot_id"] == "sh-010")
    res = client.post(
        f"/api/takes/{parent['id']}/edit",
        json={"instruction": "Hold her eyes on the nick. No new face."},
    )
    assert res.status_code == 200, res.text
    project = res.json()
    child = next(t for t in project["takes"] if t.get("parent_take_id") == parent["id"])
    assert child["origin"] == "edit"
    assert child["engine"] == "vace-fun-A14B"
    assert child["version"] == parent.get("version", 1) + 1
    assert project["shot" if False else "shots"]
    selected = next(s for s in project["shots"] if s["id"] == "sh-010")["selected_take_id"]
    assert selected == child["id"]
    assert any(e["result_take_id"] == child["id"] for e in project["edits"])
    still_parent = next(t for t in project["takes"] if t["id"] == parent["id"])
    assert still_parent["asset_720p_id"]
    ladder = client.get("/api/shots/sh-010/versions")
    assert ladder.status_code == 200
    versions = [t["version"] for t in ladder.json()["takes"]]
    assert versions == sorted(versions)
    assert ladder.json()["selected_take_id"] == child["id"]


def test_restore_older_print_without_deleting_child(tmp_path, monkeypatch) -> None:
    client = _client(tmp_path, monkeypatch)
    client.post("/api/studio/roll")
    parent_id = "sh-010-t1"
    edited = client.post(
        f"/api/takes/{parent_id}/edit",
        json={"instruction": "Darker tungsten practicals."},
    )
    child_id = next(
        t["id"] for t in edited.json()["takes"] if t.get("parent_take_id") == parent_id
    )
    restored = client.post(f"/api/shots/sh-010/select/{parent_id}")
    assert restored.status_code == 200
    shot = next(s for s in restored.json()["shots"] if s["id"] == "sh-010")
    assert shot["selected_take_id"] == parent_id
    assert any(t["id"] == child_id for t in restored.json()["takes"])


def test_picture_lock_refuses_edit(tmp_path, monkeypatch) -> None:
    client = _client(tmp_path, monkeypatch)
    client.post("/api/shots/sh-020/select/sh-020-t1")
    client.post("/api/shots/sh-030/select/sh-030-t1")
    locked = client.post("/api/gates/picture/lock", json={"locked_by": "test"})
    assert locked.status_code == 200
    res = client.post(
        "/api/takes/sh-010-t2/edit",
        json={"instruction": "Make the rain heavier."},
    )
    assert res.status_code == 400
    assert "lock" in res.json()["detail"].lower()


def test_artifact_file_is_served(tmp_path, monkeypatch) -> None:
    client = _client(tmp_path, monkeypatch)
    project = client.get("/api/project").json()
    take = next(t for t in project["takes"] if t["id"] == "sh-010-t2")
    arts = take.get("artifacts") or []
    assert arts, "demo take must carry versioned artifacts"
    art_id = arts[0]["id"]
    res = client.get(f"/api/artifacts/{art_id}/file")
    assert res.status_code == 200
    assert res.content
