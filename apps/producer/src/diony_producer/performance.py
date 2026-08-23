"""Durable production-stage latency and reliability evidence."""

from __future__ import annotations

import json
import math
import os
import statistics
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


_WRITE_LOCK = threading.Lock()


def _vault_root() -> Path:
    return Path(
        os.environ.get("DIONY_PRODUCER_VAULT")
        or os.environ.get("DIONY_VAULT")
        or ".diony-vault"
    ).expanduser()


def _nearest_rank(values: list[float], quantile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, math.ceil(quantile * len(ordered)) - 1))
    return ordered[index]


def _number(value: float) -> int | float:
    return int(value) if float(value).is_integer() else round(value, 6)


class PerformanceJournal:
    def __init__(self, path: Path | None = None) -> None:
        self._explicit_path = path

    @property
    def path(self) -> Path:
        """Resolve the packaged writable vault after Desk startup configuration."""
        return self._explicit_path or (_vault_root() / ".cluster" / "performance.jsonl")

    def record(
        self,
        *,
        stage: str,
        project_id: str,
        take_id: str,
        duration_seconds: float,
        outcome: str,
        retry: bool = False,
        peak_vram_mib: float = 0,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        row = {
            "schema_version": 1,
            "recorded_at": datetime.now(timezone.utc).isoformat(),
            "stage": stage,
            "project_id": project_id,
            "take_id": take_id,
            "duration_seconds": round(max(0.0, float(duration_seconds)), 6),
            "outcome": outcome,
            "retry": bool(retry),
            "peak_vram_mib": round(max(0.0, float(peak_vram_mib)), 2),
            "metadata": metadata or {},
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with _WRITE_LOCK, self.path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(row, separators=(",", ":"), sort_keys=True) + "\n")
            stream.flush()

    def rows(self, limit: int = 10_000) -> list[dict[str, Any]]:
        try:
            lines = self.path.read_text(encoding="utf-8").splitlines()[-limit:]
        except OSError:
            return []
        rows: list[dict[str, Any]] = []
        for line in lines:
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(row, dict) and row.get("schema_version") == 1:
                rows.append(row)
        return rows

    def snapshot(self) -> dict[str, dict[str, int | float]]:
        grouped: dict[str, list[dict[str, Any]]] = {}
        for row in self.rows():
            grouped.setdefault(str(row.get("stage") or "unknown"), []).append(row)
        result: dict[str, dict[str, int | float]] = {}
        for stage, rows in grouped.items():
            succeeded = [row for row in rows if row.get("outcome") == "succeeded"]
            latencies = [float(row.get("duration_seconds") or 0) for row in succeeded]
            result[stage] = {
                "completed": len(succeeded),
                "failed": sum(row.get("outcome") == "failed" for row in rows),
                "retries": sum(bool(row.get("retry")) for row in rows),
                "latency_seconds_avg": _number(sum(latencies) / len(latencies)) if latencies else 0,
                "latency_seconds_p50": _number(statistics.median(latencies)) if latencies else 0,
                "latency_seconds_p95": _number(_nearest_rank(latencies, 0.95)),
                "peak_vram_mib": _number(max(float(row.get("peak_vram_mib") or 0) for row in rows)),
            }
        return result


def prometheus_lines(snapshot: dict[str, dict[str, int | float]] | None = None) -> list[str]:
    rows = snapshot if snapshot is not None else PerformanceJournal().snapshot()
    lines = [
        "# HELP diony_wan_camera_latency_seconds_p50 Measured successful camera clip p50 latency.",
        "# TYPE diony_wan_camera_latency_seconds_p50 gauge",
    ]
    for stage in sorted(rows):
        safe_stage = "".join(ch if ch.isalnum() or ch == "_" else "_" for ch in stage)
        for name, value in rows[stage].items():
            lines.append(f"diony_{safe_stage}_{name} {value}")
    return lines
