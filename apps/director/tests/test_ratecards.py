"""Rate cards: versioned provider pricing loaded from config, never hardcoded in code."""

from datetime import UTC, date, datetime

from diony_director import ratecards


def test_packaged_grok_card_was_fetched_on_2026_10_13() -> None:
    """The 2026-08-31 card went stale on 2026-10-15 and would have refused every
    Grok run. This pins the refresh so the next fetch updates the date on purpose."""
    grok = ratecards.load_files()["grok"]
    assert grok.fetched_at == date(2026, 10, 13)
    assert grok.stale_after_days == 45
    assert grok.source == "https://docs.x.ai/developers/pricing"
    assert "images/generation" in grok.official_source
    assert ratecards.is_stale(grok, now=datetime(2026, 10, 13, tzinfo=UTC)) is False


def test_grok_image_rates_follow_the_published_quality_tiers() -> None:
    files = ratecards.load_files()
    low_1k = ratecards.resolve(files, "grok", "grok-imagine-image-2.0", "generation:1k:low")
    low_2k = ratecards.resolve(files, "grok", "grok-imagine-image-2.0", "generation:2k:low")
    med_2k = ratecards.resolve(files, "grok", "grok-imagine-image-2.0", "generation:2k:medium")
    assert low_1k.usd_micros_per_unit == 40_000
    assert low_2k.usd_micros_per_unit == 60_000
    assert med_2k.usd_micros_per_unit == 80_000
    video = ratecards.resolve(files, "grok", "grok-imagine-video", "video_input_second")
    assert video.usd_micros_per_unit == 10_000
    try:
        ratecards.resolve(files, "grok", "grok-imagine-video-1.5", "video_input_second")
    except ratecards.RateCardMissing as err:
        assert "video_input_second" in str(err)
    else:  # pragma: no cover
        raise AssertionError("1.5 publishes no video-input rate")


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
    fresh_now = datetime.combine(grok.fetched_at, datetime.min.time(), tzinfo=UTC)
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


def test_flow_stills_are_included_in_the_subscription() -> None:
    files = ratecards.load_files()
    for model in ("nano-banana-2-lite", "nano-banana-2"):
        rate = ratecards.resolve(files, "google-flow", model, "generation")
        assert rate.internal_credits_per_unit == 0
        assert rate.usd_micros_per_unit == 0


def test_flow_omni_edits_cost_forty_credits_and_carry_no_ultra_discount(monkeypatch) -> None:
    files = ratecards.load_files()
    pro = ratecards.resolve(files, "google-flow", "omni-flash", "v2v:720p")
    assert pro.internal_credits_per_unit == 40
    monkeypatch.setenv("DIONY_FLOW_PLAN", "ultra-199")
    ultra = ratecards.resolve(files, "google-flow", "omni-flash", "generation:720p:10s")
    assert ultra.internal_credits_per_unit == 15


def test_lite_lower_priority_is_free_only_on_the_top_ultra_plan(monkeypatch) -> None:
    files = ratecards.load_files()
    pro = ratecards.resolve(files, "google-flow", "veo-3.1-lite-low-priority", "generation")
    assert pro.internal_credits_per_unit == 10
    monkeypatch.setenv("DIONY_FLOW_PLAN", "ultra-99")
    mid = ratecards.resolve(files, "google-flow", "veo-3.1-lite-low-priority", "generation")
    assert mid.internal_credits_per_unit == 5
    monkeypatch.setenv("DIONY_FLOW_PLAN", "ultra-199")
    top = ratecards.resolve(files, "google-flow", "veo-3.1-lite-low-priority", "generation")
    assert top.internal_credits_per_unit == 0
    assert top.usd_micros_per_unit == 0


def test_plan_prices_per_credit_follow_the_published_plans() -> None:
    files = ratecards.load_files()
    flow = files["google-flow"]
    for plan, monthly_usd in flow.plans_usd_per_month.items():
        credits = flow.plans_monthly_credits[plan]
        expected = round(monthly_usd * 1_000_000 / credits)
        assert abs(flow.plans_usd_micros_per_credit[plan] - expected) <= 1, plan
