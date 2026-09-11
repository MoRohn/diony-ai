from __future__ import annotations

from pathlib import Path

from diony_contracts.launch import (
    CostCenter,
    CostRateCard,
    Department,
    Membership,
    MembershipRole,
    ProviderCapability,
)
from diony_producer.control_plane import ControlPlane
from diony_producer.costs import CostAnalyticsService, UsageSample


def test_fixed_point_cost_allocation_is_idempotent_and_reconciles_rollups(tmp_path: Path) -> None:
    plane = ControlPlane(tmp_path / "control.sqlite3")
    plane.bootstrap_local()
    service = CostAnalyticsService(plane)
    service.save_rate_card(
        CostRateCard(
            id="rate-video-v1",
            workspace_id="local-workspace",
            capability=ProviderCapability.VIDEO,
            provider_id="diony.wan22",
            model="grok-imagine-video-1.5",
            unit="video_second",
            internal_credits_per_unit=250,
            cost_usd_micros_per_unit=125_000,
            source="Measured lease, energy, and storage model v1",
        )
    )
    sample = UsageSample(
        id="usage-one",
        workspace_id="local-workspace",
        project_id="project-one",
        run_id="run-one",
        job_id="job-one",
        capability=ProviderCapability.VIDEO,
        provider_id="diony.wan22",
        model="grok-imagine-video-1.5",
        quantity_microunits=1_500_000,
        unit="video_second",
        cost_center_id="local-production",
        department_id="local-department",
        team_id="local-admin-team",
    )
    first = service.record(sample)
    again = service.record(sample)
    assert first.allocation.id == again.allocation.id
    assert first.usage.internal_credits == 375
    assert first.usage.cost_breakdown.total_usd_micros == 187_500
    summary = service.summary("local-workspace", organization_wide=True)
    assert summary["totals"]["internal_credits"] == 375
    assert summary["totals"]["cost_usd_micros"] == 187_500
    assert summary["reconciliation"]["difference_usd_micros"] == 0
    assert summary["reconciliation"]["component_difference_usd_micros"] == 0
    assert summary["rollups"]["cost_centers"][0]["id"] == "local-production"
    assert (
        sum(row["share_basis_points"] for row in summary["distributions"]["cost_centers"]) == 10_000
    )


def test_cost_center_budget_is_monitoring_not_a_paywall(tmp_path: Path) -> None:
    plane = ControlPlane(tmp_path / "control.sqlite3")
    plane.bootstrap_local()
    service = CostAnalyticsService(plane)
    department = Department(
        id="dept-a",
        workspace_id="local-workspace",
        name="Animation",
        code="ANIM",
        team_ids=["team-a"],
    )
    center = CostCenter(
        id="cc-a",
        workspace_id="local-workspace",
        department_id=department.id,
        name="Animation Monthly",
        code="ANIM-MONTHLY",
        team_ids=["team-a"],
        internal_credit_budget=10,
        cost_budget_usd_micros=100,
    )
    service.save_department(department)
    service.save_cost_center(center)
    recorded = service.record(
        UsageSample(
            id="usage-over",
            workspace_id="local-workspace",
            project_id="project-a",
            run_id="run-a",
            job_id="job-a",
            capability=ProviderCapability.VIDEO,
            provider_id="diony.wan22",
            model="grok-imagine-video-1.5",
            quantity_microunits=1_000_000,
            unit="video_second",
            cost_center_id=center.id,
            department_id=department.id,
            team_id="team-a",
            actual_internal_credits=20,
            actual_cost_usd_micros=200,
        )
    )
    assert recorded.usage.internal_credits == 20
    budget = next(
        row
        for row in service.summary("local-workspace", organization_wide=True)["budgets"]
        if row["cost_center_id"] == "cc-a"
    )
    assert budget["status"] == "over_budget"
    assert budget["monitoring_mode"] == "monitor_only"


def test_cost_scope_is_team_or_department_until_governance_grants_org_rollup(
    tmp_path: Path,
) -> None:
    plane = ControlPlane(tmp_path / "control.sqlite3")
    plane.bootstrap_local()
    service = CostAnalyticsService(plane)
    for suffix in ("a", "b"):
        department = Department(
            id=f"dept-{suffix}",
            workspace_id="local-workspace",
            name=f"Department {suffix.upper()}",
            code=f"D{suffix.upper()}",
            manager_user_ids=["manager-a"] if suffix == "a" else [],
            team_ids=[f"team-{suffix}"],
        )
        service.save_department(department)
        service.save_cost_center(
            CostCenter(
                id=f"cc-{suffix}",
                workspace_id="local-workspace",
                department_id=department.id,
                name=f"Center {suffix.upper()}",
                code=f"CC-{suffix.upper()}",
                team_ids=[f"team-{suffix}"],
            )
        )
        service.record(
            UsageSample(
                id=f"usage-{suffix}",
                workspace_id="local-workspace",
                project_id=f"project-{suffix}",
                run_id=f"run-{suffix}",
                job_id=f"job-{suffix}",
                capability=ProviderCapability.VIDEO,
                provider_id="diony.wan22",
                model="grok-imagine-video-1.5",
                quantity_microunits=1_000_000,
                unit="video_second",
                cost_center_id=f"cc-{suffix}",
                department_id=f"dept-{suffix}",
                team_id=f"team-{suffix}",
                actual_internal_credits=10 if suffix == "a" else 20,
                actual_cost_usd_micros=100 if suffix == "a" else 200,
            )
        )

    scoped = service.summary(
        "local-workspace",
        allowed_team_ids=["team-a"],
        department_ids=[],
    )
    managed = service.summary(
        "local-workspace",
        allowed_team_ids=[],
        department_ids=["dept-a"],
    )
    organization = service.summary("local-workspace", organization_wide=True)
    assert scoped["totals"]["cost_usd_micros"] == 100
    assert managed["totals"]["cost_usd_micros"] == 100
    assert organization["totals"]["cost_usd_micros"] == 300

    ordinary_admin = Membership(
        id="local-workspace:ordinary-admin",
        user_id="ordinary-admin",
        workspace_id="local-workspace",
        role=MembershipRole.ADMIN,
        team_ids=["team-a"],
    )
    configured_team_member = ordinary_admin.model_copy(
        update={"user_id": "cost-admin", "team_ids": ["local-admin-team"]}
    )
    assert service.is_organization_admin(ordinary_admin) is False
    assert service.is_organization_admin(configured_team_member) is True
