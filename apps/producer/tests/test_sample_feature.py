from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from diony_producer.app import app


STATIC = Path(__file__).resolve().parents[1] / "src" / "diony_producer" / "static"


def test_sample_intake_uses_the_idea_reference_and_normal_production_run() -> None:
    client = TestClient(app)
    uploaded = client.post(
        "/api/assets",
        files={"file": ("glassblower.png", b"sample-image-bytes", "image/png")},
        data={"role": "idea_ref"},
    )
    assert uploaded.status_code == 200
    reference_id = uploaded.json()["id"]

    opened = client.post(
        "/api/studio/open",
        json={
            "idea": "Animate this image as a glassblower shaping a tiny sun.",
            "customer_kind": "feature",
            "production_kind": "sample",
            "sample_seconds": 10,
            "attachment_ids": [reference_id],
        },
    )

    assert opened.status_code == 200
    sample = opened.json()
    assert sample["brief"]["production_kind"] == "sample"
    assert sample["brief"]["sample_seconds"] == 10
    assert sample["brief"]["target_minutes"] == pytest.approx(10 / 60)
    assert len(sample["shots"]) == 2
    assert all(shot["keyframe_asset_ids"] == [reference_id] for shot in sample["shots"])

    rolled = client.post("/api/studio/roll")
    assert rolled.status_code == 200
    assert len(rolled.json()["takes"]) == 2
    run = client.get("/api/run").json()
    assert run["production_kind"] == "sample"
    assert run["sample_seconds"] == 10
    assert run["goal"] == "10s sample"
    assert run["phase"] == "dry_run"
    assert run["waiting"] is None
    assert "No master or Gallery media" in run["now"]
    assert run["production_standard"]["master_ready"] is False
    assert run["monitoring"]["signal"]["title"] == "Dry run complete — no master"
    assert run["monitoring"]["signal"]["state"] == "waiting"
    assert [step["id"] for step in run["steps"]] == [
        "bible",
        "farm",
        "stitch",
        "picture",
        "mix",
        "distribution",
    ]


def test_sample_intake_rejects_a_duration_outside_the_product_window() -> None:
    client = TestClient(app)
    response = client.post(
        "/api/studio/open",
        json={
            "idea": "A glassblower shapes a tiny sun before dawn.",
            "customer_kind": "feature",
            "production_kind": "sample",
            "sample_seconds": 12,
        },
    )

    assert response.status_code == 400
    assert "between 5 and 10 seconds" in response.json()["detail"]


def test_idea_page_presents_sample_as_an_input_driven_generation_action() -> None:
    html = (STATIC / "index.html").read_text()
    js = (STATIC / "app.js").read_text()

    assert 'id="make-sample"' in html
    assert 'aria-label="Sample length"' in html
    assert 'data-sample-seconds="5"' in html
    assert 'data-sample-seconds="10"' in html
    assert "finished 5–10s clip" in html
    assert 'production_kind: "sample"' in js
    assert "sample_seconds: sampleSeconds" in js
    assert 'document.getElementById("make-sample")' in js
    sample_handler = js[js.index('document.getElementById("make-sample")') :]
    assert "/api/reset" not in sample_handler[:700]


def test_sample_controls_stack_inside_the_mobile_idea_folio() -> None:
    css = (STATIC / "app.css").read_text()

    assert "@media (max-width: 620px)" in css
    assert "#bay-idea {\n    overflow-x: hidden;\n    overflow-y: auto;" in css
    assert ".idea-tools {\n    display: grid;\n    grid-template-columns: auto minmax(0, 1fr);" in css
    assert ".sample-maker {\n    grid-column: 1 / -1;" in css
    assert ".sample-controls {\n    width: 100%;" in css
