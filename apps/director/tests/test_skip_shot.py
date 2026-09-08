"""Skip shot — the way past a clip whose retries keep failing (2026-09-06).

A skipped shot is DISCARDED, never a gap: it leaves the batch, the cut leaves
it out without allow_gaps, the resume slot stops counting it, and the run
continues past it — the later links rejoin behind it and open from the last
shot that printed. The decision survives a restart.
"""

from __future__ import annotations

import uuid

from fastapi.testclient import TestClient

from diony_director import app as app_module, review as review_mod, session
from test_look_bible import ANIMATED
from test_oneshot_chain import _print_link


def _sid() -> str:
    return f"s-test-{uuid.uuid4().hex[:8]}"


def test_a_skipped_link_leaves_the_cut_and_the_run_continues_past_it(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(app_module, "_kick_submitter", lambda _sid: None)
    sid = _sid()
    proposal = session.propose(sid, ANIMATED, provider="google-flow", duration_s=24)
    first, second, third = proposal.ops
    client = TestClient(app_module.app)
    res = client.post(
        f"/api/director/sessions/{sid}/run_batch",
        json={"op_ids": [op.id for op in proposal.ops], "assemble": True},
    )
    assert res.status_code == 200
    _print_link(first, tmp_path)
    second.status = "failed"
    session.record_failure(second, "moderated", "Google Flow refused the clip: prominent person")
    session.runtime(second.id)["retries"] = 2
    # the chain-order hold dropped clip 3 when clip 2 failed
    session.set_batch_targets(sid, set())
    assert third.id not in session.batch_targets(sid)

    # a live op cannot be skipped — only a failed one
    assert client.post(f"/api/director/ops/{third.id}/skip").status_code == 409

    res = client.post(f"/api/director/ops/{second.id}/skip")
    assert res.status_code == 200
    body = res.json()
    assert body["op"]["status"] == "discarded"
    assert body["label"] == "clip 2"
    assert body["retargeted"] == [third.id]
    assert body["run_plan"]["assemble"] is True
    assert "skipped" in body["reply"] and "leaves it out" in body["reply"]
    assert "prominent person" in second.rationale
    assert session.runtime(second.id)["skipped"]["reason"].startswith("Google Flow refused")
    # the chain is no longer broken: clip 3 is offered to the submitter and
    # opens from clip 1's frame — the last shot that printed
    assert second.id not in session.batch_targets(sid)
    assert [op.id for op in session.ready_ops(sid)] == [third.id]
    assert session.previous_completed_clip_op(sid, third).id == first.id
    assert session.drain_held(sid) == []
    # the cut: clip 2 is a decision, never a gap
    _print_link(third, tmp_path)
    assert [o.id for o in session.completed_clip_ops(sid)] == [first.id, third.id]
    assert app_module._cut_gaps(sid) == ([], [], [])
    assert app_module._skipped_labels(sid) == ["clip 2/3"]
    # the room stops nagging to resume a session whose only failure was skipped
    assert all(row["session_id"] != sid for row in session.resumable_sessions())
    # the decision survives a restart
    session.persist(sid)
    session.reset()
    session.rehydrate_from_vault()
    restored = session.get_op(second.id)
    assert restored.status == "discarded"
    assert session.runtime(restored.id)["skipped"]["reason"].startswith("Google Flow refused")
    assert app_module._cut_gaps(sid)[0] == []


def test_a_printed_link_that_failed_its_look_review_can_be_skipped(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(app_module, "_kick_submitter", lambda _sid: None)
    sid = _sid()
    proposal = session.propose(sid, ANIMATED, provider="google-flow", duration_s=24)
    first, second, third = proposal.ops
    _print_link(first, tmp_path)
    _print_link(second, tmp_path)
    session.register_review(
        second.id,
        review_mod.ClipReview(
            id="rev-bad", clip_id=second.id, op_id=second.id, verdict="fail",
            families={"continuity": "fail"}, issues=["the clip ignored its opening frame"],
        ),
    )
    assert not session.usable_take(second)
    client = TestClient(app_module.app)
    body = client.post(f"/api/director/ops/{second.id}/skip").json()
    assert body["op"]["status"] == "discarded"
    assert "look review failed: the clip ignored its opening frame" in second.rationale
    assert body["retargeted"] == [third.id]
    # the rejected print is out of the cut list; clip 3 bridges from clip 1
    assert [o.id for o in session.completed_clip_ops(sid)] == [first.id]
    assert session.previous_completed_clip_op(sid, third).id == first.id
    # once skipped, it cannot be skipped (or retried) again
    assert client.post(f"/api/director/ops/{second.id}/skip").status_code == 409
    assert client.post(f"/api/director/ops/{second.id}/retry").status_code == 409


def test_scripted_skips_follow_the_latest_take_per_clip() -> None:
    sid = _sid()
    proposal = session.propose(sid, ANIMATED, provider="google-flow", duration_s=24)
    first, second, third = proposal.ops
    first.clip_id, second.clip_id, third.clip_id = "s1-c1", "s1-c2", "s1-c2"
    second.status = "failed"
    session.skip_op(second, "gave up")
    # a later take of the same clip un-skips it
    assert session.skipped_clip_ids(sid) == set()
    third.status = "failed"
    session.skip_op(third, "gave up again")
    assert session.skipped_clip_ids(sid) == {"s1-c2"}
    assert second.id not in session.batch_targets(sid) and third.id not in session.batch_targets(sid)


def test_retry_all_re_queues_every_failed_clip_from_the_strip(monkeypatch, tmp_path) -> None:
    """One press at the bottom of the room instead of a Retry on each card:
    every failed clip of the film re-queues in shot order at its locked
    price; a clip the ladder refuses stays held with the reason."""
    monkeypatch.setattr(app_module, "_kick_submitter", lambda _sid: None)
    sid = _sid()
    proposal = session.propose(sid, ANIMATED, provider="google-flow", duration_s=24)
    first, second, third = proposal.ops
    client = TestClient(app_module.app)
    assert client.post(
        f"/api/director/sessions/{sid}/run_batch",
        json={"op_ids": [op.id for op in proposal.ops], "assemble": True},
    ).status_code == 200
    _print_link(first, tmp_path)
    for op in (second, third):
        op.status = "failed"
        session.record_failure(op, "provider_error", "Google Flow returned no media")
    session.set_batch_targets(sid, set())

    quote = client.get(f"/api/director/sessions/{sid}/failed").json()
    assert [row["op_id"] for row in quote["ops"]] == [second.id, third.id]
    assert quote["count"] == 2
    assert quote["total_usd_micros"] == sum(
        op.estimate.total_usd_micros for op in (second, third)
    ) > 0

    res = client.post(f"/api/director/sessions/{sid}/retry-all")
    assert res.status_code == 200
    body = res.json()
    assert [r["op_id"] for r in body["retried"]] == [second.id, third.id]
    assert body["held"] == []
    assert body["total_usd_micros"] == quote["total_usd_micros"]
    assert body["run_plan"]["assemble"] is True
    assert body["reply"].startswith("2 failed clips re-queued at their locked price")
    assert second.status == "proposed" and third.status == "proposed"
    assert {second.id, third.id} <= session.batch_targets(sid)
    assert session.runtime(second.id)["retries"] == 1

    # nothing failed → nothing spent, and the room says so
    again = client.post(f"/api/director/sessions/{sid}/retry-all").json()
    assert again["retried"] == [] and again["total_usd_micros"] == 0
    assert again["reply"].startswith("Nothing is failed")


def test_retry_all_holds_a_clip_whose_ladder_is_spent_and_rolls_the_rest(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(app_module, "_kick_submitter", lambda _sid: None)
    sid = _sid()
    proposal = session.propose(sid, ANIMATED, provider="google-flow", duration_s=24)
    first, second, third = proposal.ops
    client = TestClient(app_module.app)
    _print_link(first, tmp_path)
    for op in (second, third):
        op.status = "failed"
        session.record_failure(op, "moderated", "Google Flow refused the clip: prominent person")
        session.runtime(op.id)["retries"] = 2
    session.runtime(third.id)["frame_redraws"] = session.FRAME_REDRAW_LIMIT
    # the single-card Retry refuses clip 3 with a 409 — Retry all reports it
    assert client.post(f"/api/director/ops/{third.id}/retry").status_code == 409
    body = client.post(f"/api/director/sessions/{sid}/retry-all").json()
    assert [r["op_id"] for r in body["retried"]] == [second.id]
    assert [h["op_id"] for h in body["held"]] == [third.id]
    assert "opening frame was redrawn" in body["held"][0]["detail"]
    assert "Still held:" in body["reply"] and "clip 3" in body["reply"] or third.id in body["reply"]
    assert second.status == "proposed" and third.status == "failed"
