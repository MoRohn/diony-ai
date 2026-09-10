"""Key loading from a local file and the live provider readiness check."""

from diony_director import config, live_check
from diony_director.providers import grok


def test_keys_file_populates_missing_env(tmp_path, monkeypatch) -> None:
    keys = tmp_path / "keys.env"
    keys.write_text(
        "# Diony provider keys\n"
        "XAI_API_KEY=real-xai\n"
        'USEAPI_TOKEN="real-useapi"\n'
        "DIONY_FLOW_EMAIL=studio@example.com\n"
        "DIONY_FLOW_PLAN=ultra-99\n"
    )
    monkeypatch.setenv("DIONY_KEYS_FILE", str(keys))
    monkeypatch.delenv("XAI_API_KEY", raising=False)
    monkeypatch.delenv("USEAPI_TOKEN", raising=False)
    monkeypatch.delenv("DIONY_FLOW_EMAIL", raising=False)
    loaded = config.load_keys_env()
    assert loaded["XAI_API_KEY"] == "real-xai"
    import os

    assert os.environ["USEAPI_TOKEN"] == "real-useapi"
    assert os.environ["DIONY_FLOW_EMAIL"] == "studio@example.com"


def test_keys_file_never_overrides_explicit_env(tmp_path, monkeypatch) -> None:
    keys = tmp_path / "keys.env"
    keys.write_text("XAI_API_KEY=from-file\n")
    monkeypatch.setenv("DIONY_KEYS_FILE", str(keys))
    monkeypatch.setenv("XAI_API_KEY", "from-env")
    config.load_keys_env()
    import os

    assert os.environ["XAI_API_KEY"] == "from-env"


def test_missing_keys_file_is_fine(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("DIONY_KEYS_FILE", str(tmp_path / "absent.env"))
    assert config.load_keys_env() == {}


def test_offline_report_covers_keys_engine_and_ratecards() -> None:
    report = live_check.readiness_report()
    assert report["engine"] == "cloud"
    assert report["keys"]["grok"] is True  # conftest sets test keys
    assert report["keys"]["google-flow"] is True
    assert report["rate_cards"]["grok"]["fetched_at"]
    assert report["rate_cards"]["google-flow"]["stale"] in {True, False}
    assert report["flow_plan"] == "pro"


class _Response:
    def __init__(self, body, status_code=200):
        self._body = body
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise grok.httpx.HTTPStatusError("boom", request=None, response=None)

    def json(self):
        return self._body


def test_live_probes_report_provider_health(monkeypatch) -> None:
    monkeypatch.setattr(
        live_check.httpx,
        "get",
        lambda url, **kw: _Response(
            {"data": [{"id": "grok-imagine-video"}]}
            if "x.ai" in url
            else [{"email": "studio@example.com", "credits": 940}]
        ),
    )
    report = live_check.live_probe()
    assert report["grok"]["ok"] is True
    assert "grok-imagine-video" in report["grok"]["detail"]
    assert report["google-flow"]["ok"] is True


def test_live_probe_reports_auth_failures_honestly(monkeypatch) -> None:
    def bad(url, **kw):
        return _Response({"error": "invalid key"}, status_code=401)

    monkeypatch.setattr(live_check.httpx, "get", bad)
    report = live_check.live_probe()
    assert report["grok"]["ok"] is False
    assert report["google-flow"]["ok"] is False


def test_live_probe_parses_the_dict_keyed_account_shape(monkeypatch) -> None:
    """Verified live 2026-09-02: /accounts returns a dict keyed by email, and
    a signed-out account carries an "error" field — the probe must call that
    unhealthy, never "0 accounts connected" with ok=true."""
    monkeypatch.setattr(
        live_check.httpx,
        "get",
        lambda url, **kw: _Response(
            {"data": [{"id": "grok-imagine-video"}]}
            if "x.ai" in url
            else {
                "studio@example.com": {
                    "created": "2026-09-01T17:37:19Z",
                    "error": "Google has signed your account out. Reconnect at https://useapi.net/docs",
                }
            }
        ),
    )
    report = live_check.live_probe()
    assert report["google-flow"]["ok"] is False
    assert report["google-flow"]["accounts"] == 1
    assert "signed your account out" in report["google-flow"]["detail"]
