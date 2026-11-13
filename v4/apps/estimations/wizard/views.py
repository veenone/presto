"""Wizard views: page, step navigation, autosave, save, preview and dialogs.

Every POST carries the current step's fields (``_step`` + the step inputs);
they are merged into the session state first, so dialogs and autosave never
lose what is on screen.
"""

from __future__ import annotations

import json
from functools import wraps

from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.http import Http404, HttpResponse, JsonResponse
from django.shortcuts import render
from django.views.decorators.http import require_GET, require_POST

from apps.accounts.roles import has_role
from apps.catalog import services as catalog_services
from apps.catalog.models import Feature
from apps.core.errors import ServiceError
from apps.estimations import services
from apps.estimations.models import Estimation
from apps.inbox.models import Request
from apps.web.htmx import toast, trigger

from . import state as st
from . import steps
from .forms import PresetForm, ProjectFeatureForm

EDITABLE = ("DRAFT", "REVISED")
WRITE_ROLE = "ESTIMATOR"  # POST /estimations, PUT /estimations/{id}/draft and /revise


def _detail(status: int, detail: str) -> JsonResponse:
    return JsonResponse({"detail": detail}, status=status)


def _load(request, estimation_id: int | None) -> Estimation | None:
    if not has_role(request.user.role, WRITE_ROLE):
        raise PermissionDenied
    if estimation_id is None:
        return None
    est = Estimation.objects.filter(pk=estimation_id).first()
    if est is None:
        raise Http404("Estimation not found")
    if est.status == "DRAFT" and not services.can_edit_draft(est, request.user):
        raise PermissionDenied
    return est


def wizard_view(fn):
    """POST action on an editable wizard: role and access checks, then ``fn(request, wiz, ...)``."""
    @wraps(fn)
    def inner(request, estimation_id: int | None = None, **kwargs):
        est = _load(request, estimation_id)
        if est is not None and est.status not in EDITABLE:
            return _detail(400, "Only DRAFT or REVISED estimations can be edited.")
        try:
            return fn(request, st.Wizard(request, est), **kwargs)
        except ServiceError as exc:
            return _detail(exc.status_code, exc.detail)
    return require_POST(inner)


def _merge_posted(request, wiz: st.Wizard) -> tuple[int, dict]:
    """Merge the posted step fields into the state (valid values only). Returns (step, errors)."""
    step = steps.clamp_step(request.POST.get("_step"))
    updates, meta, errors = steps.parse(step, request.POST, wiz.data)
    wiz.merge(updates, meta)
    wiz.save()
    return step, errors


# ── Rendering ────────────────────────────────────────────


def _base_context(request, wiz: st.Wizard, step: int, errors: dict | None = None) -> dict:
    est = wiz.estimation
    if wiz.mode == st.NEW:
        heading, eyebrow, save_label = "New Estimation", "New Estimation", "Save Estimation"
    elif wiz.mode == st.DRAFT:
        heading = f"Edit Estimation — {est.estimation_number or ''} (v{est.version or 1})"
        eyebrow, save_label = "Edit Draft", "Save Draft"
    else:
        heading = f"Edit Estimation — {est.estimation_number or ''} (v{est.version or 1})"
        eyebrow, save_label = "Revision", "Save Revision"
    return {
        "wiz": wiz, "data": wiz.data, "meta": wiz.meta, "estimation": est,
        "step": steps.STEPS[step - 1], "steps": steps.STEPS, "last_step": steps.LAST,
        "errors": errors or {}, "rail": steps.rail(wiz.data, wiz.meta),
        "heading": heading, "eyebrow": eyebrow, "save_label": save_label,
        "is_revision": wiz.mode == st.REVISION,
        **steps.context(step, wiz),
    }


def _body(request, wiz: st.Wizard, step: int, errors: dict | None = None, status: int = 200,
          message: str | None = None, level: str = "success") -> HttpResponse:
    ctx = {**_base_context(request, wiz, step, errors), "oob": True}
    resp = render(request, "estimations/wizard/_body.html", ctx, status=status)
    resp["HX-Push-Url"] = wiz.step_url(step)
    if errors and not message:
        message, level = next(iter(errors.values())), "warning"
    if message:
        toast(resp, message, level)
    return resp


def _body_from_modal(request, wiz: st.Wizard, step: int, message: str) -> HttpResponse:
    """Close the dialog and re-render the wizard body (the dialog form targets #modal)."""
    resp = _body(request, wiz, step, message=message)
    resp["HX-Retarget"] = "#wizard"
    resp["HX-Reswap"] = "innerHTML show:none"
    return trigger(resp, **{"close-modal": True})


# ── Page ─────────────────────────────────────────────────


