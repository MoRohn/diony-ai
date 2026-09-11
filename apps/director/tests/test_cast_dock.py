"""Blueprint 33 §3 — the cast dock: every landed face approved in one press;
a sheet still drawing is named and left for the next press."""

from __future__ import annotations

import importlib
from pathlib import Path

from fastapi.testclient import TestClient

from diony_director import app as app_module
from diony_director import session

IDEA = "a getaway driver decides to leave town from a neon diner"


def _client() -> TestClient:
    importlib.reload(app_module)
    return TestClient(app_module.app, raise_server_exceptions=False)


def _locked(client: TestClient) -> str:
    sid = client.post("/api/director/sessions").json()["session_id"]
    client.post(f"/api/director/sessions/{sid}/produce", json={"idea": IDEA})
    assert client.post(f"/api/director/sessions/{sid}/lock_script").status_code == 200
    return sid


def test_approve_all_freezes_every_landed_face_and_names_the_rest() -> None:
    client = _client()
    sid = _locked(client)
    package = session.package_for(sid)
    assert package is not None and len(package.characters) >= 2
    landed, drawing = package.characters[0], package.characters[1]
    landed.hero_stills = {"front": "/vault/front.png"}
    res = client.post(f"/api/director/sessions/{sid}/bible/approve_all")
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["approved"] == [landed.name]
    assert drawing.name in body["waiting"]
    assert "Still drawing" in body["reply"]
    assert landed.face_approved is True and drawing.face_approved is False
    # the board reads the decision back for the dock
    board = client.get(f"/api/director/sessions/{sid}/board").json()
    by_id = {c["char_id"]: c for c in board["characters"]}
    assert by_id[landed.char_id]["face_approved"] is True
    assert by_id[drawing.char_id]["face_approved"] is False
    session.reset()


def test_approve_all_refuses_when_no_sheet_has_landed() -> None:
    client = _client()
    sid = _locked(client)
    res = client.post(f"/api/director/sessions/{sid}/bible/approve_all")
    assert res.status_code == 409
    assert "nothing to approve" in res.json()["detail"]
    session.reset()


def test_approve_all_is_idempotent_once_every_face_is_frozen() -> None:
    client = _client()
    sid = _locked(client)
    package = session.package_for(sid)
    for char in package.characters:
        char.hero_stills = {"front": f"/vault/{char.char_id}.png"}
    first = client.post(f"/api/director/sessions/{sid}/bible/approve_all").json()
    assert len(first["approved"]) == len(package.characters) and not first["waiting"]
    again = client.post(f"/api/director/sessions/{sid}/bible/approve_all")
    assert again.status_code == 200
    assert again.json()["approved"] == [] and "already approved" in again.json()["reply"]
    session.reset()


def test_approve_all_needs_a_locked_package() -> None:
    client = _client()
    sid = client.post("/api/director/sessions").json()["session_id"]
    assert client.post(f"/api/director/sessions/{sid}/bible/approve_all").status_code == 404
    session.reset()


def test_the_board_reads_each_sheets_state_from_its_ops() -> None:
    client = _client()
    sid = _locked(client)
    board = client.get(f"/api/director/sessions/{sid}/board").json()
    # locked, nothing rolled: every sheet waits for Run — never "drawing"
    assert {c["sheet_state"] for c in board["characters"]} == {"waiting"}
    package = session.package_for(sid)
    char = package.characters[0]
    char.hero_stills = {"front": "/vault/front.png"}
    for op in session.ops_for(sid):
        bible = op.artifact_refs.get("bible") or {}
        if bible.get("char_id") == char.char_id:
            op.status = "completed"
    board = client.get(f"/api/director/sessions/{sid}/board").json()
    states = {c["char_id"]: c["sheet_state"] for c in board["characters"]}
    assert states[char.char_id] == "landed"
    assert states[package.characters[1].char_id] == "waiting"
    # Blueprint 33: every clip says how many beats its take packs
    assert all(c["beats"] >= 1 for c in board["clips"])
    assert any(c["beats"] > 1 for c in board["clips"]), (
        "the template packs a line with its reaction"
    )
    session.reset()


def test_cast_and_bibles_offer_a_character_anchor_upload() -> None:
    static = Path(app_module.__file__).resolve().parent / "static"
    js = (static / "app.js").read_text()
    css = (static / "app.css").read_text()
    assert "function characterAnchorPicker" in js
    assert "function bibleMediaUrl" in js
    assert "img.src = bibleMediaUrl(c, slot)" in js
    assert "Use your image as anchor" in js
    assert "/bible/anchor/${encodeURIComponent(c.char_id)}" in js
    assert "anchor · yours" in js
    assert ".anchorbtn" in css


def test_bibles_can_link_an_existing_google_flow_character_without_a_prompt_dialog() -> None:
    static = Path(app_module.__file__).resolve().parent / "static"
    js = (static / "app.js").read_text()
    css = (static / "app.css").read_text()
    assert "function existingFlowCharacterPicker" in js
    assert "Use existing Flow character" in js
    assert "/api/director/flow/characters" in js
    assert "Choose from Diony's Flow library" in js
    assert "/bible/flow_character/${encodeURIComponent(c.char_id)}" in js
    assert "Or paste a Flow character URL, entity ID, or API reference" in js
    assert "window.prompt" not in js
    assert ".flowcharform" in css
    assert ".flowcharhint" in css


def test_linked_flow_characters_are_visible_in_the_cast_dock() -> None:
    static = Path(app_module.__file__).resolve().parent / "static"
    js = (static / "app.js").read_text()
    css = (static / "app.css").read_text()
    assert 'c.flow_registered ? "flow"' in js
    assert "/bible/flow-preview/${encodeURIComponent(c.char_id)}" in js
    assert 'mk("span", "flowbadge", "FLOW ✓")' in js
    assert "Flow linked · local review" in js
    assert ".flowbadge" in css and ".castthumb.flow" in css


def test_run_status_tracks_bibles_before_clips_and_reconnects_after_refresh() -> None:
    static = Path(app_module.__file__).resolve().parent / "static"
    js = (static / "app.js").read_text()
    assert 'preparing: { tag: "Preparing", title: "Preparing cast and locations" }' in js
    assert "function batchTrackerSpec" in js
    assert 'kind: "bibles"' in js
    assert "`${visible.printed}/${visible.total} BIBLE assets ready`" in js
    assert "refreshProduction(); // uploaded anchors" in js
    assert "resumeFromPlan(counts.run_plan, counts)" in js
