"""Assemble and price the production package the Director presents for lock."""

from __future__ import annotations

import uuid

from diony_contracts.director import (
    CostEstimate,
    CostEstimateLine,
)
from diony_contracts.production import ProductionPackage
from diony_director import estimate as estimate_mod
from diony_director import splitter
from diony_director.screenwriter import ScreenplayDraft


def build_package(
    session_id: str,
    idea: str,
    draft: ScreenplayDraft,
    *,
    split: bool = True,
    provider_policy: str = "auto",
    production_style: str = "narrated",
    cast_lock: str = "standard",
) -> ProductionPackage:
    from diony_director import cast as cast_mod
    from diony_director import styles

    style = styles.normalize_style(production_style)
    lock_mode = cast_mod.normalize_mode(cast_lock)
    if lock_mode == "locked":
        # Blueprint 32: every character carries a card before any prompt is
        # compiled — the brain's when it wrote one, derived otherwise
        for char in draft.characters:
            cast_mod.ensure_identity(char)
    clips = (
        splitter.split_script(
            draft.script,
            draft.characters,
            draft.settings,
            provider_policy=provider_policy,
            production_style=style.value,
            cast_lock=lock_mode,
        )
        if split
        else []
    )
    package = ProductionPackage(
        id=f"pkg-{uuid.uuid4().hex[:10]}",
        session_id=session_id,
        idea=idea,
        characters=draft.characters,
        settings=draft.settings,
        script=draft.script,
        clips=clips,
        # the pre-split finalized artifact the screenplay realized — every
        # compiled clip inherits its lighting, physics, anchors, constraints
        master_prompt=getattr(draft, "master", None),
        production_style=style,
        cast_lock=lock_mode,
    )
    if split:
        price_package(package)
    return package


def price_package(package: ProductionPackage) -> CostEstimate:
    """Project estimate = the sum of the plan + a named revision allowance."""

    lines: list[CostEstimateLine] = []
    stale = False
    for clip in package.clips:
        est = estimate_mod.estimate_op(
            provider=clip.provider,
            model=clip.model,
            mode=clip.mode,
            duration_s=clip.duration_s,
            resolution=clip.resolution,
            image_inputs=estimate_mod.image_inputs_for(clip),
            workspace_id="local-workspace",
        )
        clip.estimate_usd_micros = est.total_usd_micros
        stale = stale or est.rate_cards_stale
        lines.append(
            CostEstimateLine(
                label=f"{clip.clip_id} · {clip.model} · {clip.duration_s}s ({clip.clip_class})",
                provider_id=clip.provider.value if clip.provider else "",
                model=clip.model,
                unit="clip",
                quantity_microunits=1_000_000,
                internal_credits=sum(l.internal_credits for l in est.lines),
                usd_micros=est.total_usd_micros,
            )
        )
    subtotal = sum(line.usd_micros for line in lines)
    allowance = subtotal * package.revision_allowance_bp // 10_000
    package.estimate = CostEstimate(
        id=f"est-{uuid.uuid4().hex[:12]}",
        workspace_id="local-workspace",
        lines=lines,
        revision_allowance_usd_micros=allowance,
        rate_cards_stale=stale,
    )
    return package.estimate


def add_bible_line(package: ProductionPackage, bible_ops: list) -> CostEstimate | None:
    """Fold the planned plates & sheets into the locked estimate so every
    total the user sees covers everything the batch will actually spend."""
    if package.estimate is None or not bible_ops:
        return package.estimate
    bible_cost = sum(op.estimate.total_usd_micros for op in bible_ops if op.estimate)
    if not bible_cost:
        return package.estimate
    lines = list(package.estimate.lines)
    lines.append(
        CostEstimateLine(
            label=f"plates & character sheets × {len(bible_ops)}",
            provider_id=bible_ops[0].provider.value if bible_ops[0].provider else "",
            model="bible-stills",
            unit="stills",
            quantity_microunits=len(bible_ops) * 1_000_000,
            internal_credits=sum(
                line.internal_credits
                for op in bible_ops
                if op.estimate
                for line in op.estimate.lines
            ),
            usd_micros=bible_cost,
        )
    )
    package.estimate = CostEstimate(
        id=package.estimate.id,
        workspace_id=package.estimate.workspace_id,
        lines=lines,
        revision_allowance_usd_micros=package.estimate.revision_allowance_usd_micros,
        rate_cards_stale=package.estimate.rate_cards_stale,
    )
    return package.estimate


def add_keyframe_line(package: ProductionPackage, keyframes: list) -> CostEstimate | None:
    """Blueprint 32: the cast keyframes a Locked package composes before its
    clips roll — one line, so the locked total covers them like the sheets."""
    if package.estimate is None or not keyframes:
        return package.estimate
    cost = sum(op.estimate.total_usd_micros for op in keyframes if op.estimate)
    if not cost:
        return package.estimate
    lines = list(package.estimate.lines)
    lines.append(
        CostEstimateLine(
            label=f"cast keyframes × {len(keyframes)}",
            provider_id=keyframes[0].provider.value if keyframes[0].provider else "",
            model="cast-keyframes",
            unit="stills",
            quantity_microunits=len(keyframes) * 1_000_000,
            internal_credits=sum(
                line.internal_credits
                for op in keyframes
                if op.estimate
                for line in op.estimate.lines
            ),
            usd_micros=cost,
        )
    )
    package.estimate = CostEstimate(
        id=package.estimate.id,
        workspace_id=package.estimate.workspace_id,
        lines=lines,
        revision_allowance_usd_micros=package.estimate.revision_allowance_usd_micros,
        rate_cards_stale=package.estimate.rate_cards_stale,
    )
    return package.estimate
