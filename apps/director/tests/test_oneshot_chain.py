"""The uncapped one-shot: a booked runtime past the route's single-clip
length becomes a CHAIN of sequential clips — split by the master derivation
(brain or template), never by the screenplay stage. Each link is priced, the
chain is sequenced, and every later link opens from its predecessor's final
frame."""

from __future__ import annotations

import json
import uuid

import pytest

from diony_contracts.director import CloudProvider
from diony_director import master_prompt, screenwriter, session


def _sid() -> str:
    return f"s-test-{uuid.uuid4().hex[:8]}"


def test_grok_plans_split_evenly_at_the_cap() -> None:
    assert session._clip_plan(CloudProvider.GROK, "grok-imagine-video-1.5", 90) == [15] * 6
    assert session._clip_plan(CloudProvider.GROK, "grok-imagine-video-1.5", 12) == []
    plan = session._clip_plan(CloudProvider.GROK, "grok-imagine-video-1.5", 100)
    assert sum(plan) == 100
    assert max(plan) <= 15 and min(plan) >= 14  # even links, never a stub tail


def test_flow_plans_book_grid_units() -> None:
    # non-Ultra plan: the 8s grid only — the remainder quantizes UP
    plan = session._clip_plan(CloudProvider.GOOGLE_FLOW, "veo-3.1-fast", 20)
    assert plan == [8, 8, 8]
    assert session._clip_plan(CloudProvider.GOOGLE_FLOW, "veo-3.1-fast", 8) == []


def test_a_long_one_shot_becomes_a_priced_sequenced_chain() -> None:
    sid = _sid()
    proposal = session.propose(
        sid, "a lighthouse keeper crosses the lamp room and opens the iron door", duration_s=90
    )
    assert len(proposal.ops) == 6
    assert proposal.op.id == proposal.ops[0].id
    assert all(op.status == "proposed" for op in proposal.ops)
    assert all(op.estimate is not None for op in proposal.ops)
    assert "chained one-shot" in proposal.ops[2].rationale
    assert "chained one-shot" in proposal.reply
    # the chain rides the directed-sequencing rails
    seq = session.sequence_for(sid)
    chains = [c for key, c in seq["scenes"].items() if key.startswith("oneshot-")]
    assert chains == [[op.id for op in proposal.ops]]
    # the split is on the master artifact, one per link
    master = session.master_for_op(proposal.ops[0].id)
    assert master is not None and len(master.clips) == 6
    assert master.target_duration_s == 90


def test_chain_links_carry_their_own_segment_prompts() -> None:
    sid = _sid()
    proposal = session.propose(sid, "a keeper crosses the lamp room", duration_s=45)
    prompts = [op.prompt for op in proposal.ops]
    assert len(prompts) == 3
    assert len(set(prompts)) == 3  # each link is its own segment, not a copy
    assert "The clip ends with" in prompts[-1]
    assert "The clip ends with" not in prompts[0]
    assert "Continue the same continuous action" in prompts[1]


def test_the_chain_order_guard_refuses_out_of_order_runs() -> None:
    from diony_director import app as director_app

    sid = _sid()
    proposal = session.propose(sid, "a keeper crosses the lamp room", duration_s=40)
    # clip 1 passes the guard; clip 2 cannot roll before clip 1's frame exists
    director_app._guard_chain_order(proposal.ops[0])
    with pytest.raises(director_app.HTTPException) as err:
        director_app._guard_chain_order(proposal.ops[1])
    assert err.value.status_code == 409
    assert "chained one-shot" in err.value.detail


def test_template_split_restates_continuity_and_paces_each_link() -> None:
    master = master_prompt.derive(
        "a keeper crosses the lamp room at night", target_duration_s=24, clip_plan=[8, 8, 8]
    )
    assert len(master.clips) == 3
    for index, clip in enumerate(master.clips):
        assert clip["duration_s"] == 8
        assert "Pace the action to play in about 8 seconds." in clip["prompt"]
        if index:
            assert "Continue the same continuous action" in clip["prompt"]
    assert "The clip ends with" in master.clips[-1]["prompt"]
    assert all("The clip ends with" not in c["prompt"] for c in master.clips[:-1])


