"""The Retry ladder: a fresh opening frame, and the run resumes on its own.

2026-09-06, clip 6/30 of a chained one-shot: Google Flow refused the clip
(prominent-people filter) and every Retry re-rolled it from the SAME style
frame — the still that carried the recognizable face — into the same refusal.
And each Retry only re-armed that one clip: the later links had left the
batch when their predecessor failed, so the user had to press Run & Assemble
All again after every retake. The doctrine under test:

- a refusal redraws the link's opening frame at once, steered clear of any
  recognizable person, and composes it from fewer film frames each time;
- any other failure retries once on the same frame, then redraws (by the
  second failure the frame is the common factor); `fresh_frame` overrides;
- a frame redrawn FRAME_REDRAW_LIMIT times refuses the retry loudly;
- a Retry re-targets the run plan's waiting ops and the chain behind the
  retake, and hands the run plan (assemble, finish) back to the room;
- the run plan the press recorded survives a restart.
"""

from __future__ import annotations

import hashlib
import uuid
from pathlib import Path

from fastapi.testclient import TestClient
from test_look_bible import ANIMATED, _chain_brain, _wait_proposed

from diony_contracts.production import LookBible
from diony_director import app as app_module
from diony_director import look as look_mod
from diony_director import review as review_mod
from diony_director import session
from diony_director.providers import base, flow

PROMINENT = (
    flow._FILTER_REASONS["PROMINENT_PERSON"]
    + " [PUBLIC_ERROR_PROMINENT_PEOPLE_FILTER_FAILED, PROMINENT_PERSON]"
)


def _sid() -> str:
    return f"s-test-{uuid.uuid4().hex[:8]}"


def _lanes(monkeypatch):
    """Video and image lanes that print instantly, record what they were sent,
    and refuse (moderate) the video jobs named in `refuse`."""
    sent: dict = {"videos": [], "images": [], "refuse": set()}
    counter = {"n": 0}

    def fake_submit(op):
        counter["n"] += 1
        sent["videos"].append(
            {"op_id": op.id, "refs": dict(op.artifact_refs), "ext": f"vid-{counter['n']}"}
        )
        return base.CloudJobHandle(
            provider_id="google-flow",
            external_id=f"vid-{counter['n']}",
            model=op.model,
            duration_s=int(op.duration_s),
            mode="t2v",
        )

    def fake_submit_image(prompt, *, model, references=None, aspect_ratio="landscape"):
        counter["n"] += 1
        sent["images"].append({"prompt": prompt, "references": list(references or [])})
        return base.CloudJobHandle(
            provider_id="google-flow",
            external_id=f"img-{counter['n']}",
            model=model,
            duration_s=0,
        )

    def fake_poll(handle):
        if handle.external_id in sent["refuse"]:
            return base.CloudJobStatus(state="moderated", detail=PROMINENT)
        return base.CloudJobStatus(
            state="done",
            result_url=f"https://flow/{handle.external_id}",
            metadata={"media_generation_id": f"user:{handle.external_id}"},
        )

    def fake_fetch(handle, status, dest):
        dest.parent.mkdir(parents=True, exist_ok=True)
        payload = (b"png" if handle.duration_s == 0 else b"mp4") * 2048
        dest.write_bytes(payload)
        return base.FetchedClip(
            path=dest,
            sha256=hashlib.sha256(payload).hexdigest(),
            bytes=len(payload),
            media_url=status.result_url,
        )

    monkeypatch.setattr(flow, "submit", fake_submit)
    monkeypatch.setattr(flow, "submit_image", fake_submit_image)
    monkeypatch.setattr(flow, "poll", fake_poll)
    monkeypatch.setattr(flow, "fetch", fake_fetch)
    return sent


