"""Keep the Google Flow lane connected without a manual cookie copy.

useapi manages the Google session by replaying a captured cookie table, and
Google periodically signs that session out (see [[google-flow-live-contract]]).
Re-linking by hand means: clean browser, log in, open devtools, copy the
cookie table, paste it into a form. This module collapses that to one call —
as long as a browser ON THIS MACHINE still holds a live, authenticated
Google session, it re-captures the cookie table over CDP and re-pushes it to
useapi.

What it CANNOT do: manufacture a login. If the browser session is itself
signed out, Google bounces the useapi OAuth refresh back to the sign-in page
("OAuth stuck on login page") and a human must sign in once at
``labs.google/fx/tools/flow`` — after that, this carries the session forward
untouched. The verify step reports the account's real health, never a green
light over a signed-out account.

Two pure, tested cores (cookie-table formatting and the useapi push) plus a
thin ``agent-browser`` subprocess wrapper that reads the live cookie jar.
"""

from __future__ import annotations

import json
import subprocess
from datetime import datetime, timezone

import httpx

from diony_director import config, live_check
from diony_director.providers import flow

PUSH_TIMEOUT_S = 120.0
# The cookie jar we replay to useapi is the Google auth session — only
# google.com hosts belong in it; anything else is noise that risks a 400.
_GOOGLE_SUFFIX = "google.com"


def _iso(expires: float | int | None) -> str:
    """Format a CDP epoch-seconds expiry as the ISO-8601 Z string the useapi
    parser accepts (verified live). A session cookie (no/negative expiry)
    carries an empty Expires cell, exactly as the devtools table exports it."""
    if not expires or expires < 0:
        return ""
    try:
        return datetime.fromtimestamp(float(expires), tz=timezone.utc).isoformat().replace(
            "+00:00", "Z"
        )
    except (OverflowError, OSError, ValueError):
        return ""


def cookie_table_from_records(cookies: list[dict]) -> str:
    """Turn CDP cookie records into the tab-delimited Name/Value/Domain/Path/
    Expires table useapi's ``POST /accounts`` parses. Google hosts only, in a
    stable order, empty values dropped — a cookie with no value is not a
    credential and only muddies the parse."""
    rows: list[str] = []
    seen: set[tuple[str, str]] = set()
    for c in cookies:
        if not isinstance(c, dict):
            continue
        name = str(c.get("name", "")).strip()
        value = str(c.get("value", "")).strip()
        domain = str(c.get("domain", "")).strip()
        if not name or not value or not domain:
            continue
        host = domain.lstrip(".").lower()
        if not (host == _GOOGLE_SUFFIX or host.endswith("." + _GOOGLE_SUFFIX)):
            continue
        key = (name, domain)
        if key in seen:  # a name can repeat across domains; not within one
            continue
        seen.add(key)
        path = str(c.get("path", "/")).strip() or "/"
        rows.append("\t".join([name, value, domain, path, _iso(c.get("expires"))]))
    rows.sort()
    return "\n".join(rows)


def push_cookies(cookie_table: str) -> dict:
    """Push a cookie table to useapi's account endpoint. Returns
    ``{"ok", "status", "detail"}`` — a 200/201 with a healthy account is the
    only success; a parsed-but-signed-out account is reported, not hidden."""
    if not cookie_table.strip():
        return {"ok": False, "status": 0, "detail": "no Google cookies were captured"}
    config.load_keys_env()
    if not flow.configured():
        return {"ok": False, "status": 0, "detail": "USEAPI_TOKEN not set"}
    try:
        response = flow.paced(
            lambda: httpx.post(
                f"{flow.api_base()}/accounts",
                headers={
                    "Authorization": f"Bearer {flow.api_token()}",
                    "Content-Type": "application/json",
                },
                json={"cookies": cookie_table},
                timeout=PUSH_TIMEOUT_S,
            ),
            what="account push",
        )
    except Exception as err:  # noqa: BLE001 - a push reports, never raises
        return {"ok": False, "status": 0, "detail": f"push failed: {type(err).__name__}: {err}"}
    detail = ""
    try:
        body = response.json()
        if isinstance(body, dict):
            detail = str(body.get("error") or body.get("message") or "")
    except Exception:  # noqa: BLE001
        detail = response.text[:200]
    if response.status_code not in (200, 201):
        return {"ok": False, "status": response.status_code, "detail": detail or "rejected"}
    # A 200 only means the cookies parsed — verify the account can actually
    # roll before calling it connected (the live_check dict-shape health read).
    probe = live_check.live_probe().get("google-flow", {})
    return {
        "ok": bool(probe.get("ok")),
        "status": response.status_code,
        "detail": probe.get("detail", "pushed"),
    }


def capture_google_cookies(*, cdp: str = "", profile: str = "") -> list[dict]:
    """Read the live Google cookie jar from a browser on this machine via
    ``agent-browser``. Point it at the user's running browser with ``cdp``
    (a debug port or ws URL) or ``profile`` (a Chrome profile whose login
    state survives); with neither it drives its own browser, which only
    carries a session a prior login left in that context. Navigates to a
    google.com page first so the jar for that origin is populated."""

    def _run(args: list[str]) -> subprocess.CompletedProcess:
        head = ["agent-browser"]
        if cdp:
            head += ["--cdp", cdp]
        if profile:
            head += ["--profile", profile]
        return subprocess.run(head + args, capture_output=True, text=True, timeout=90)

    _run(["open", "https://myaccount.google.com/"])
    got = _run(["cookies", "get", "--json"])
    if got.returncode != 0:
        raise RuntimeError(f"agent-browser cookie read failed: {got.stderr.strip()[:200]}")
    try:
        payload = json.loads(got.stdout)
    except json.JSONDecodeError as err:
        raise RuntimeError(f"agent-browser returned no JSON: {err}") from err
    data = payload.get("data") if isinstance(payload, dict) else None
    cookies = (data or {}).get("cookies") if isinstance(data, dict) else None
    return cookies if isinstance(cookies, list) else []


def reconnect_from_browser(*, cdp: str = "", profile: str = "") -> dict:
    """Capture the live Google session and re-push it to useapi in one shot.
    Returns the push result, with a ``captured`` count for the report."""
    cookies = capture_google_cookies(cdp=cdp, profile=profile)
    table = cookie_table_from_records(cookies)
    result = push_cookies(table)
    result["captured"] = table.count("\n") + 1 if table else 0
    return result


def ensure_connected(*, cdp: str = "", profile: str = "") -> dict:
    """Reconnect ONLY if the account is currently unhealthy — the auto-heal
    entry point. A healthy account is left untouched (no needless cookie
    replay); a signed-out one is re-pushed from the live browser session."""
    probe = live_check.live_probe().get("google-flow", {})
    if probe.get("ok"):
        return {"ok": True, "status": 200, "detail": "already connected", "reconnected": False}
    result = reconnect_from_browser(cdp=cdp, profile=profile)
    result["reconnected"] = True
    return result
