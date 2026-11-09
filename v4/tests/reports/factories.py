"""Estimation rows equivalent to what v3 ``POST /estimations`` stores, built directly with the ORM."""

from __future__ import annotations

from datetime import date

from apps.catalog.models import DocumentType, DutType, Feature, RiskItem, TaskTemplate, TestProfile
from apps.estimations.models import (
    Estimation,
    EstimationRisk,
    EstimationTask,
    EstimationTeamAllocation,
    HistoricalProject,
)
from apps.inbox.models import Request
from apps.teams.models import TeamMember


def make_estimation(
    *,
    project_name: str = "Report Test",
    estimation_number: str | None = "EST-2026-0001",
    wizard_extra: dict | None = None,
    with_request: bool = False,
    with_team: bool = False,
    with_risk: bool = False,
    grand_total_days: float = 12.5,
    **fields,
) -> Estimation:
    features = list(Feature.objects.order_by("id")[:2])
    dut = DutType.objects.order_by("id").first()
    profile = TestProfile.objects.order_by("id").first()
    wizard = {
        "feature_ids": [f.id for f in features],
        "new_feature_ids": [],
        "dut_ids": [dut.id] if dut else [],
        "profile_ids": [profile.id] if profile else [],
        "dut_profile_matrix": [[dut.id, profile.id]] if dut and profile else [],
        "pr_fixes": {"simple": 1, "medium": 0, "complex": 0},
        "team_size": 2,
        "has_leader": False,
        "working_days": 15,
    }
    wizard.update(wizard_extra or {})

    req = None
    if with_request:
        req = Request.objects.create(
            request_number="REQ_26/0015", title="Req", requester_name="Banking BU",
            business_unit="Banking", priority="HIGH", received_date=date(2026, 1, 5),
        )

    est = Estimation.objects.create(
        request=req,
        estimation_number=estimation_number,
        project_name=project_name,
        project_type=fields.pop("project_type", "NEW"),
        dut_count=1,
        profile_count=1,
        dut_profile_combinations=1,
        pr_fix_count=1,
        total_tester_hours=80.0,
        total_leader_hours=0.0,
        pr_fix_hours=2.0,
        study_hours=0.0,
        buffer_hours=8.2,
        grand_total_hours=90.2,
        grand_total_days=grand_total_days,
        feasibility_status="FEASIBLE",
        created_by="admin",
        wizard_inputs=wizard,
        **fields,
    )
    for tmpl in TaskTemplate.objects.order_by("id")[:3]:
        EstimationTask.objects.create(
            estimation=est, task_template=tmpl, task_name=tmpl.name, task_type=tmpl.task_type,
            base_hours=tmpl.base_effort_hours, calculated_hours=tmpl.base_effort_hours * 2,
            notes="note",
        )
    if with_team:
        tm = TeamMember.objects.create(name="Alice", role="TESTER", skills=["SIM", "eSIM"])
        EstimationTeamAllocation.objects.create(estimation=est, team_member=tm, allocated_hours=40.0)
    if with_risk:
        ri = RiskItem.objects.create(name="Late hardware", category="Supply", likelihood="HIGH", impact="LOW")
        EstimationRisk.objects.create(estimation=est, risk_item=ri)
    return est


def make_document_type(name: str = "Test Plan", hours: float = 6.0, linked: TaskTemplate | None = None):
    return DocumentType.objects.create(
        name=name, base_effort_hours=hours, category="Planning", task_template=linked,
    )


def make_historical(name: str = "Old project", **fields) -> HistoricalProject:
    defaults = {
        "project_type": "EVOLUTION", "estimated_hours": 400.0, "actual_hours": 450.0,
        "dut_count": 2, "profile_count": 2, "pr_count": 3, "completion_date": date(2025, 6, 1),
    }
    defaults.update(fields)
    return HistoricalProject.objects.create(project_name=name, **defaults)
