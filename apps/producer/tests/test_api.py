from fastapi.testclient import TestClient

from diony_producer.app import app


def test_project_loads() -> None:
    client = TestClient(app)
    client.post("/api/reset")
    project = client.get("/api/project").json()
    assert project["id"] == "ep-pilot-night-press"
    assert project["bible"]["width"] == 1920


def test_catalog_lists_length_allotments() -> None:
    client = TestClient(app)
    cat = client.get("/api/catalog").json()
    labels = [item["label"] for item in cat["allotments"]]
    assert labels == ["30s", "1m", "3m", "5m", "10m", "15m", "20m", "30m", "40m", "1hr"]
    assert cat["allotments"][0]["minutes"] == 0.5
    assert cat["allotments"][-1]["minutes"] == 60


def test_note_requires_shot() -> None:
    client = TestClient(app)
    res = client.post("/api/notes", json={"body": "orphan"})
    assert res.status_code == 400


def test_mix_before_picture_refused() -> None:
    client = TestClient(app)
    client.post("/api/reset")
    res = client.post("/api/gates/mix/lock", json={"locked_by": "test"})
    assert res.status_code == 400


def test_picture_lock_after_selects() -> None:
    client = TestClient(app)
    client.post("/api/reset")
    client.post("/api/shots/sh-020/select/sh-020-t1")
    client.post("/api/shots/sh-030/select/sh-030-t1")
    res = client.post("/api/gates/picture/lock", json={"locked_by": "test"})
    assert res.status_code == 200
    project = res.json()
    assert project["timeline"]["picture_lock"] == "locked"


def test_studio_open_from_idea() -> None:
    client = TestClient(app)
    res = client.post(
        "/api/studio/open",
        json={
            "idea": "A lighthouse keeper paints the fog the color of warnings.",
            "customer_kind": "feature",
            "target_minutes": 5,
        },
    )
    assert res.status_code == 200
    project = res.json()
    assert project["customer_kind"] == "feature"
    assert project["brief"]["target_minutes"] == 5
    assert len(project["shots"]) == 10
    assert project["bible"]["width"] == 1920
    rolled = client.post("/api/studio/roll")
    assert rolled.status_code == 200
    assert len(rolled.json()["takes"]) == len(project["shots"])


def test_studio_open_length_goal_is_a_goal_not_trt() -> None:
    client = TestClient(app)
    short = client.post(
        "/api/studio/open",
        json={
            "idea": "A lighthouse keeper paints the fog the color of warnings.",
            "customer_kind": "feature",
            "target_minutes": 0.5,
        },
    )
    assert short.status_code == 200
    assert short.json()["brief"]["target_minutes"] == 0.5
    assert len(short.json()["shots"]) == 4
    hour = client.post(
        "/api/studio/open",
        json={
            "idea": "A season of a newsroom that prints the future.",
            "customer_kind": "series",
            "target_minutes": 60,
        },
    )
    assert hour.status_code == 200
    assert hour.json()["brief"]["target_minutes"] == 60
    assert len(hour.json()["shots"]) == 12
    assert any("First reel" in n["body"] for n in hour.json()["notes"])


def test_program_stitch_after_roll() -> None:
    client = TestClient(app)
    client.post("/api/reset")
    opened = client.post(
        "/api/studio/open",
        json={
            "idea": "A lighthouse keeper paints the fog the color of warnings.",
            "customer_kind": "feature",
            "target_minutes": 3,
        },
    )
    assert opened.status_code == 200
    rolled = client.post("/api/studio/roll")
    assert rolled.status_code == 200
    body = rolled.json()
    assert body["timeline"]["stitched"] is True
    assert body["timeline"]["total_frames"] > 0
    i2v = next(s for s in body["shots"] if s["engine"] == "i2v-A14B")
    event = next(e for e in body["timeline"]["events"] if e["shot_id"] == i2v["id"])
    assert event["keyframe_from_take_id"]
    view = client.get("/api/program").json()
    assert view["clips"]
    assert view["reel"] == "short"
    again = client.post("/api/program/assemble")
    assert again.status_code == 200
    assert again.json()["program"]["stitched"] is True


def test_site_lock_stamps_dual_and_retake() -> None:
    from diony_contracts.cluster import write_site_lock

    write_site_lock(
        {
            "sku": "spark-2x-v1",
            "compute_path": "dual",
            "compute": {
                "path": "dual",
                "profile": "spark-2x-v1",
                "gpu_class": "2×GB10",
                "exception_by": "Rohn Springfield",
            },
            "nodes": ["spark-01", "spark-02"],
            "vault": "nfs://vault",
            "recipe": "spark-2x-v1 TP=2",
            "sequential": True,
            "producer_url": "http://producer.diony.local",
            "exception_by": "Rohn Springfield",
        }
    )
    client = TestClient(app)
    client.post("/api/reset")
    site = client.get("/api/site").json()
    assert site["path"] == "dual"
    assert site["sequential"] is True
    assert site["label"] == "Leased Dual-Spark Cluster"
    catalog = client.get("/api/catalog").json()
    assert catalog["site"]["path"] == "dual"
    assert "Do not quote" in catalog["clock"]
    project = client.get("/api/project").json()
    assert project["compute"]["path"] == "dual"
    opened = client.post(
        "/api/studio/open",
        json={
            "idea": "A lighthouse keeper paints the fog the color of warnings.",
            "customer_kind": "feature",
            "target_minutes": 2,
        },
    )
    assert opened.status_code == 200
    assert opened.json()["compute"]["path"] == "dual"
    rolled = client.post("/api/studio/roll")
    assert rolled.status_code == 200
    shot_id = opened.json()["shots"][0]["id"]
    retake = client.post(f"/api/shots/{shot_id}/retake")
    assert retake.status_code == 200
    gens = [
        t
        for t in retake.json()["takes"]
        if t["shot_id"] == shot_id and t["origin"] == "generation"
    ]
    assert len(gens) == 2
    from diony_producer.vault import load_current_project

    loaded = load_current_project()
    assert loaded is not None
    assert loaded.id == retake.json()["id"]
    assert loaded.compute.path.value == "dual"


def test_retake_cap_and_picture_lock() -> None:
    client = TestClient(app)
    client.post("/api/reset")
    opened = client.post(
        "/api/studio/open",
        json={
            "idea": "A lighthouse keeper paints the fog the color of warnings.",
            "customer_kind": "feature",
            "target_minutes": 2,
        },
    )
    assert opened.status_code == 200
    client.post("/api/studio/roll")
    shot_id = opened.json()["shots"][0]["id"]
    for _ in range(3):
        assert client.post(f"/api/shots/{shot_id}/retake").status_code == 200
    capped = client.post(f"/api/shots/{shot_id}/retake")
    assert capped.status_code == 400
    project = client.get("/api/project").json()
    for shot in project["shots"]:
        client.post(f"/api/shots/{shot['id']}/select/{shot['selected_take_id']}")
    locked = client.post("/api/gates/picture/lock", json={"locked_by": "test"})
    assert locked.status_code == 200
    refused = client.post(f"/api/shots/{shot_id}/retake")
    assert refused.status_code == 400

