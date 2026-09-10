"""Flow auto-reconnect: cookie-table formatting, the useapi push, and the
health-gated auto-heal — the parts that must be right without a live browser.
"""

from diony_director import flow_reconnect

# --- cookie-table formatting -------------------------------------------------


def _cookie(name, value, domain, path="/", expires=1893456000):
    return {"name": name, "value": value, "domain": domain, "path": path, "expires": expires}


def test_only_google_cookies_with_values_reach_the_table() -> None:
    records = [
        _cookie("SID", "g.abc", ".google.com"),
        _cookie("LSID", "s.you", "accounts.google.com"),
        _cookie("tracker", "x", ".doubleclick.net"),  # not a google.com host
        _cookie("EMPTY", "", ".google.com"),  # no value → not a credential
        {"name": "MALFORMED"},  # missing fields → skipped, never crashes
    ]
    table = flow_reconnect.cookie_table_from_records(records)
    lines = table.splitlines()
    assert len(lines) == 2, "only the two valued google.com cookies survive"
    names = {ln.split("\t")[0] for ln in lines}
    assert names == {"SID", "LSID"}
    # five tab-delimited columns: Name, Value, Domain, Path, Expires
    assert all(len(ln.split("\t")) == 5 for ln in lines)


def test_session_cookie_expiry_is_an_empty_cell() -> None:
    table = flow_reconnect.cookie_table_from_records(
        [_cookie("SID", "g.abc", ".google.com", expires=-1)]
    )
    cols = table.split("\t")
    assert cols[-1] == "", "a session cookie carries no Expires, like the devtools export"


def test_epoch_expiry_becomes_an_iso_z_string() -> None:
    table = flow_reconnect.cookie_table_from_records(
        [_cookie("SID", "g.abc", ".google.com", expires=1893456000)]
    )
    assert table.split("\t")[-1].endswith("Z") and "2030-01-01" in table


def test_a_name_repeats_across_domains_but_not_within_one() -> None:
    records = [
        _cookie("SAPISID", "v1", ".google.com"),
        _cookie("SAPISID", "v1", ".google.com"),  # exact dupe → one row
        _cookie("SAPISID", "v2", "accounts.google.com"),  # other domain → kept
    ]
    lines = flow_reconnect.cookie_table_from_records(records).splitlines()
    assert len(lines) == 2


# --- the useapi push ---------------------------------------------------------


class _Resp:
    def __init__(self, status, body):
        self.status_code = status
        self._body = body
        self.text = str(body)

    def json(self):
        return self._body


def test_push_reports_a_signed_out_account_as_not_ok(monkeypatch) -> None:
    """A 200 that only means 'cookies parsed' must not read as connected —
    the health probe decides, and a signed-out account is not ok."""
    monkeypatch.setattr(flow_reconnect.flow, "configured", lambda: True)
    monkeypatch.setattr(flow_reconnect.flow, "api_token", lambda: "t")
    monkeypatch.setattr(flow_reconnect.flow, "api_base", lambda: "https://api.example/v1")
    monkeypatch.setattr(flow_reconnect.httpx, "post", lambda *a, **k: _Resp(200, {}))
    monkeypatch.setattr(
        flow_reconnect.live_check,
        "live_probe",
        lambda: {"google-flow": {"ok": False, "detail": "UNHEALTHY — signed out"}},
    )
    result = flow_reconnect.push_cookies("SID\tg.abc\t.google.com\t/\t")
    assert result["ok"] is False and "signed out" in result["detail"]


def test_push_ok_when_the_account_verifies_healthy(monkeypatch) -> None:
    monkeypatch.setattr(flow_reconnect.flow, "configured", lambda: True)
    monkeypatch.setattr(flow_reconnect.flow, "api_token", lambda: "t")
    monkeypatch.setattr(flow_reconnect.flow, "api_base", lambda: "https://api.example/v1")
    monkeypatch.setattr(flow_reconnect.httpx, "post", lambda *a, **k: _Resp(201, {}))
    monkeypatch.setattr(
        flow_reconnect.live_check,
        "live_probe",
        lambda: {"google-flow": {"ok": True, "detail": "auth OK; 1 account(s) connected"}},
    )
    assert flow_reconnect.push_cookies("SID\tg.abc\t.google.com\t/\t")["ok"] is True


def test_push_refuses_an_empty_table_without_a_network_call(monkeypatch) -> None:
    def _boom(*a, **k):
        raise AssertionError("no cookies must never hit the network")

    monkeypatch.setattr(flow_reconnect.httpx, "post", _boom)
    result = flow_reconnect.push_cookies("   ")
    assert result["ok"] is False and "no Google cookies" in result["detail"]


def test_push_surfaces_a_rejection_detail(monkeypatch) -> None:
    monkeypatch.setattr(flow_reconnect.flow, "configured", lambda: True)
    monkeypatch.setattr(flow_reconnect.flow, "api_token", lambda: "t")
    monkeypatch.setattr(flow_reconnect.flow, "api_base", lambda: "https://api.example/v1")
    monkeypatch.setattr(
        flow_reconnect.httpx,
        "post",
        lambda *a, **k: _Resp(400, {"error": "No cookies could be parsed from input"}),
    )
    result = flow_reconnect.push_cookies("garbage")
    assert result["ok"] is False and result["status"] == 400 and "parsed" in result["detail"]


# --- the health-gated auto-heal ---------------------------------------------


def test_ensure_connected_leaves_a_healthy_account_untouched(monkeypatch) -> None:
    monkeypatch.setattr(
        flow_reconnect.live_check,
        "live_probe",
        lambda: {"google-flow": {"ok": True, "detail": "connected"}},
    )

    def _no_capture(**k):
        raise AssertionError("a healthy account must not be re-captured")

    monkeypatch.setattr(flow_reconnect, "reconnect_from_browser", _no_capture)
    result = flow_reconnect.ensure_connected()
    assert result == {
        "ok": True,
        "status": 200,
        "detail": "already connected",
        "reconnected": False,
    }


def test_ensure_connected_reconnects_only_when_unhealthy(monkeypatch) -> None:
    monkeypatch.setattr(
        flow_reconnect.live_check,
        "live_probe",
        lambda: {"google-flow": {"ok": False, "detail": "signed out"}},
    )
    called = {}

    def _fake_reconnect(**k):
        called["yes"] = True
        return {"ok": True, "status": 201, "detail": "reconnected", "captured": 21}

    monkeypatch.setattr(flow_reconnect, "reconnect_from_browser", _fake_reconnect)
    result = flow_reconnect.ensure_connected()
    assert called.get("yes") and result["reconnected"] is True and result["ok"] is True