def _frames_by_ffmpeg_stub(monkeypatch):
    """_last_frame and _conform_still without ffmpeg: the destination file is
    written and named, so reference lists carry real, distinct paths."""

    def fake_last_frame(clip_path, dest, *, at_s=None, sid=""):
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(b"jpg" * 64)
        return str(dest)

    def fake_conform(src, dest, *, sid=""):
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(Path(src).read_bytes() if Path(src).is_file() else b"jpg" * 64)
        return str(dest)

    monkeypatch.setattr(app_module, "_last_frame", fake_last_frame)
    monkeypatch.setattr(app_module, "_conform_still", fake_conform)


def _sync_submitter(monkeypatch):
    """The submit wave runs inline (no worker thread) so a test can reason
    about state after each call."""

    def kick(sid):
        for _ in range(12):
            submitted, held = app_module._submit_ready(sid)
            session.log_held(sid, held)
            if not submitted:
                break

    monkeypatch.setattr(app_module, "_kick_submitter", kick)


def _pass_reviews(monkeypatch):
    monkeypatch.setattr(
        review_mod,
        "review_chain_link",
        lambda path, **kw: review_mod.ClipReview(
            id="rev-ok",
            clip_id=kw["op_id"],
            op_id=kw["op_id"],
            verdict="pass",
            families={"delivery": "pass"},
            recommended_op="accept",
        ),
    )


# --- the prompt ---------------------------------------------------------------------


def test_a_redrawn_frame_is_a_fresh_composition_and_safe_when_asked() -> None:
    look = LookBible(medium="2.5D animated explainer", palette="muted navy with one amber accent")
    plain = look_mod.frame_redraw_prompt(
        "A night skyline of glass towers.", look, safe=False, attempt=1
    )
    assert "a night skyline of glass towers." in plain
    assert look.block() in plain
    assert "attempt 1" in plain and "different angle" in plain
    assert look_mod.SAFE_FRAME_STEER not in plain
    safe = look_mod.frame_redraw_prompt(
        "A man at a desk.", look, safe=True, attempt=2, continues=True
    )
    assert look_mod.SAFE_FRAME_STEER in safe
    assert "continues the previous shot directly" in safe
    assert "attempt 2" in safe
    # a film without a look still composes a frame (template chains)
    bare = look_mod.frame_redraw_prompt("the same skyline", None, safe=True, attempt=1)
    assert bare.startswith(look_mod.STYLE_FRAME_LEAD)
    assert look_mod.SAFE_FRAME_STEER in bare


# --- the ladder ---------------------------------------------------------------------


def test_the_ladder_redraws_on_refusal_and_on_the_second_plain_failure() -> None:
    sid = _sid()
    proposal = session.propose(sid, ANIMATED, provider="google-flow", duration_s=24)
    first, second, _third = proposal.ops
    # the opener opens the film — it has no frame to redraw
    first.status = "failed"
    session.record_failure(first, "moderated", PROMINENT)
    assert app_module._plan_frame_redraw(first, None) == (False, False, "")
    # a refusal redraws at once, likeness-safe
    second.status = "failed"
    session.record_failure(second, "moderated", PROMINENT)
    redraw, safe, why = app_module._plan_frame_redraw(second, None)
    assert (redraw, safe) == (True, True) and "refused" in why
    # the detail alone (an old op with no state) still reads as a refusal
    session.runtime(second.id)["failure"] = {"detail": "Google Flow refused the clip: its filter…"}
    assert app_module._plan_frame_redraw(second, None)[:2] == (True, True)
    # a plain failure: the first retry keeps the frame, the second redraws it
    session.record_failure(second, "failed", "the provider job died mid-render")
    assert app_module._plan_frame_redraw(second, None) == (False, False, "")
    session.runtime(second.id)["retries"] = 1
    redraw, safe, why = app_module._plan_frame_redraw(second, None)
    assert (redraw, safe) == (True, False) and "failed 2 times" in why
    # the user's word wins either way
    assert app_module._plan_frame_redraw(second, app_module.RetryIn(fresh_frame=False))[0] is False
    session.runtime(second.id)["retries"] = 0
    redraw, safe, why = app_module._plan_frame_redraw(second, app_module.RetryIn(fresh_frame=True))
    assert redraw is True and safe is False and "asked for" in why


