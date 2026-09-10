"""Blueprint 33 §5 — a script locked and priced but never rolled is unfinished
business: the room resumes it with its priced set instead of starting fresh."""

from __future__ import annotations

import importlib

from fastapi.testclient import TestClient

from diony_director import app as app_module
from diony_director import session

IDEA = "a getaway driver decides to leave town from a neon diner"


def _client() -> TestClient:
    importlib.reload(app_module)
    return TestClient(app_module.app, raise_server_exceptions=False)


def _locked(client: TestClient) -> tuple[str, dict]:
    sid = client.post("/api/director/sessions").json()["session_id"]
    client.post(f"/api/director/sessions/{sid}/produce", json={"idea": IDEA})
    locked = client.post(f"/api/director/sessions/{sid}/lock_script").json()
    return sid, locked


def test_a_priced_unrun_script_resumes_and_a_run_outranks_it() -> None:
    client = _client()
    sid, locked = _locked(client)
    rows = client.get("/api/director/resumable").json()["sessions"]
    row = next(r for r in rows if r["session_id"] == sid)
    assert row["stage"] == "priced"
    assert row["proposed"] == len(locked["ops"]) + len(locked["bible_ops"])
    assert row["completed"] == 0 and row["failed"] == 0
    # a second room with a run in progress comes first, however new the priced one is
    sid2, locked2 = _locked(client)
    session.get_op(locked2["ops"][0]["id"]).status = "submitted"
    session.persist(sid2)
    rows = client.get("/api/director/resumable").json()["sessions"]
    assert [r["session_id"] for r in rows[:2]] == [sid2, sid]
    assert rows[0]["stage"] == "running"
    session.reset()


def test_a_priced_script_that_printed_anything_is_no_longer_priced() -> None:
    client = _client()
    sid, locked = _locked(client)
    session.get_op(locked["ops"][0]["id"]).status = "completed"
    session.persist(sid)
    rows = client.get("/api/director/resumable").json()["sessions"]
    assert all(r["session_id"] != sid for r in rows), (
        "a partly printed set with nothing failed or rolling is settled"
    )
    session.reset()


def test_a_dismissed_priced_room_stays_dismissed() -> None:
    client = _client()
    sid, _locked_set = _locked(client)
    assert client.post(f"/api/director/sessions/{sid}/dismiss").status_code == 200
    rows = client.get("/api/director/resumable").json()["sessions"]
    assert all(r["session_id"] != sid for r in rows)
    session.reset()


def test_the_lock_endpoint_hands_the_set_back_the_way_lock_script_did() -> None:
    client = _client()
    sid, locked = _locked(client)
    res = client.get(f"/api/director/sessions/{sid}/lock")
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["locked"] is True
    assert [o["id"] for o in body["ops"]] == [o["id"] for o in locked["ops"]], (
        "clip ops, in shot order"
    )
    assert {b["id"] for b in body["bible_ops"]} == {b["id"] for b in locked["bible_ops"]}
    assert body["clips"] == len(locked["ops"]) and body["scenes"] >= 1
    assert body["planned_s"] == sum(o["duration_s"] for o in locked["ops"])
    assert body["estimate_usd_micros"] > 0
    # the draft the room re-renders is read-only: the script is locked
    draft = client.get(f"/api/director/sessions/{sid}/draft").json()
    assert draft["script"]["locked"] is True
    session.reset()


def test_the_lock_endpoint_needs_a_locked_package() -> None:
    client = _client()
    sid = client.post("/api/director/sessions").json()["session_id"]
    assert client.get(f"/api/director/sessions/{sid}/lock").status_code == 404
    session.reset()
