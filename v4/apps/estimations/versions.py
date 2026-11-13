"""Estimation version snapshots, history listing and version diffs (v3 behaviour)."""

from __future__ import annotations

from typing import Any

from .models import Estimation, EstimationVersionSnapshot

DIFF_FIELDS = [
    ("project_name", "Project Name"),
    ("project_type", "Project Type"),
    ("dut_count", "DUT Count"),
    ("profile_count", "Profile Count"),
    ("dut_profile_combinations", "DUT x Profile Combinations"),
    ("pr_fix_count", "PR Fix Count"),
    ("start_date", "Start Date"),
    ("expected_delivery", "Expected Delivery"),
    ("total_tester_hours", "Total Tester Hours"),
    ("total_leader_hours", "Total Leader Hours"),
    ("pr_fix_hours", "PR Fix Hours"),
    ("study_hours", "Study Hours"),
    ("buffer_hours", "Buffer Hours"),
    ("grand_total_hours", "Grand Total Hours"),
    ("grand_total_days", "Grand Total Days"),
    ("feasibility_status", "Feasibility"),
    ("expected_releases", "Expected Releases"),
    ("release_extra_hours", "Release Extra Hours"),
    ("project_goals", "Project Goals"),
    ("target_customer", "Target Customer"),
]

WIZARD_LIST_KEYS = ["feature_ids", "new_feature_ids", "dut_ids", "profile_ids"]


class VersionNotFound(Exception):
    def __init__(self, version: int) -> None:
        super().__init__(f"Version {version} snapshot not found")
        self.version = version


def _base_state(e: Estimation) -> dict[str, Any]:
    return {
        "project_name": e.project_name,
        "project_type": e.project_type,
        "dut_count": e.dut_count,
        "profile_count": e.profile_count,
        "dut_profile_combinations": e.dut_profile_combinations,
        "pr_fix_count": e.pr_fix_count,
        "start_date": str(e.start_date) if e.start_date else None,
        "expected_delivery": str(e.expected_delivery) if e.expected_delivery else None,
        "total_tester_hours": e.total_tester_hours,
        "total_leader_hours": e.total_leader_hours,
        "pr_fix_hours": e.pr_fix_hours,
        "study_hours": e.study_hours,
        "buffer_hours": e.buffer_hours,
        "grand_total_hours": e.grand_total_hours,
        "grand_total_days": e.grand_total_days,
        "feasibility_status": e.feasibility_status,
        "status": e.status,
        "expected_releases": e.expected_releases,
        "release_extra_hours": e.release_extra_hours,
        "project_goals": e.project_goals,
        "target_customer": e.target_customer,
        "wizard_inputs": e.wizard_inputs if isinstance(e.wizard_inputs, dict) else {},
        "tasks": [
            {"task_name": t.task_name, "task_type": t.task_type, "base_hours": t.base_hours,
             "calculated_hours": t.calculated_hours}
            for t in e.tasks.order_by("id")
        ],
    }


def snapshot_estimation(e: Estimation) -> dict[str, Any]:
    """Persisted state of an estimation for version history (v3 ``_snapshot_estimation``)."""
    data = _base_state(e)
    data["team_allocations"] = [
        {"member_name": a.team_member.name if a.team_member else "Unknown", "role": a.role,
         "allocated_hours": a.allocated_hours}
        for a in e.team_allocations.select_related("team_member").order_by("id")
    ]
    data["risks"] = [
        {"risk_name": r.risk_item.name if r.risk_item else "Unknown", "notes": r.notes}
        for r in e.risks.select_related("risk_item").order_by("id")
    ]
    return data


def list_versions(e: Estimation) -> list[dict[str, Any]]:
    """Snapshots in version order followed by the current version."""
    versions = []
    for snap in EstimationVersionSnapshot.objects.filter(estimation=e).order_by("version"):
        data = snap.snapshot if isinstance(snap.snapshot, dict) else {}
        versions.append({
            "id": snap.id,
            "version": snap.version,
            "created_at": snap.created_at.isoformat() if snap.created_at else None,
            "project_name": data.get("project_name", ""),
            "grand_total_hours": data.get("grand_total_hours", 0),
            "feasibility_status": data.get("feasibility_status", ""),
            "status": data.get("status", ""),
        })
    versions.append({
        "id": None,
        "version": e.version,
        "created_at": e.created_at.isoformat() if e.created_at else None,
        "project_name": e.project_name,
        "grand_total_hours": e.grand_total_hours,
        "feasibility_status": e.feasibility_status,
        "status": e.status,
        "is_current": True,
    })
    return versions


def _version_data(e: Estimation, ver: int) -> dict[str, Any]:
    if ver == e.version:
        return _base_state(e)
    snap = EstimationVersionSnapshot.objects.filter(estimation=e, version=ver).first()
    if not snap:
        raise VersionNotFound(ver)
    return snap.snapshot if isinstance(snap.snapshot, dict) else {}


def diff_versions(e: Estimation, version_a: int, version_b: int) -> dict[str, Any]:
    """Field, task and wizard-input differences between two versions."""
    data_a = _version_data(e, version_a)
    data_b = _version_data(e, version_b)

    changes = []
    for key, label in DIFF_FIELDS:
        val_a, val_b = data_a.get(key), data_b.get(key)
        if val_a != val_b:
            changes.append({"field": label, "version_a": val_a, "version_b": val_b})

    tasks_a = data_a.get("tasks", [])
    tasks_b = data_b.get("tasks", [])
    names_a = {t["task_name"] for t in tasks_a}
    names_b = {t["task_name"] for t in tasks_b}
    added = [t for t in tasks_b if t["task_name"] not in names_a]
    removed = [t for t in tasks_a if t["task_name"] not in names_b]
    modified = []
    for tb in tasks_b:
        ta = next((t for t in tasks_a if t["task_name"] == tb["task_name"]), None)
        if ta and ta.get("calculated_hours") != tb.get("calculated_hours"):
            modified.append({
                "task_name": tb["task_name"],
                "hours_a": ta.get("calculated_hours", 0),
                "hours_b": tb.get("calculated_hours", 0),
            })

    wi_a = data_a.get("wizard_inputs", {})
    wi_b = data_b.get("wizard_inputs", {})
    input_changes = {}
    for key in WIZARD_LIST_KEYS:
        set_a, set_b = set(wi_a.get(key, [])), set(wi_b.get(key, []))
        if set_a != set_b:
            input_changes[key] = {"added": sorted(set_b - set_a), "removed": sorted(set_a - set_b)}

    return {
        "estimation_id": e.id,
        "version_a": version_a,
        "version_b": version_b,
        "changes": changes,
        "added_tasks": added,
        "removed_tasks": removed,
        "modified_tasks": modified,
        "input_changes": input_changes,
    }
