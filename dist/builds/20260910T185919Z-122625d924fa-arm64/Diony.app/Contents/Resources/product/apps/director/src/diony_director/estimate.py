"""Locked cost estimates for Director operations.

Durations quantize UP to the provider grid (trimming surplus in assembly is
free; a clipped word is a retake), and every line carries the rate card that
priced it so the ledger can reconcile actuals against the same source.
"""

from __future__ import annotations

import math
import uuid

from diony_contracts.director import (
    CloudGenerationMode,
    CloudProvider,
    CostEstimate,
    CostEstimateLine,
)
from diony_director import ratecards

GROK_MIN_S = 1
GROK_MAX_S = 15
VEO_DURATIONS = (4, 6, 8)
OMNI_DURATIONS = (4, 6, 8, 10)

GROK_MODELS = {"grok-imagine-video", "grok-imagine-video-1.5"}
VEO_MODELS = {"veo-3.1-lite", "veo-3.1-lite-low-priority", "veo-3.1-fast", "veo-3.1-quality"}
OMNI_MODELS = {"omni-flash"}


class EstimateError(ValueError):
    pass


def veo_grid() -> tuple[int, ...]:
    """The legal Veo durations for the CONFIGURED plan: 4/6s are Ultra-only
    (verified against useapi docs) — a non-Ultra plan books 8s or nothing."""
    plan = str(ratecards.flow_plan() or "").lower()
    return VEO_DURATIONS if plan.startswith("ultra") else (8,)


def quantize_duration(provider: CloudProvider, model: str, seconds: float) -> int:
    if seconds <= 0:
        seconds = GROK_MIN_S
    if provider is CloudProvider.GROK:
        return max(GROK_MIN_S, min(GROK_MAX_S, math.ceil(seconds)))
    grid = OMNI_DURATIONS if model in OMNI_MODELS else veo_grid()
    for value in grid:
        if seconds <= value:
            return value
    return grid[-1]


def _flow_unit(model: str, duration: int, resolution: str, mode: CloudGenerationMode) -> str:
    if model in OMNI_MODELS:
        if mode is CloudGenerationMode.V2V_EDIT:
            return f"v2v:{resolution}"
        return f"generation:{resolution}:{duration}s"
    return "generation"


def image_inputs_for(clip, provider: CloudProvider | None = None) -> int:
    """Planned image-input count for a Grok clip: a still-lock feeds one frame;
    a reference-locked clip with cast feeds the 3-ref pack. Flow meters credits
    flat, so only Grok books image inputs."""
    if (provider or clip.provider) is not CloudProvider.GROK:
        return 0
    if clip.continuity.lock_recipe == "still":
        return 1
    if clip.continuity.character_packs:
        return 3
    return 0


UPSCALE_RESOLUTIONS = ("720p", "1080p", "4K")


def upscale_rate(files: dict, resolution: str) -> ratecards.ResolvedRate:
    """The rate for one server-side upscale to `resolution`: the per-resolution
    unit first, the legacy flat unit as a fallback for overridden card dirs."""
    try:
        return ratecards.resolve(files, "google-flow", "video-upscale", f"generation:{resolution}")
    except ratecards.RateCardMissing:
        return ratecards.resolve(files, "google-flow", "video-upscale", "generation")