def test_retry_redraws_the_opening_frame_of_a_continuing_link(monkeypatch) -> None:
    """A template chain has no scene changes: every link opens from the
    previous link's last frame. A refusal still gets a fresh frame — a still
    composed FROM that last frame, marked as continuing the shot."""
    monkeypatch.setattr(app_module, "_kick_submitter", lambda _sid: None)
    sid = _sid()
    proposal = session.propose(sid, ANIMATED, provider="google-flow", duration_s=24)
    first, second, third = proposal.ops
    first.status = "completed"
    second.status = "failed"
    second.artifact_refs["start_image"] = "/vault/frames/first.jpg"
    session.record_failure(second, "moderated", PROMINENT)
    assert session.style_frame_op_for(second) is None
    client = TestClient(app_module.app)
    res = client.post(f"/api/director/ops/{second.id}/retry")
    assert res.status_code == 200
    body = res.json()
    assert body["op"]["status"] == "proposed"
    assert body["redraw"]["attempt"] == 1 and body["redraw"]["safe"] is True
    assert "redrawn" in body["redraw"]["note"] and "recognizable person" in body["redraw"]["note"]
    frame_op = session.style_frame_op_for(second)
    assert frame_op is not None and frame_op.status == "proposed"
    assert frame_op.mode.value == "image" and frame_op.estimate.total_usd_micros > 0
    info = frame_op.artifact_refs["style_frame"]
    assert info["for_op"] == second.id and info["redraw"] == 1 and info["safe"] is True
    assert info["continues"] is True
    assert look_mod.SAFE_FRAME_STEER in frame_op.prompt
    assert "continues the previous shot directly" in frame_op.prompt
    # the stale start image is gone — the new frame binds at submit
    assert "start_image" not in second.artifact_refs
    assert session.runtime(second.id)["frame_redraws"] == 1
    assert session.runtime(second.id)["retries"] == 1
    assert "opening frame redrawn (attempt 1, likeness-safe)" in second.rationale
    # the frame op is registered in the room, never a batch target
    assert frame_op.id in session.get_session(sid).op_ids
    assert frame_op.id not in session.batch_targets(sid)
    # the retake is targeted, and the chain behind it rejoins the run
    assert second.id in session.batch_targets(sid)
    assert body["retargeted"] == [third.id]
    assert third.id in session.batch_targets(sid)
    state = client.get(f"/api/director/ops/{second.id}").json()["style_frame"]
    assert state["op_id"] == frame_op.id and state["ready"] is False
    assert state["redraws"] == 1 and state["continues"] is True and state["safe"] is True
    # the ladder survives a restart
    session.persist(sid)
    session.reset()
    session.rehydrate_from_vault()
    restored = session.get_op(second.id)
    assert session.runtime(restored.id)["frame_redraws"] == 1
    assert session.runtime(restored.id)["retries"] == 1
    assert session.runtime(restored.id)["failure"]["state"] == "moderated"
    assert session.style_frame_op_for(restored).id == frame_op.id


def test_a_frame_redrawn_to_the_limit_refuses_the_retry_loudly(monkeypatch) -> None:
    monkeypatch.setattr(app_module, "_kick_submitter", lambda _sid: None)
    sid = _sid()
    proposal = session.propose(sid, ANIMATED, provider="google-flow", duration_s=24)
    _first, second, _third = proposal.ops
    second.status = "failed"
    session.record_failure(second, "moderated", PROMINENT)
    session.runtime(second.id)["frame_redraws"] = session.FRAME_REDRAW_LIMIT
    client = TestClient(app_module.app)
    res = client.post(f"/api/director/ops/{second.id}/retry")
    assert res.status_code == 409
    assert f"redrawn {session.FRAME_REDRAW_LIMIT} times" in res.json()["detail"]
    assert "Rewrite the clip" in res.json()["detail"]
    assert second.status == "failed"  # nothing changed, nothing spent
    # the user may still insist on the same frame
    res = client.post(f"/api/director/ops/{second.id}/retry", json={"fresh_frame": False})
    assert res.status_code == 200 and res.json()["redraw"] is None


