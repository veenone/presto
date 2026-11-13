"""build_report_data / build_risk_messages / report_filename (ORM data assembly)."""

from __future__ import annotations

import re

import pytest

from apps.catalog.models import TaskTemplate
from apps.core import config
from apps.reports.builders import build_report_data, build_risk_messages, report_filename

from .factories import make_document_type, make_estimation, make_historical

pytestmark = pytest.mark.django_db


def test_basic_fields_and_names(seeded):
    est = make_estimation(with_request=True, with_team=True)
    data = build_report_data(est)
    assert data.project_name == "Report Test"
    assert data.estimation_number == "EST-2026-0001"
    assert data.request_number == "REQ_26/0015"
    assert data.business_unit == "Banking"
    assert data.priority == "HIGH"
    assert len(data.tasks) == 3
    assert data.dut_types and data.dut_types[0]["name"]
    assert data.profiles and data.profiles[0]["name"]
    assert data.pr_simple == 1
    assert [f["name"] for f in data.features_breakdown]
    assert data.team_members == [{
        "name": "Alice", "role": "TESTER", "hours_per_day": 7.0,
        "skills": '["SIM", "eSIM"]', "allocated_hours": 40.0,
    }]


def test_capacity_and_working_weeks(seeded):
    config.set_value("working_hours_per_day", "7")
    est = make_estimation(grand_total_days=12.5, wizard_extra={"has_leader": True})
    data = build_report_data(est)
    # (team_size 2 + leader) * 15 days * 7h
    assert data.capacity_hours == 315.0
    assert data.utilization_pct == pytest.approx(90.2 / 315.0 * 100)
    assert data.working_weeks == 2.5
    assert data.working_hours_per_day == 7.0


def test_zero_hours_per_day_falls_back(seeded):
    config.set_value("working_hours_per_day", "0")
    data = build_report_data(make_estimation())
    assert data.working_hours_per_day == 7.0


def test_document_deliverables_and_synthetic_task(seeded):
    linked = TaskTemplate.objects.order_by("id").first()  # included in the estimation's tasks
    dt_linked = make_document_type("Test Plan", 6.0, linked=linked)
    dt_free = make_document_type("Release Notes", 3.0)
    est = make_estimation(wizard_extra={
        "document_type_ids": [dt_linked.id, dt_free.id],
        "document_counts": {str(dt_linked.id): 2},
    })
    data = build_report_data(est)
    by_name = {d["name"]: d for d in data.document_deliverables}
    assert by_name["Test Plan"]["count"] == 2
    assert by_name["Test Plan"]["total_hours"] == 12.0
    # The linked template's hours are deducted once.
    assert by_name["Test Plan"]["effective_hours"] == pytest.approx(12.0 - linked.base_effort_hours)
    assert by_name["Test Plan"]["overlap_note"]
    assert by_name["Release Notes"]["effective_hours"] == 3.0
    doc_tasks = [t for t in data.tasks if t["task_type"] == "DOCUMENTATION"]
    assert {t["task_name"] for t in doc_tasks} == {
        f"{linked.name} (additional documentation)", "Documentation effort",
    }


def test_pr_test_creation_task(seeded):
    config.set_value("pr_no_test_hours", "5")
    est = make_estimation(wizard_extra={"pr_details": [
        {"pr_number": "PR-1", "complexity": "simple", "test_available": False},
        {"pr_number": "PR-2", "complexity": "medium", "test_available": True},
        {"pr_number": "PR-3", "complexity": "simple", "test_available": False},
    ]}, pr_no_test_hours=10.0)
    data = build_report_data(est)
    pr_tasks = [t for t in data.tasks if t["task_type"] == "PR_TEST_CREATION"]
    assert len(pr_tasks) == 1
    assert pr_tasks[0]["calculated_hours"] == 10.0
    assert "PR-1, PR-3" in pr_tasks[0]["notes"]
    assert data.pr_no_test_hours == 10.0
    assert len(data.pr_details) == 3


def test_reference_projects_and_presets(seeded):
    ref = make_historical()
    est = make_estimation(
        reference_project_ids=[ref.id],
        wizard_extra={"applied_presets": [{"name": "Basic"}, {"id": 3}, "x"]},
    )
    data = build_report_data(est)
    assert data.reference_projects[0]["project_name"] == "Old project"
    assert data.presets_used == ["Basic"]


def test_risk_messages_include_registry_items(seeded):
    est = make_estimation(with_risk=True)
    msgs = build_risk_messages(est)
    assert "[Supply] Late hardware (Likelihood: HIGH, Impact: LOW)" in msgs
    # No reference projects -> auto-assessment flags it.
    assert any("reference" in m.lower() for m in msgs)
    data = build_report_data(est)
    assert data.risks_detailed == [
        {"name": "Late hardware", "category": "Supply", "likelihood": "HIGH", "impact": "LOW"},
    ]


def test_report_filename(seeded):
    est = make_estimation(project_name="My Project / v2")
    assert re.fullmatch(r"EST-2026-0001-My_Project_v2-\d{12}\.pdf", report_filename(est, "pdf"))
    with_req = make_estimation(estimation_number="EST-2", with_request=True)
    assert report_filename(with_req, "xlsx").startswith("REQ_26_0015-Report_Test-")
    no_num = make_estimation(estimation_number=None)
    assert report_filename(no_num, "docx").startswith(f"EST-{no_num.id}-")
