"""Sheet stills through the app, face approval, Flow character registration, identity family."""

import base64
import hashlib
import importlib
from pathlib import Path

import httpx
from fastapi.testclient import TestClient

from diony_contracts.director import CloudProvider
from diony_contracts.production import Scene  # noqa: F401 - contract import sanity
from diony_director import app as app_module
from diony_director import artifacts, compiler, review, session
from diony_director.providers import base, flow


def _client() -> TestClient:
    importlib.reload(app_module)
    return TestClient(app_module.app, raise_server_exceptions=False)


def _fake_image_lane(monkeypatch):
    counter = {"n": 0}
    submitted = []

    def fake_submit_image(prompt, *, model, references=None, aspect_ratio="landscape"):
        counter["n"] += 1
        submitted.append({"prompt": prompt, "references": list(references or [])})
        return base.CloudJobHandle(
            provider_id="google-flow", external_id=f"img-{counter['n']}", model=model, duration_s=0
        )

    def fake_poll(handle):
        return base.CloudJobStatus(
            state="done",
            result_url=f"https://flow/{handle.external_id}.png",
            metadata={"media_generation_id": f"user:{handle.external_id}"},
        )

    def fake_fetch(handle, status, dest):
        dest.parent.mkdir(parents=True, exist_ok=True)
        payload = b"png" * 2048
        dest.write_bytes(payload)
        return base.FetchedClip(
            path=dest,
            sha256=hashlib.sha256(payload).hexdigest(),
            bytes=len(payload),
            media_url=status.result_url,
        )

    monkeypatch.setattr(flow, "submit_image", fake_submit_image)
    monkeypatch.setattr(flow, "poll", fake_poll)
    monkeypatch.setattr(flow, "fetch", fake_fetch)
    return submitted


def _locked_session(client) -> str:
    sid = client.post("/api/director/sessions").json()["session_id"]
    client.post(
        f"/api/director/sessions/{sid}/produce",
        json={"idea": "a getaway driver decides to leave town from a neon diner"},
    )
    client.post(f"/api/director/sessions/{sid}/lock_script")
    return sid


def _locked_cast_session(client) -> str:
    sid = client.post("/api/director/sessions").json()["session_id"]
    client.post(f"/api/director/sessions/{sid}/cast", json={"cast": "locked"})
    client.post(
        f"/api/director/sessions/{sid}/produce",
        json={"idea": "a getaway driver decides to leave town from a neon diner"},
    )
    client.post(f"/api/director/sessions/{sid}/lock_script")
    return sid


def test_sheet_stills_run_and_land_in_the_bible(monkeypatch) -> None:
    client = _client()
    submitted = _fake_image_lane(monkeypatch)
    sid = _locked_session(client)

    body = client.post(f"/api/director/sessions/{sid}/bible/stills").json()
    assert body["ops"]
    front = next(o for o in body["ops"] if o["artifact_refs"]["bible"]["slot"] == "front")
    profile = next(
        o
        for o in body["ops"]
        if o["artifact_refs"]["bible"]
        == {"char_id": front["artifact_refs"]["bible"]["char_id"], "slot": "profile"}
    )

    client.post(f"/api/director/ops/{front['id']}/run")
    state = client.get(f"/api/director/ops/{front['id']}").json()
    assert state["op"]["status"] == "completed"
    assert "Sheet still landed" in state["narration"]

    package = session.package_for(sid)
    char = package.character(front["artifact_refs"]["bible"]["char_id"])
    assert "front" in char.hero_stills
    assert char.flow_media_ids["front"] == "user:img-1"

    client.post(f"/api/director/ops/{profile['id']}/run")
    assert "user:img-1" in submitted[-1]["references"], "later slots must chain earlier stills"


