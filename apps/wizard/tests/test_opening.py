"""The Studio opening: engine selection is the whole of Studio."""

from fastapi.testclient import TestClient

from diony_contracts.cluster import read_engine_choice
from diony_wizard.app import app


def _client() -> TestClient:
    return TestClient(app)


def test_root_always_serves_the_opening() -> None:
    html = _client().get("/").text
    assert 'data-diony="studio-opening"' in html
    assert "camera" in html


def test_choosing_a_cloud_engine_points_at_the_director() -> None:
    client = _client()
    body = client.post("/api/opening/engine", json={"engine": "grok"}).json()
    assert body["next"] == "http://director.diony.local"
    assert read_engine_choice() == "grok"
    # The opening stays the front door for cloud engines (one-click through).
    assert 'data-diony="studio-opening"' in client.get("/").text


def test_the_legacy_engine_is_refused() -> None:
    client = _client()
    res = client.post("/api/opening/engine", json={"engine": "local_legacy"})
    assert res.status_code == 422
    assert read_engine_choice() == ""


def test_there_is_no_classic_rail_any_more() -> None:
    client = _client()
    assert client.get("/classic").status_code == 404
    assert client.get("/api/state").status_code == 404
    assert "classic_url" not in client.get("/api/opening").json()


def test_unknown_engines_are_refused() -> None:
    res = _client().post("/api/opening/engine", json={"engine": "wan-local"})
    assert res.status_code == 422


def test_opening_state_reports_provider_readiness(monkeypatch, tmp_path) -> None:
    # the opening reloads ~/.diony/keys.env — on a box holding the live
    # USEAPI token that re-injects what delenv removed; isolate the file
    monkeypatch.setenv("DIONY_KEYS_FILE", str(tmp_path / "keys.env"))
    monkeypatch.setenv("XAI_API_KEY", "k")
    monkeypatch.delenv("USEAPI_TOKEN", raising=False)
    state = _client().get("/api/opening").json()
    assert state["providers"]["grok"] is True
    assert state["providers"]["google-flow"] is False
    assert state["director_url"] == "http://director.diony.local"
    assert state["producer_url"] == "http://producer.diony.local"


def test_the_opening_offers_exactly_the_two_cameras() -> None:
    html = _client().get("/").text
    assert html.count('class="card"') == 2
    assert 'data-engine="grok"' in html and 'data-engine="google-flow"' in html
    assert 'data-engine="auto"' not in html
    assert "Director’s choice" not in html and "Director's choice" not in html


def test_there_is_no_automatic_route() -> None:
    client = _client()
    assert client.post("/api/opening/engine", json={"engine": "auto"}).status_code == 422
    assert client.post("/api/opening/engine", json={"engine": "director"}).status_code == 422
    assert read_engine_choice() == ""
