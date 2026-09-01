"""Guided planning: a raw idea is never sent raw, and every proposal carries its price."""

from diony_contracts.director import CloudProvider, DirectorOpType
from diony_director import session


def test_auto_provider_defaults_to_grok_for_drafts() -> None:
    sid = session.create_session()
    proposal = session.propose(sid, "a dusty truck rolls into a desert town at dusk")
    op = proposal.op
    assert op.op is DirectorOpType.GENERATE
    assert op.provider is CloudProvider.GROK
    assert op.model == "grok-imagine-video-1.5"
    assert op.status == "proposed"


def test_the_raw_idea_is_never_the_compiled_prompt() -> None:
    sid = session.create_session()
    idea = "a dusty truck rolls into a desert town at dusk"
    proposal = session.propose(sid, idea)
    assert proposal.op.prompt != idea
    assert idea in proposal.op.prompt
    assert proposal.op.prompt_version == "v1"


def test_every_proposal_carries_a_locked_estimate_and_speaks_its_cost() -> None:
    sid = session.create_session()
    proposal = session.propose(sid, "neon diner at night", duration_s=6)
    assert proposal.op.estimate is not None
    assert proposal.op.estimate.total_usd_micros == 840_000
    assert "$0.84" in proposal.reply
    assert "total becomes" in proposal.reply


def test_flow_proposals_quantize_to_the_veo_grid() -> None:
    # 4/6s Veo bookings are Ultra-plan-only (verified against useapi docs);
    # the conftest plan is "pro", so everything quantizes up to 8s.
    sid = session.create_session()
    proposal = session.propose(sid, "neon diner at night", provider="google-flow", duration_s=5)
    op = proposal.op
    assert op.provider is CloudProvider.GOOGLE_FLOW
    assert op.model == "veo-3.1-fast"
    assert op.duration_s == 8
    assert op.estimate is not None and op.estimate.total_usd_micros == 20 * 19_990 + 3_000


def test_ultra_plans_unlock_the_short_veo_durations(monkeypatch) -> None:
    monkeypatch.setenv("DIONY_FLOW_PLAN", "ultra-99")
    sid = session.create_session()
    proposal = session.propose(sid, "neon diner at night", provider="google-flow", duration_s=5)
    assert proposal.op.duration_s == 6
