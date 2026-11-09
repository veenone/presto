"""Shared estimation helpers used by calculation, detail, report and export code.

Ported from the helper section at the top of v3 ``api/routes.py``; behaviour
is unchanged (including the once-per-template documentation overlap rule).
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from apps.catalog.models import DocumentType, PublicHoliday, TaskTemplate
from apps.core import config

from .models import Estimation


def get_pr_config() -> dict[str, Any]:
    """PR fix configuration from Settings (v3 ``_get_pr_config``)."""
    return {
        "complexity_hours": {
            "simple": config.get_float("pr_hours_simple", 2.0),
            "medium": config.get_float("pr_hours_medium", 4.0),
            "complex": config.get_float("pr_hours_complex", 8.0),
        },
        "no_test_hours": config.get_float("pr_no_test_hours", 8.0),
        "no_test_task_template_id": config.get_str("pr_no_test_task_template_id", ""),
    }


def count_no_test_prs(pr_details: list[dict]) -> int:
    return sum(1 for pr in pr_details if not pr.get("test_available", True))


def calc_doc_hours_and_deliverables(
    doc_type_ids: list[int],
    doc_counts: dict[str, int],
    included_template_ids: set[int],
) -> tuple[float, list[dict]]:
    """Documentation hours and deliverables with overlap info.

    When several document types link to the same task template that is already
    in the estimation, that template's hours are deducted once from the group
    total, and effective hours are split proportionally.
    """
    if not doc_type_ids:
        return 0.0, []

    dt_map = {
        dt.id: dt
        for dt in DocumentType.objects.select_related("task_template").filter(id__in=doc_type_ids)
    }
    raw_entries: list[dict] = []
    template_groups: dict[int, list[int]] = {}
    for dtid in doc_type_ids:
        dt = dt_map.get(dtid)
        if not dt:
            continue
        count = doc_counts.get(str(dtid), 1)
        total_hours = dt.base_effort_hours * count
        idx = len(raw_entries)
        raw_entries.append({
            "name": dt.name,
            "category": dt.category,
            "count": count,
            "base_effort_hours": dt.base_effort_hours,
            "total_hours": total_hours,
            "effective_hours": total_hours,
            "overlap_note": None,
            "linked_task": dt.task_template.name if dt.task_template else None,
            "_task_template_id": dt.task_template_id,
        })
        if dt.task_template_id and dt.task_template_id in included_template_ids:
            template_groups.setdefault(dt.task_template_id, []).append(idx)

    for tmpl_id, indices in template_groups.items():
        linked = TaskTemplate.objects.filter(pk=tmpl_id).first()
        if not linked:
            continue
        deducted = linked.base_effort_hours
        group_total = sum(raw_entries[i]["total_hours"] for i in indices)
        group_effective = max(0.0, group_total - deducted)
        for i in indices:
            entry = raw_entries[i]
            proportion = entry["total_hours"] / group_total if group_total > 0 else 0
            entry["effective_hours"] = round(group_effective * proportion, 2)
            entry["overlap_note"] = (
                f"Deducted {deducted:.1f}h across group (already in task '{linked.name}')"
            )

    deliverables = []
    documentation_hours = 0.0
    for entry in raw_entries:
        entry.pop("_task_template_id", None)
        deliverables.append(entry)
        documentation_hours += entry["effective_hours"]
    return documentation_hours, deliverables


def build_doc_synthetic_tasks(deliverables: list[dict]) -> list[dict]:
    """One synthetic DOCUMENTATION task per linked task with remaining hours."""
    groups: dict[str, float] = {}
    doc_names: dict[str, list[str]] = {}
    for dd in deliverables:
        if dd["effective_hours"] <= 0:
            continue
        key = dd.get("linked_task") or ""
        groups[key] = groups.get(key, 0) + dd["effective_hours"]
        doc_names.setdefault(key, []).append(dd["name"])

    tasks: list[dict] = []
    for key, hours in groups.items():
        tasks.append({
            "task_name": f"{key} (additional documentation)" if key else "Documentation effort",
            "task_type": "DOCUMENTATION",
            "base_hours": round(hours, 2),
            "calculated_hours": round(hours, 2),
            "leader_hours": 0,
            "is_new_feature_study": False,
            "notes": f"Additional effort for: {', '.join(doc_names[key])}",
        })
    return tasks


def build_pr_no_test_synthetic_tasks(pr_details: list[dict], no_test_hours: float) -> list[dict]:
    """One consolidated PR_TEST_CREATION task for PRs without existing tests."""
    no_test_prs = [pr for pr in pr_details if not pr.get("test_available", True)]
    if not no_test_prs:
        return []
    count = len(no_test_prs)
    pr_ids = ", ".join(pr.get("pr_number", "?") for pr in no_test_prs)
    return [{
        "task_name": "Test Creation (PR without existing tests)",
        "task_type": "PR_TEST_CREATION",
        "base_hours": no_test_hours,
        "calculated_hours": round(count * no_test_hours, 2),
        "leader_hours": 0,
        "is_new_feature_study": False,
        "notes": f"{count} PR(s) without tests: {pr_ids} ({no_test_hours:.1f}h each)",
    }]


def compute_doc_deliverables(estimation: Estimation) -> list[dict]:
    """Document deliverables for a saved estimation, from its wizard inputs."""
    wizard = estimation.wizard_inputs if isinstance(estimation.wizard_inputs, dict) else {}
    included = {
        tid for tid in estimation.tasks.values_list("task_template_id", flat=True) if tid
    }
    _, deliverables = calc_doc_hours_and_deliverables(
        wizard.get("document_type_ids", []), wizard.get("document_counts", {}), included,
    )
    return deliverables


def holiday_dates(start_year: int, years: int = 3) -> set[date]:
    """Public holidays, with recurring ones expanded over ``years`` years."""
    days: set[date] = set()
    for h in PublicHoliday.objects.all():
        if h.is_recurring:
            for y in range(start_year, start_year + years):
                try:
                    days.add(date(y, h.date.month, h.date.day))
                except ValueError:  # 29 Feb in a non-leap year
                    pass
        else:
            days.add(h.date)
    return days


def compute_completion_date(start: date, elapsed_days: float) -> date:
    """Walk forward ``elapsed_days`` working days, skipping weekends and holidays."""
    holidays = holiday_dates(start.year)
    remaining = elapsed_days
    current = start
    while remaining > 0:
        if current.weekday() < 5 and current not in holidays:
            remaining -= 1
        current += timedelta(days=1)
    return current - timedelta(days=1)
