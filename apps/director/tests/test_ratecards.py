"""Rate cards: versioned provider pricing loaded from config, never hardcoded in code."""

from datetime import date, datetime, timezone

from diony_director import ratecards


def test_packaged_grok_card_carries_the_720p_second_rate() -> None:
    files = ratecards.load_files()
    rate = ratecards.resolve(files, "grok", "grok-imagine-video", "second:720p")
    assert rate.usd_micros_per_unit == 70_000
    assert rate.internal_credits_per_unit == 0
    assert rate.rate_card_id


def test_flow_veo_fast_costs_twenty_credits_at_the_pro_rate() -> None:
    files = ratecards.load_files()
    rate = ratecards.resolve(files, "google-flow", "veo-3.1-fast", "generation")
    assert rate.internal_credits_per_unit == 20
    assert rate.usd_micros_per_unit == 20 * 19_990


def test_ultra_plans_halve_veo_credits(monkeypatch) -> None:
    monkeypatch.setenv("DIONY_FLOW_PLAN", "ultra-99")
    files = ratecards.load_files()
    rate = ratecards.resolve(files, "google-flow", "veo-3.1-fast", "generation")
    assert rate.internal_credits_per_unit == 10
    assert rate.usd_micros_per_unit == 10 * 9_999


def test_unknown_unit_raises_a_named_error() -> None:
    files = ratecards.load_files()
    try:
        ratecards.resolve(files, "grok", "grok-imagine-video", "second:4k")
    except ratecards.RateCardMissing as err:
        assert "second:4k" in str(err)
    else:  # pragma: no cover
        raise AssertionError("expected RateCardMissing")


def test_staleness_is_computed_from_fetched_at() -> None:
    files = ratecards.load_files()
    grok = files["grok"]
    fresh_now = datetime.combine(grok.fetched_at, datetime.min.time(), tzinfo=timezone.utc)
    assert ratecards.is_stale(grok, now=fresh_now) is False
    old = grok.model_copy(update={"fetched_at": date(2025, 1, 1)})
    assert ratecards.is_stale(old, now=fresh_now) is True


def test_contract_rate_cards_are_valid_and_matchable() -> None:
    cards = ratecards.contract_rate_cards("local-workspace")
    assert cards, "expected seedable CostRateCard contracts"
    by_key = {(c.provider_id, c.model, c.unit): c for c in cards}
    grok = by_key[("grok", "grok-imagine-video", "second:720p")]
    assert grok.cost_usd_micros_per_unit == 70_000
    assert grok.active is True
    flow = by_key[("google-flow", "veo-3.1-quality", "generation")]
    assert flow.internal_credits_per_unit == 100
