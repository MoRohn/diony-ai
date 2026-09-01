"""Versioned provider rate cards.

Pricing lives in JSON documents (packaged defaults under
``diony_director/ratecards/``, overridable via ``DIONY_RATECARD_DIR``), never
in code. Each file names its source and fetch date; estimates go stale with
the file, and Flow pricing is always modeled from the user's declared plan —
it is never presented as Google's official meter.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from pydantic import BaseModel, Field

from diony_contracts.launch import CostRateCard, ProviderCapability

PACKAGED_DIR = Path(__file__).resolve().parent / "ratecards"

FLOW_PROVIDER_ID = "google-flow"
DEFAULT_FLOW_PLAN = "pro"


class RateCardMissing(LookupError):
    pass


class RateEntry(BaseModel):
    capability: str = "video"
    model: str
    unit: str
    usd_micros_per_unit: int = Field(default=0, ge=0)
    credits: int = Field(default=0, ge=0)
    credits_ultra: int | None = Field(default=None, ge=0)
    confidence: str = "documented"


class RateCardFile(BaseModel):
    provider_id: str
    label: str = ""
    pricing_basis: str = "metered"
    source: str
    fetched_at: date
    stale_after_days: int = Field(default=45, gt=0)
    plans_usd_micros_per_credit: dict[str, int] = Field(default_factory=dict)
    captcha_usd_micros_per_solve: int = Field(default=0, ge=0)
    cards: list[RateEntry] = Field(default_factory=list)


@dataclass(frozen=True)
class ResolvedRate:
    provider_id: str
    model: str
    unit: str
    usd_micros_per_unit: int
    internal_credits_per_unit: int
    rate_card_id: str
    pricing_basis: str
    source: str
    stale: bool


def ratecard_dir() -> Path:
    raw = os.environ.get("DIONY_RATECARD_DIR", "").strip()
    if raw:
        return Path(raw)
    return PACKAGED_DIR


def flow_plan() -> str:
    return os.environ.get("DIONY_FLOW_PLAN", DEFAULT_FLOW_PLAN).strip().lower() or DEFAULT_FLOW_PLAN


def load_files(directory: Path | None = None) -> dict[str, RateCardFile]:
    root = directory or ratecard_dir()
    files: dict[str, RateCardFile] = {}
    for path in sorted(root.glob("*.json")):
        card_file = RateCardFile.model_validate(json.loads(path.read_text()))
        files[card_file.provider_id] = card_file
    if not files:
        raise RateCardMissing(f"no rate card files found in {root}")
    return files


def is_stale(card_file: RateCardFile, now: datetime | None = None) -> bool:
    moment = now or datetime.now(timezone.utc)
    fetched = datetime.combine(card_file.fetched_at, datetime.min.time(), tzinfo=timezone.utc)
    return moment - fetched > timedelta(days=card_file.stale_after_days)


def usd_per_credit(card_file: RateCardFile, plan: str | None = None) -> int:
    plan_name = (plan or flow_plan()).lower()
    try:
        return card_file.plans_usd_micros_per_credit[plan_name]
    except KeyError as err:
        known = ", ".join(sorted(card_file.plans_usd_micros_per_credit))
        raise RateCardMissing(
            f"unknown Flow plan {plan_name!r}; known plans: {known}"
        ) from err


def _entry_credits(entry: RateEntry, plan: str) -> int:
    if plan.startswith("ultra") and entry.credits_ultra is not None:
        return entry.credits_ultra
    return entry.credits


def _rate_card_id(provider_id: str, model: str, unit: str, plan: str) -> str:
    slug = f"{provider_id}-{model}-{unit}".replace(":", "-").replace(".", "-")
    if provider_id == FLOW_PROVIDER_ID:
        slug = f"{slug}-{plan}"
    return f"rc-{slug}"


def resolve(
    files: dict[str, RateCardFile],
    provider_id: str,
    model: str,
    unit: str,
    *,
    plan: str | None = None,
    now: datetime | None = None,
) -> ResolvedRate:
    try:
        card_file = files[provider_id]
    except KeyError as err:
        raise RateCardMissing(f"no rate card file for provider {provider_id!r}") from err
    for entry in card_file.cards:
        if entry.model == model and entry.unit == unit:
            plan_name = (plan or flow_plan()).lower()
            credits = _entry_credits(entry, plan_name)
            usd = entry.usd_micros_per_unit
            if credits and not usd:
                usd = credits * usd_per_credit(card_file, plan_name)
            return ResolvedRate(
                provider_id=provider_id,
                model=model,
                unit=unit,
                usd_micros_per_unit=usd,
                internal_credits_per_unit=credits,
                rate_card_id=_rate_card_id(provider_id, model, unit, plan_name),
                pricing_basis=card_file.pricing_basis,
                source=card_file.source,
                stale=is_stale(card_file, now=now),
            )
    raise RateCardMissing(
        f"no rate for provider={provider_id!r} model={model!r} unit={unit!r}"
    )


def captcha_usd_micros(files: dict[str, RateCardFile]) -> int:
    flow = files.get(FLOW_PROVIDER_ID)
    return flow.captcha_usd_micros_per_solve if flow else 0


def contract_rate_cards(
    workspace_id: str, files: dict[str, RateCardFile] | None = None
) -> list[CostRateCard]:
    """Materialize every entry as a ``CostRateCard`` for the control plane."""

    loaded = files or load_files()
    plan = flow_plan()
    cards: list[CostRateCard] = []
    for card_file in loaded.values():
        effective = datetime.combine(
            card_file.fetched_at, datetime.min.time(), tzinfo=timezone.utc
        )
        for entry in card_file.cards:
            rate = resolve(loaded, card_file.provider_id, entry.model, entry.unit, plan=plan)
            cards.append(
                CostRateCard(
                    id=rate.rate_card_id,
                    workspace_id=workspace_id,
                    capability=ProviderCapability(entry.capability),
                    provider_id=card_file.provider_id,
                    model=entry.model,
                    unit=entry.unit,
                    internal_credits_per_unit=rate.internal_credits_per_unit,
                    cost_usd_micros_per_unit=rate.usd_micros_per_unit,
                    source=card_file.source,
                    effective_from=effective,
                )
            )
    return cards