# --- the run resumes ----------------------------------------------------------------


def test_run_batch_records_the_run_plan_and_retry_hands_it_back(monkeypatch) -> None:
    monkeypatch.setattr(app_module, "_kick_submitter", lambda _sid: None)
    sid = _sid()
    proposal = session.propose(sid, ANIMATED, provider="google-flow", duration_s=24)
    first, second, third = proposal.ops
    client = TestClient(app_module.app)
    res = client.post(
        f"/api/director/sessions/{sid}/run_batch",
        json={
            "op_ids": [op.id for op in proposal.ops],
            "assemble": True,
            "finish": "1080p",
            "upscale": True,
        },
    )
    assert res.status_code == 200
    plan = res.json()["run_plan"]
    assert plan["assemble"] is True and plan["finish"] == "1080p" and plan["upscale"] is True
    assert plan["op_ids"] == [first.id, second.id, third.id]
    # a plain Run (no assemble) leaves the plan alone
    assert (
        client.post(
            f"/api/director/sessions/{sid}/run_batch", json={"op_ids": [first.id]}
        ).status_code
        == 200
    )
    assert session.run_plan(sid)["finish"] == "1080p"
    # the plan survives a restart
    session.persist(sid)
    session.reset()
    session.rehydrate_from_vault()
    assert session.run_plan(sid)["op_ids"] == [first.id, second.id, third.id]
    first, second, third = (session.get_op(op.id) for op in (first, second, third))
    # clip 1 failed on the network; the chain-order hold dropped 2 and 3
    first.status = "failed"
    session.record_failure(first, "failed", "the provider job died mid-render")
    session.set_batch_targets(sid, set())
    res = client.post(f"/api/director/ops/{first.id}/retry")
    assert res.status_code == 200
    body = res.json()
    assert body["redraw"] is None  # the opener has no frame; first plain retry keeps everything
    assert body["run_plan"]["finish"] == "1080p"
    assert body["retargeted"] == [second.id, third.id]
    assert session.batch_targets(sid) == {first.id, second.id, third.id}
    assert (
        client.post(f"/api/director/sessions/{sid}/advance").json()["run_plan"]["assemble"] is True
    )
    # a retake of a review-failed link re-arms the run the same way
    session.set_batch_targets(sid, set())
    first.status = "completed"
    session.register_review(
        first.id,
        review_mod.ClipReview(
            id="rev-bad",
            clip_id=first.id,
            op_id=first.id,
            verdict="fail",
            families={"continuity": "fail"},
            issues=["the clip ignored its opening frame"],
        ),
    )
    body = client.post(f"/api/director/ops/{first.id}/retry").json()
    assert body["op"]["status"] == "proposed" and body["retargeted"] == [second.id, third.id]
    assert body["run_plan"]["assemble"] is True


def test_missing_flow_captcha_holds_the_account_once_and_stops_the_batch(monkeypatch) -> None:
    """A deterministic account setup error is not 3 independent clip
    failures and must not be retried by every automatic advance tick."""
    sid = _sid()
    proposal = session.propose(sid, ANIMATED, provider="google-flow", duration_s=24)
    targets = {op.id for op in proposal.ops}
    session.set_batch_targets(sid, targets)
    calls = {"n": 0}

    def missing_solver(_op):
        calls["n"] += 1
        raise base.CloudAdapterConfigurationError(
            "Google Flow needs a captcha solver configured at "
            f"{flow.CAPTCHA_PROVIDER_DOCS}; then press Run again. Nothing was spent."
        )

    monkeypatch.setattr(flow, "submit", missing_solver)
    submitted, held = app_module._submit_ready(sid)

    assert submitted == []
    assert calls["n"] == 1
    assert len(held) == 1
    assert held[0]["kind"] == "configuration"
    assert held[0]["provider"] == "google-flow"
    assert held[0]["count"] == len(targets)
    assert held[0]["help_url"] == flow.CAPTCHA_PROVIDER_DOCS
    assert all(op.status == "proposed" for op in proposal.ops)
    assert session.batch_targets(sid) == set()

    # The next automatic tick is quiet. A human can configure useapi.net and
    # press Run again, which creates a fresh target set without redrafting.
    assert app_module._submit_ready(sid) == ([], [])
    assert calls["n"] == 1


