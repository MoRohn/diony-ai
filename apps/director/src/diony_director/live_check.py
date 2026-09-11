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
from diony_director import brain as brain_mod
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
        "keys": {
            "grok": grok.configured(),
            "google-flow": flow.configured(),
            "gemini": brain_mod.gemini_configured(),
        },
        "brain": brain_mod.status(),
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
        except Exception as err:
            out["grok"] = {"ok": False, "detail": f"auth failed: {err}"}

    if not flow.configured():
        out["google-flow"] = {"ok": False, "detail": "USEAPI_TOKEN not set"}
    else:
        try:
            response = flow.paced(
                lambda: httpx.get(
                    f"{flow.api_base()}/accounts",
                    headers={"Authorization": f"Bearer {flow.api_token()}"},
                    timeout=PROBE_TIMEOUT_S,
                ),
                what="account probe",
            )
            response.raise_for_status()
            body = response.json()
            if isinstance(body, dict) and "accounts" not in body:
                # live wire shape (verified 2026-09-02): a dict keyed by
                # account email — {"user@gmail.com": {..., "error": ...}}.
                # Parsing it as an empty list reported "0 accounts" over a
                # connected-but-signed-out account.
                accounts = [
                    dict(entry, email=email)
                    for email, entry in body.items()
                    if isinstance(entry, dict)
                ]
            else:
                accounts = body if isinstance(body, list) else body.get("accounts", [])
            problems = [
                f"{a.get('email', '(unknown)')}: {a['error']}"
                for a in accounts
                if isinstance(a, dict) and a.get("error")
            ]
            captcha = flow.captcha_status()
            if not captcha["ready"]:
                problems.append(
                    "captcha solver missing — the included solves are exhausted; add a "
                    f"provider at {flow.CAPTCHA_PROVIDER_DOCS}"
                )
            detail = f"auth OK; {len(accounts)} Flow account(s) connected"
            credits = [a.get("credits") for a in accounts if isinstance(a, dict) and "credits" in a]
            if credits:
                detail += f"; credits: {credits}"
            if captcha["providers"]:
                detail += f"; captcha: {', '.join(captcha['providers'])} configured"
            elif captcha["free_credits"]:
                detail += f"; captcha: {captcha['free_credits']} included solve(s) remain"
            if problems:
                # the token works but the account can't roll — that is NOT ok
                detail += "; UNHEALTHY — " + "; ".join(problems)
            out["google-flow"] = {
                "ok": not problems,
                "detail": detail,
                "accounts": len(accounts),
                "captcha": captcha,
            }
        except Exception as err:
            out["google-flow"] = {"ok": False, "detail": f"auth failed: {err}"}

    if not brain_mod.gemini_configured():
        out["gemini"] = {"ok": False, "detail": "GEMINI_API_KEY not set"}
    else:
        try:
            response = httpx.get(
                f"{brain_mod.gemini_base()}/v1beta/models",
                headers={"x-goog-api-key": brain_mod.gemini_key()},
                timeout=PROBE_TIMEOUT_S,
            )
            response.raise_for_status()
            models = [m.get("name", "") for m in response.json().get("models", [])]
            tts = [m for m in models if "tts" in m]
            out["gemini"] = {
                "ok": True,
                "detail": (
                    f"auth OK; {len(models)} models visible"
                    + (f"; speech: {', '.join(tts)}" if tts else "; no speech model visible")
                ),
            }
        except Exception as err:
            out["gemini"] = {"ok": False, "detail": f"auth failed: {err}"}
    return out


def roll_one(provider: str) -> dict:
    """One minimal real generation, cost-visible, persisted, ledger-recorded."""

    import uuid

    from diony_contracts.director import (
        CloudGenerationMode,
        CloudProvider,
        DirectorOp,
        DirectorOpType,
    )
    from diony_director import estimate as estimate_mod

    config.load_keys_env()
    if provider == "grok":
        chosen, model, duration, resolution = (
            CloudProvider.GROK,
            "grok-imagine-video-1.5",
            1,
            "480p",
        )
    else:
        # 8s: the live account grid is 8s-only on every veo tier (4/6s never
        # appear in GET /accounts/{email} model lists, whatever the plan)
        chosen, model, duration, resolution = CloudProvider.GOOGLE_FLOW, "veo-3.1-lite", 8, "720p"
    est = estimate_mod.estimate_op(
        provider=chosen,
        model=model,
        mode=CloudGenerationMode.T2V,
        duration_s=duration,
        resolution=resolution,
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
    print(
        f"submitting: {model} {duration}s {resolution} — estimated ${est.total_usd_micros / 1e6:.2f}"
    )
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
    parser.add_argument(
        "--live", action="store_true", help="run $0 auth probes against both providers"
    )
    parser.add_argument(
        "--roll-one", choices=["grok", "flow"], help="submit ONE minimal paid generation"
    )
    parser.add_argument("--yes", action="store_true", help="confirm the paid --roll-one")
    parser.add_argument(
        "--reconnect",
        action="store_true",
        help="re-link the Google Flow account from a live browser session ($0) — "
        "only acts if the account is currently signed out",
    )
    parser.add_argument("--cdp", default="", help="--reconnect: browser CDP port or ws URL")
    parser.add_argument("--profile", default="", help="--reconnect: Chrome profile name/path")
    parser.add_argument(
        "--stage-face-models",
        action="store_true",
        help="download OpenCV Zoo's YuNet + SFace (Apache-2.0, ~37 MB) into the desk's "
        "vault so Cast Lock can MEASURE sheets and faces here (Blueprint 32)",
    )
    args = parser.parse_args(argv)

    if args.stage_face_models:
        from diony_director import cast as cast_mod

        detector, recognizer = cast_mod.stage_face_models()
        print(
            json.dumps(
                {"face_models": {"detector": str(detector), "recognizer": str(recognizer)}},
                indent=2,
            )
        )
        return 0

    report = readiness_report()
    print(json.dumps(report, indent=2))
    if args.live:
        print(json.dumps({"live": live_probe()}, indent=2))
    if args.reconnect:
        from diony_director import flow_reconnect

        result = flow_reconnect.ensure_connected(cdp=args.cdp, profile=args.profile)
        print(json.dumps({"reconnect": result}, indent=2, default=str))
        return 0 if result.get("ok") else 1
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
