"""ORM-dependent data assembly for the report generators.

Ported from v3 ``api/routes.py`` (``_build_report_data``, ``_build_risk_messages``,
``_report_filename``). The generators themselves stay pure and unchanged.
"""

from __future__ import annotations

import json
import re
from datetime import datetime

from apps.catalog.models import DutType, Feature, TestProfile
from apps.core import config
from apps.estimations import calc
from apps.estimations.models import Estimation, HistoricalProject
from engine.feasibility import assess_risks

from .excel_report import ExcelReportData


def _wizard(estimation: Estimation) -> dict:
    return estimation.wizard_inputs if isinstance(estimation.wizard_inputs, dict) else {}


def _reference_ids(estimation: Estimation) -> list:
    refs = estimation.reference_project_ids
    return list(refs) if isinstance(refs, list) else []


def _risk_items(estimation: Estimation):
    return [er.risk_item for er in estimation.risks.select_related("risk_item").order_by("id")]


def build_report_data(estimation: Estimation) -> ExcelReportData:
    """Build the shared report data object from an estimation."""
    req = estimation.request
    ref_ids = _reference_ids(estimation)
    ref_projects = []
    if ref_ids:
        ref_projects = [
            {
                "project_name": r.project_name,
                "project_type": r.project_type,
                "estimated_hours": r.estimated_hours,
                "actual_hours": r.actual_hours,
                "dut_count": r.dut_count,
                "profile_count": r.profile_count,
                "pr_count": r.pr_count,
            }
            for r in HistoricalProject.objects.filter(id__in=ref_ids).order_by("id")
        ]

    task_rows = list(estimation.tasks.all())
    tasks: list[dict] = [
        {
            "task_name": t.task_name,
            "task_type": t.task_type,
            "base_hours": t.base_hours,
            "calculated_hours": t.calculated_hours,
            "leader_hours": t.leader_hours,
            "is_new_feature_study": t.is_new_feature_study,
            "notes": t.notes or "",
            "feature_name": t.feature_name,
        }
        for t in task_rows
    ]

    wizard = _wizard(estimation)
    dut_ids = wizard.get("dut_ids", [])
    profile_ids = wizard.get("profile_ids", [])
    dut_profile_matrix = wizard.get("dut_profile_matrix", [])
    pr_fixes_data = wizard.get("pr_fixes", {}) or {}

    dut_types_data = []
    if dut_ids:
        dut_types_data = [
            {"id": d.id, "name": d.name} for d in DutType.objects.filter(id__in=dut_ids).order_by("id")
        ]

    profiles_data = []
    if profile_ids:
        profiles_data = [
            {"id": p.id, "name": p.name}
            for p in TestProfile.objects.filter(id__in=profile_ids).order_by("id")
        ]

    team_members_data = []
    for alloc in estimation.team_allocations.select_related("team_member").order_by("id"):
        tm = alloc.team_member
        team_members_data.append({
            "name": tm.name if tm else "Unknown",
            "role": alloc.role or (tm.role if tm else ""),
            "hours_per_day": tm.available_hours_per_day if tm else 7.0,
            # v3 passed the raw skills_json text; keep the generators' input identical.
            "skills": json.dumps(tm.skills) if tm else "[]",
            "allocated_hours": alloc.allocated_hours,
        })

    team_size = wizard.get("team_size", 1)
    has_leader = wizard.get("has_leader", False)
    working_days = wizard.get("working_days", 20)
    hours_per_day = config.get_float("working_hours_per_day", 7.0)
    if hours_per_day <= 0:
        # Report generators divide person-hours by this; never let it be zero.
        hours_per_day = 7.0
    capacity_hours = team_size * working_days * hours_per_day
    if has_leader:
        capacity_hours += working_days * hours_per_day
    utilization_pct = (
        (estimation.grand_total_hours / capacity_hours * 100) if capacity_hours > 0 else 0
    )

    included_template_ids = {t.task_template_id for t in task_rows if t.task_template_id}
    _, doc_deliverables = calc.calc_doc_hours_and_deliverables(
        wizard.get("document_type_ids", []), wizard.get("document_counts", {}), included_template_ids,
    )

    # Synthetic tasks so documentation excess and PR test creation appear in the breakdown.
    tasks.extend(calc.build_doc_synthetic_tasks(doc_deliverables))
    pr_details = wizard.get("pr_details", [])
    tasks.extend(calc.build_pr_no_test_synthetic_tasks(pr_details, calc.get_pr_config()["no_test_hours"]))

    grand_total_days = estimation.grand_total_days or 0
    working_weeks = round(grand_total_days / 5.0, 1)

    feature_ids_sel = wizard.get("feature_ids", [])
    new_feature_ids_sel = set(wizard.get("new_feature_ids", []))
    features_breakdown: list[dict] = []
    if feature_ids_sel:
        feat_by_id = {f.id: f for f in Feature.objects.filter(id__in=feature_ids_sel)}
        for fid in feature_ids_sel:
            f = feat_by_id.get(fid)
            if not f:
                continue
            features_breakdown.append({
                "name": f.name,
                "category": f.category or "",
                "complexity_weight": f.complexity_weight,
                "is_new": fid in new_feature_ids_sel,
                "has_existing_tests": bool(f.has_existing_tests),
                "description": f.description or "",
            })
    presets_used = [
        p.get("name") for p in wizard.get("applied_presets", [])
        if isinstance(p, dict) and p.get("name")
    ]

    risks_detailed = [
        {
            "name": ri.name,
            "category": ri.category or "General",
            "likelihood": ri.likelihood or "",
            "impact": ri.impact or "",
        }
        for ri in _risk_items(estimation) if ri
    ]

    return ExcelReportData(
        project_name=estimation.project_name,
        estimation_number=estimation.estimation_number or "",
        project_type=estimation.project_type,
        created_by=estimation.created_by,
        created_at=str(estimation.created_at.date()) if estimation.created_at else "",
        request_number=req.request_number if req else None,
        requester_name=req.requester_name if req else None,
        business_unit=req.business_unit if req else None,
        priority=req.priority if req else None,
        dut_count=estimation.dut_count,
        profile_count=estimation.profile_count,
        dut_profile_combinations=estimation.dut_profile_combinations,
        pr_fix_count=estimation.pr_fix_count,
        expected_delivery=str(estimation.expected_delivery) if estimation.expected_delivery else "",
        total_tester_hours=estimation.total_tester_hours,
        total_leader_hours=estimation.total_leader_hours,
        pr_fix_hours=estimation.pr_fix_hours,
        study_hours=estimation.study_hours,
        buffer_hours=estimation.buffer_hours,
        grand_total_hours=estimation.grand_total_hours,
        grand_total_days=estimation.grand_total_days,
        feasibility_status=estimation.feasibility_status,
        capacity_hours=capacity_hours,
        utilization_pct=utilization_pct,
        tasks=tasks,
        dut_types=dut_types_data,
        profiles=profiles_data,
        dut_profile_matrix=dut_profile_matrix,
        pr_simple=pr_fixes_data.get("simple", 0),
        pr_medium=pr_fixes_data.get("medium", 0),
        pr_complex=pr_fixes_data.get("complex", 0),
        reference_projects=ref_projects,
        pr_details=pr_details,
        team_members=team_members_data,
        team_size=team_size,
        has_leader=has_leader,
        risk_messages=build_risk_messages(estimation),
        release_extra_hours=estimation.release_extra_hours,
        documentation_hours=estimation.documentation_hours or 0,
        pr_no_test_hours=estimation.pr_no_test_hours or 0,
        project_goals=estimation.project_goals,
        target_customer=estimation.target_customer,
        version=estimation.version or 1,
        status=estimation.status,
        start_date=str(estimation.start_date) if estimation.start_date else "",
        testing_start_date=wizard.get("testing_start_date", ""),
        document_deliverables=doc_deliverables,
        working_weeks=working_weeks,
        working_hours_per_day=hours_per_day,
        features_breakdown=features_breakdown,
        presets_used=presets_used,
        risks_detailed=risks_detailed,
    )


