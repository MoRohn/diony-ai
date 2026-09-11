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
    assert body["next"] == "http://127.0.0.1:8790"
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
    assert state["director_url"] == "http://127.0.0.1:8790"
    assert state["producer_url"] == "http://127.0.0.1:8788"


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
    assert (
        'href="https://useapi.net/docs/api-google-flow-v1/'
        'post-google-flow-accounts-captcha-providers"' in html
    )
    for line in ("XAI_API_KEY=", "USEAPI_TOKEN=", "DIONY_FLOW_EMAIL=", "DIONY_FLOW_PLAN="):
        assert line in html, line
    assert "Google AI plan" in html and "monthly credits" in html
    assert "300 included captcha solves" in html
    assert "captcha solver" in html
    # the keys read by camera: each house explains its own keys and carries its own lines
    grok = html.index('data-house="grok"')
    flow = html.index('data-house="google-flow"')
    assert grok < html.index("XAI_API_KEY=") < flow
    assert flow < html.index("USEAPI_TOKEN=") < html.index("GEMINI_API_KEY=")
    assert html.count("<h4>Grok Imagine</h4>") == 1 and html.count("<h4>Google Flow</h4>") == 1
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


def test_the_opening_is_browser_first_and_has_no_desktop_setup_copy() -> None:
    html = _client().get("/").text
    assert 'href="http://127.0.0.1:8790" id="director-link"' in html
    assert 'href="http://127.0.0.1:8788" id="producer-link"' in html
    assert "start Diony and open Studio in your browser" in html
    for retired in (
        'id="platform-seg"',
        'data-platform="mac"',
        'data-platform="windows"',
        'id="ffmpeg-line"',
        "Diony.app",
        "Open Diony AI.command",
        "Open Diony AI.bat",
        ".diony.local",
        "User Account Control",
        'fetch("/api/opening/platform"',
    ):
        assert retired not in html, retired
