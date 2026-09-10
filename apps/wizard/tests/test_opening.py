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
    assert state["providers"]["gemini"] is False
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


def test_the_opening_explains_the_keys_and_names_useapi() -> None:
    html = _client().get("/").text
    assert "A key is required" in html
    # collapsed by default: a native <details> whose summary carries the chevron
    assert (
        '<details class="keys" id="keys">' in html
        and '<details class="keys" id="keys" open' not in html
    )
    assert '<summary id="keys-title"><svg class="chev"' in html
    assert "~/.diony/keys.env" in html
    assert 'href="https://console.x.ai/"' in html
    assert 'href="https://useapi.net/"' in html
    assert 'href="https://useapi.net/docs/api-google-flow-v1"' in html
    for line in ("XAI_API_KEY=", "USEAPI_TOKEN=", "DIONY_FLOW_EMAIL=", "DIONY_FLOW_PLAN="):
        assert line in html, line
    assert "Google AI plan" in html and "monthly credits" in html
    # a camera without its key cannot be chosen
    assert "ready[engine] === true" in html
    assert "Add USEAPI_TOKEN to ${keysPath()}" in html


def test_the_opening_names_the_google_house(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("DIONY_KEYS_FILE", str(tmp_path / "keys.env"))
    monkeypatch.setenv("GEMINI_API_KEY", "g")
    client = _client()
    assert client.get("/api/opening").json()["providers"]["gemini"] is True
    html = client.get("/").text
    assert 'id="flow-house"' in html and 'id="grok-house"' in html
    assert "Script and narrator: Gemini, on GEMINI_API_KEY" in html
    assert "the script and narrator borrow Grok" in html
    assert 'href="https://aistudio.google.com/apikey"' in html
    assert "GEMINI_API_KEY=" in html


def test_the_opening_carries_the_mac_windows_toggle_and_follows_it(monkeypatch) -> None:
    from diony_contracts.cluster import read_platform_choice

    client = _client()
    state = client.get("/api/opening").json()["platform"]
    assert state["detected"] in {"mac", "windows", "linux"}
    assert state["chosen"] in {"mac", "windows"}
    assert state["hints"]["keys_path"] and state["hints"]["ffmpeg_install"]
    html = client.get("/").text
    assert 'id="platform-seg"' in html
    assert 'data-platform="mac"' in html and 'data-platform="windows"' in html
    assert "winget install Gyan.FFmpeg" in html and "brew install ffmpeg" in html
    assert "Open Diony AI.bat" in html and "Open Diony AI.command" in html
    assert "User Account Control" in html
    assert 'fetch("/api/opening/platform"' in html
    chosen = client.post("/api/opening/platform", json={"platform": "windows"}).json()
    assert chosen["platform"] == "windows"
    assert (
        chosen["hints"]["keys_path"].startswith("C:\\Users")
        and "winget" in chosen["hints"]["ffmpeg_install"]
    )
    assert read_platform_choice() == "windows"
    assert client.get("/api/opening").json()["platform"]["chosen"] == "windows"
    assert client.post("/api/opening/platform", json={"platform": "amiga"}).status_code == 422
    back = client.post("/api/opening/platform", json={"platform": "mac"}).json()
    assert (
        back["hints"]["keys_path"] == "~/.diony/keys.env"
        and "brew" in back["hints"]["ffmpeg_install"]
    )