def test_a_refused_link_rolls_again_from_a_fresh_frame_and_the_run_finishes(
    monkeypatch, tmp_path
) -> None:
    """End to end through the API — the 2026-09-06 shape. Clip 2 cuts to a new
    place and opens from a style frame; Flow refuses the clip for a prominent
    person. ONE Retry: the frame is redrawn likeness-safe without the previous
    link's last frame among its references, the clip rolls from the NEW still,
    clip 3 (dropped from the batch by the break) rejoins the run behind it,
    and the chain prints out with no second press."""
    _chain_brain(monkeypatch)
    sent = _lanes(monkeypatch)
    _frames_by_ffmpeg_stub(monkeypatch)
    _pass_reviews(monkeypatch)
    _sync_submitter(monkeypatch)
    client = TestClient(app_module.app)
    sid = client.post("/api/director/sessions").json()["session_id"]
    body = client.post(
        f"/api/director/sessions/{sid}/message",
        json={"text": ANIMATED, "provider": "google-flow", "duration_s": 24},
    ).json()
    ops = [session.get_op(o["id"]) for o in body["ops"]]
    _wait_proposed(ops)
    first, second, third = ops
    frame_op = session.style_frame_op_for(second)
    assert frame_op is not None

    # Run & Assemble All: clip 1 prints; clip 2's frame prints and the clip is refused
    sent["refuse"].add("vid-3")  # vid-1 = clip 1, img-2 = the style frame, vid-3 = clip 2
    res = client.post(
        f"/api/director/sessions/{sid}/run_batch",
        json={"op_ids": [op.id for op in ops], "assemble": True, "finish": "auto"},
    )
    assert res.status_code == 200
    for _ in range(6):
        counts = client.post(f"/api/director/sessions/{sid}/advance").json()
        if counts["pending"] == 0 and not counts["queued"]:
            break
    assert first.status == "completed"
    assert frame_op.status == "completed"
    old_frame = session.runtime(second.id).get("style_frame")
    assert second.status == "failed"
    assert session.runtime(second.id)["failure"]["state"] == "moderated"
    assert sent["videos"][-1]["op_id"] == second.id
    assert sent["videos"][-1]["refs"]["start_image"] == old_frame
    assert len(sent["images"]) == 1
    prev_last_frame = str(
        app_module.cluster_root() / "director" / sid / "frames" / f"{first.id}.jpg"
    )
    assert prev_last_frame in sent["images"][0]["references"]
    # the break dropped clip 3 from the batch
    client.post(f"/api/director/sessions/{sid}/advance")
    assert third.status == "proposed" and third.id not in session.batch_targets(sid)
    state = client.get(f"/api/director/ops/{second.id}").json()
    assert "prominent-people filter" in state["job"]["error_message"]

    # ONE Retry
    res = client.post(f"/api/director/ops/{second.id}/retry")
    assert res.status_code == 200
    body = res.json()
    assert body["redraw"]["safe"] is True and body["redraw"]["attempt"] == 1
    assert body["retargeted"] == [third.id]
    assert body["run_plan"]["assemble"] is True
    new_frame_op = session.style_frame_op_for(second)
    assert new_frame_op is not None and new_frame_op.id != frame_op.id
    assert frame_op.status == "completed"  # the spent frame stays on the ledger
    # the synchronous wave already rolled: the redraw printed, the clip re-submitted
    assert len(sent["images"]) == 2
    redraw = sent["images"][1]
    assert look_mod.SAFE_FRAME_STEER in redraw["prompt"]
    assert "attempt 1" in redraw["prompt"]
    assert prev_last_frame not in redraw["references"], (
        "a safe redraw drops the previous link's last frame"
    )
    new_frame = session.runtime(second.id)["style_frame"]
    assert new_frame and new_frame != old_frame and Path(new_frame).is_file()
    assert sent["videos"][-1]["op_id"] == second.id
    assert sent["videos"][-1]["refs"]["start_image"] == new_frame
    assert "references" not in sent["videos"][-1]["refs"]
    # the run finishes on its own: clip 2 prints, clip 3 rolls behind it
    for _ in range(8):
        counts = client.post(f"/api/director/sessions/{sid}/advance").json()
        if counts["pending"] == 0 and not counts["queued"] and not counts["submitting"]:
            break
    assert second.status == "completed"
    assert third.status == "completed", session.drain_held(sid)
    assert [op.id for op in session.completed_clip_ops(sid)] == [first.id, second.id, third.id]
    assert app_module._cut_gaps(sid) == ([], [], [])
    state = client.get(f"/api/director/ops/{second.id}").json()
    assert state["style_frame"]["op_id"] == new_frame_op.id
    assert state["style_frame"]["ready"] is True and state["style_frame"]["redraws"] == 1
    assert client.get(f"/api/director/ops/{second.id}/style_frame").status_code == 200
    assert "opening frame redrawn (attempt 1, likeness-safe)" in second.rationale


