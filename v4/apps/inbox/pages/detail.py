"""Request detail page (v3 ``/requests/{id}``): fields, description, attachments, assignment and
linked estimations.

The page body is one htmx region (``#req-detail``) that reloads on ``crud-refresh``
(sent by the edit dialog, assignment and uploads).
"""

from __future__ import annotations

from django.shortcuts import render
from django.views.decorators.http import require_GET

from apps.estimations.pages.common import FeasibilityCell, StatusCell, version_label
from apps.estimations.pages.markup import render_description
from apps.web.htmx import is_htmx

from .actions import assignable_users
from .common import (
    CLOSED,
    ROLE_ASSIGN,
    ROLE_EDIT,
    ROLE_ESTIMATE,
    ROLE_UPLOAD,
    can,
    check_view,
    get_request_or_404,
    priority_badge,
    status_badge,
    user_label,
)


def _size(n) -> str:
    try:
        n = int(n or 0)
    except (TypeError, ValueError):
        return ""
    if n < 1024:
        return f"{n} B"
    if n < 1024 * 1024:
        return f"{n / 1024:.1f} KB"
    return f"{n / (1024 * 1024):.1f} MB"


def _attachments(req) -> list[dict]:
    out = []
    items = req.attachments if isinstance(req.attachments, list) else []
    for i, att in enumerate(items):
        if not isinstance(att, dict):
            continue
        ext = str(att.get("external_url") or "")
        size = _size(att.get("file_size_bytes")) if att.get("file_size_bytes") else ""
        meta = [m for m in (att.get("mime_type"), size, str(att.get("uploaded_at") or "")[:10], att.get("source")) if m]
        out.append({
            "index": i,
            "filename": att.get("filename") or "unknown",
            "meta": " · ".join(str(m) for m in meta),
            "external_url": ext if ext.startswith(("http://", "https://")) else "",
            "has_file": bool(att.get("filepath") or att.get("filename")),
        })
    return out


def _fmt_date(value) -> str:
    return value.strftime("%Y-%m-%d") if value else ""


def detail_context(request, req) -> dict:
    user = request.user
    perms = {
        "edit": can(user, ROLE_EDIT),
        "estimate": can(user, ROLE_ESTIMATE),
        "upload": can(user, ROLE_UPLOAD),
        "assign": can(user, ROLE_ASSIGN),
    }
    estimations = []
    for e in req.estimations.order_by("id"):
        estimations.append({
            "id": e.id, "number": version_label(e), "project_name": e.project_name or "",
            "project_type": e.project_type or "", "hours": e.grand_total_hours or 0,
            "feasibility": FeasibilityCell(e.feasibility_status or "") if e.feasibility_status else "",
            "status": StatusCell(e.status or ""),
            "created": _fmt_date(e.created_at),
        })
    fields = [
        [("Request Number", req.request_number), ("Source", req.request_source),
         ("External ID", req.external_id), ("Business Unit", req.business_unit)],
        [("Requester", req.requester_name), ("Email", req.requester_email),
         ("Received", _fmt_date(req.received_date)), ("Delivery Requested", _fmt_date(req.requested_delivery_date))],
        [("Priority", priority_badge(req.priority)), ("Status", status_badge(req.status)),
         ("Product Type", req.product_type), ("Created", _fmt_date(req.created_at)),
         ("Updated", _fmt_date(req.updated_at))],
    ]
    return {
        "page_title": f"Request {req.request_number}",
        "req": req,
        "fields": fields,
        "description": render_description(req.description),
        "attachments": _attachments(req),
        "assigned_name": user_label(req.assigned_to),
        "users": assignable_users() if perms["assign"] else [],
        "estimations": estimations,
        "perms": perms,
        "is_open": (req.status or "") not in CLOSED,
    }


@require_GET
def detail_view(request, request_id: int):
    check_view(request.user)
    req = get_request_or_404(request_id)
    ctx = detail_context(request, req)
    if is_htmx(request) and request.headers.get("HX-Target") == "req-detail":
        return render(request, "inbox/detail/_region.html", ctx)
    return render(request, "inbox/detail/page.html", ctx)
