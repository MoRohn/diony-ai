"""P0 hardening: legal Flow media, surviving edit/extend sources, the encoded
feature matrix, review-gated assembly, sequenced bibles, per-session budgets."""

import base64
import importlib

import pytest
from fastapi.testclient import TestClient

from diony_contracts.director import (
    CloudGenerationMode,
    CloudProvider,
    CostEstimate,
    CostEstimateLine,
    DirectorOp,
    DirectorOpType,
)
from diony_contracts.production import ClipReview
from diony_director import app as app_module
from diony_director import capabilities, revisions, session
from diony_director.providers import base, flow, grok


def _client() -> TestClient:
    importlib.reload(app_module)
    return TestClient(app_module.app, raise_server_exceptions=False)


def _locked(client) -> tuple[str, dict]:
    sid = client.post("/api/director/sessions").json()["session_id"]
    client.post(
        f"/api/director/sessions/{sid}/produce",
        json={"idea": "a getaway driver decides to leave town from a neon diner"},
    )
    return sid, client.post(f"/api/director/sessions/{sid}/lock_script").json()


def _estimate() -> CostEstimate:
    return CostEstimate(
        id="est-p0",
        workspace_id="local-workspace",
        lines=[
            CostEstimateLine(
                label="x",
                provider_id="google-flow",
                model="veo-3.1-fast",
                unit="generation",
                unit_rate_usd_micros=1,
                quantity_microunits=1_000_000,
                usd_micros=1,
            )
        ],
    )


def _flow_op(**overrides) -> DirectorOp:
    payload = {
        "id": "op-p0",
        "session_id": "s-p0",
        "op": DirectorOpType.GENERATE,
        "provider": CloudProvider.GOOGLE_FLOW,
        "model": "veo-3.1-fast",
        "mode": CloudGenerationMode.T2V,
        "prompt": "a diner at night",
        "duration_s": 8,
        "resolution": "720p",
        "estimate": _estimate(),
    }
    payload.update(overrides)
    return DirectorOp(**payload)


# --- A1: the Flow lane uploads local media instead of leaking paths ---------


def test_flow_submit_uploads_local_media_as_assets(tmp_path, monkeypatch) -> None:
    plate = tmp_path / "plate.png"
    plate.write_bytes(b"\x89PNG\r\n\x1a\nplate")
    face = tmp_path / "face.jpg"
    face.write_bytes(b"\xff\xd8\xffface")
    calls = []

    class _Response:
        def __init__(self, body):
            self._body = body
            self.status_code = 200

        def json(self):
            return self._body

    def fake_post(url, **kwargs):
        calls.append({"url": url, "json": kwargs.get("json"), "content": kwargs.get("content")})
        if "/assets" in url:
            return _Response({"mediaGenerationId": f"user:asset-{len(calls)}"})
        return _Response({"jobId": "j-1"})

    monkeypatch.setattr(flow.httpx, "post", fake_post)
    op = _flow_op(artifact_refs={"start_image": str(plate), "references": [str(face), "user:img7"]})
    flow.submit(op)

    video_call = next(c for c in calls if c["url"].endswith("/videos"))
    assert video_call["json"]["startImage"].startswith("user:asset-")
    assert video_call["json"]["referenceImage_1"].startswith("user:asset-")
    assert video_call["json"]["referenceImage_2"] == "user:img7"
    upload_calls = [c for c in calls if "/assets" in c["url"]]
    assert len(upload_calls) == 2
    assert upload_calls[0]["content"] == plate.read_bytes()

    # Content-hash cache: the same file never uploads twice.
    calls.clear()
    flow.submit(op)
    assert not [c for c in calls if "/assets" in c["url"]]


def test_flow_submit_refuses_urls_and_dangling_paths(monkeypatch) -> None:
    monkeypatch.setattr(
        flow.httpx, "post", lambda *a, **k: pytest.fail("no request may leave with illegal media")
    )
    with pytest.raises(base.CloudAdapterError, match="mediaGenerationId, not a URL"):
        flow.submit(_flow_op(artifact_refs={"start_image": "https://cdn/frame.jpg"}))
    with pytest.raises(base.CloudAdapterError, match="missing local file"):
        flow.submit(_flow_op(artifact_refs={"start_image": "/vault/frames/gone.jpg"}))


