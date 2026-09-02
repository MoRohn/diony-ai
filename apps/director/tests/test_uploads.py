"""Uploaded reference stills ride the initial shot.

One-shot: the first upload becomes the opening frame (I2V start image), the
rest ride as reference images. Draft Script: uploads anchor clip one as
reference images for character/scene consistency — never a forced first
frame — and the brain is told to keep the piece consistent with them. Locks
recomputed at submit time must not lose the uploads.
"""

import base64

from fastapi.testclient import TestClient

from diony_director import session
from diony_director.app import _late_bind_locks, app

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 24
JPG = b"\xff\xd8\xff\xe0" + b"\x00" * 24


def _upload(sid: str, name: str, data: bytes = PNG) -> dict:
    return session.add_upload(sid, name=name, data=data)


def test_upload_endpoint_stores_and_lists() -> None:
    client = TestClient(app)
    sid = client.post("/api/director/sessions").json()["session_id"]
    body = client.post(
        f"/api/director/sessions/{sid}/uploads",
        json={"name": "hero.png", "data_base64": base64.b64encode(PNG).decode()},
    ).json()
    assert body["upload"]["name"] == "hero.png"
    assert body["upload"]["bytes"] == len(PNG)
    listed = client.get(f"/api/director/sessions/{sid}/uploads").json()["uploads"]
    assert [u["id"] for u in listed] == [body["upload"]["id"]]


def test_upload_rejects_non_image_bytes() -> None:
    client = TestClient(app)
    sid = client.post("/api/director/sessions").json()["session_id"]
    res = client.post(
        f"/api/director/sessions/{sid}/uploads",
        json={"name": "notes.txt", "data_base64": base64.b64encode(b"hello").decode()},
    )
    assert res.status_code == 422
    assert "PNG" in res.json()["detail"]


def test_upload_rejects_bad_base64() -> None:
    client = TestClient(app)
    sid = client.post("/api/director/sessions").json()["session_id"]
    res = client.post(
        f"/api/director/sessions/{sid}/uploads",
        json={"name": "x.png", "data_base64": "@@not-base64@@"},
    )
    assert res.status_code == 422


def test_oversize_upload_is_refused(monkeypatch) -> None:
    monkeypatch.setattr(session, "MAX_UPLOAD_BYTES", 16)
    client = TestClient(app)
    sid = client.post("/api/director/sessions").json()["session_id"]
    res = client.post(
        f"/api/director/sessions/{sid}/uploads",
        json={"name": "big.png", "data_base64": base64.b64encode(PNG).decode()},
    )
    assert res.status_code == 422


def test_delete_upload_removes_the_still() -> None:
    client = TestClient(app)
    sid = client.post("/api/director/sessions").json()["session_id"]
    record = _upload(sid, "gone.png")
    res = client.delete(f"/api/director/sessions/{sid}/uploads/{record['id']}")
    assert res.status_code == 200
    assert session.uploads_for(sid) == []
    assert client.delete(f"/api/director/sessions/{sid}/uploads/{record['id']}").status_code == 404


def test_one_shot_proposal_opens_on_the_uploaded_still() -> None:
    """The first still opens the frame; extras never ride the op as identity
    refs — every routed lane is frames-exclusive, and an op proposed with
    both could never submit (the 2026-09-02 clip-1 refusal). Extras still
    shape the master derivation, and the reply says exactly that."""
    sid = session.create_session()
    first = _upload(sid, "opener.png")
    _upload(sid, "mood.jpg", JPG)
    proposal = session.propose(sid, "a lighthouse at dawn", duration_s=6)
    assert proposal.op.artifact_refs["start_image"] == first["path"]
    assert "references" not in proposal.op.artifact_refs
    assert "first still opens the frame" in proposal.reply
    assert "never both" in proposal.reply


def test_proposal_without_uploads_carries_no_start_image() -> None:
    sid = session.create_session()
    proposal = session.propose(sid, "a lighthouse at dawn", duration_s=6)
    assert "start_image" not in proposal.op.artifact_refs


def test_locked_opener_references_the_uploaded_still() -> None:
    sid = session.create_session()
    record = _upload(sid, "hero.png")
    session.start_production(sid, "a getaway driver leaves a neon diner", 60)
    package, ops, _bible, reply = session.lock_script(sid)
    opener = next(op for op in ops if op.clip_id == package.clips[0].clip_id)
    assert record["path"] in opener.artifact_refs["references"]
    # consistency reference, not a forced first frame
    assert opener.artifact_refs.get("start_image") != record["path"]
    others = [op for op in ops if op.clip_id != package.clips[0].clip_id]
    for op in others:
        assert record["path"] not in (op.artifact_refs.get("references") or [])
    assert "uploaded reference image" in reply


def test_late_bind_preserves_the_uploaded_references() -> None:
    sid = session.create_session()
    record = _upload(sid, "hero.png")
    session.start_production(sid, "a getaway driver leaves a neon diner", 60)
    package, ops, _bible, _reply = session.lock_script(sid)
    opener = next(op for op in ops if op.clip_id == package.clips[0].clip_id)
    _late_bind_locks(opener)
    assert record["path"] in opener.artifact_refs["references"]
    # a plate start_image would make Grok drop the references — it must be gone
    assert opener.artifact_refs.get("start_image") != record["path"]


def test_screenwriter_idea_carries_the_consistency_note() -> None:
    sid = session.create_session()
    _upload(sid, "hero.png")
    noted = session._idea_with_uploads(sid, "a lighthouse at dawn")
    assert "a lighthouse at dawn" in noted
    assert "hero.png" in noted
    assert "consistent" in noted
    assert "does not have to open" in noted
    bare = session._idea_with_uploads(session.create_session(), "plain idea")
    assert bare == "plain idea"