def test_brain_clip_split_is_parsed_and_durations_are_booked(monkeypatch) -> None:
    from master_brain_fakes import master_brain_body

    monkeypatch.setenv("DIONY_DIRECTOR_BRAIN_URL", "http://127.0.0.1:9999")
    body = master_brain_body(
        clips=[
            {"prompt": "clip one prose", "duration_s": 99},
            {"prompt": "clip two prose", "duration_s": 1},
        ]
    )

    captured: dict = {}

    class _Response:
        status_code = 200

        def json(self) -> dict:
            return {"choices": [{"message": {"content": json.dumps(body)}}]}

    def fake_post(url, **kwargs):
        captured["json"] = kwargs.get("json")
        return _Response()

    monkeypatch.setattr(screenwriter.httpx, "post", fake_post)
    master = master_prompt.derive("a keeper waits", target_duration_s=16, clip_plan=[8, 8])
    assert [c["prompt"] for c in master.clips] == ["clip one prose", "clip two prose"]
    # the BOOKED durations are law; the brain's numbers are advisory
    assert [c["duration_s"] for c in master.clips] == [8, 8]
    assert "CLIP DELIVERY" in captured["json"]["messages"][0]["content"]


def test_a_brain_without_clips_is_a_named_failure_never_half_a_chain(monkeypatch) -> None:
    from master_brain_fakes import master_brain_body

    monkeypatch.setenv("DIONY_DIRECTOR_BRAIN_URL", "http://127.0.0.1:9999")
    body = master_brain_body()  # no clips key at all

    class _Response:
        status_code = 200

        def json(self) -> dict:
            return {"choices": [{"message": {"content": json.dumps(body)}}]}

    monkeypatch.setattr(screenwriter.httpx, "post", lambda *a, **k: _Response())
    with pytest.raises(master_prompt.MasterUnavailable) as err:
        master_prompt.derive("a keeper waits", target_duration_s=16, clip_plan=[8, 8])
    assert "clip" in str(err.value)


def test_set_sequence_preserves_oneshot_chains() -> None:
    sid = _sid()
    session._register_oneshot_chain(sid, ["op-a", "op-b"])
    session.set_sequence(sid, [], {"sc-1": ["op-x", "op-y"]})
    seq = session.sequence_for(sid)
    assert seq["scenes"]["oneshot-op-a"] == ["op-a", "op-b"]
    assert seq["scenes"]["sc-1"] == ["op-x", "op-y"]


def test_a_short_one_shot_stays_a_single_clip() -> None:
    sid = _sid()
    proposal = session.propose(sid, "a keeper waits by the lamp", duration_s=8)
    assert proposal.ops == []
    assert proposal.op.duration_s == 8


def test_a_failed_link_stops_the_chain_once_and_loudly() -> None:
    """A failed link BREAKS the chain at that point: later links leave the
    batch (they could only open from the missing frame), the hold is logged
    exactly once against the FAILED link — not once per wave per successor,
    the 12-bubble spam of 2026-09-02 — and a retry re-arms the chain."""
    from diony_director import app as director_app

    sid = _sid()
    proposal = session.propose(sid, "a keeper crosses the lamp room", duration_s=40)
    first, second = proposal.ops[0], proposal.ops[1]
    first.status = "failed"
    session.set_batch_targets(sid, {second.id})
    submitted, held = director_app._submit_ready(sid)
    assert submitted == []
    assert held == []  # never offered to the submitter at all
    drained = session.drain_held(sid)
    assert [h["op_id"] for h in drained] == [first.id]
    assert "clip 1 failed" in drained[0]["reason"]
    assert "Retry" in drained[0]["reason"]
    assert second.status == "proposed"
    assert second.id not in session.batch_targets(sid)
    # the hold logs once — a second wave stays quiet
    session.set_batch_targets(sid, {second.id})
    director_app._submit_ready(sid)
    assert session.drain_held(sid) == []
    # retrying the broken link re-arms the chain: link 1 is offered again
    first.status = "proposed"
    session.set_batch_targets(sid, {first.id, second.id})
    ready = [op.id for op in session.ready_ops(sid)]
    assert ready == [first.id]
