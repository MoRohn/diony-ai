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


def test_prompt_to_edit_api_is_not_shipped(tmp_path, monkeypatch) -> None:
    client = _client(tmp_path, monkeypatch)

    response = client.post(
        "/api/takes/sh-010-t2/edit",
        json={"instruction": "Make the rain heavier."},
    )

    assert response.status_code == 404


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
