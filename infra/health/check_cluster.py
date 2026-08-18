#!/usr/bin/env python3
"""Cluster health probe for Diony profiles.

Day 1/2: dry-run friendly. Checks profile files, optional vault path, optional HTTP.
Exit 0 = pass, 1 = fail. JSON on stdout.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PROFILES = [
    ROOT / "infra" / "profiles" / "spark-4x-v1.yaml",
    ROOT / "infra" / "profiles" / "spark-2x-v1.yaml",
    ROOT / "infra" / "profiles" / "lease-gpu-v1.yaml",
    ROOT / "infra" / "profiles" / "b300-v1.yaml",
]


def _http_ok(url: str, timeout: float = 2.0) -> tuple[bool, str]:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            if 200 <= resp.status < 300:
                return True, f"{resp.status}"
            return False, f"status {resp.status}"
    except urllib.error.URLError as exc:
        return False, str(exc.reason)
    except Exception as exc:  # noqa: BLE001 — probe must never crash
        return False, str(exc)


def main() -> int:
    parser = argparse.ArgumentParser(description="Diony cluster health")
    parser.add_argument("--live", action="store_true", help="Hit brain/wan/finish HTTP")
    parser.add_argument("--vault", default=os.environ.get("DIONY_VAULT", "/diony/vault"))
    parser.add_argument("--brain", default="http://127.0.0.1:8000/v1/models")
    parser.add_argument("--wan", default="http://127.0.0.1:8010/health")
    parser.add_argument("--finish", default="http://127.0.0.1:8020/health")
    args = parser.parse_args()

    checks: list[dict] = []

    for profile in PROFILES:
        checks.append(
            {
                "name": f"profile_{profile.stem}",
                "ok": profile.is_file(),
                "detail": str(profile),
            }
        )

    vault = Path(args.vault)
    vault_ok = vault.is_dir() and os.access(vault, os.W_OK)
    checks.append(
        {
            "name": "vault_writable",
            "ok": vault_ok,
            "detail": str(vault),
            "optional_on_laptop": True,
        }
    )

    if args.live:
        for name, url in (
            ("brain", args.brain),
            ("wan", args.wan),
            ("finish", args.finish),
        ):
            ok, detail = _http_ok(url)
            checks.append({"name": name, "ok": ok, "detail": f"{url} {detail}"})
    else:
        checks.append(
            {
                "name": "http",
                "ok": True,
                "detail": "skipped (dry-run; pass --live on cluster)",
            }
        )

    # On a developer laptop, vault is expected missing. Fail only hard checks.
    hard = [c for c in checks if not c.get("optional_on_laptop")]
    ok = all(c["ok"] for c in hard)
    print(json.dumps({"ok": ok, "dry_run": not args.live, "checks": checks}, indent=2))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