def _request_description(req: Request) -> str:
    """v3 prefill: reference to the originating request, its title, requester and text."""
    head = f"Estimation request based on {req.request_number or f'#{req.id}'}"
    if (req.title or "").strip():
        head += f": {req.title.strip()}"
    parts = [head]
    if (req.requester_name or "").strip():
        parts.append(f"Requested by {req.requester_name.strip()}.")
    if (req.description or "").strip():
        parts += ["", "Original request:", req.description.strip()]
    return "\n".join(parts)


@require_GET
def page(request, estimation_id: int | None = None):
    est = _load(request, estimation_id)
    if est is not None and est.status not in EDITABLE:
        return render(request, "estimations/wizard/locked.html",
                      {"estimation": est, "page_title": "Edit Estimation"})
    wiz = st.Wizard(request, est)
    if "step" not in request.GET:
        # A fresh start: new wizard (optionally from an inbox request) or the saved inputs.
        req = None
        if est is None and request.GET.get("request_id"):
            rid = steps._int(request.GET["request_id"])
            req = Request.objects.filter(pk=rid).first() if rid else None
            if req is None:
                raise Http404("Request not found")
        wiz.reset(request_id=req.id if req else None,
                  description=_request_description(req) if req else "")
        step = 1
    else:
        step = steps.clamp_step(request.GET.get("step"))
    wiz.save()
    ctx = _base_context(request, wiz, step)
    ctx["page_title"] = ctx["eyebrow"] if wiz.mode != st.NEW else "New Estimation"
    req_id = wiz.data.get("request_id")
    ctx["request_obj"] = Request.objects.filter(pk=req_id).first() if req_id else None
    # The address bar always carries the step, so a reload resumes instead of starting over.
    ctx["current_url"] = wiz.step_url(step)
    resp = render(request, "estimations/wizard/page.html", ctx)
    resp["HX-Push-Url"] = ctx["current_url"]
    return resp


# ── Navigation and saving ────────────────────────────────


@wizard_view
def step_view(request, wiz):
    current, errors = _merge_posted(request, wiz)
    target = steps.clamp_step(request.POST.get("goto", current))
    if target > current and errors:
        return _body(request, wiz, current, errors, status=422)
    return _body(request, wiz, target)


def _label(text: str) -> HttpResponse:
    return HttpResponse(text)


@wizard_view
def autosave_view(request, wiz):
    """Background autosave (every 30 s and 5 s after the first input): only when something changed."""
    if not wiz.autosave:
        return HttpResponse(status=204)
    _merge_posted(request, wiz)
    if not (wiz.data.get("project_name") or "").strip() or not wiz.dirty:
        return HttpResponse(status=204)
    try:
        wiz.persist()
    except ServiceError:
        return _label("Autosave pending…")
    return _label("✓ Draft autosaved")


@wizard_view
def save_draft_view(request, wiz):
    if not wiz.autosave:
        return _detail(400, "Revisions are saved with Save Revision only.")
    _merge_posted(request, wiz)
    if not (wiz.data.get("project_name") or "").strip():
        return _detail(400, "Enter a project name before saving a draft.")
    est = wiz.persist()
    resp = _label("✓ Draft saved")
    return toast(resp, f"Draft saved (ID {est.id}). Resume it any time from Estimations.")


@wizard_view
def preview_view(request, wiz):
    _merge_posted(request, wiz)
    out = st.preview(wiz.data)
    wiz.meta["calc_total"] = out["result"]["grand_total_hours"]
    wiz.save()
    testers = wiz.data.get("task_assigned_testers", {})
    tasks = [dict(t, testers=testers.get(t["name"], 1)) for t in out["result"]["tasks"]]
    return render(request, "estimations/wizard/_preview.html", {
        **out, "res": out["result"], "tasks": tasks, "rail": steps.rail(wiz.data, wiz.meta),
        "working_weeks": round(out["result"]["grand_total_days"] / 5.0, 1),
        "task_count": len(tasks) + len(out["synthetic_tasks"]),
        "flags": [f.replace("_", " ").title() for f in out["result"]["risk_flags"]],
    })


@wizard_view
def save_view(request, wiz):
    current, errors = _merge_posted(request, wiz)
    if errors:
        return _body(request, wiz, current, errors, status=422)
    invalid = steps.first_invalid_step(wiz.data)
    if invalid:
        return _body(request, wiz, invalid[0], invalid[1], status=422)
    mode = wiz.mode
    est = wiz.finish()
    if mode == st.REVISION:
        messages.success(request, f"Revision saved (v{est.version}).")
    elif mode == st.DRAFT:
        messages.success(request, "Draft updated.")
    else:
        messages.success(request, f"Estimation saved (ID {est.id}).")
    resp = HttpResponse(status=204)
    resp["HX-Redirect"] = f"/estimation/{est.id}"
    return resp


