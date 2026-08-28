"""Append-only production decision log (Blueprint 24).

Every choice the autonomous run makes that a showrunner would ask about —
which seed rolled, why a retake was directed rather than repaired, which
attempt settled a Shot, which candidate became the hero print — is written
with the options it was chosen from. The Project snapshot keeps the most
recent entries; ``projects/<id>/decision-log.jsonl`` keeps all of them.
"""

from __future__ import annotations

import json
from pathlib import Path

from diony_contracts.models import Project
from diony_contracts.montage import Decision, DecisionCategory, DecisionOption, append_decision
from diony_producer.vault import record_activity, vault_root


def decision_log_path(project_id: str) -> Path:
    return vault_root() / "projects" / project_id / "decision-log.jsonl"


def record_decision(
    project: Project,
    *,
    stage: str,
    category: DecisionCategory,
    subject: str,
    options: list[DecisionOption],
    selected: str,
    reason: str,
    confidence: float = 0.7,
    shot_id: str = "",
    take_id: str = "",
    announce: bool = False,
) -> Decision:
    """Record one decision on the Project and in the durable log; never raises on I/O."""
    sequence = _next_sequence(project)
    decision = Decision(
        id=f"d-{sequence:04d}",
        stage=stage,
        category=category,
        subject=subject,
        options=options,
        selected=selected,
        reason=reason[:600],
        confidence=max(0.0, min(0.99, float(confidence))),
        shot_id=shot_id,
        take_id=take_id,
    )
    append_decision(project.decisions, decision)
    path = decision_log_path(project.id)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as stream:
            stream.write(decision.model_dump_json() + "\n")
    except OSError:
        pass
    if announce:
        chosen = next((option.label for option in options if option.id == selected), selected)
        record_activity("decision", f"{subject}: {chosen} — {reason}"[:400], shot_id=shot_id)
    return decision


def _next_sequence(project: Project) -> int:
    highest = 0
    for decision in project.decisions:
        try:
            highest = max(highest, int(decision.id.split("-", 1)[1]))
        except (IndexError, ValueError):
            continue
    path = decision_log_path(project.id)
    try:
        if path.is_file():
            with path.open("r", encoding="utf-8") as stream:
                lines = [line for line in stream if line.strip()]
            if lines:
                tail = json.loads(lines[-1])
                highest = max(highest, int(str(tail.get("id", "d-0")).split("-", 1)[1]))
    except (OSError, ValueError, json.JSONDecodeError, IndexError):
        pass
    return highest + 1


def read_decision_log(project_id: str, limit: int = 1000) -> list[dict]:
    path = decision_log_path(project_id)
    try:
        lines = path.read_text(encoding="utf-8").splitlines()[-limit:]
    except OSError:
        return []
    rows: list[dict] = []
    for line in lines:
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows


__all__ = ["decision_log_path", "read_decision_log", "record_decision"]