def test_a_second_refusal_composes_from_the_users_stills_alone(monkeypatch, tmp_path) -> None:
    """Redraw two after two refusals keeps only the user's own uploads as
    references: every frame pulled from the film could carry the face."""
    _chain_brain(monkeypatch)
    sent = _lanes(monkeypatch)
    _frames_by_ffmpeg_stub(monkeypatch)
    _pass_reviews(monkeypatch)
    _sync_submitter(monkeypatch)
    client = TestClient(app_module.app)
    sid = client.post("/api/director/sessions").json()["session_id"]
    still = client.post(
        f"/api/director/sessions/{sid}/uploads",
        json={
            "name": "mood.png",
            "data_base64": __import__("base64")
            .b64encode(b"\x89PNG\r\n\x1a\n" + b"\x00" * 24)
            .decode(),
        },
    ).json()["upload"]
    body = client.post(
        f"/api/director/sessions/{sid}/message",
        json={"text": ANIMATED, "provider": "google-flow", "duration_s": 24},
    ).json()
    ops = [session.get_op(o["id"]) for o in body["ops"]]
    _wait_proposed(ops)
    first, second, _third = ops
    assert client.post(f"/api/director/ops/{first.id}/run").status_code == 200
    assert client.get(f"/api/director/ops/{first.id}").json()["op"]["status"] == "completed"
    sent["refuse"].update({"vid-3", "vid-5"})  # clip 2's first two attempts
    assert client.post(f"/api/director/ops/{second.id}/run").status_code == 200
    assert client.get(f"/api/director/ops/{second.id}").json()["op"]["status"] == "failed"
    # redraw 1: no previous last frame, but the look frame and the still ride
    assert client.post(f"/api/director/ops/{second.id}/retry").status_code == 200
    assert client.get(f"/api/director/ops/{second.id}").json()["op"]["status"] == "failed"
    look_frame = str(
        app_module.cluster_root() / "director" / sid / "frames" / f"look-{first.id}.jpg"
    )
    assert look_frame in sent["images"][1]["references"]
    assert still["path"] in sent["images"][1]["references"]
    # redraw 2: the user's still alone
    res = client.post(f"/api/director/ops/{second.id}/retry")
    assert res.status_code == 200 and res.json()["redraw"]["attempt"] == 2
    assert sent["images"][2]["references"] == [still["path"]]
    assert "attempt 2" in sent["images"][2]["prompt"]
    assert client.get(f"/api/director/ops/{second.id}").json()["op"]["status"] == "completed"
    assert session.runtime(second.id)["frame_redraws"] == 2
    assert len(session.runtime(second.id)["discarded_frames"]) == 2
