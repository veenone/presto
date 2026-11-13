"""Schemas for the estimation lifecycle (port of the v3 ``api/schemas.py`` classes).

``serialize_estimation(estimation)`` builds the v3 ``EstimationOut`` payload
(JSON columns as JSON text, related names resolved) and is reused by other
domains (for example ``GET /requests/{id}/detail``).
"""

from __future__ import annotations

import json
from datetime import date, datetime
from typing import Any

from ninja import Schema
from pydantic import BaseModel, ConfigDict, Field

from apps.api.common import OptionalDate

# ── Shared input pieces ──────────────────────────────────


class PRFixInput(BaseModel):
    simple: int = 0
    medium: int = 0
    complex_: int = Field(0, alias="complex")

    model_config = ConfigDict(populate_by_name=True)


class PRDetailItem(BaseModel):
    pr_number: str
    link: str | None = None
    description: str | None = None
    priority: str | None = None
    complexity: str = "simple"
    status: str = "Open"
    test_available: bool = True


class TeamAllocationItem(Schema):
    team_member_id: int
    role: str | None = None
    allocated_hours: float = 0
    team_member_name: str | None = None


# ── Outputs ──────────────────────────────────────────────


class EstimationTaskOut(Schema):
    id: int
    task_template_id: int | None = None
    task_name: str
    task_type: str
    base_hours: float
    calculated_hours: float
    assigned_testers: int
    has_leader_support: bool
    leader_hours: float
    is_new_feature_study: bool
    formula: str | None = None
    notes: str | None = None
    feature_id: int | None = None
    feature_name: str | None = None


class EstimationRiskOut(Schema):
    id: int
    risk_item_id: int
    risk_item_name: str | None = None
    risk_item_category: str | None = None
    risk_item_likelihood: str | None = None
    risk_item_impact: str | None = None
    notes: str | None = None


class EstimationOut(Schema):
    id: int
    request_id: int | None = None
    estimation_number: str | None = None
    project_name: str
    project_type: str
    reference_project_ids: str = "[]"
    dut_count: int
    profile_count: int
    dut_profile_combinations: int
    pr_fix_count: int
    start_date: date | None = None
    expected_delivery: date | None = None
    total_tester_hours: float
    total_leader_hours: float
    pr_fix_hours: float = 0
    pr_no_test_hours: float = 0
    study_hours: float = 0
    release_extra_hours: float = 0
    documentation_hours: float = 0
    buffer_hours: float = 0
    grand_total_hours: float
    grand_total_days: float
    elapsed_hours: float = 0
    elapsed_days: float = 0
    elapsed_weeks: float = 0
    estimated_completion_date: date | None = None
    feasibility_status: str
    status: str
    version: int = 1
    wizard_inputs_json: str = "{}"
    expected_releases: int = 1
    project_goals: str | None = None
    target_customer: str | None = None
    project_reference: str | None = None
    created_at: datetime | None = None
    created_by: str | None = None
    approved_by: str | None = None
    approved_at: datetime | None = None
    assigned_to_id: int | None = None
    assigned_to_name: str | None = None
    team_id: int | None = None
    team_name: str | None = None
    request_number: str | None = None
    tasks: list[EstimationTaskOut] = []
    team_allocations: list[TeamAllocationItem] = []
    risks: list[EstimationRiskOut] = []
    document_deliverables: list[dict] = []


def _json_text(value: Any, empty: str) -> str:
    """JSON column value as the JSON text v3 stored (``json.dumps`` defaults)."""
    if value is None:
        return empty
    if isinstance(value, str):
        return value
    return json.dumps(value)


def serialize_task(t) -> dict[str, Any]:
    return {
        "id": t.id,
        "task_template_id": t.task_template_id,
        "task_name": t.task_name,
        "task_type": t.task_type,
        "base_hours": t.base_hours,
        "calculated_hours": t.calculated_hours,
        "assigned_testers": t.assigned_testers,
        "has_leader_support": t.has_leader_support,
        "leader_hours": t.leader_hours,
        "is_new_feature_study": t.is_new_feature_study,
        "formula": t.formula,
        "notes": t.notes,
        "feature_id": t.feature_id,
        "feature_name": t.feature_name,
    }