def test_face_approval_is_a_human_gate(monkeypatch) -> None:
    client = _client()
    _fake_image_lane(monkeypatch)
    sid = _locked_session(client)
    package = session.package_for(sid)
    char = package.characters[0]

    res = client.post(f"/api/director/sessions/{sid}/bible/approve/{char.char_id}")
    assert res.status_code == 409  # nothing to approve yet

    ops = client.post(f"/api/director/sessions/{sid}/bible/stills").json()["ops"]
    front = next(
        o for o in ops if o["artifact_refs"]["bible"] == {"char_id": char.char_id, "slot": "front"}
    )
    client.post(f"/api/director/ops/{front['id']}/run")
    client.get(f"/api/director/ops/{front['id']}")

    res = client.post(f"/api/director/sessions/{sid}/bible/approve/{char.char_id}")
    assert res.status_code == 200
    assert char.face_approved is True


def test_user_image_becomes_the_character_anchor_and_replans_the_board() -> None:
    client = _client()
    sid = _locked_cast_session(client)
    package = session.package_for(sid)
    char = package.characters[0]
    original_ops = [
        op
        for op in session.ops_for(sid)
        if (op.artifact_refs.get("bible") or {}).get("char_id") == char.char_id
    ]
    png = b"\x89PNG\r\n\x1a\n" + b"user-face" * 128

    res = client.post(
        f"/api/director/sessions/{sid}/bible/anchor/{char.char_id}",
        json={"name": "My Hero.png", "data_base64": base64.b64encode(png).decode()},
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["character"]["anchor_source"] == "uploaded"
    assert body["character"]["anchor_name"] == "My Hero.png"
    assert body["character"]["anchor_sha256"] == hashlib.sha256(png).hexdigest()
    assert {op["artifact_refs"]["bible"]["slot"] for op in body["ops"]} == {"board"}

    anchor = Path(char.hero_stills["front"])
    assert anchor.is_file() and anchor.read_bytes() == png
    assert char.grok_ref_pack == [str(anchor)]
    assert char.flow_media_ids == {}
    assert char.face_approved is False and char.sheet_version == 2
    assert all(
        pack["sheet_version"] == 2
        for clip in package.clips
        for pack in clip.continuity.character_packs
        if pack.get("char_id") == char.char_id
    )
    assert all(op.status == "discarded" for op in original_ops)
    assert session.uploads_for(sid) == [], "a cast anchor is not a film-wide look upload"

    new_board = session.get_op(body["ops"][0]["id"])
    new_board.provider = new_board.provider.__class__("google-flow")
    assert artifacts.references_for(package, new_board) == [str(anchor)]
    board = client.get(f"/api/director/sessions/{sid}/board").json()
    row = next(c for c in board["characters"] if c["char_id"] == char.char_id)
    assert row["anchor_source"] == "uploaded" and row["anchor_name"] == "My Hero.png"
    assert row["stills"] == ["front"]
    assert row["still_tokens"]["front"] == anchor.name


def test_character_anchor_refuses_invalid_bytes_and_started_footage() -> None:
    client = _client()
    sid = _locked_cast_session(client)
    package = session.package_for(sid)
    char = package.characters[0]
    url = f"/api/director/sessions/{sid}/bible/anchor/{char.char_id}"
    invalid = client.post(
        url,
        json={"name": "notes.txt", "data_base64": base64.b64encode(b"not an image").decode()},
    )
    assert invalid.status_code == 422

    clip_id = next(
        clip.clip_id
        for clip in package.clips
        if any(pack.get("char_id") == char.char_id for pack in clip.continuity.character_packs)
    )
    next(op for op in session.ops_for(sid) if op.clip_id == clip_id).status = "completed"
    png = b"\x89PNG\r\n\x1a\n" + b"face"
    started = client.post(
        url,
        json={"name": "new.png", "data_base64": base64.b64encode(png).decode()},
    )
    assert started.status_code == 409
    assert "already in filmed footage" in started.json()["detail"]


def test_register_flow_character_needs_hosted_stills(monkeypatch) -> None:
    client = _client()
    sid = _locked_session(client)
    char = session.package_for(sid).characters[0]
    res = client.post(f"/api/director/sessions/{sid}/bible/register_character/{char.char_id}")
    assert res.status_code == 409


def test_existing_flow_character_link_is_verified_and_drives_clip_payload(monkeypatch) -> None:
    client = _client()
    sid = _locked_cast_session(client)
    package = session.package_for(sid)
    char = package.characters[0]
    character_ref = "user:7-email:abc-character:8b927a1f-e309-4512-bbb3-ee62ef69cca5-imgs:2"
    supplied = (
        "https://flow.google.com/u/1/project/project-id/character/"
        "8b927a1f-e309-4512-bbb3-ee62ef69cca5"
    )
    seen = []

    def resolve(value):
        seen.append(value)
        return {
            "entityId": "8b927a1f-e309-4512-bbb3-ee62ef69cca5",
            "character": character_ref,
            "displayName": "Ghost",
        }

    monkeypatch.setattr(flow, "resolve_character", resolve)
    res = client.post(
        f"/api/director/sessions/{sid}/bible/flow_character/{char.char_id}",
        json={"reference": supplied},
    )
    assert res.status_code == 200, res.text
    assert seen == [supplied]
    assert res.json()["flow_character_ref"] == character_ref
    assert char.flow_character_ref == character_ref

    clip = next(
        clip
        for clip in package.clips
        if any(pack.get("char_id") == char.char_id for pack in clip.continuity.character_packs)
    )
    clip.provider = CloudProvider.GOOGLE_FLOW
    clip.model = "omni-flash"
    refs = compiler.provider_artifact_refs(clip, package)
    assert refs["characters"] == [character_ref]
    assert refs["character_names"] == [char.name]
    board = client.get(f"/api/director/sessions/{sid}/board").json()
    row = next(c for c in board["characters"] if c["char_id"] == char.char_id)
    assert row["flow_registered"] is True
    assert row["flow_preview_token"] == hashlib.sha256(character_ref.encode()).hexdigest()[:12]


def test_linked_flow_character_preview_is_proxied_once_and_cached(monkeypatch) -> None:
    client = _client()
    sid = _locked_cast_session(client)
    package = session.package_for(sid)
    char = package.characters[0]
    character_ref = "user:7-email:abc-character:char-ghost-imgs:2"
    char.flow_character_ref = character_ref
    png = b"\x89PNG\r\n\x1a\n" + b"flow-portrait" * 64
    resolved = []
    fetched = []

    def resolve(value):
        resolved.append(value)
        return {"character": character_ref, "thumbnailUrl": "https://signed.example/ghost.png"}

    def get(url, **_kwargs):
        fetched.append(url)
        return httpx.Response(200, content=png)

    monkeypatch.setattr(flow, "resolve_character", resolve)
    monkeypatch.setattr(base, "get_with_retries", get)
    url = f"/api/director/sessions/{sid}/bible/flow-preview/{char.char_id}"
    first = client.get(url)
    second = client.get(url)
    assert first.status_code == 200 and first.content == png
    assert first.headers["content-type"].startswith("image/png")
    assert second.status_code == 200 and second.content == png
    assert resolved == [character_ref]
    assert fetched == ["https://signed.example/ghost.png"]


def test_flow_character_library_lists_safe_cast_choices(monkeypatch) -> None:
    client = _client()
    monkeypatch.setattr(flow, "configured", lambda: True)
    monkeypatch.setattr(
        flow,
        "list_characters",
        lambda: [
            {
                "entityId": "char-ghost",
                "character": "user:7-email:abc-character:char-ghost-imgs:2-voice:iapetus",
                "displayName": "Ghost",
                "personalityNotes": "not needed by the picker",
                "imageReferences": [{"workflowId": "portrait"}, {"workflowId": "body"}],
                "voice": {"source": "system", "voice": "Iapetus", "displayName": "Iapetus"},
                "thumbnailUrl": "https://signed.example/secret-preview",
            }
        ],
    )
    res = client.get("/api/director/flow/characters")
    assert res.status_code == 200, res.text
    assert res.json() == {
        "characters": [
            {
                "reference": "char-ghost",
                "entity_id": "char-ghost",
                "display_name": "Ghost",
                "image_count": 2,
                "voice": "Iapetus",
            }
        ]
    }
    assert "signed.example" not in res.text and "personalityNotes" not in res.text


def test_existing_flow_character_cannot_replace_cast_after_footage_starts(monkeypatch) -> None:
    client = _client()
    sid = _locked_cast_session(client)
    package = session.package_for(sid)
    char = package.characters[0]
    clip_id = next(
        clip.clip_id
        for clip in package.clips
        if any(pack.get("char_id") == char.char_id for pack in clip.continuity.character_packs)
    )
    next(op for op in session.ops_for(sid) if op.clip_id == clip_id).status = "completed"
    monkeypatch.setattr(
        flow,
        "resolve_character",
        lambda _value: {
            "entityId": "new-id",
            "character": "user:7-email:abc-character:new-id-imgs:2",
            "displayName": "Other Ghost",
        },
    )
    res = client.post(
        f"/api/director/sessions/{sid}/bible/flow_character/{char.char_id}",
        json={"reference": "new-id"},
    )
    assert res.status_code == 409
    assert "already in filmed footage" in res.json()["detail"]


class _Receipt:
    measured = True
    verified = False
    similarity_min = 0.31
    threshold = 0.363


def test_identity_family_fails_a_drifted_face(monkeypatch, tmp_path) -> None:
    from diony_director import packagebuilder, screenwriter

    draft = screenwriter.write_script("a chase through a desert town", runtime_target_s=60)
    package = packagebuilder.build_package("s-id", "a chase", draft)
    dialogue = next(c for c in package.clips if c.clip_class == "sync-dialogue")
    char = package.character(dialogue.continuity.character_packs[0]["char_id"])
    still = tmp_path / "front.png"
    still.write_bytes(b"png" * 500)
    char.hero_stills["front"] = str(still)

    clip_path = tmp_path / "clip.mp4"
    clip_path.write_bytes(b"clip" * 1024)
    monkeypatch.setattr(review, "_face_receipt", lambda *a, **k: _Receipt())

    result = review.review_clip(clip_path, dialogue, package=package)
    assert result.families["identity"] == "fail"
    assert result.verdict == "fail"
    assert any("drifted" in issue for issue in result.issues)
    assert result.measured[f"identity_min:{char.char_id}"] == 0.31


def test_grok_only_setups_build_sheets_on_the_grok_image_lane(monkeypatch) -> None:
    monkeypatch.delenv("USEAPI_TOKEN", raising=False)
    from diony_director import artifacts, packagebuilder, screenwriter

    draft = screenwriter.write_script("a chase through a desert town", runtime_target_s=60)
    package = packagebuilder.build_package("s-gk", "a chase", draft)
    ops = artifacts.plan_sheet_ops("s-gk", package)
    assert ops
    for op in ops:
        assert op.provider.value == "grok"
        assert op.model == "grok-imagine-image-2.0"
        # Standard sheets are generations. xAI bills those at low quality:
        # $0.06 per 2K still (docs.x.ai, fetched 2026-10-13). A locked board
        # is an edit and prices at medium, $0.08.
        assert op.estimate.total_usd_micros == 60_000


def test_grok_image_submit_is_synchronous(monkeypatch, tmp_path) -> None:
    from diony_director.providers import grok

    class _Response:
        status_code = 200

        def raise_for_status(self):
            return None

        def json(self):
            return {"data": [{"url": "https://imgen.x.ai/still.png"}]}

    seen = {}

    def fake_post(url, **kwargs):
        seen["url"] = url
        seen["json"] = kwargs.get("json")
        return _Response()

    monkeypatch.setattr(grok.httpx, "post", fake_post)
    still = tmp_path / "front.png"
    still.write_bytes(b"pngbytes")
    handle = grok.submit_image("casting photo", references=[str(still)])
    assert seen["url"].endswith("/v1/images/generations")
    assert seen["json"]["image_url"].startswith("data:image/png;base64,")
    status = grok.poll(handle)
    assert status.state == "done"
    assert status.result_url.endswith("still.png")
