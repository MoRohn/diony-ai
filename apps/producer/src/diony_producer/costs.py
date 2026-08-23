"""Fixed-point internal Cost Center accounting and scoped analytics."""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, Field

from diony_contracts.launch import (
    CostAllocationEntry,
    CostBreakdown,
    CostCenter,
    CostGovernance,
    CostRateCard,
    Department,
    Membership,
    ProviderCapability,
    UsageEvent,
)
from diony_producer.control_plane import ControlPlane, DocumentNotFound


MICROUNITS_PER_UNIT = 1_000_000
BASIS_POINTS = 10_000


class UsageSample(BaseModel):
    id: str
    workspace_id: str
    project_id: str
    run_id: str
    job_id: str
    capability: ProviderCapability
    provider_id: str
    model: str
    quantity_microunits: int = Field(ge=0)
    unit: str
    cost_center_id: str = ""
    department_id: str = ""
    team_id: str = ""
    actual_internal_credits: int | None = Field(default=None, ge=0)
    actual_cost_usd_micros: int | None = Field(default=None, ge=0)
    actual_cost_breakdown: CostBreakdown | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    occurred_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class RecordedCost(BaseModel):
    usage: UsageEvent
    allocation: CostAllocationEntry


def _scale(quantity_microunits: int, per_unit: int) -> int:
    """Round half-up using integers only; money and credits never use binary floats."""
    return (quantity_microunits * per_unit + MICROUNITS_PER_UNIT // 2) // MICROUNITS_PER_UNIT


class CostAnalyticsService:
    def __init__(self, plane: ControlPlane) -> None:
        self.plane = plane

    def save_department(self, department: Department) -> Department:
        self.plane.put_document(
            "department",
            department.id,
            department.workspace_id,
            department.model_dump(mode="json"),
        )
        return department

    def save_cost_center(self, center: CostCenter) -> CostCenter:
        department = Department.model_validate(
            self.plane.get_document("department", center.department_id, center.workspace_id)
        )
        unknown_teams = set(center.team_ids).difference(department.team_ids)
        if unknown_teams:
            raise ValueError(
                "cost center teams must belong to its department: "
                + ", ".join(sorted(unknown_teams))
            )
        self.plane.put_document(
            "cost_center",
            center.id,
            center.workspace_id,
            center.model_dump(mode="json"),
        )
        return center

    def save_rate_card(self, rate: CostRateCard) -> CostRateCard:
        self.plane.put_document(
            "cost_rate_card", rate.id, rate.workspace_id, rate.model_dump(mode="json")
        )
        return rate

    def governance(self, workspace_id: str) -> CostGovernance:
        return CostGovernance.model_validate(
            self.plane.get_document("cost_governance", "cost-governance", workspace_id)
        )

    def is_organization_admin(self, membership: Membership) -> bool:
        governance = self.governance(membership.workspace_id)
        return (
            membership.user_id == governance.admin_user_id
            or governance.admin_team_id in membership.team_ids
        )

    def _rate(self, sample: UsageSample) -> CostRateCard | None:
        rows = [
            CostRateCard.model_validate(row)
            for row in self.plane.list_documents("cost_rate_card", sample.workspace_id)
        ]
        eligible = [
            row
            for row in rows
            if row.active
            and row.capability == sample.capability
            and row.provider_id == sample.provider_id
            and row.model == sample.model
            and row.unit == sample.unit
            and row.effective_from <= sample.occurred_at
            and (row.effective_to is None or sample.occurred_at < row.effective_to)
        ]
        return max(eligible, key=lambda row: row.effective_from, default=None)

    def _dimensions(self, sample: UsageSample) -> tuple[CostCenter, str, str]:
        governance = self.governance(sample.workspace_id)
        center_id = sample.cost_center_id or governance.default_cost_center_id
        center = CostCenter.model_validate(
            self.plane.get_document("cost_center", center_id, sample.workspace_id)
        )
        department_id = sample.department_id or center.department_id
        if department_id != center.department_id:
            raise ValueError("usage department must match its cost center")
        team_id = sample.team_id or (center.team_ids[0] if center.team_ids else "unallocated")
        if center.team_ids and team_id not in center.team_ids:
            raise ValueError("usage team is not assigned to its cost center")
        return center, department_id, team_id

    def record(self, sample: UsageSample) -> RecordedCost:
        allocation_id = f"cost:{sample.id}"
        try:
            usage = UsageEvent.model_validate(
                self.plane.get_document("usage_event", sample.id, sample.workspace_id)
            )
            allocation = CostAllocationEntry.model_validate(
                self.plane.get_document("cost_allocation", allocation_id, sample.workspace_id)
            )
            return RecordedCost(usage=usage, allocation=allocation)
        except DocumentNotFound:
            pass

        center, department_id, team_id = self._dimensions(sample)
        rate = self._rate(sample)
        if sample.actual_cost_breakdown is not None:
            breakdown = sample.actual_cost_breakdown
            measurement = "actual"
        elif sample.actual_cost_usd_micros is not None:
            breakdown = CostBreakdown(compute_usd_micros=sample.actual_cost_usd_micros)
            measurement = "actual"
        elif rate is not None:
            breakdown = CostBreakdown(
                compute_usd_micros=_scale(
                    sample.quantity_microunits, rate.cost_usd_micros_per_unit
                )
            )
            measurement = "rate_card"
        else:
            breakdown = CostBreakdown()
            measurement = "unrated"
        if sample.actual_internal_credits is not None:
            internal_credits = sample.actual_internal_credits
        elif rate is not None:
            internal_credits = _scale(
                sample.quantity_microunits, rate.internal_credits_per_unit
            )
        else:
            internal_credits = 0
        usage = UsageEvent(
            id=sample.id,
            workspace_id=sample.workspace_id,
            project_id=sample.project_id,
            run_id=sample.run_id,
            job_id=sample.job_id,
            department_id=department_id,
            team_id=team_id,
            cost_center_id=center.id,
            capability=sample.capability,
            provider_id=sample.provider_id,
            model=sample.model,
            quantity_microunits=sample.quantity_microunits,
            unit=sample.unit,
            internal_credits=internal_credits,
            cost_breakdown=breakdown,
            rate_card_id=rate.id if rate else "",
            measurement=measurement,
            metadata=sample.metadata,
            occurred_at=sample.occurred_at,
        )
        allocation = CostAllocationEntry(
            id=allocation_id,
            workspace_id=sample.workspace_id,
            usage_event_id=usage.id,
            project_id=usage.project_id,
            job_id=usage.job_id,
            department_id=usage.department_id,
            team_id=usage.team_id,
            cost_center_id=usage.cost_center_id,
            internal_credits=usage.internal_credits,
            cost_usd_micros=usage.cost_breakdown.total_usd_micros,
        )
        self.plane.save_usage(usage)
        self.plane.save_cost_allocation(allocation)
        return RecordedCost(usage=usage, allocation=allocation)

    @staticmethod
    def _rollup(rows: list[dict[str, Any]], key: str) -> list[dict[str, Any]]:
        grouped: dict[str, dict[str, int]] = defaultdict(
            lambda: {"internal_credits": 0, "cost_usd_micros": 0, "usage_events": 0}
        )
        for row in rows:
            bucket = grouped[str(row[key] or "unallocated")]
            bucket["internal_credits"] += int(row["internal_credits"])
            bucket["cost_usd_micros"] += int(row["cost_usd_micros"])
            bucket["usage_events"] += 1
        return [
            {"id": identifier, **values}
            for identifier, values in sorted(
                grouped.items(), key=lambda item: (-item[1]["cost_usd_micros"], item[0])
            )
        ]

    @staticmethod
    def _distribution(
        rollup: list[dict[str, Any]], total_cost: int
    ) -> list[dict[str, Any]]:
        """Allocate exactly 10,000 basis points with deterministic largest remainders."""
        if total_cost <= 0:
            return [{**row, "share_basis_points": 0} for row in rollup]
        ranked: list[tuple[int, str, int]] = []
        shares: dict[str, int] = {}
        for row in rollup:
            numerator = int(row["cost_usd_micros"]) * BASIS_POINTS
            shares[row["id"]] = numerator // total_cost
            ranked.append((numerator % total_cost, str(row["id"]), int(row["cost_usd_micros"])))
        unassigned = BASIS_POINTS - sum(shares.values())
        for _, identifier, _ in sorted(
            ranked, key=lambda item: (-item[0], -item[2], item[1])
        )[:unassigned]:
            shares[identifier] += 1
        return [{**row, "share_basis_points": shares[row["id"]]} for row in rollup]

    def summary(
        self,
        workspace_id: str,
        *,
        organization_wide: bool = False,
        allowed_team_ids: list[str] | None = None,
        department_ids: list[str] | None = None,
    ) -> dict[str, Any]:
        usage_by_id = {row.id: row for row in self.plane.usage(workspace_id)}
        allocations = self.plane.cost_allocations(workspace_id)
        rows: list[dict[str, Any]] = []
        for allocation in allocations:
            usage = usage_by_id.get(allocation.usage_event_id)
            if usage is None:
                continue
            if not organization_wide:
                team_match = bool(
                    allowed_team_ids is not None
                    and allocation.team_id in allowed_team_ids
                )
                department_match = bool(
                    department_ids is not None
                    and allocation.department_id in department_ids
                )
                if not team_match and not department_match:
                    continue
            rows.append(
                {
                    **allocation.model_dump(mode="json"),
                    "provider_id": usage.provider_id,
                    "model": usage.model,
                    "capability": usage.capability.value,
                    "measurement": usage.measurement,
                    "cost_breakdown": usage.cost_breakdown.model_dump(mode="json"),
                }
            )
        total_cost = sum(int(row["cost_usd_micros"]) for row in rows)
        total_credits = sum(int(row["internal_credits"]) for row in rows)
        component_keys = (
            "compute_usd_micros",
            "energy_usd_micros",
            "storage_usd_micros",
            "network_usd_micros",
            "license_usd_micros",
            "other_usd_micros",
        )
        cost_breakdown = {
            key: sum(int(row["cost_breakdown"][key]) for row in rows)
            for key in component_keys
        }
        cost_breakdown["total_usd_micros"] = sum(cost_breakdown.values())
        rollups = {
            "departments": self._rollup(rows, "department_id"),
            "cost_centers": self._rollup(rows, "cost_center_id"),
            "teams": self._rollup(rows, "team_id"),
            "projects": self._rollup(rows, "project_id"),
            "providers": self._rollup(rows, "provider_id"),
            "models": self._rollup(rows, "model"),
            "capabilities": self._rollup(rows, "capability"),
        }
        center_rollup = {row["id"]: row for row in rollups["cost_centers"]}
        centers = [
            CostCenter.model_validate(row)
            for row in self.plane.list_documents("cost_center", workspace_id)
        ]
        budgets = []
        for center in centers:
            if not organization_wide:
                team_match = bool(
                    allowed_team_ids is not None
                    and set(center.team_ids).intersection(allowed_team_ids)
                )
                department_match = bool(
                    department_ids is not None and center.department_id in department_ids
                )
                if not team_match and not department_match:
                    continue
            actual = center_rollup.get(
                center.id,
                {"internal_credits": 0, "cost_usd_micros": 0, "usage_events": 0},
            )
            credit_ratio = (
                actual["internal_credits"] * BASIS_POINTS // center.internal_credit_budget
                if center.internal_credit_budget
                else 0
            )
            cost_ratio = (
                actual["cost_usd_micros"] * BASIS_POINTS // center.cost_budget_usd_micros
                if center.cost_budget_usd_micros
                else 0
            )
            highest = max(credit_ratio, cost_ratio)
            status = (
                "over_budget"
                if highest > BASIS_POINTS
                else "warning"
                if highest >= center.warning_threshold_basis_points
                else "within_budget"
            )
            budgets.append(
                {
                    "cost_center_id": center.id,
                    "status": status,
                    "monitoring_mode": center.monitoring_mode,
                    "used_internal_credits": actual["internal_credits"],
                    "internal_credit_budget": center.internal_credit_budget,
                    "used_cost_usd_micros": actual["cost_usd_micros"],
                    "cost_budget_usd_micros": center.cost_budget_usd_micros,
                    "utilization_basis_points": highest,
                }
            )
        rated = sum(1 for row in rows if row["measurement"] != "unrated")
        center_total = sum(row["cost_usd_micros"] for row in rollups["cost_centers"])
        distributions = {
            key: self._distribution(value, total_cost) for key, value in rollups.items()
        }
        return {
            "scope": "organization" if organization_wide else "assigned",
            "currency": "USD",
            "totals": {
                "internal_credits": total_credits,
                "cost_usd_micros": total_cost,
                "usage_events": len(rows),
                "cost_breakdown": cost_breakdown,
            },
            "rollups": rollups,
            "distributions": distributions,
            "budgets": budgets,
            "quality": {
                "rated_usage_basis_points": rated * BASIS_POINTS // len(rows) if rows else 0,
                "unrated_usage_events": len(rows) - rated,
                "cost_model_version": self.governance(workspace_id).cost_model_version,
            },
            "reconciliation": {
                "allocation_total_usd_micros": total_cost,
                "cost_center_rollup_usd_micros": center_total,
                "component_total_usd_micros": cost_breakdown["total_usd_micros"],
                "difference_usd_micros": total_cost - center_total,
                "component_difference_usd_micros": (
                    total_cost - cost_breakdown["total_usd_micros"]
                ),
            },
        }