def test_flow_extend_refuses_omni_and_local_files(tmp_path) -> None:
    with pytest.raises(base.CloudAdapterError, match="omni-flash cannot perform an extension"):
        flow.submit_extend("user:vid1", prompt="go on", duration_s=8, model="omni-flash")
    local = tmp_path / "clip.mp4"
    local.write_bytes(b"vid")
    with pytest.raises(base.CloudAdapterError, match="Veo-generated"):
        flow.submit_extend(str(local), prompt="go on", duration_s=8)


def test_flow_handle_keeps_mode_and_resolution() -> None:
    handle = flow.submit_handle(
        "j-9", model="omni-flash", duration_s=6, mode="t2v", resolution="360p"
    )
    assert handle.resolution == "360p"  # the credit fallback prices from this
    assert handle.mode == "t2v"


# --- A2: edit/extend sources survive to the provider ------------------------


def test_grok_video_input_reads_the_vault_copy(tmp_path) -> None:
    clip = tmp_path / "take.mp4"
    clip.write_bytes(b"vault-bytes")
    body = grok._video_input(str(clip))
    assert base64.b64decode(body["base64"]) == b"vault-bytes"


def test_extend_revision_carries_its_source_through_dispatch(monkeypatch, tmp_path) -> None:
    client = _client()
    sid, locked = _locked(client)
    grok_op_id = next(o["id"] for o in locked["ops"] if o["provider"] == "grok")
    failed_op = session.get_op(grok_op_id)

    take = tmp_path / "take.mp4"
    take.write_bytes(b"printed take")
    session.runtime(failed_op.id)["clip"] = base.FetchedClip(path=take, sha256="x", bytes=12)

    review = ClipReview(
        id="rev-p0",
        clip_id=failed_op.clip_id,
        op_id=failed_op.id,
        verdict="fail",
        families={"duration": "fail"},
        issues=["measured 3.0s vs booked 6s"],
    )
    revision, _ = session.next_revision(sid, failed_op, review)
    assert revision is not None and revision.op is DirectorOpType.EXTEND
    assert revision.artifact_refs["source_video"] == str(take)
    assert revision.model == "grok-imagine-video"  # extension runs and prices on base

    seen = {}

    def fake_extension(video, *, prompt, model, duration_s):
        seen["video"] = video
        return base.CloudJobHandle(
            provider_id="grok", external_id="req-ext", model=model, duration_s=duration_s
        )

    monkeypatch.setattr(grok, "submit_extension", fake_extension)
    app_module._dispatch_submit(revision)
    assert seen["video"] == str(take), "late binding must never wipe the source"


def test_flow_extend_revision_without_media_id_regenerates_honestly(tmp_path) -> None:
    # this production rolls on Google Flow (the 10 s Omni grid, the Flow finish lane)
    from diony_contracts.cluster import write_engine_choice

    write_engine_choice("google-flow")
    client = _client()
    sid, locked = _locked(client)
    flow_op_id = next(o["id"] for o in locked["ops"] if o["provider"] == "google-flow")
    failed_op = session.get_op(flow_op_id)
    take = tmp_path / "take.mp4"
    take.write_bytes(b"printed")
    # A local copy exists but there is no Flow media id — extend is impossible
    # on Flow without one, so the plan falls back to a stated regenerate.
    session.runtime(failed_op.id)["clip"] = base.FetchedClip(path=take, sha256="x", bytes=7)
    review = ClipReview(
        id="rev-p1",
        clip_id=failed_op.clip_id,
        op_id=failed_op.id,
        verdict="fail",
        families={"duration": "fail"},
        issues=["measured 3.0s vs booked 6s"],
    )
    revision, _ = session.next_revision(sid, failed_op, review)
    assert revision is not None
    if revision.op is DirectorOpType.GENERATE:
        assert "regenerating" in revision.rationale
    else:  # omni dialogue clips fix duration through a v2v edit instead
        assert revision.op is DirectorOpType.EDIT
        assert revision.artifact_refs["source_video"] == str(take)


# --- A4: the feature matrix refuses illegal combinations --------------------


def test_capabilities_block_illegal_ops() -> None:
    omni_extend = _flow_op(
        model="omni-flash", mode=CloudGenerationMode.EXTENSION, op=DirectorOpType.EXTEND
    )
    assert any("cannot be extended" in p for p in capabilities.violations(omni_extend))

    chars_on_quality = _flow_op(model="veo-3.1-quality", artifact_refs={"characters": ["char-1"]})
    assert any(
        "does not support characters" in p for p in capabilities.violations(chars_on_quality)
    )

    chars_off_grid = _flow_op(duration_s=4, artifact_refs={"characters": ["char-1"]})
    assert any("require a duration" in p for p in capabilities.violations(chars_off_grid))

    omni_480 = _flow_op(model="omni-flash", resolution="480p", duration_s=6)
    assert any("does not render" in p for p in capabilities.violations(omni_480))

    legal = _flow_op(artifact_refs={"characters": ["char-1"], "references": ["user:img1"]})
    assert capabilities.violations(legal) == []