def build_risk_messages(estimation: Estimation) -> list[str]:
    """Combined risk messages from the auto-assessment and the selected registry items."""
    wizard = _wizard(estimation)
    risks = assess_risks(
        total_features=len(wizard.get("feature_ids", [])),
        new_feature_count=len(wizard.get("new_feature_ids", [])),
        reference_project_count=len(_reference_ids(estimation)),
        delivery_date=estimation.expected_delivery,
        dut_profile_combinations=estimation.dut_profile_combinations,
    )
    messages = list(risks.messages)

    for ri in _risk_items(estimation):
        if ri:
            label = f"[{ri.category}] " if ri.category else ""
            level = ""
            if ri.likelihood or ri.impact:
                level = f" (Likelihood: {ri.likelihood or '?'}, Impact: {ri.impact or '?'})"
            messages.append(f"{label}{ri.name}{level}")
    return messages


def _clean_filename_part(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", (value or "").strip())
    return cleaned.strip("_") or "x"


def report_filename(estimation: Estimation, ext: str) -> str:
    """Report filename: ``<request id>-<title>-YYYYMMDDHHmm.<ext>``.

    Request id is the linked request's number when present, else the estimation
    number (or EST-<id>). Title is the project name. Both are sanitised.
    """
    req = estimation.request
    req_id = (
        req.request_number
        if req and req.request_number
        else (estimation.estimation_number or f"EST-{estimation.id}")
    )
    title = estimation.project_name or "estimation"
    stamp = datetime.now().strftime("%Y%m%d%H%M")
    return f"{_clean_filename_part(req_id)}-{_clean_filename_part(title)}-{stamp}.{ext}"