def serialize_estimation(estimation) -> dict[str, Any]:
    """The v3 ``EstimationOut`` for a saved estimation (no synthetic tasks/deliverables)."""
    e = estimation
    assigned = e.assigned_to
    allocations = e.team_allocations.select_related("team_member").order_by("id")
    risks = e.risks.select_related("risk_item").order_by("id")
    return {
        "id": e.id,
        "request_id": e.request_id,
        "estimation_number": e.estimation_number,
        "project_name": e.project_name,
        "project_type": e.project_type,
        "reference_project_ids": _json_text(e.reference_project_ids, "[]"),
        "dut_count": e.dut_count,
        "profile_count": e.profile_count,
        "dut_profile_combinations": e.dut_profile_combinations,
        "pr_fix_count": e.pr_fix_count,
        "start_date": e.start_date,
        "expected_delivery": e.expected_delivery,
        "total_tester_hours": e.total_tester_hours,
        "total_leader_hours": e.total_leader_hours,
        "pr_fix_hours": e.pr_fix_hours,
        "pr_no_test_hours": e.pr_no_test_hours,
        "study_hours": e.study_hours,
        "release_extra_hours": e.release_extra_hours,
        "documentation_hours": e.documentation_hours,
        "buffer_hours": e.buffer_hours,
        "grand_total_hours": e.grand_total_hours,
        "grand_total_days": e.grand_total_days,
        "elapsed_hours": e.elapsed_hours,
        "elapsed_days": e.elapsed_days,
        "elapsed_weeks": e.elapsed_weeks,
        "estimated_completion_date": e.estimated_completion_date,
        "feasibility_status": e.feasibility_status,
        "status": e.status,
        "version": e.version,
        "wizard_inputs_json": _json_text(e.wizard_inputs, "{}"),
        "expected_releases": e.expected_releases,
        "project_goals": e.project_goals,
        "target_customer": e.target_customer,
        "project_reference": e.project_reference,
        "created_at": e.created_at,
        "created_by": e.created_by,
        "approved_by": e.approved_by,
        "approved_at": e.approved_at,
        "assigned_to_id": e.assigned_to_id,
        "assigned_to_name": (assigned.display_name or assigned.username) if assigned else None,
        "team_id": e.team_id,
        "team_name": e.team.name if e.team_id and e.team else None,
        "request_number": e.request.request_number if e.request_id and e.request else None,
        "tasks": [serialize_task(t) for t in e.tasks.order_by("id")],
        "team_allocations": [
            {
                "team_member_id": a.team_member_id,
                "role": a.role,
                "allocated_hours": a.allocated_hours,
                "team_member_name": a.team_member.name if a.team_member else None,
            }
            for a in allocations
        ],
        "risks": [
            {
                "id": r.id,
                "risk_item_id": r.risk_item_id,
                "risk_item_name": r.risk_item.name if r.risk_item else None,
                "risk_item_category": r.risk_item.category if r.risk_item else None,
                "risk_item_likelihood": r.risk_item.likelihood if r.risk_item else None,
                "risk_item_impact": r.risk_item.impact if r.risk_item else None,
                "notes": r.notes,
            }
            for r in risks
        ],
        "document_deliverables": [],
    }


class CalculationResultOut(Schema):
    tasks: list[dict]
    total_tester_hours: float
    total_leader_hours: float
    pr_fix_hours: float
    pr_no_test_hours: float = 0
    study_hours: float
    release_extra_hours: float = 0
    documentation_hours: float = 0
    buffer_hours: float
    grand_total_hours: float
    grand_total_days: float
    feasibility_status: str
    capacity_hours: float
    utilization_pct: float
    elapsed_hours: float = 0
    elapsed_days: float = 0
    elapsed_weeks: float = 0
    risk_flags: list[str] = []
    risk_messages: list[str] = []


class CalibrationResultOut(Schema):
    accuracy_ratio: float
    adjustment_factor: float
    suggestion: str
    reference_projects: list[dict] = []
    adjusted_grand_total: float = 0


# ── Inputs ───────────────────────────────────────────────


class CalculateInput(BaseModel):
    """Calculation-only preview (no persistence)."""

    project_type: str
    features: list[int] = Field(default_factory=list, alias="feature_ids")
    new_features: list[int] = Field(default_factory=list, alias="new_feature_ids")
    reference_project_ids: list[int] = []
    dut_ids: list[int] = []
    profile_ids: list[int] = []
    dut_profile_matrix: list[list[int]] = []
    pr_fixes: PRFixInput = Field(default_factory=PRFixInput)
    team_size: int = 1
    has_leader: bool = False
    expected_delivery: OptionalDate = None
    working_days: int = 20
    delivery_date: OptionalDate = None
    expected_releases: int = 1
    risk_item_ids: list[int] = []
    document_type_ids: list[int] = []
    document_counts: dict[str, int] = {}

    model_config = ConfigDict(populate_by_name=True)

    @property
    def resolved_feature_ids(self) -> list[int]:
        return self.features

    @property
    def resolved_new_feature_ids(self) -> list[int]:
        return self.new_features


class EstimationRevise(BaseModel):
    """Wizard inputs shared by create, revise and draft edit."""

    project_name: str
    project_type: str
    feature_ids: list[int] = []
    new_feature_ids: list[int] = []
    reference_project_ids: list[int] = []
    dut_ids: list[int] = []
    profile_ids: list[int] = []
    dut_profile_matrix: list[list[int]] = []
    pr_fixes: PRFixInput = Field(default_factory=PRFixInput)
    pr_details: list[PRDetailItem] = []
    team_size: int = 1
    has_leader: bool = False
    start_date: OptionalDate = None
    expected_delivery: OptionalDate = None
    working_days: int = 20
    team_allocations: list[TeamAllocationItem] = []
    expected_releases: int = 1
    project_goals: str | None = None
    target_customer: str | None = None
    project_reference: str | None = None
    team_id: int | None = None
    risk_item_ids: list[int] = []
    document_type_ids: list[int] = []
    document_counts: dict[str, int] = {}
    task_assigned_testers: dict[str, int] = {}
    testing_start_date: str | None = None
    product_type_filter: str | None = None
    applied_presets: list[dict] = []


class EstimationCreate(EstimationRevise):
    request_id: int | None = None
    created_by: str | None = None


class EstimationUpdate(BaseModel):
    project_name: str | None = None
    project_type: str | None = None
    expected_delivery: OptionalDate = None
    notes: str | None = None
    project_goals: str | None = None
    target_customer: str | None = None
    project_reference: str | None = None


class EstimationStatusUpdate(BaseModel):
    status: str  # DRAFT, FINAL, APPROVED, REVISED
    approved_by: str | None = None


class EstimationReinit(BaseModel):
    """Confirmation payload for the destructive estimation reinitialize."""

    confirm: str