def test_omni_duration_fail_plans_a_v2v_edit_not_an_extension() -> None:
    # this production rolls on Google Flow (the 10 s Omni grid, the Flow finish lane)
    from diony_contracts.cluster import write_engine_choice

    write_engine_choice("google-flow")
    from diony_director import packagebuilder, screenwriter
    from diony_director.compiler import compile_clip_prompt

    draft = screenwriter.write_script("a chase through a desert town", runtime_target_s=60)
    package = packagebuilder.build_package("s-om", "a chase", draft)
    clip = next(c for c in package.clips if c.model == "omni-flash")
    clip.add_prompt(compile_clip_prompt(clip, package))
    review = ClipReview(
        id="r-om",
        clip_id=clip.clip_id,
        verdict="fail",
        families={"duration": "fail"},
        issues=["measured 4.0s vs booked 6s"],
    )
    op, _ = revisions.plan_revision(
        review, clip, package, session_id="s-om", revision_count=0, failure_memory=set()
    )
    assert op is not None
    assert op.op is DirectorOpType.EDIT
    assert op.mode is CloudGenerationMode.V2V_EDIT


# --- B5: review gates the film ----------------------------------------------


def test_failed_review_takes_never_assemble_until_accepted() -> None:
    client = _client()
    sid, locked = _locked(client)
    op = session.get_op(locked["ops"][0]["id"])
    op.status = "completed"
    session.register_review(
        op.id,
        ClipReview(
            id="r-f",
            clip_id=op.clip_id,
            op_id=op.id,
            verdict="fail",
            families={"delivery": "fail"},
            issues=["garbage frames"],
        ),
    )
    assert session.completed_clip_ops(sid) == []
    assert session.previous_completed_clip_op(sid, session.get_op(locked["ops"][1]["id"])) is None

    session.mark_accepted(op.id)
    assert [o.id for o in session.completed_clip_ops(sid)] == [op.id]


def test_accept_endpoint_needs_a_completed_take() -> None:
    client = _client()
    sid, locked = _locked(client)
    op_id = locked["ops"][0]["id"]
    assert client.post(f"/api/director/ops/{op_id}/accept").status_code == 409
    session.get_op(op_id).status = "completed"
    body = client.post(f"/api/director/ops/{op_id}/accept")
    assert body.status_code == 200
    assert session.is_accepted(op_id)


# --- B1/B6: sequenced bible, held scenes ------------------------------------


def _bible_by_kind(locked: dict) -> tuple[list[dict], list[dict]]:
    sheets = [b for b in locked["bible_ops"] if b["artifact_refs"]["bible"].get("char_id")]
    plates = [b for b in locked["bible_ops"] if b["artifact_refs"]["bible"].get("setting_id")]
    return sheets, plates


def test_bible_rolls_anchor_stills_before_later_angles() -> None:
    client = _client()
    sid, locked = _locked(client)
    sheets, plates = _bible_by_kind(locked)
    all_ids = {o["id"] for o in locked["ops"]} | {b["id"] for b in locked["bible_ops"]}
    session.set_batch_targets(sid, all_ids)

    ready = session.ready_ops(sid)
    ready_sheets = [o for o in ready if (o.artifact_refs.get("bible") or {}).get("char_id")]
    chars = {s["artifact_refs"]["bible"]["char_id"] for s in sheets}
    assert len(ready_sheets) == len(chars), "one anchor still per character, nothing more"
    assert all(o.artifact_refs["bible"]["slot"] == "front" for o in ready_sheets)
    assert {o.id for o in ready if (o.artifact_refs.get("bible") or {}).get("setting_id")} == {
        p["id"] for p in plates
    }

    anchor = ready_sheets[0]
    anchor.status = "completed"
    second_wave = session.ready_ops(sid)
    released_slots = {
        o.artifact_refs["bible"]["slot"]
        for o in second_wave
        if (o.artifact_refs.get("bible") or {}).get("char_id")
        == anchor.artifact_refs["bible"]["char_id"]
    }
    assert released_slots == {"q34l"}, "the next angle chains only after the anchor lands"


