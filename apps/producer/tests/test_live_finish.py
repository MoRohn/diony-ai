import time

from fastapi.testclient import TestClient

from diony_producer.app import app


def test_picture_lock_writes_1080_master(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("DIONY_DRY_RUN", raising=False)
    monkeypatch.setenv("DIONY_LIVE", "1")
    monkeypatch.setenv("DIONY_FINISH_FAKE", "1")
    monkeypatch.delenv("DIONY_FINISH_URL", raising=False)
    monkeypatch.setenv("DIONY_VAULT", str(tmp_path / "vault"))
    monkeypatch.setenv("DIONY_PRODUCER_VAULT", str(tmp_path / "vault"))
    client = TestClient(app)
    client.post("/api/reset")
    sample = client.get("/api/project").json()
    for take in sample["takes"]:
        dest = tmp_path / "vault" / "takes" / take["id"] / "negative_720.mp4"
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 200)
    for shot in sample["shots"]:
        if shot.get("selected_take_id"):
            client.post(f"/api/shots/{shot['id']}/select/{shot['selected_take_id']}")
    client.post("/api/shots/sh-020/select/sh-020-t1")
    client.post("/api/shots/sh-030/select/sh-030-t1")
    locked = client.post("/api/gates/picture/lock", json={"locked_by": "test"})
    assert locked.status_code == 200
    fin = client.get("/api/finish").json()
    assert fin["live"] is True
    deadline = time.time() + 20
    found = None
    while time.time() < deadline:
        project = client.get("/api/project").json()
        for take in project["takes"]:
            for art in take["artifacts"]:
                if "finish_1080" in art["path"] and art.get("width") == 1920:
                    path = tmp_path / "vault" / art["path"]
                    if path.is_file() and path.stat().st_size > 32:
                        found = path
                        break
        if found:
            break
        time.sleep(0.2)
    assert found is not None, "picture lock should write a 1080 master"
    master_file = None
    deadline = time.time() + 20
    while time.time() < deadline:
        project = client.get("/api/project").json()
        master = project["timeline"]["preview_path"]
        if "master_1080" in (master or ""):
            dest = tmp_path / "vault" / master
            if dest.is_file() and dest.stat().st_size > 32:
                master_file = dest
                break
        time.sleep(0.2)
    assert master_file is not None, "picture lock should stitch a 1080 program master"
    idle = time.time() + 15
    while time.time() < idle:
        if not client.get("/api/finish").json()["busy"]:
            mix = client.post("/api/gates/mix/lock", json={"locked_by": "test"})
            assert mix.status_code == 200
            return
        time.sleep(0.2)
    raise AssertionError("finish loop did not go idle")


def test_picture_lock_refuses_live_without_sr(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("DIONY_DRY_RUN", raising=False)
    monkeypatch.setenv("DIONY_LIVE", "1")
    monkeypatch.delenv("DIONY_FINISH_FAKE", raising=False)
    monkeypatch.setenv("DIONY_SR_CKPT", str(tmp_path / "missing-sr"))
    monkeypatch.setenv("DIONY_VAULT", str(tmp_path / "vault"))
    monkeypatch.setenv("DIONY_PRODUCER_VAULT", str(tmp_path / "vault"))
    client = TestClient(app)
    client.post("/api/reset")
    sample = client.get("/api/project").json()
    for shot in sample["shots"]:
        if shot.get("selected_take_id"):
            client.post(f"/api/shots/{shot['id']}/select/{shot['selected_take_id']}")
    client.post("/api/shots/sh-020/select/sh-020-t1")
    client.post("/api/shots/sh-030/select/sh-030-t1")
    locked = client.post("/api/gates/picture/lock", json={"locked_by": "test"})
    assert locked.status_code == 400
    assert "diony_sr_v1" in locked.json()["detail"]
