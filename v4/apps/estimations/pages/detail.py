"""Estimation detail page (v3 ``/estimation/{id}``): hero, tabs, workflow and downloads.

The page body is one htmx region (``#est-detail``). Actions answer with
``HX-Trigger`` toasts plus ``est-refresh``, which reloads that region; the
selected tab survives because its state lives outside the region.
"""

from __future__ import annotations

import re

from django.shortcuts import render
from django.views.decorators.http import require_GET

from apps.accounts.models import User
from apps.core import config
from apps.estimations import calc, services, versions
from apps.estimations.models import Estimation, HistoricalProject
from apps.reports.builders import build_risk_messages
from apps.web.htmx import is_htmx

from .common import (
    ROLE_ARCHIVE,
    ROLE_ASSIGN,
    ROLE_EDIT,
    ROLE_EXPORT,
    ROLE_SEND_REPORT,
    ROLE_STATUS,
    can,
    get_estimation_or_404,
    is_author_or_admin,
    version_label,
)
from .markup import render_description

# v3 status button colours (Move to FINAL = primary, APPROVED = positive, REVISED = orange, DRAFT = grey).
STATUS_TONES = {"FINAL": "primary", "APPROVED": "positive", "REVISED": "warning", "DRAFT": "grey"}

HOUR_ITEMS = [
    ("Tester Hours", "total_tester_hours", "person"),
    ("Leader Hours", "total_leader_hours", "manage_accounts"),
    ("PR Fix Hours", "pr_fix_hours", "bug_report"),
    ("PR Test Creation", "pr_no_test_hours", "science"),
    ("Study Hours", "study_hours", "school"),
    ("Release Extra", "release_extra_hours", "rocket_launch"),
    ("Documentation", "documentation_hours", "description"),
    ("Buffer", "buffer_hours", "security"),
]


def _label(key: str, default: str) -> str:
    """Configured field label minus a trailing '(optional)' form hint (v3 ``_disp_label``)."""
    raw = config.get_str(key, "") or default
    return re.sub(r"\s*\(optional\)\s*$", "", raw, flags=re.I)


def _plural(n: int, word: str) -> str:
    return f"{n} {word}{'' if n == 1 else 's'}"


def _spec_rows(d: dict) -> list[dict]:
    rows = [
        ("Project Type", d["project_type"] or "—"),
        ("DUTs", d["dut_count"]),
        ("Profiles", d["profile_count"]),
        ("Combinations", d["dut_profile_combinations"]),
        (_label("label_project_start_date", "Start Date"), d["start_date"] or "—"),
        (_label("label_deadline", "Deadline"), d["expected_delivery"] or "—"),
        ("Created", d["created_at"].strftime("%Y-%m-%d") if d["created_at"] else "—"),
        ("Releases", d["expected_releases"] or 1),
    ]
    if d["team_name"]:
        rows.append(("Team", d["team_name"]))
    if d["project_reference"]:
        rows.append(("Reference", d["project_reference"]))
    if d["target_customer"]:
        rows.append(("Target Customer", d["target_customer"]))
    if d["created_by"]:
        rows.append(("Created By", d["created_by"]))
    if d["approved_by"]:
        rows.append(("Approved By", d["approved_by"]))
    out = [{"label": label, "value": value, "long": False} for label, value in rows]
    if d["project_goals"]:
        out.append({"label": "Project Goals", "value": d["project_goals"], "long": True})
    return out


def _hour_bars(d: dict) -> list[dict]:
    total = max(d["grand_total_hours"] or 1, 1)
    bars = []
    for label, key, icon in HOUR_ITEMS:
        val = d.get(key) or 0
        if val:
            bars.append({"label": label, "icon": icon, "value": val, "pct": f"{max(val / total * 100, 0.5):.1f}"})
    return bars


def _summary(text: str | None) -> str:
    """First non-empty line of a PR description without Markdown/Jira heading marks (plain text)."""
    for line in (text or "").splitlines():
        line = re.sub(r"^\s*(#{1,6}\s+|h[1-6]\.\s+)", "", line).strip()
        if line:
            return line[:90] + ("…" if len(line) > 90 else "")
    return ""


def _pr_context(est: Estimation, d: dict, wizard: dict) -> dict:
    cfg = calc.get_pr_config()
    rates = cfg["complexity_hours"]
    fixes = wizard.get("pr_fixes") or {}
    tiles = []
    for key, label in (("simple", "Simple"), ("medium", "Medium"), ("complex", "Complex")):
        count = int(fixes.get(key, 0) or 0)
        tiles.append({"label": label, "count": count, "rate": rates[key], "hours": count * rates[key]})
    details = []
    for pr in wizard.get("pr_details") or []:
        if not isinstance(pr, dict):
            continue
        details.append({
            "pr_number": pr.get("pr_number") or "?",
            "link": pr.get("link") if str(pr.get("link") or "").startswith(("http://", "https://")) else "",
            "raw_link": pr.get("link") or "",
            "description": render_description(pr.get("description")),
            "summary": _summary(pr.get("description")),
            "priority": pr.get("priority") or "",
            "complexity": pr.get("complexity") or "simple",
            "status": pr.get("status") or "",
            "test_available": pr.get("test_available", True) is not False,
        })
    no_test = calc.count_no_test_prs([p for p in wizard.get("pr_details") or [] if isinstance(p, dict)])
    return {
        "tiles": tiles, "details": details, "total": d["pr_fix_count"] or 0,
        "hours": d["pr_fix_hours"] or 0, "no_test": no_test, "no_test_rate": cfg["no_test_hours"],
        "no_test_hours": d["pr_no_test_hours"] or 0,
    }