def estimate_op(
    *,
    provider: CloudProvider,
    model: str,
    mode: CloudGenerationMode,
    duration_s: float,
    resolution: str = "720p",
    image_inputs: int = 0,
    video_input_s: int = 0,
    workspace_id: str = "local-workspace",
    project_id: str = "",
) -> CostEstimate:
    files = ratecards.load_files()
    duration = quantize_duration(provider, model, duration_s)
    lines: list[CostEstimateLine] = []
    stale = False

    try:
        if mode is CloudGenerationMode.UPSCALE:
            # POST /videos/upscale is priced per target resolution (useapi.net
            # docs, verified 2026-09-03: 720p and 1080p are free on any paid
            # Google AI plan, 4K costs 50 credits and needs Ultra). An older
            # card that still carries the flat "generation" unit resolves too.
            rate = upscale_rate(files, resolution)
            lines = [
                CostEstimateLine(
                    label=f"server-side upscale · {resolution}",
                    provider_id="google-flow",
                    model="video-upscale",
                    unit=rate.unit,
                    unit_rate_usd_micros=rate.usd_micros_per_unit,
                    quantity_microunits=1_000_000,
                    internal_credits=rate.internal_credits_per_unit,
                    usd_micros=rate.usd_micros_per_unit,
                    rate_card_id=rate.rate_card_id,
                )
            ]
            captcha = ratecards.captcha_usd_micros(files)
            if captcha:
                # the upscale call solves a captcha like every Flow request —
                # a "free" finish still carries the solve on the ledger
                lines.append(
                    CostEstimateLine(
                        label="captcha solve",
                        provider_id="google-flow",
                        model="captcha",
                        unit="solve",
                        unit_rate_usd_micros=captcha,
                        quantity_microunits=1_000_000,
                        usd_micros=captcha,
                    )
                )
            return CostEstimate(
                id=f"est-{uuid.uuid4().hex[:12]}",
                workspace_id=workspace_id,
                project_id=project_id,
                lines=lines,
                rate_cards_stale=rate.stale,
            )
        if mode is CloudGenerationMode.IMAGE:
            if provider is CloudProvider.GROK:
                unit = f"generation:{resolution}" if resolution in {"1k", "2k"} else "generation:2k"
            else:
                unit = "generation"
            rate = ratecards.resolve(files, provider.value, model, unit)
            count = max(1, int(duration_s) or 1)
            lines = [
                CostEstimateLine(
                    label=f"{model} still × {count}",
                    provider_id=provider.value,
                    model=model,
                    unit=rate.unit,
                    unit_rate_usd_micros=rate.usd_micros_per_unit,
                    quantity_microunits=count * 1_000_000,
                    internal_credits=count * rate.internal_credits_per_unit,
                    usd_micros=count * rate.usd_micros_per_unit,
                    rate_card_id=rate.rate_card_id,
                )
            ]
            if provider is CloudProvider.GOOGLE_FLOW:
                # Flow stills are included in the Google AI plan (no credits),
                # but every Flow request still solves a captcha — the solve is
                # the still's whole price and belongs on the ledger.
                captcha = ratecards.captcha_usd_micros(files)
                if captcha:
                    lines.append(
                        CostEstimateLine(
                            label=f"captcha solve × {count}",
                            provider_id=provider.value,
                            model="captcha",
                            unit="solve",
                            unit_rate_usd_micros=captcha,
                            quantity_microunits=count * 1_000_000,
                            usd_micros=count * captcha,
                        )
                    )
            return CostEstimate(
                id=f"est-{uuid.uuid4().hex[:12]}",
                workspace_id=workspace_id,
                project_id=project_id,
                lines=lines,
                rate_cards_stale=rate.stale,
            )
        if provider is CloudProvider.GROK:
            if model not in GROK_MODELS:
                raise EstimateError(f"unknown Grok model {model!r}")
            rate = ratecards.resolve(files, provider.value, model, f"second:{resolution}")
            stale = stale or rate.stale
            lines.append(
                CostEstimateLine(
                    label=f"video seconds · {resolution}",
                    provider_id=provider.value,
                    model=model,
                    unit=rate.unit,
                    unit_rate_usd_micros=rate.usd_micros_per_unit,
                    quantity_microunits=duration * 1_000_000,
                    usd_micros=duration * rate.usd_micros_per_unit,
                    rate_card_id=rate.rate_card_id,
                )
            )
            if image_inputs:
                img = ratecards.resolve(files, provider.value, model, "image_input")
                stale = stale or img.stale
                lines.append(
                    CostEstimateLine(
                        label=f"image input × {image_inputs}",
                        provider_id=provider.value,
                        model=model,
                        unit=img.unit,
                        unit_rate_usd_micros=img.usd_micros_per_unit,
                        quantity_microunits=image_inputs * 1_000_000,
                        usd_micros=image_inputs * img.usd_micros_per_unit,
                        rate_card_id=img.rate_card_id,
                    )
                )
            if video_input_s:
                vid = ratecards.resolve(files, provider.value, model, "video_input_second")
                stale = stale or vid.stale
                lines.append(
                    CostEstimateLine(
                        label=f"video input · {video_input_s}s",
                        provider_id=provider.value,
                        model=model,
                        unit=vid.unit,
                        unit_rate_usd_micros=vid.usd_micros_per_unit,
                        quantity_microunits=video_input_s * 1_000_000,
                        usd_micros=video_input_s * vid.usd_micros_per_unit,
                        rate_card_id=vid.rate_card_id,
                    )
                )
        elif provider is CloudProvider.GOOGLE_FLOW:
            if model not in VEO_MODELS | OMNI_MODELS:
                raise EstimateError(f"unknown Flow model {model!r}")
            unit = _flow_unit(model, duration, resolution, mode)
            rate = ratecards.resolve(files, provider.value, model, unit)
            stale = stale or rate.stale
            lines.append(
                CostEstimateLine(
                    label=f"{model} generation · {duration}s",
                    provider_id=provider.value,
                    model=model,
                    unit=rate.unit,
                    unit_rate_usd_micros=rate.usd_micros_per_unit,
                    quantity_microunits=1_000_000,
                    internal_credits=rate.internal_credits_per_unit,
                    usd_micros=rate.usd_micros_per_unit,
                    rate_card_id=rate.rate_card_id,
                )
            )
            captcha = ratecards.captcha_usd_micros(files)
            if captcha:
                lines.append(
                    CostEstimateLine(
                        label="captcha solve",
                        provider_id=provider.value,
                        model="captcha",
                        unit="solve",
                        unit_rate_usd_micros=captcha,
                        quantity_microunits=1_000_000,
                        usd_micros=captcha,
                    )
                )
        else:  # pragma: no cover - CloudProvider is closed
            raise EstimateError(f"unknown provider {provider!r}")
    except ratecards.RateCardMissing as err:
        raise EstimateError(str(err)) from err

    return CostEstimate(
        id=f"est-{uuid.uuid4().hex[:12]}",
        workspace_id=workspace_id,
        project_id=project_id,
        lines=lines,
        rate_cards_stale=stale,
    )
