"""The seven wizard steps: field parsing, validation and render context.

``parse(step, post, data)`` reads one step's form fields and returns the
updates for the state, ``meta`` updates and errors. Type errors leave the old
value in place; rule errors (``check``) use the v3 messages. ``check`` also
runs on the saved state before the final save, so a step skipped through the
progress strip cannot slip past its rules.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date
from itertools import zip_longest
from typing import Any

from django.db.models import Q

from apps.accounts.roles import has_role
from apps.catalog import services as catalog_services
from apps.catalog.models import DocumentType, DutType, Feature, RiskItem, TestProfile
from apps.core import config
from apps.core import services as core_services
from apps.estimations.models import Estimation, HistoricalProject
from apps.teams.models import Team, TeamMember

from . import state as st


@dataclass(frozen=True)
class Step:
    number: int
    title: str
    short: str
    intro: str


STEPS: tuple[Step, ...] = (
    Step(1, "Project Info", "Project Info", "Enter the basic project details."),
    Step(2, "Features", "Features",
         "Select the features under test. Toggle 'New' for features that require study time."),
    Step(3, "Reference Projects", "References",
         "Pick historical projects to use as baselines for calibration (optional)."),
    Step(4, "DUT x Profile Matrix", "DUTs / Profiles",
         "Select the DUTs and Profiles to test, then tick the combinations you actually need."),
    Step(5, "PR Fixes", "PR Fixes",
         "Enter the expected number of PR fixes by complexity. These add fixed effort per PR."),
    Step(6, "Delivery & Team", "Schedule",
         "Specify the start date, deadline, and team capacity for feasibility assessment."),
    Step(7, "Review & Save", "Review", "Review your inputs, run the calculation, then save."),
)
LAST = len(STEPS)

PR_COMPLEXITIES = ["simple", "medium", "complex"]
PR_STATUSES = ["Open", "In Progress", "Postponed", "Merged", "Closed"]
ALLOC_ROLES = ["TESTER", "LEADER"]


def clamp_step(value) -> int:
    try:
        n = int(value)
    except (TypeError, ValueError):
        return 1
    return min(max(n, 1), LAST)


# ── Parsing helpers ──────────────────────────────────────


def _int(value) -> int | None:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def _float(value) -> float | None:
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return None


def _ids(post, name: str) -> list[int]:
    out: list[int] = []
    for raw in post.getlist(name):
        v = _int(raw)
        if v is not None and v not in out:
            out.append(v)
    return out


def _text(post, name: str) -> str | None:
    value = (post.get(name) or "").strip()
    return value or None


def _date(post, name: str, errors: dict) -> tuple[bool, str | None]:
    raw = (post.get(name) or "").strip()
    if not raw:
        return True, None
    try:
        return True, date.fromisoformat(raw).isoformat()
    except ValueError:
        errors[name] = "Enter a valid date (YYYY-MM-DD)."
        return False, None


def _count(post, name: str, errors: dict, minimum: int, message: str) -> int | None:
    v = _int(post.get(name))
    if v is None or v < minimum:
        errors[name] = message
        return None
    return v


# ── Rules (v3 messages) ──────────────────────────────────


def check(step: int, data: dict[str, Any]) -> dict[str, str]:
    errors: dict[str, str] = {}
    if step == 1:
        if not (data.get("project_name") or "").strip():
            errors["project_name"] = "Project Name is required."
        if data.get("project_type") == "CHANGE_REQUEST" and not (data.get("project_reference") or "").strip():
            errors["project_reference"] = "Project Reference is required for Change Request."
    elif step == 2:
        if not data.get("feature_ids"):
            errors["feature_ids"] = "Select at least one feature to continue."
    elif step == 4:
        if not data.get("dut_ids"):
            errors["dut_ids"] = "Select at least one DUT."
        elif not data.get("profile_ids"):
            errors["profile_ids"] = "Select at least one Profile."
        elif not data.get("dut_profile_matrix"):
            errors["matrix"] = "Tick at least one DUT×Profile combination in the matrix."
    elif step == 6:
        if (data.get("team_size") or 0) < 1:
            errors["team_size"] = "Team size must be at least 1."
    return errors


def first_invalid_step(data: dict[str, Any]) -> tuple[int, dict[str, str]] | None:
    for s in STEPS:
        errors = check(s.number, data)
        if errors:
            return s.number, errors
    return None


# ── Parsing per step ─────────────────────────────────────


def parse(step: int, post, data: dict[str, Any]) -> tuple[dict, dict, dict]:
    """(state updates, meta updates, errors) for the posted fields of ``step``."""
    errors: dict[str, str] = {}
    meta: dict[str, Any] = {}
    up: dict[str, Any] = {}
    if step == 1:
        up = {
            "project_name": (post.get("project_name") or "").strip(),
            "project_type": (post.get("project_type") or "").strip() or data.get("project_type"),
            "project_reference": _text(post, "project_reference"),
            "project_goals": _text(post, "project_goals"),
            "target_customer": _text(post, "target_customer"),
            "product_type_filter": (post.get("product_type_filter") or "").strip() or "All",
        }
        if "description" in post:
            meta["description"] = post.get("description") or ""
    elif step == 2:
        fids = _ids(post, "feature_ids")
        up = {"feature_ids": fids, "new_feature_ids": [f for f in _ids(post, "new_feature_ids") if f in fids]}
    elif step == 3:
        up = {"reference_project_ids": _ids(post, "reference_project_ids")}
    elif step == 4:
        duts, profs = _ids(post, "dut_ids"), _ids(post, "profile_ids")
        matrix: list[list[int]] = []
        for raw in post.getlist("matrix"):
            d, _, p = str(raw).partition(":")
            pair = [_int(d), _int(p)]
            if pair[0] in duts and pair[1] in profs and pair not in matrix:
                matrix.append(pair)
        up = {"dut_ids": duts, "profile_ids": profs, "dut_profile_matrix": matrix}
    elif step == 5:
        up = _parse_prs(post, data, errors)
    elif step == 6:
        up = _parse_delivery(post, errors)
    elif step == 7 and post.get("_tasks"):
        testers: dict[str, int] = {}
        for name, raw in zip(post.getlist("tt_name"), post.getlist("tt_value"), strict=False):
            v = _int(raw)
            if v is not None and v > 1:
                testers[name] = v
        up = {"task_assigned_testers": testers}
    merged = {**data, **up}
    errors.update({k: v for k, v in check(step, merged).items() if k not in errors})
    return up, meta, errors


def _parse_prs(post, data: dict, errors: dict) -> dict:
    up: dict[str, Any] = {}
    msg = "Enter a whole number of 0 or more."
    counts = dict(data.get("pr_fixes") or {})
    for key in ("simple", "medium", "complex"):
        v = _count(post, f"pr_{key}", errors, 0, msg)
        if v is not None:
            counts[key] = v
    up["pr_fixes"] = counts
    rows = []
    for num, link, pri, cx, status, desc, test in zip_longest(
        post.getlist("pr_number"), post.getlist("pr_link"), post.getlist("pr_priority"),
        post.getlist("pr_complexity"), post.getlist("pr_status"), post.getlist("pr_description"),
        post.getlist("pr_test"), fillvalue="",
    ):
        num = (num or "").strip()
        if not num:
            continue
        rows.append({
            "pr_number": num,
            "link": (link or "").strip(),
            "description": desc or "",
            "priority": (pri or "").strip(),
            "complexity": cx if cx in PR_COMPLEXITIES else "simple",
            "status": (status or "").strip() or "Open",
            "test_available": test != "0",
        })
    up["pr_details"] = rows
    doc_ids = _ids(post, "doc_ids")
    counts_by_doc: dict[str, int] = {}
    for raw_id in post.getlist("doc_all"):
        did = _int(raw_id)
        if did is None:
            continue
        v = _int(post.get(f"doc_count_{did}"))
        if v is None or v < 1:
            if did in doc_ids:
                errors[f"doc_count_{did}"] = "At least 1."
            v = 1
        counts_by_doc[str(did)] = v
    up["document_type_ids"] = doc_ids
    up["document_counts"] = counts_by_doc
    return up


def _parse_delivery(post, errors: dict) -> dict:
    up: dict[str, Any] = {}
    for field, key in (("start_date", "start_date"), ("testing_start_date", "testing_start_date"),
                       ("expected_delivery", "expected_delivery")):
        ok, value = _date(post, field, errors)
        if ok:
            up[key] = value
    wd = _count(post, "working_days", errors, 1, "Working days must be at least 1.")
    if wd is not None:
        up["working_days"] = wd
    ts = _count(post, "team_size", errors, 1, "Team size must be at least 1.")
    if ts is not None:
        up["team_size"] = ts
    rel = _count(post, "expected_releases", errors, 1, "Expected releases must be at least 1.")
    if rel is not None:
        up["expected_releases"] = rel
    up["has_leader"] = post.get("has_leader") in ("on", "1", "true")
    up["team_id"] = _int(post.get("team_id")) or None
    allocs = []
    for member, role, hours in zip_longest(post.getlist("alloc_member"), post.getlist("alloc_role"),
                                           post.getlist("alloc_hours"), fillvalue=""):
        mid = _int(member)
        if not mid:
            continue
        hrs = _float(hours)
        allocs.append({"team_member_id": mid, "role": role if role in ALLOC_ROLES else "TESTER",
                       "allocated_hours": max(hrs, 0.0) if hrs is not None else 0.0})
    up["team_allocations"] = allocs
    up["risk_item_ids"] = _ids(post, "risk_item_ids")
    return up


# ── Render context ───────────────────────────────────────


def product_types() -> list[str]:
    """Settings product types plus any used by features, DUTs or profiles (v3 derived them)."""
    found = {str(p) for p in core_services.product_types() if p}
    for model in (Feature, DutType, TestProfile):
        found.update(p for p in model.objects.exclude(product_type__isnull=True)
                     .exclude(product_type="").values_list("product_type", flat=True).distinct())
    return sorted(found)


def _pt_filter(qs, pt: str | None):
    if pt and pt != "All":
        qs = qs.filter(Q(product_type=pt) | Q(product_type__isnull=True))
    return qs


def _as_json(value) -> str:
    return json.dumps(value, default=str)


def context(step: int, wiz: st.Wizard) -> dict[str, Any]:
    data = wiz.data
    builder = {1: _ctx_info, 2: _ctx_features, 3: _ctx_refs, 4: _ctx_matrix, 5: _ctx_prs,
               6: _ctx_delivery, 7: _ctx_review}[step]
    return builder(wiz, data)


def _ctx_info(wiz, data) -> dict:
    types = core_services.project_types()
    if data.get("project_type") and data["project_type"] not in types:
        types = [*types, data["project_type"]]
    refs = []
    for num, name in Estimation.objects.order_by("-id").values_list("estimation_number", "project_name"):
        refs.append(f"{num} — {name}" if num else name)
    return {"project_types": types, "product_types": product_types(), "reference_options": refs}


def project_features(wiz) -> list[Feature]:
    if not wiz.draft_id:
        return []
    return list(Feature.objects.filter(is_global=False, owner_estimation_id=wiz.draft_id).order_by("id"))


def _ctx_features(wiz, data) -> dict:
    pt = data.get("product_type_filter") or "All"
    cond = Q(is_global=True)
    if wiz.draft_id:
        cond |= Q(owner_estimation_id=wiz.draft_id)
    feats = list(_pt_filter(Feature.objects.filter(cond), pt).order_by("id"))
    selected, new = set(data.get("feature_ids", [])), set(data.get("new_feature_ids", []))
    groups: dict[str, list[dict]] = {}
    for f in feats:
        groups.setdefault(f.category or "Other", []).append({
            "id": f.id, "name": f.name, "description": (f.description or "").strip(),
            "weight": f.complexity_weight, "is_project": not f.is_global,
            "checked": f.id in selected, "new": f.id in new,
        })
    presets = catalog_services.list_feature_presets(pt if pt != "All" else None)
    hidden = [fid for fid in selected if fid not in {f.id for f in feats}]
    return {
        "groups": groups, "feature_count": len(feats), "categories": list(groups),
        "all_selected": bool(feats) and all(f.id in selected for f in feats),
        "presets": presets, "hidden_selected": len(hidden),
        "can_custom": wiz.mode != st.REVISION,
    }


def _ctx_refs(wiz, data) -> dict:
    sel = set(data.get("reference_project_ids", []))
    rows = []
    for p in HistoricalProject.objects.order_by("id"):
        est, act = p.estimated_hours or 0, p.actual_hours or 0
        rows.append({"id": p.id, "name": p.project_name, "type": p.project_type,
                     "accuracy": (act / est) if est else None, "checked": p.id in sel,
                     "estimated": p.estimated_hours, "actual": p.actual_hours})
    return {"projects": rows}


def _ctx_matrix(wiz, data) -> dict:
    pt = data.get("product_type_filter") or "All"
    duts = [{"id": d.id, "name": d.name, "category": d.category or ""}
            for d in _pt_filter(DutType.objects.order_by("id"), pt)]
    profs = [{"id": p.id, "name": p.name}
             for p in _pt_filter(TestProfile.objects.order_by("id"), pt)]
    return {
        "duts": duts, "profiles": profs, "pt": pt,
        "matrix_json": _as_json({
            "duts": duts, "profiles": profs,
            "selDuts": [str(i) for i in data.get("dut_ids", [])],
            "selProfs": [str(i) for i in data.get("profile_ids", [])],
            "cells": [f"{d}:{p}" for d, p in data.get("dut_profile_matrix", [])],
        }),
    }


def pr_priorities() -> list[str]:
    return config.get_list("pr_priority_list", ["LOW", "MEDIUM", "HIGH", "CRITICAL"]) or ["MEDIUM"]


def _ctx_prs(wiz, data) -> dict:
    from apps.integrations import service as integrations

    hours = {k: config.get_float(f"pr_hours_{k}", d) for k, d in (("simple", 2.0), ("medium", 4.0),
                                                                   ("complex", 8.0))}
    pri = pr_priorities()
    default_pri = pri[1] if len(pri) > 1 else pri[0]
    rows = [{"num": r.get("pr_number", ""), "link": r.get("link") or "", "pri": r.get("priority") or default_pri,
             "cx": r.get("complexity") or "simple", "status": r.get("status") or "Open",
             "desc": r.get("description") or "", "test": r.get("test_available", True), "open": False}
            for r in data.get("pr_details", [])]
    priorities = list(dict.fromkeys([*pri, *(r["pri"] for r in rows if r["pri"])]))
    statuses = list(dict.fromkeys([*PR_STATUSES, *(r["status"] for r in rows)]))
    doc_ids = set(data.get("document_type_ids", []))
    counts = data.get("document_counts", {})
    docs = [{"id": d.id, "name": d.name, "category": d.category, "hours": d.base_effort_hours,
             "checked": d.id in doc_ids, "count": int(counts.get(str(d.id), 1) or 1),
             "err_key": f"doc_count_{d.id}"}
            for d in DocumentType.objects.filter(is_active=True).order_by("name")]
    pr = data.get("pr_fixes") or {}
    return {
        "hours": hours, "docs": docs,
        "pr_json": _as_json({
            "rows": rows, "priorities": priorities, "complexities": PR_COMPLEXITIES, "statuses": statuses,
            "defaultPri": default_pri, "hours": hours,
            "s": pr.get("simple", 0), "m": pr.get("medium", 0), "c": pr.get("complex", 0),
        }),
        "jira_configured": integrations.jira_pr_configured(),
    }


def date_labels() -> dict[str, str]:
    return {
        "start": config.get_str("label_project_start_date") or "Project Start Date - T0 (optional)",
        "testing": config.get_str("label_testing_start_date") or "Testing Start Date (optional)",
        "deadline": config.get_str("label_deadline") or "Deadline (optional)",
    }


def _ctx_delivery(wiz, data) -> dict:
    members = [{"id": m.id, "name": m.name, "role": m.role, "team": m.team_id}
               for m in TeamMember.objects.order_by("name")]
    risks: dict[str, list] = {}
    sel = set(data.get("risk_item_ids", []))
    for r in RiskItem.objects.filter(is_active=True).order_by("id"):
        risks.setdefault(r.category or "General", []).append(
            {"id": r.id, "name": r.name, "likelihood": r.likelihood, "impact": r.impact, "checked": r.id in sel})
    allocs = [{"member": str(a.get("team_member_id") or ""), "role": a.get("role") or "TESTER",
               "hours": a.get("allocated_hours") or 0} for a in data.get("team_allocations", [])]
    return {
        "labels": date_labels(), "teams": list(Team.objects.order_by("name")), "risk_groups": risks,
        "has_members": bool(members),
        "team_json": _as_json({"members": members, "rows": allocs,
                               "team": str(data.get("team_id") or "")}),
    }


def _ctx_review(wiz, data) -> dict:
    user = wiz.request.user
    return {
        "custom_features": project_features(wiz),
        "can_promote": has_role(user.role, "APPROVER"),
        "summary": summary(data),
    }


def summary(data: dict) -> list[tuple[str, str]]:
    pr = data.get("pr_fixes") or {}
    team = f"{data.get('team_size', 1)} tester(s)" + (" + leader" if data.get("has_leader") else "")
    items = [
        ("Project", data.get("project_name") or "—"),
        ("Type", data.get("project_type") or "—"),
        ("Features", f"{len(data.get('feature_ids', []))} selected, {len(data.get('new_feature_ids', []))} new"),
        ("Reference Projects", str(len(data.get("reference_project_ids", [])))),
        ("DUTs x Profiles", f"{len(data.get('dut_ids', []))} DUTs, {len(data.get('profile_ids', []))} profiles, "
                            f"{len(data.get('dut_profile_matrix', []))} combinations"),
        ("PR Fixes", f"{pr.get('simple', 0)}s / {pr.get('medium', 0)}m / {pr.get('complex', 0)}c"),
        ("Working Days", str(data.get("working_days", 20))),
        ("Team", team),
    ]
    if (data.get("expected_releases") or 1) > 1:
        items.append(("Expected Releases", str(data["expected_releases"])))
    if data.get("project_goals"):
        items.append(("Project Goals", data["project_goals"]))
    if data.get("target_customer"):
        items.append(("Target Customer", data["target_customer"]))
    return items


def rail(data: dict, meta: dict) -> dict:
    pr = data.get("pr_fixes") or {}
    pt = data.get("product_type_filter")
    return {
        "title": data.get("project_name") or "Untitled estimation",
        "sections": [
            [("Type", data.get("project_type")), ("Customer", data.get("target_customer")),
             *([("Product", pt)] if pt and pt != "All" else [])],
            [("Features", len(data.get("feature_ids", [])) or None),
             ("New Feat.", len(data.get("new_feature_ids", [])) or None),
             ("References", len(data.get("reference_project_ids", [])) or None)],
            [("DUTs", len(data.get("dut_ids", [])) or None),
             ("Profiles", len(data.get("profile_ids", [])) or None),
             ("Combinations", len(data.get("dut_profile_matrix", [])) or None)],
            [("PR Fixes", (pr.get("simple", 0) + pr.get("medium", 0) + pr.get("complex", 0)) or None),
             ("Documents", len(data.get("document_type_ids", [])) or None)],
            [("Team Size", data.get("team_size") or None), ("Working Days", data.get("working_days") or None),
             ("Releases", data.get("expected_releases") or None),
             ("Risks", len(data.get("risk_item_ids", [])) or None)],
        ],
        "total": meta.get("calc_total"),
    }