# ── Project features (step 2 dialogs, step 7 promotion) ──


def _draft_for_features(wiz) -> Estimation:
    """The estimation owning project features; a new wizard saves its draft first (as v3)."""
    if wiz.mode == st.REVISION:
        raise ServiceError(400, "Project features can only be added to a DRAFT estimation "
                                f"(current: {wiz.estimation.status})")
    if wiz.estimation is not None:
        return wiz.estimation
    draft = Estimation.objects.filter(pk=wiz.meta.get("draft_id")).first() if wiz.meta.get("draft_id") else None
    if draft is None:
        if not (wiz.data.get("project_name") or "").strip():
            raise ServiceError(400, "Enter a project name before saving a draft.")
        draft = wiz.persist()
    return draft


def _feature_modal(request, wiz, form, action: str, heading: str, status: int = 200, oob: str = ""):
    return render(request, "estimations/wizard/_feature_modal.html", {
        "form": form, "action": wiz.url(action), "heading": heading, "oob_label": oob,
    }, status=status)


@wizard_view
def feature_new_view(request, wiz):
    _merge_posted(request, wiz)
    had_draft = bool(wiz.draft_id)
    _draft_for_features(wiz)
    form = ProjectFeatureForm(prefix="pf", product_types=steps.product_types(),
                              initial={"product_type": wiz.data.get("product_type_filter") or "All"})
    return _feature_modal(request, wiz, form, "feature/create", "Add custom feature for this project",
                          oob="" if had_draft else "✓ Draft saved")


@wizard_view
def feature_create_view(request, wiz):
    step, _ = _merge_posted(request, wiz)
    form = ProjectFeatureForm(request.POST, prefix="pf", product_types=steps.product_types())
    if not form.is_valid():
        return _feature_modal(request, wiz, form, "feature/create", "Add custom feature for this project", 422)
    try:
        feat = services.create_project_feature(_draft_for_features(wiz), form.service_fields(), request.user)
    except ServiceError as exc:
        form.add_error(None, exc.detail)
        return _feature_modal(request, wiz, form, "feature/create", "Add custom feature for this project", 422)
    if feat.id not in wiz.data["feature_ids"]:
        wiz.data["feature_ids"] = [*wiz.data["feature_ids"], feat.id]
    wiz.save()
    return _body_from_modal(request, wiz, step, f"Added project feature '{feat.name}'.")


def _own_feature(wiz, feature_id: int) -> Feature:
    feat = Feature.objects.filter(pk=feature_id, is_global=False).first()
    if feat is None or not wiz.draft_id or feat.owner_estimation_id != wiz.draft_id:
        raise Http404("Feature not found")
    return feat


@wizard_view
def feature_edit_view(request, wiz, feature_id: int):
    feat = _own_feature(wiz, feature_id)
    form = ProjectFeatureForm(prefix="pf", product_types=steps.product_types(), initial={
        "name": feat.name, "category": feat.category or "", "complexity_weight": feat.complexity_weight,
        "base_effort_hours": feat.base_effort_hours or 0.0, "product_type": feat.product_type or "All",
        "has_existing_tests": feat.has_existing_tests, "description": feat.description or "",
    })
    return _feature_modal(request, wiz, form, f"feature/{feat.id}/update", "Edit project feature")


@wizard_view
def feature_update_view(request, wiz, feature_id: int):
    feat = _own_feature(wiz, feature_id)
    step, _ = _merge_posted(request, wiz)
    form = ProjectFeatureForm(request.POST, prefix="pf", product_types=steps.product_types())
    action = f"feature/{feat.id}/update"
    if not form.is_valid():
        return _feature_modal(request, wiz, form, action, "Edit project feature", 422)
    try:
        out = catalog_services.update_feature(feat.id, request.user, form.service_fields())
    except ServiceError as exc:
        form.add_error(None, exc.detail)
        return _feature_modal(request, wiz, form, action, "Edit project feature", 422)
    return _body_from_modal(request, wiz, step, f"Updated '{out['name']}'.")


def _custom_features(request, wiz, message: str) -> HttpResponse:
    resp = render(request, "estimations/wizard/_custom_features.html", {
        "custom_features": steps.project_features(wiz),
        "can_promote": has_role(request.user.role, "APPROVER"), "wiz": wiz,
    })
    return toast(resp, message)


@wizard_view
def feature_request_promotion_view(request, wiz, feature_id: int):
    feat = _own_feature(wiz, feature_id)
    catalog_services.request_feature_promotion(feat.id, request.user)
    return _custom_features(request, wiz, f"Requested adding '{feat.name}' to the global catalog.")


