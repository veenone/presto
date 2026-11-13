"""Estimation detail actions: status, assignment, archive, calibration, exports, email, versions.

Each action needs the same minimum role as its REST endpoint and calls the
same service code. Success answers 204 with ``HX-Trigger`` toast +
``est-refresh`` (and ``close-modal`` for dialogs). Service errors answer with
the v3 ``{"detail": ...}`` body (shown as a toast), or re-render the dialog
with the message (422).
"""

from __future__ import annotations

from django import forms
from django.http import HttpResponse, JsonResponse
from django.shortcuts import render
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from apps.accounts.models import User
from apps.api.common import client_ip
from apps.core.errors import ServiceError
from apps.estimations import services, versions
from apps.estimations.models import Estimation
from apps.web.forms import StyledForm
from apps.web.htmx import trigger

from .common import (
    ROLE_ARCHIVE,
    ROLE_ASSIGN,
    ROLE_EXPORT,
    ROLE_SEND_REPORT,
    ROLE_STATUS,
    get_estimation_or_404,
    require_role,
    version_label,
)

EXPORT_SYSTEMS = ["JIRA", "REDMINE"]


def _done(message: str, level: str = "success", close: bool = False, refresh: bool = True) -> HttpResponse:
    events: dict = {"toast": {"message": message, "level": level}}
    if refresh:
        events["est-refresh"] = True
    if close:
        events["close-modal"] = True
    return trigger(HttpResponse(status=204), **events)


def _error(detail: str, status: int = 400, refresh: bool = False) -> JsonResponse:
    resp = JsonResponse({"detail": detail}, status=status)
    if refresh:
        trigger(resp, **{"est-refresh": True})
    return resp


def _status_value(status) -> str:
    return status.value if hasattr(status, "value") else str(status)


# ── Status workflow ──────────────────────────────────────


@require_POST
def status_view(request, estimation_id: int):
    """POST /estimation/<id>/status (API: POST /api/estimations/{id}/status, ESTIMATOR)."""
    require_role(request.user, ROLE_STATUS)
    est = get_estimation_or_404(estimation_id)
    target = (request.POST.get("status") or "").strip().upper()
    approved_by = (request.user.display_name or request.user.username) if target == "APPROVED" else None
    try:
        services.change_status(est, target, approved_by, request.user)
    except ServiceError as exc:
        # v3 reloads the estimation and rebuilds the buttons after a failed transition.
        return _error(f"Status update failed: {exc.detail}", exc.status_code, refresh=True)
    return _done(f"Status changed to {target}.")


# ── Assignment ───────────────────────────────────────────


@require_POST
def assign_view(request, estimation_id: int):
    """POST /estimation/<id>/assign (API: POST /api/estimations/{id}/assign, APPROVER)."""
    require_role(request.user, ROLE_ASSIGN)
    est = get_estimation_or_404(estimation_id)
    try:
        user_id = int(request.POST.get("assigned_to_id") or "")
    except ValueError:
        return _error("Select a user first.")
    try:
        services.assign(est, user_id, request.user, client_ip(request))
    except ServiceError as exc:
        return _error(f"Assignment failed: {exc.detail}", exc.status_code)
    target = User.objects.get(pk=user_id)
    return _done(f"Assigned to {target.display_name or target.username}.")


# ── Archive and calibration ──────────────────────────────


@require_POST
def archive_view(request, estimation_id: int):
    """POST /estimation/<id>/archive (API: POST /api/estimations/{id}/archive, APPROVER)."""
    require_role(request.user, ROLE_ARCHIVE)
    est = get_estimation_or_404(estimation_id)
    try:
        services.archive_to_history(est)
    except ServiceError as exc:
        return _error(f"Archive failed: {exc.detail}", exc.status_code)
    return _done("Estimation archived to Historical Projects.")


