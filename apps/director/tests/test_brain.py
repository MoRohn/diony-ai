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
    assert target.provider == "gemini" and target.model == "gemini-3.1-pro-preview"
    assert target.url == "https://generativelanguage.googleapis.com/v1beta/openai"
    assert target.endpoint.endswith("/v1beta/openai/chat/completions"), "no /v1 on Gemini's shape"
    assert screenwriter.chat_completions_url() == target.endpoint
    assert screenwriter._brain_headers() == {"Authorization": "Bearer test-gemini-key"}
    assert (
        screenwriter.brain_url() == target.url
        and screenwriter.brain_model() == "gemini-3.1-pro-preview"
    )


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
    assert target.provider == "custom" and target.model == "gemini-3.1-pro-preview"
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
    # the Voice pill's Auto option is plain "Auto": no house suffix
    assert "casts)" not in js and 'cat.pinned ? `Auto (site pin: ${cat.pinned})` : "Auto"' in js


class _Rejection:
    def __init__(self, status_code: int, text: str) -> None:
        self.status_code = status_code
        self.text = text


_GOOGLE_RETIREMENT = (
    '[{ "error": { "code": 404, "message": "This model models/gemini-2.5-pro is no '
    "longer available to new users. Please update your code to use "
    'models/gemini-3.1-pro-preview for the latest features." } }]'
)


def test_a_retirement_rejection_names_the_successor_and_the_brain_follows_it(monkeypatch) -> None:
    _auto(monkeypatch)
    monkeypatch.setenv("GEMINI_API_KEY", "g")
    monkeypatch.delenv("DIONY_GEMINI_BRAIN_MODEL", raising=False)
    write_engine_choice("google-flow")
    brain.forget_learned_models()
    try:
        assert brain.retirement_pointer(_GOOGLE_RETIREMENT) == "gemini-3.1-pro-preview"
        assert brain.retirement_pointer('{"error": {"code": 404, "message": "not found"}}') == ""
        # Google's message names the model the desk already runs — nothing to follow
        assert brain.follow_retirement("gemini", 404, _GOOGLE_RETIREMENT) == ""
        assert brain.resolve().model == "gemini-3.1-pro-preview"

        newer = _GOOGLE_RETIREMENT.replace("models/gemini-3.1-pro-preview", "models/gemini-4-pro")
        err = screenwriter.brain_rejection(_Rejection(404, newer))
        assert "brain rejected (404)" in str(err) and "switched to gemini-4-pro" in str(err)
        target = brain.resolve()
        assert target.model == "gemini-4-pro", "the next attempt runs on the named successor"
        assert "named in its place" in target.reason
        assert screenwriter.brain_model() == "gemini-4-pro"
        assert brain.status()["models"]["gemini"] == "gemini-4-pro"
        # a 5xx or a rejection without a pointer changes nothing
        screenwriter.brain_rejection(_Rejection(503, "overloaded"))
        screenwriter.brain_rejection(_Rejection(400, "bad request"))
        assert brain.resolve().model == "gemini-4-pro"
        # an explicit model in the environment is never overridden
        monkeypatch.setenv("DIONY_GEMINI_BRAIN_MODEL", "gemini-pinned")
        assert brain.resolve().model == "gemini-pinned"
    finally:
        brain.forget_learned_models()
    assert brain.resolve().model == "gemini-pinned"
    monkeypatch.delenv("DIONY_GEMINI_BRAIN_MODEL", raising=False)
    assert brain.resolve().model == "gemini-3.1-pro-preview"
