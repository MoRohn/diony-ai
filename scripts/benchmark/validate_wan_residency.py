#!/usr/bin/env python3
"""Validate isolated JSONL results and write an activation receipt.

Usage:
  python scripts/benchmark/validate_wan_residency.py \
    --results /vault/qualification/wan-resident-results.jsonl \
    --approved-by "Named reviewer" \
    --output /vault/qualification/wan-resident-validation.json
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from diony_wan.validation import build_validation_receipt


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--results", type=Path, required=True)
    parser.add_argument("--approved-by", required=True)
    parser.add_argument("--max-canary-percent", type=int, default=10)
    parser.add_argument(
        "--kernel-mode",
        choices=("eager", "regional_compile"),
        default="eager",
    )
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    rows = [json.loads(line) for line in args.results.read_text().splitlines() if line.strip()]
    receipt = build_validation_receipt(
        rows,
        approved_by=args.approved_by,
        max_canary_percent=args.max_canary_percent,
        kernel_mode=args.kernel_mode,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    pending = args.output.with_suffix(args.output.suffix + ".part")
    pending.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n")
    pending.replace(args.output)
    print(json.dumps(receipt, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