def test_a_failed_plate_holds_its_scene_loudly() -> None:
    client = _client()
    sid, locked = _locked(client)
    sheets, plates = _bible_by_kind(locked)
    all_ids = {o["id"] for o in locked["ops"]} | {b["id"] for b in locked["bible_ops"]}
    session.set_batch_targets(sid, all_ids)

    for sheet in sheets:
        session.get_op(sheet["id"]).status = "completed"
    for plate in plates:
        session.get_op(plate["id"]).status = "failed"

    ready = session.ready_ops(sid)
    assert ready == [], "no scene may roll unplated in silence"
    held = session.drain_held(sid)
    assert held and all("held" in h["reason"] for h in held)
    clip_ids = {o["id"] for o in locked["ops"]}
    assert not (clip_ids & session.batch_targets(sid)), "held scenes leave the batch"


# --- A5: budgets are per-session, GETs never spend --------------------------


def test_committed_spend_is_scoped_per_session() -> None:
    client = _client()
    sid_a, locked_a = _locked(client)
    sid_b = client.post("/api/director/sessions").json()["session_id"]
    client.post(
        f"/api/director/sessions/{sid_b}/produce",
        json={"idea": "a lighthouse keeper hears a knock"},
    )
    locked_b = client.post(f"/api/director/sessions/{sid_b}/lock_script").json()
    session.get_op(locked_a["ops"][0]["id"]).status = "submitted"
    session.get_op(locked_b["ops"][0]["id"]).status = "submitted"

    both = session.committed_usd_micros()
    only_a = session.committed_usd_micros(sid_a)
    assert 0 < only_a < both, "a session ceiling must never count another session's spend"


def test_autopilot_never_spends_on_a_get(monkeypatch) -> None:
    client = _client()
    sid, locked = _locked(client)
    session.set_budget(sid, max_usd_micros=100_000_000, max_revisions_per_clip=3, autopilot=True)
    op = session.get_op(locked["ops"][0]["id"])
    calls = []
    monkeypatch.setattr(app_module, "_run_approved", lambda o: calls.append(o.id))

    rt: dict = {}
    app_module._maybe_autopilot_run(sid, op, rt, allow_spend=False)
    assert calls == [], "a GET is not an approval"
    assert "queued" in rt["narration"]
    app_module._maybe_autopilot_run(sid, op, rt, allow_spend=True)
    assert calls == [op.id]


def test_frames_are_exclusive_with_identity_refs() -> None:
    """The matrix declares frames_exclusive_with_refs on every model — the
    gate must actually refuse the combination, not discover it as a provider
    400 after money is committed."""
    framed = _flow_op(
        mode=CloudGenerationMode.I2V,
        artifact_refs={
            "start_image": "user:still",
            "references": ["user:img1"],
            "voices": ["v-1"],
        },
    )
    problems = capabilities.violations(framed)
    assert any("exclusive" in p for p in problems)

    frame_only = _flow_op(mode=CloudGenerationMode.I2V, artifact_refs={"start_image": "user:still"})
    assert capabilities.violations(frame_only) == []


def test_mode_matrix_is_enforced() -> None:
    first_last_on_grok = _flow_op(
        provider=CloudProvider.GROK,
        model="grok-imagine-video-1.5",
        mode=CloudGenerationMode.FIRST_LAST,
        duration_s=6,
        resolution="720p",
    )
    assert any("first_last" in p for p in capabilities.violations(first_last_on_grok))

    first_last_on_veo = _flow_op(mode=CloudGenerationMode.FIRST_LAST)
    assert capabilities.violations(first_last_on_veo) == []


def test_none_mode_never_crashes_the_gate() -> None:
    """DirectorOp.mode is Optional; a rehydrated op without one must not
    AttributeError on the money path — it's simply out of mode-matrix scope."""
    op = _flow_op(mode=None)
    assert capabilities.violations(op) == []


def test_grok_edit_mode_matrix_agrees_with_supports_edit() -> None:
    """grok-imagine-video advertises supports_edit=True, so its mode matrix
    must accept v2v_edit — the gate can't refuse an edit the record allows."""
    edit = _flow_op(
        provider=CloudProvider.GROK,
        model="grok-imagine-video",
        mode=CloudGenerationMode.V2V_EDIT,
        op=DirectorOpType.EDIT,
        resolution="720p",
        artifact_refs={"source_video": "/vault/takes/x.mp4"},
    )
    assert not any("v2v_edit lane" in p for p in capabilities.violations(edit))