@require_POST
def calibrate_view(request, estimation_id: int):
    """POST /estimation/<id>/calibrate (API: POST /api/estimations/{id}/calibrate, any user)."""
    est = get_estimation_or_404(estimation_id)
    try:
        result = services.calibrate_estimation(est)
    except ServiceError as exc:
        return _error(f"Calibration failed: {exc.detail}", exc.status_code)
    return render(request, "estimations/detail/_calibrate_modal.html", {"est": est, "result": result})


# ── Export to the external system ────────────────────────


@require_POST
def export_view(request, estimation_id: int):
    """POST /estimation/<id>/export (API: POST /api/estimations/{id}/export, ESTIMATOR)."""
    require_role(request.user, ROLE_EXPORT)
    est = get_estimation_or_404(estimation_id)
    if not est.request_id:
        return _error("Export failed: Estimation is not linked to a request")
    req = est.request
    if not req or not req.external_id:
        return _error("Export failed: Linked request has no external ID")
    if req.request_source == "MANUAL":
        return _error("Export failed: Request source is MANUAL — no external system to export to")

    from apps.integrations import service

    result = service.sync_export(req.request_source, {
        "external_id": req.external_id,
        "grand_total_hours": est.grand_total_hours,
        "feasibility_status": est.feasibility_status,
        "estimation_number": est.estimation_number or f"EST-{est.id}",
        "project_name": est.project_name,
        "status": est.status,
    })
    if result.errors:
        return _done(f"Export completed with errors: {', '.join(result.errors)}", "warning", refresh=False)
    return _done(f"Export successful (status: {_status_value(result.status)}).", refresh=False)


class ExportTasksForm(StyledForm):
    target_system = forms.ChoiceField(label="Target System", choices=[(s, s) for s in EXPORT_SYSTEMS])
    issue_key = forms.CharField(
        label="Parent Issue Key (optional)", required=False, max_length=64,
        widget=forms.TextInput(attrs={"placeholder": "e.g. PROJ-123 or leave empty"}),
        help_text="If empty, tasks are created as standalone issues in the configured project.",
    )


def _linked_request(est: Estimation) -> tuple[str, str]:
    req = est.request if est.request_id else None
    if not req:
        return "", ""
    return req.external_id or "", req.request_source or ""


@require_http_methods(["GET", "POST"])
def export_tasks_view(request, estimation_id: int):
    """Dialog + POST /estimation/<id>/export-tasks (API: POST /api/estimations/{id}/export-tasks, ESTIMATOR)."""
    require_role(request.user, ROLE_EXPORT)
    est = get_estimation_or_404(estimation_id)
    linked_ext, linked_source = _linked_request(est)
    ctx = {"est": est, "action": request.path, "linked_ext": linked_ext}
    if request.method == "GET":
        initial = {"target_system": linked_source if linked_source in EXPORT_SYSTEMS else "JIRA"}
        form = ExportTasksForm(initial=initial)
        return render(request, "estimations/detail/_export_tasks_modal.html", {**ctx, "form": form})

    form = ExportTasksForm(data=request.POST)
    if form.is_valid():
        from apps.integrations import service

        system = form.cleaned_data["target_system"]
        data = service.estimation_to_export_dict(est)
        issue_key = form.cleaned_data["issue_key"].strip()
        if issue_key:
            data["external_id"] = issue_key
        try:
            result = service.sync_export_task_breakdown(system, data)
        except Exception as exc:  # adapter or network failure: keep the dialog open with the reason
            form.add_error(None, f"Task export failed: {exc}")
        else:
            created, total = result.items_created, result.items_processed
            if result.errors:
                return _done(f"Task export: {created}/{total} created. Errors: {', '.join(result.errors[:3])}",
                             "warning", close=True, refresh=False)
            return _done(f"Task breakdown exported: {created}/{total} tasks created in {system}.",
                         close=True, refresh=False)
    return render(request, "estimations/detail/_export_tasks_modal.html", {**ctx, "form": form}, status=422)


# ── Send report by email ─────────────────────────────────


class SendReportForm(StyledForm):
    to_email = forms.EmailField(label="Recipient email", max_length=254,
                                widget=forms.EmailInput(attrs={"placeholder": "name@example.com"}))


