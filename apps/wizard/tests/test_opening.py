"""The Studio opening: engine selection first; the classic rail is the legacy choice."""

from fastapi.testclient import TestClient

from diony_contracts.cluster import read_engine_choice
from diony_wizard.app import app


def _client() -> TestClient:
    return TestClient(app)


def test_root_always_serves_the_opening() -> None:
    html = _client().get("/").text
    assert 'data-diony="studio-opening"' in html
    assert "production engine" in html


def test_choosing_a_cloud_engine_points_at_the_director() -> None:
    client = _client()
    body = client.post("/api/opening/engine", json={"engine": "grok"}).json()
    assert body["next"] == "http://director.diony.local"
    assert read_engine_choice() == "grok"
    # The opening stays the front door for cloud engines (one-click through).
    assert 'data-diony="studio-opening"' in client.get("/").text


def test_the_legacy_engine_is_retired_from_the_chooser() -> None:
    client = _client()
    res = client.post("/api/opening/engine", json={"engine": "local_legacy"})
    assert res.status_code == 422
    assert read_engine_choice() == ""
    # the opening stays the front door regardless
    assert 'data-diony="studio-opening"' in client.get("/").text


def test_classic_is_always_reachable_explicitly() -> None:
    html = _client().get("/classic").text
    assert 'data-diony="studio-opening"' not in html


def test_unknown_engines_are_refused() -> None:
    res = _client().post("/api/opening/engine", json={"engine": "wan-local"})
    assert res.status_code == 422


def test_opening_state_reports_provider_readiness(monkeypatch) -> None:
    monkeypatch.setenv("XAI_API_KEY", "k")
    monkeypatch.delenv("USEAPI_TOKEN", raising=False)
    state = _client().get("/api/opening").json()
    assert state["providers"]["grok"] is True
    assert state["providers"]["google-flow"] is False
    assert state["director_url"] == "http://director.diony.local"
    assert state["producer_url"] == "http://producer.diony.local"
