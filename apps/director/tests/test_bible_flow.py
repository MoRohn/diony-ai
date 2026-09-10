"""Sheet stills through the app, face approval, Flow character registration, identity family."""

import hashlib
import importlib

from fastapi.testclient import TestClient

from diony_contracts.production import Scene  # noqa: F401 - contract import sanity
from diony_director import app as app_module
from diony_director import review, session
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


def test_register_flow_character_needs_hosted_stills(monkeypatch) -> None:
    client = _client()
    sid = _locked_session(client)
    char = session.package_for(sid).characters[0]
    res = client.post(f"/api/director/sessions/{sid}/bible/register_character/{char.char_id}")
    assert res.status_code == 409


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
        assert op.estimate.total_usd_micros == 80_000  # $0.08 per 2K still


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
