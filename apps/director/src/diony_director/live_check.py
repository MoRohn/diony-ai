"""Provider readiness: offline report, live auth probes, and one cheap real roll.

CLI: ``diony-director-check`` prints the offline report; ``--live`` adds real
auth probes ($0 — a models list and an accounts list); ``--roll-one grok``
submits ONE minimal generation with the cost printed and requires ``--yes``.
"""

from __future__ import annotations

import argparse
import json
import sys
import time

import httpx

from diony_contracts.cluster import cluster_root
from diony_director import config, engine, ratecards
from diony_director.providers import flow, grok

PROBE_TIMEOUT_S = 30.0


def readiness_report() -> dict:
    config.load_keys_env()
    files = ratecards.load_files()
    cards = {}
    for provider_id, card_file in files.items():
        cards[provider_id] = {
            "source": card_file.source,
            "fetched_at": card_file.fetched_at.isoformat(),
            "stale": ratecards.is_stale(card_file),
            "pricing_basis": card_file.pricing_basis,
        }
    return {
        "engine": engine.engine_mode().value,
        "keys": {"grok": grok.configured(), "google-flow": flow.configured()},
        "keys_file": str(config.keys_file()),
        "flow_plan": ratecards.flow_plan(),
        "flow_email": flow.account_email() or "(unset — Flow picks an account)",
        "rate_cards": cards,
        "vault": str(cluster_root()),
    }


def live_probe() -> dict:
    """Real auth checks that cost nothing: model list (xAI), accounts (useapi)."""

    config.load_keys_env()
    out: dict = {}

    if not grok.configured():
        out["grok"] = {"ok": False, "detail": "XAI_API_KEY not set"}
    else:
        try:
            response = httpx.get(
                f"{grok.api_base()}/v1/models",
                headers={"Authorization": f"Bearer {grok.api_key()}"},
                timeout=PROBE_TIMEOUT_S,
            )
            response.raise_for_status()
            body = response.json()
            models = [m.get("id", "") for m in body.get("data", [])]
            video = [m for m in models if "video" in m or "imagine" in m]
            out["grok"] = {
                "ok": True,
                "detail": f"auth OK; models visible: {', '.join(video) or f'{len(models)} total'}",
            }
        except Exception as err:  # noqa: BLE001 - a probe reports, it never raises
            out["grok"] = {"ok": False, "detail": f"auth failed: {err}"}

    if not flow.configured():
        out["google-flow"] = {"ok": False, "detail": "USEAPI_TOKEN not set"}
    else:
        try:
            response = httpx.get(
                f"{flow.api_base()}/accounts",
                headers={"Authorization": f"Bearer {flow.api_token()}"},
                timeout=PROBE_TIMEOUT_S,
            )
            response.raise_for_status()
            body = response.json()
            accounts = body if isinstance(body, list) else body.get("accounts", [])
            detail = f"auth OK; {len(accounts)} Flow account(s) connected"
            credits = [a.get("credits") for a in accounts if isinstance(a, dict) and "credits" in a]
            if credits:
                detail += f"; credits: {credits}"
            out["google-flow"] = {"ok": True, "detail": detail, "accounts": len(accounts)}
        except Exception as err:  # noqa: BLE001
            out["google-flow"] = {"ok": False, "detail": f"auth failed: {err}"}

    return out


def roll_one(provider: str) -> dict:
    """One minimal real generation, cost-visible, persisted, ledger-recorded."""

    import uuid

    from diony_contracts.director import (
        CloudGenerationMode,
        CloudProvider,
        DirectorOpType,
        DirectorOp,
    )
    from diony_director import estimate as estimate_mod

    config.load_keys_env()
    if provider == "grok":
        chosen, model, duration, resolution = CloudProvider.GROK, "grok-imagine-video-1.5", 1, "480p"
    else:
        # 8s: the live account grid is 8s-only on every veo tier (4/6s never
        # appear in GET /accounts/{email} model lists, whatever the plan)
        chosen, model, duration, resolution = CloudProvider.GOOGLE_FLOW, "veo-3.1-lite", 8, "720p"
    est = estimate_mod.estimate_op(
        provider=chosen, model=model, mode=CloudGenerationMode.T2V,
        duration_s=duration, resolution=resolution,
    )
    op = DirectorOp(
        id=f"op-check-{uuid.uuid4().hex[:8]}",
        session_id="live-check",
        op=DirectorOpType.GENERATE,
        provider=chosen,
        model=model,
        mode=CloudGenerationMode.T2V,
        prompt="a single amber studio light warming up in a dark room, slow gentle push-in, quiet room tone",
        duration_s=duration,
        resolution=resolution,
        estimate=est,
    )
    adapter = grok if provider == "grok" else flow
    print(f"submitting: {model} {duration}s {resolution} — estimated ${est.total_usd_micros / 1e6:.2f}")
    handle = adapter.submit(op)
    print(f"submitted: {handle.external_id}; polling…")
    deadline = time.monotonic() + 600
    while time.monotonic() < deadline:
        status = adapter.poll(handle)
        if status.terminal:
            break
        time.sleep(5)
    else:
        return {"ok": False, "detail": "timed out after 10 minutes"}
    if status.state != "done":
        return {"ok": False, "detail": f"{status.state}: {status.detail}"}
    dest = cluster_root() / "director" / "live-check" / f"{handle.external_id}.mp4"
    clip = adapter.fetch(handle, status, dest)
    return {
        "ok": True,
        "clip": str(clip.path),
        "bytes": clip.bytes,
        "sha256": clip.sha256,
        "estimated_usd": est.total_usd_micros / 1e6,
        "credits_used": clip.metadata.get("credits_used"),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="diony-director-check")
    parser.add_argument("--live", action="store_true", help="run $0 auth probes against both providers")
    parser.add_argument("--roll-one", choices=["grok", "flow"], help="submit ONE minimal paid generation")
    parser.add_argument("--yes", action="store_true", help="confirm the paid --roll-one")
    args = parser.parse_args(argv)

    report = readiness_report()
    print(json.dumps(report, indent=2))
    if args.live:
        print(json.dumps({"live": live_probe()}, indent=2))
    if args.roll_one:
        if not args.yes:
            print("--roll-one spends real money; add --yes to confirm", file=sys.stderr)
            return 2
        result = roll_one(args.roll_one)
        print(json.dumps({"roll_one": result}, indent=2, default=str))
        return 0 if result.get("ok") else 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
