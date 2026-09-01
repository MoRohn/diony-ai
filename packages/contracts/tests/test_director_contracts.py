"""Contracts for the Director service (Blueprint 25/26): ops and locked cost estimates."""

import pytest
from pydantic import ValidationError

from diony_contracts.director import (
    CloudGenerationMode,
    CloudProvider,
    CostEstimate,
    CostEstimateLine,
    DirectorOp,
    DirectorOpType,
    EngineMode,
)


def _line(usd: int, credits: int = 0) -> CostEstimateLine:
    return CostEstimateLine(
        label="video seconds",
        provider_id="grok",
        model="grok-imagine-video",
        unit="second:720p",
        unit_rate_usd_micros=70_000,
        quantity_microunits=6_000_000,
        internal_credits=credits,
        usd_micros=usd,
    )


def _estimate(total: int = 0, lines: list[CostEstimateLine] | None = None) -> CostEstimate:
    return CostEstimate(
        id="est-1",
        workspace_id="local-workspace",
        lines=lines if lines is not None else [_line(420_000)],
        total_usd_micros=total,
    )


def test_cost_estimate_fills_total_from_lines_and_allowance() -> None:
    estimate = CostEstimate(
        id="est-1",
        workspace_id="local-workspace",
        lines=[_line(420_000), _line(10_000)],
        revision_allowance_usd_micros=172_000,
    )
    assert estimate.total_usd_micros == 602_000


def test_cost_estimate_refuses_a_total_that_disagrees_with_its_lines() -> None:
    with pytest.raises(ValidationError):
        _estimate(total=999_999)


def test_priced_director_op_requires_a_locked_estimate() -> None:
    with pytest.raises(ValidationError):
        DirectorOp(
            id="op-1",
            session_id="s-1",
            op=DirectorOpType.GENERATE,
            provider=CloudProvider.GROK,
            model="grok-imagine-video",
            mode=CloudGenerationMode.T2V,
            prompt="a truck at dusk",
            duration_s=6,
        )


def test_priced_director_op_requires_provider_and_model() -> None:
    with pytest.raises(ValidationError):
        DirectorOp(
            id="op-1",
            session_id="s-1",
            op=DirectorOpType.GENERATE,
            prompt="a truck at dusk",
            duration_s=6,
            estimate=_estimate(total=420_000),
        )


def test_generate_op_with_estimate_is_valid_and_starts_proposed() -> None:
    op = DirectorOp(
        id="op-1",
        session_id="s-1",
        op=DirectorOpType.GENERATE,
        provider=CloudProvider.GROK,
        model="grok-imagine-video",
        mode=CloudGenerationMode.T2V,
        prompt="a truck at dusk, slow push-in",
        duration_s=6,
        estimate=_estimate(total=420_000),
    )
    assert op.status == "proposed"
    assert op.requires_confirmation is True


def test_unpriced_ops_do_not_require_an_estimate() -> None:
    op = DirectorOp(
        id="op-2",
        session_id="s-1",
        op=DirectorOpType.ACCEPT,
        shot_id="sh-010",
    )
    assert op.estimate is None


def test_engine_mode_values() -> None:
    assert EngineMode.CLOUD.value == "cloud"
    assert EngineMode.LOCAL_LEGACY.value == "local_legacy"