def send_report(est: Estimation, to_email: str) -> str:
    """Same steps and messages as POST /api/estimations/{id}/send-report; raises ServiceError."""
    from apps.integrations.service import get_adapter

    adapter = get_adapter("EMAIL")
    if not adapter:
        raise ServiceError(400, "Email integration is not configured or not enabled.")
    from apps.reports.builders import build_report_data
    from apps.reports.pdf_report import generate_pdf_report

    pdf_bytes = generate_pdf_report(build_report_data(est))
    from apps.integrations.adapters import EmailAdapter

    if not isinstance(adapter, EmailAdapter):
        raise ServiceError(500, "Invalid email adapter")
    result = adapter.send_estimation_report(
        to_email=to_email,
        estimation_number=est.estimation_number or "",
        project_name=est.project_name,
        grand_total_hours=est.grand_total_hours,
        feasibility_status=est.feasibility_status,
        report_bytes=pdf_bytes,
        report_filename=f"{est.estimation_number or 'report'}.pdf",
    )
    if result.status.value == "SUCCESS":
        return f"Report sent to {to_email}"
    raise ServiceError(500, f"Failed to send: {'; '.join(result.errors)}")


@require_http_methods(["GET", "POST"])
def send_report_view(request, estimation_id: int):
    """Dialog + POST /estimation/<id>/send-report (API: POST /api/estimations/{id}/send-report, ESTIMATOR)."""
    require_role(request.user, ROLE_SEND_REPORT)
    est = get_estimation_or_404(estimation_id)
    ctx = {"est": est, "action": request.path}
    if request.method == "GET":
        initial = {"to_email": request.user.email or ""}
        return render(request, "estimations/detail/_send_report_modal.html",
                      {**ctx, "form": SendReportForm(initial=initial)})
    form = SendReportForm(data=request.POST)
    if form.is_valid():
        try:
            message = send_report(est, form.cleaned_data["to_email"])
        except ServiceError as exc:
            form.add_error(None, exc.detail)
        else:
            return _done(message, close=True, refresh=False)
    return render(request, "estimations/detail/_send_report_modal.html", {**ctx, "form": form}, status=422)


# ── Comparison report dialog ─────────────────────────────


@require_GET
def compare_modal_view(request, estimation_id: int):
    """Dialog that picks the second estimation for the comparison report (any user)."""
    est = get_estimation_or_404(estimation_id)
    others = [
        {"id": e.id, "label": f"{version_label(e)} — {e.project_name} ({e.grand_total_hours:,.1f}h, {e.status})"}
        for e in Estimation.objects.exclude(pk=est.pk).order_by("-created_at", "-id")
    ]
    return render(request, "estimations/detail/_compare_modal.html", {"est": est, "others": others})


# ── Version diff ─────────────────────────────────────────


@require_GET
def versions_diff_view(request, estimation_id: int):
    """GET /estimation/<id>/versions/diff?a=&b= (API: GET /api/estimations/{id}/versions/{a}/diff/{b})."""
    est = get_estimation_or_404(estimation_id)
    try:
        va, vb = int(request.GET.get("a", "")), int(request.GET.get("b", ""))
    except ValueError:
        return _error("Select two versions to compare.")
    if va == vb:
        return _error("Select two different versions.")
    try:
        diff = versions.diff_versions(est, va, vb)
    except versions.VersionNotFound as exc:
        return _error(f"Diff failed: {exc}", 404)
    modified = [{**m, "delta": (m.get("hours_b") or 0) - (m.get("hours_a") or 0)} for m in diff["modified_tasks"]]
    inputs = [
        {"label": key.replace("_", " ").title(), "added": vals.get("added", []), "removed": vals.get("removed", [])}
        for key, vals in diff["input_changes"].items()
    ]
    empty = not (diff["changes"] or modified or diff["added_tasks"] or diff["removed_tasks"] or inputs)
    return render(request, "estimations/detail/_diff.html", {
        "diff": diff, "modified": modified, "inputs": inputs, "empty": empty, "va": va, "vb": vb,
    })