def _risk_context(est: Estimation, d: dict) -> dict:
    messages = build_risk_messages(est)
    registry = sum(1 for r in d["risks"] if r["risk_item_name"] is not None)
    return {"items": d["risks"], "assessment": messages[:len(messages) - registry] if registry else messages}


def _edit_action(est: Estimation, user) -> dict | None:
    """v3: 'Resume editing' for a DRAFT (author or ADMIN), 'Edit' for a REVISED estimation."""
    if not can(user, ROLE_EDIT):
        return None
    if est.status == "DRAFT" and is_author_or_admin(est, user):
        return {"label": "Resume editing", "title": "Edit this draft in place (same version)"}
    if est.status == "REVISED":
        return {"label": "Edit", "title": f"Revise: saving creates version {(est.version or 1) + 1}"}
    return None


def detail_context(request, est: Estimation) -> dict:
    user = request.user
    d = services.estimation_detail(est)
    wizard = est.wizard_inputs if isinstance(est.wizard_inputs, dict) else {}
    tasks = d["tasks"]
    for t in tasks:
        t["synthetic"] = t["id"] == 0
    deliverables = d["document_deliverables"]
    allocations = d["team_allocations"]
    version_list = versions.list_versions(est)
    status = est.status or "DRAFT"
    archived = HistoricalProject.objects.filter(estimation=est).first()

    sub = []
    if est.dut_count:
        sub.append(_plural(est.dut_count, "DUT"))
    if est.profile_count:
        sub.append(_plural(est.profile_count, "profile"))
    if est.dut_profile_combinations:
        sub.append(f"{est.dut_profile_combinations} combinations")
    if (est.expected_releases or 1) > 1:
        sub.append(f"{est.expected_releases} releases")

    perms = {
        "status": can(user, ROLE_STATUS),
        "export": can(user, ROLE_EXPORT),
        "send_report": can(user, ROLE_SEND_REPORT),
        "assign": can(user, ROLE_ASSIGN),
        "archive": can(user, ROLE_ARCHIVE) and status == "APPROVED" and archived is None,
    }
    feas = est.feasibility_status or ""
    return {
        "page_title": f"{est.estimation_number or f'EST-{est.id}'} · {est.project_name}",
        "est": est,
        "d": d,
        "number": est.estimation_number,
        "number_label": version_label(est),
        "version": est.version or 1,
        "feasibility": feas,
        "status": status,
        "stripe": feas if feas in ("FEASIBLE", "AT_RISK", "NOT_FEASIBLE") else "default",
        "hero_sub": " · ".join(sub),
        "weeks": round((est.grand_total_days or 0) / 5.0, 1) if est.grand_total_days else 0,
        "spec": _spec_rows(d),
        "bars": _hour_bars(d),
        "tasks": tasks,
        "tasks_total": sum(t["calculated_hours"] or 0 for t in tasks),
        "synthetic_count": sum(1 for t in tasks if t["synthetic"]),
        "deliverables": deliverables,
        "overlap_count": sum(1 for dd in deliverables if dd.get("overlap_note")),
        "pr": _pr_context(est, d, wizard),
        "allocations": allocations,
        "alloc_total": sum(a["allocated_hours"] or 0 for a in allocations),
        "risks": _risk_context(est, d),
        "versions": version_list,
        "version_default_a": version_list[0]["version"],
        "version_default_b": version_list[-1]["version"],
        "transitions": [{"target": t, "tone": STATUS_TONES.get(t, "grey")}
                        for t in services.VALID_TRANSITIONS.get(status, [])],
        "edit_action": _edit_action(est, user),
        "archived": archived,
        "perms": perms,
        "users": (list(User.objects.filter(is_active=True).order_by("display_name", "username"))
                  if perms["assign"] else []),
        "assigned_name": d["assigned_to_name"],
        "has_references": bool(est.reference_project_ids),
        "tab_counts": {
            "tasks": len(tasks), "docs": len(deliverables), "prs": d["pr_fix_count"] or 0,
            "team": len(allocations), "risks": len(d["risks"]), "versions": len(version_list),
        },
    }


@require_GET
def detail_view(request, estimation_id: int):
    est = get_estimation_or_404(estimation_id)
    ctx = detail_context(request, est)
    if is_htmx(request) and request.headers.get("HX-Target") == "est-detail":
        return render(request, "estimations/detail/_region.html", ctx)
    return render(request, "estimations/detail/page.html", ctx)
