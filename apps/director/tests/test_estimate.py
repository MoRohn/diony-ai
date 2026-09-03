"""Cost estimates: computed from rate cards, provider-legal durations, no float money."""

import pytest

from diony_contracts.director import CloudGenerationMode, CloudProvider
from diony_director import estimate


def test_grok_six_seconds_at_720p_is_42_cents() -> None:
    est = estimate.estimate_op(
        provider=CloudProvider.GROK,
        model="grok-imagine-video",
        mode=CloudGenerationMode.T2V,
        duration_s=6,
        resolution="720p",
    )
    assert est.total_usd_micros == 420_000
    assert len(est.lines) == 1
    assert est.lines[0].rate_card_id


def test_grok_15_image_input_is_priced_separately() -> None:
    est = estimate.estimate_op(
        provider=CloudProvider.GROK,
        model="grok-imagine-video-1.5",
        mode=CloudGenerationMode.I2V,
        duration_s=6,
        resolution="720p",
        image_inputs=1,
    )
    assert est.total_usd_micros == 6 * 140_000 + 10_000
    labels = [line.label for line in est.lines]
    assert any("image" in label for label in labels)


def test_flow_omni_8s_720p_includes_the_captcha_line() -> None:
    est = estimate.estimate_op(
        provider=CloudProvider.GOOGLE_FLOW,
        model="omni-flash",
        mode=CloudGenerationMode.T2V,
        duration_s=8,
        resolution="720p",
    )
    assert est.total_usd_micros == 12 * 19_990 + 3_000
    assert sum(line.internal_credits for line in est.lines) == 12


def test_veo_durations_quantize_up_to_the_plan_aware_grid(monkeypatch) -> None:
    # 4/6s Veo is Ultra-only (useapi docs); the conftest plan is "pro" → 8s only.
    assert estimate.quantize_duration(CloudProvider.GOOGLE_FLOW, "veo-3.1-fast", 5) == 8
    assert estimate.quantize_duration(CloudProvider.GOOGLE_FLOW, "veo-3.1-fast", 8.5) == 8
    monkeypatch.setenv("DIONY_FLOW_PLAN", "ultra-99")
    assert estimate.quantize_duration(CloudProvider.GOOGLE_FLOW, "veo-3.1-fast", 5) == 6
    assert estimate.quantize_duration(CloudProvider.GOOGLE_FLOW, "omni-flash", 9) == 10
    assert estimate.quantize_duration(CloudProvider.GROK, "grok-imagine-video", 20) == 15
    assert estimate.quantize_duration(CloudProvider.GROK, "grok-imagine-video", 0.4) == 1


def test_unknown_model_refuses_to_estimate() -> None:
    with pytest.raises(estimate.EstimateError):
        estimate.estimate_op(
            provider=CloudProvider.GROK,
            model="grok-imagine-video-9",
            mode=CloudGenerationMode.T2V,
            duration_s=6,
        )


def test_upscale_is_free_at_1080p_and_50_credits_at_4k() -> None:
    # useapi.net docs (verified 2026-09-03): 720p/1080p upscales spend no
    # credits on any paid plan; 4K costs 50 credits and needs Ultra. The
    # captcha solve still rides every Flow call.
    finish = estimate.estimate_op(
        provider=CloudProvider.GOOGLE_FLOW,
        model="video-upscale",
        mode=CloudGenerationMode.UPSCALE,
        duration_s=8,
        resolution="1080p",
    )
    assert [line.label for line in finish.lines] == ["server-side upscale · 1080p", "captcha solve"]
    assert finish.lines[0].internal_credits == 0
    assert finish.lines[0].unit == "generation:1080p"
    assert finish.total_usd_micros == 3000
    ultra = estimate.estimate_op(
        provider=CloudProvider.GOOGLE_FLOW,
        model="video-upscale",
        mode=CloudGenerationMode.UPSCALE,
        duration_s=8,
        resolution="4K",
    )
    assert ultra.lines[0].internal_credits == 50
    assert ultra.total_usd_micros == 50 * 19_990 + 3000  # pro plan in conftest
