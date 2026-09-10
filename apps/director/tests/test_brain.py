"""The brain follows the camera: Gemini on Google Flow, Grok on Grok Imagine,
a named brain when its key exists, and never a guessed key."""

from __future__ import annotations

from fastapi.testclient import TestClient

from diony_contracts.cluster import write_engine_choice
from diony_director import app as app_module
from diony_director import brain, screenwriter


def _auto(monkeypatch):
    # the conftest turns the brain OFF with an explicit empty URL; these tests
    # exercise the resolution the URL overrides
    monkeypatch.delenv("DIONY_DIRECTOR_BRAIN_URL", raising=False)
    monkeypatch.delenv("DIONY_DIRECTOR_BRAIN_KEY", raising=False)
    monkeypatch.delenv("DIONY_DIRECTOR_BRAIN_MODEL", raising=False)


def test_the_grok_camera_thinks_on_grok_and_the_flow_camera_on_gemini(monkeypatch) -> None:
    _auto(monkeypatch)
    monkeypatch.setenv("GEMINI_API_KEY", "test-gemini-key")
    write_engine_choice("grok")
    target = brain.resolve()
    assert target.provider == "grok" and target.model == "grok-4.6"
    assert target.endpoint == "https://api.x.ai/v1/chat/completions"
    assert screenwriter._brain_headers() == {"Authorization": "Bearer test-xai-key"}

    write_engine_choice("google-flow")
    target = brain.resolve()
    assert target.provider == "gemini" and target.model == "gemini-2.5-pro"
    assert target.url == "https://generativelanguage.googleapis.com/v1beta/openai"
    assert target.endpoint.endswith("/v1beta/openai/chat/completions"), "no /v1 on Gemini's shape"
    assert screenwriter.chat_completions_url() == target.endpoint
    assert screenwriter._brain_headers() == {"Authorization": "Bearer test-gemini-key"}
    assert screenwriter.brain_url() == target.url and screenwriter.brain_model() == "gemini-2.5-pro"


def test_a_missing_house_key_borrows_the_other_and_says_so(monkeypatch) -> None:
    _auto(monkeypatch)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    write_engine_choice("google-flow")
    target = brain.resolve()
    assert target.provider == "grok"
    assert "GEMINI_API_KEY" in target.reason

    monkeypatch.setenv("GEMINI_API_KEY", "g")
    monkeypatch.delenv("XAI_API_KEY", raising=False)
    write_engine_choice("grok")
    target = brain.resolve()
    assert target.provider == "gemini" and "only brain key" in target.reason

    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    assert brain.resolve().configured is False
    assert screenwriter.brain_url() == ""


def test_a_named_brain_needs_its_key_and_the_explicit_url_still_wins(monkeypatch) -> None:
    _auto(monkeypatch)
    write_engine_choice("google-flow")
    brain.write_brain_choice("grok")
    assert brain.resolve().provider == "grok" and brain.resolve().reason == "chosen"
    brain.write_brain_choice("gemini")
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    target = brain.resolve()
    assert not target.configured and "key is missing" in target.reason
    # an explicit endpoint outranks every choice, Gemini-shaped or not
    monkeypatch.setenv(
        "DIONY_DIRECTOR_BRAIN_URL", "https://generativelanguage.googleapis.com/v1beta/openai"
    )
    monkeypatch.setenv("GEMINI_API_KEY", "g")
    target = brain.resolve()
    assert target.provider == "custom" and target.model == "gemini-2.5-pro"
    assert target.endpoint.endswith("/openai/chat/completions") and target.key == "g"
    monkeypatch.setenv("DIONY_DIRECTOR_BRAIN_URL", "https://api.x.ai")
    assert brain.resolve().endpoint == "https://api.x.ai/v1/chat/completions"
    monkeypatch.setenv("DIONY_DIRECTOR_BRAIN_URL", "")
    assert brain.resolve().configured is False


def test_the_brain_endpoint_reports_and_sets_the_choice(monkeypatch) -> None:
    _auto(monkeypatch)
    monkeypatch.setenv("GEMINI_API_KEY", "g")
    write_engine_choice("google-flow")
    client = TestClient(app_module.app)
    state = client.get("/api/director/brain").json()
    assert state["provider"] == "gemini" and state["choice"] == "auto"
    assert state["available"] == {"grok": True, "gemini": True}
    assert client.get("/api/director/status").json()["brain"]["provider"] == "gemini"
    chosen = client.post("/api/director/brain", json={"brain": "grok"}).json()
    assert chosen["provider"] == "grok" and chosen["choice"] == "grok"
    assert client.post("/api/director/brain", json={"brain": "claude"}).status_code == 422
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    refused = client.post("/api/director/brain", json={"brain": "gemini"})
    assert refused.status_code == 409 and "GEMINI_API_KEY" in refused.text
    assert client.post("/api/director/brain", json={"brain": "auto"}).json()["provider"] == "grok"


def test_the_console_carries_the_brain_pill_and_the_houses() -> None:
    from pathlib import Path

    static = Path(app_module.__file__).resolve().parent / "static"
    html = (static / "index.html").read_text()
    js = (static / "app.js").read_text()
    assert 'id="brain-pill"' in html and '<select id="brain">' in html
    assert "function paintBrain" in js and "function pushBrain" in js
    assert '"/api/director/brain"' in js
    assert '$("brain-pill").hidden = entries.length < 2' in js
    assert "Auto (${house} casts)" in js
