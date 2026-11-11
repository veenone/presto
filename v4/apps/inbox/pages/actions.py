"""Request inbox actions: assignment, attachments (upload / download) and import from Redmine / Jira.

Each action needs the same minimum role as its REST endpoint and calls the same
service code. Successful POSTs answer 204 with ``HX-Trigger`` toast +
``crud-refresh`` (the list results and the detail region both listen to it).
"""

from __future__ import annotations

import logging
import mimetypes

from django.http import FileResponse, Http404, HttpResponse, JsonResponse
from django.shortcuts import render
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from apps.accounts import services as account_services
from apps.accounts.audit import log_action
from apps.accounts.models import User
from apps.api.common import client_ip
from apps.inbox import services
from apps.integrations.models import IntegrationConfig
from apps.web.htmx import trigger

from .common import (
    ROLE_ASSIGN,
    ROLE_SYNC,
    ROLE_UPLOAD,
    SYNC_SYSTEMS,
    check_view,
    get_request_or_404,
    require_role,
    user_label,
)
from .forms import AssignForm, UploadForm

logger = logging.getLogger(__name__)

SYSTEM_LABELS = {"REDMINE": "Redmine", "JIRA": "Jira"}


def _done(message: str, *, close: bool = False) -> HttpResponse:
    events: dict = {"toast": {"message": message, "level": "success"}, "crud-refresh": True}
    if close:
        events["close-modal"] = True
    return trigger(HttpResponse(status=204), **events)


def _error(detail: str, status: int = 400) -> JsonResponse:
    return JsonResponse({"detail": detail}, status=status)


def assignable_users() -> list[User]:
    return sorted(account_services.list_assignable_users(), key=lambda u: user_label(u).lower())


# ── Assignment ───────────────────────────────────────────


@require_http_methods(["GET", "POST"])
def assign_view(request, request_id: int):
    """GET: assign dialog (list rows). POST: assign (API: POST /api/requests/{id}/assign, APPROVER).

    The detail page posts its inline form with ``hx-swap="none"``: errors come back as a toast.
    The dialog posts with ``HX-Target: modal``: errors re-render the dialog (422).
    """
    check_view(request.user)
    require_role(request.user, ROLE_ASSIGN)
    req = get_request_or_404(request_id)
    users = assignable_users()
    in_modal = request.headers.get("HX-Target") == "modal"
    ctx = {"req": req, "action": request.path}
    if request.method == "GET":
        form = AssignForm(users=users, initial={"assigned_to_id": req.assigned_to_id})
        return render(request, "inbox/_assign_modal.html", {**ctx, "form": form})

    form = AssignForm(data=request.POST, users=users)
    if not form.is_valid():
        if in_modal:
            return render(request, "inbox/_assign_modal.html", {**ctx, "form": form}, status=422)
        return _error("Please select a user.")
    target = User.objects.filter(pk=form.cleaned_data["assigned_to_id"]).first()
    if target is None:  # pragma: no cover - the choice list only holds existing users
        return _error("Target user not found", 404)
    services.assign_request(req, target)
    log_action(request.user, "ASSIGN", "request", req.id,
               details={"assigned_to_id": target.id}, ip_address=client_ip(request))
    return _done(f"Request assigned to {user_label(target)}.", close=in_modal)


# ── Attachments ──────────────────────────────────────────


@require_POST
def upload_view(request, request_id: int):
    """POST /requests/<id>/attachments (API: POST /api/requests/{id}/attachments, ESTIMATOR)."""
    check_view(request.user)
    require_role(request.user, ROLE_UPLOAD)
    req = get_request_or_404(request_id)
    form = UploadForm(data=request.POST, files=request.FILES)
    if not form.is_valid():
        return _error("Choose a file to upload.")
    upload = form.cleaned_data["file"]
    result = services.save_attachment(req, upload.name, upload.read(), upload.content_type)
    return _done(f"Uploaded {result['filename']} ({result['size']:,} bytes).")


@require_GET
def download_view(request, request_id: int, index: int):
    """Stored attachment file; only files under ATTACHMENTS_DIR are served."""
    check_view(request.user)
    req = get_request_or_404(request_id)
    found = services.attachment_file(req, index)
    if found is None:
        raise Http404("Attachment not found")
    att, path = found
    content_type = att.get("mime_type") or mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    return FileResponse(path.open("rb"), as_attachment=True, filename=path.name, content_type=content_type)


# ── Import from Redmine / Jira ───────────────────────────


def _systems() -> list[dict]:
    configs = {c.system_name: c for c in IntegrationConfig.objects.filter(system_name__in=SYNC_SYSTEMS)}
    out = []
    for name in SYNC_SYSTEMS:
        cfg = configs.get(name)
        out.append({
            "name": name, "label": SYSTEM_LABELS[name],
            "enabled": bool(cfg and cfg.enabled),
            "last_sync_at": cfg.last_sync_at if cfg else None,
        })
    return out


@require_GET
def sync_modal_view(request):
    """Import dialog: Redmine and Jira with their configured state."""
    check_view(request.user)
    require_role(request.user, ROLE_SYNC)
    return render(request, "inbox/_sync_modal.html", {"systems": _systems()})


@require_POST
def sync_view(request, system: str):
    """POST /requests/sync/<system> (API: POST /api/integrations/{system}/sync, ADMIN)."""
    check_view(request.user)
    require_role(request.user, ROLE_SYNC)
    name = system.upper()
    if name not in SYNC_SYSTEMS:
        raise Http404("Unknown system")
    from apps.integrations import service

    ctx: dict = {"systems": _systems(), "system": name, "label": SYSTEM_LABELS[name]}
    try:
        result = service.sync_import(name)
    except Exception as exc:  # adapter or network failure: show it in the dialog
        logger.warning("Request import from %s failed", name, exc_info=True)
        ctx["failure"] = f"Sync failed: {exc}"
        return render(request, "inbox/_sync_modal.html", ctx)

    status = result.status.value if hasattr(result.status, "value") else str(result.status)
    not_configured = status == "FAILED" and any("not configured" in e for e in result.errors)
    ctx.update({
        "result": result, "status": status, "not_configured": not_configured,
        "can_configure": request.user.role == "ADMIN",
    })
    resp = render(request, "inbox/_sync_modal.html", ctx)
    if status in ("SUCCESS", "PARTIAL"):
        message = (f"{SYSTEM_LABELS[name]}: {result.items_created} created, "
                   f"{result.items_updated} updated.")
        trigger(resp, **{"crud-refresh": True}, toast={"message": message, "level": "success"})
    return resp