@wizard_view
def feature_promote_view(request, wiz, feature_id: int):
    if not has_role(request.user.role, "APPROVER"):
        raise PermissionDenied
    feat = _own_feature(wiz, feature_id)
    catalog_services.promote_feature(feat.id)
    return _custom_features(request, wiz, f"'{feat.name}' is now a global feature.")


# ── Feature presets ──────────────────────────────────────


def _pt_or_none(wiz) -> str | None:
    pt = wiz.data.get("product_type_filter") or "All"
    return None if pt == "All" else pt


@wizard_view
def preset_apply_view(request, wiz):
    step, _ = _merge_posted(request, wiz)
    pid = steps._int(request.POST.get("preset_id"))
    preset = next((p for p in catalog_services.list_feature_presets(_pt_or_none(wiz)) if p["id"] == pid), None)
    if preset is None:
        return _detail(400, "Pick a preset to apply.")
    fids = list(wiz.data["feature_ids"])
    for fid in preset["feature_ids"]:  # additive: only ever selects
        if fid not in fids:
            fids.append(fid)
    wiz.data["feature_ids"] = fids
    applied = list(wiz.data.get("applied_presets") or [])
    if not any(a.get("id") == preset["id"] for a in applied):
        applied.append({"id": preset["id"], "name": preset["name"]})
    wiz.data["applied_presets"] = applied
    wiz.save()
    return _body(request, wiz, step,
                 message=f"Applied preset '{preset['name']}' (+{len(preset['feature_ids'])} feature(s)).")


def _preset_modal(request, wiz, form, status: int = 200):
    return render(request, "estimations/wizard/_preset_modal.html", {
        "form": form, "action": wiz.url("preset/create"), "count": len(wiz.data["feature_ids"]),
        "pt": wiz.data.get("product_type_filter") or "All",
    }, status=status)


@wizard_view
def preset_new_view(request, wiz):
    _merge_posted(request, wiz)
    if not wiz.data["feature_ids"]:
        return _detail(400, "Select at least one feature first.")
    return _preset_modal(request, wiz, PresetForm(prefix="ps"))


@wizard_view
def preset_create_view(request, wiz):
    step, _ = _merge_posted(request, wiz)
    form = PresetForm(request.POST, prefix="ps")
    if not form.is_valid():
        return _preset_modal(request, wiz, form, 422)
    try:
        catalog_services.create_feature_preset(
            request.user, form.cleaned_data["preset_name"], form.cleaned_data["preset_description"],
            _pt_or_none(wiz), wiz.data["feature_ids"])
    except ServiceError as exc:
        form.add_error(None, exc.detail)
        return _preset_modal(request, wiz, form, 422)
    return _body_from_modal(request, wiz, step, "Preset saved.")


# ── Jira PR import ───────────────────────────────────────


@wizard_view
def jira_view(request, wiz):
    from apps.integrations import service as integrations

    _merge_posted(request, wiz)
    items = integrations.jira_pr_items()
    existing = {r.get("pr_number") for r in wiz.data.get("pr_details", [])}
    rows = [dict(i, json=json.dumps(i), exists=i.get("key") in existing) for i in items]
    resp = render(request, "estimations/wizard/_jira_modal.html", {"items": rows, "wiz": wiz})
    return toast(resp, f"Loaded {len(rows)} PR item(s).")


def jira_row(item: dict) -> dict:
    """v3 import mapping: complexity from the Jira priority, link from the registry."""
    priority = (item.get("priority") or "Medium").lower()
    complexity = "simple"
    if priority in ("high", "highest", "critical", "blocker"):
        complexity = "complex"
    elif priority == "medium":
        complexity = "medium"
    return {
        "pr_number": item.get("key", ""), "link": item.get("link") or "",
        "description": item.get("description") or item.get("summary", ""),
        "priority": item.get("priority", "Medium"), "complexity": complexity,
        "status": item.get("status", "Open"), "test_available": True,
    }


@wizard_view
def jira_import_view(request, wiz):
    step, _ = _merge_posted(request, wiz)
    selected = []
    for raw in request.POST.getlist("jira_item"):
        try:
            item = json.loads(raw)
        except ValueError:
            continue
        if isinstance(item, dict):
            selected.append(item)
    if not selected:
        return _detail(400, "No items selected.")
    rows = list(wiz.data.get("pr_details", []))
    imported = 0
    for item in selected:
        key = item.get("key", "")
        if key and key not in {r.get("pr_number") for r in rows}:
            rows.append(jira_row(item))
            imported += 1
    wiz.data["pr_details"] = rows
    wiz.save()
    return _body_from_modal(request, wiz, step, f"Imported {imported} PR item(s).")
