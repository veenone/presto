"""Audit Log page (v3 ``pages/audit.py``): read-only, filtered, paged, newest first.

Filters: action (segmented pills as in v3, plus a select), resource type, user
and a date range. Paging happens in the database (50 per page, v3's default
limit). Each entry's details JSON opens in a dialog.
"""

from __future__ import annotations

import json
import math
from collections import Counter
from datetime import date
from urllib.parse import urlencode

from django.db.models import Count
from django.http import Http404
from django.shortcuts import render
from django.views.decorators.http import require_GET

from apps.accounts import services
from apps.accounts.models import AuditLog, User
from apps.web.htmx import is_htmx

from .common import require_admin

PAGE_SIZE = 50
STANDARD = ("CREATE", "UPDATE", "DELETE", "LOGIN", "LOGOUT", "LOGIN_FAILED")
OTHER = "CHANGE"  # v3 pill "Backup/Restore": every non-standard action
PILLS = [("All", ""), ("Create", "CREATE"), ("Update", "UPDATE"), ("Delete", "DELETE"),
         ("Login", "LOGIN"), ("Failed Login", "LOGIN_FAILED"), ("Backup/Restore", OTHER)]
ACTION_TONES = {"CREATE": "positive", "UPDATE": "warning", "DELETE": "negative", "LOGIN_FAILED": "negative"}
FILTER_KEYS = ("action", "resource_type", "user", "date_from", "date_to")


def _date(value: str) -> date | None:
    try:
        return date.fromisoformat(value) if value else None
    except ValueError:
        return None


def _int(value: str, default: int | None = None) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def parse_filters(request) -> dict:
    g = request.GET
    user = _int(g.get("user", ""))
    d_from, d_to = _date(g.get("date_from", "").strip()), _date(g.get("date_to", "").strip())
    return {
        "action": g.get("action", "").strip(), "resource_type": g.get("resource_type", "").strip(),
        "user": user, "date_from": d_from, "date_to": d_to,
        "page": max(1, _int(g.get("page", "1"), 1) or 1),
    }


def _query(f: dict, **overrides) -> str:
    p = {**f, **overrides}
    out = {k: (p[k].isoformat() if isinstance(p[k], date) else p[k]) for k in FILTER_KEYS if p.get(k) not in (None, "")}
    if p.get("page", 1) > 1:
        out["page"] = p["page"]
    return urlencode(out)


def _details_preview(details) -> str:
    if not details:
        return ""
    text = json.dumps(details, default=str, ensure_ascii=False)
    return text if len(text) <= 60 else text[:60] + "…"


def build(request) -> dict:
    f = parse_filters(request)
    base = services.audit_log_queryset(
        user_id=f["user"], resource_type=f["resource_type"] or None,
        date_from=f["date_from"], date_to=f["date_to"],
    )
    counts = Counter(dict(base.order_by().values_list("action").annotate(n=Count("id"))))
    total_base = sum(counts.values())
    if f["action"] == OTHER:
        qs = base.exclude(action__in=STANDARD)
    elif f["action"]:
        qs = base.filter(action=f["action"])
    else:
        qs = base
    filtered = qs.count()
    pages = max(1, math.ceil(filtered / PAGE_SIZE))
    page = min(f["page"], pages)
    start = (page - 1) * PAGE_SIZE
    entries = [
        {"id": e.pk, "created_at": e.created_at, "username": e.user.username if e.user else None,
         "action": e.action, "tone": ACTION_TONES.get(e.action, ""), "resource_type": e.resource_type,
         "resource_id": e.resource_id, "ip_address": e.ip_address, "preview": _details_preview(e.details)}
        for e in qs[start:start + PAGE_SIZE]
    ]

    def url(**o) -> str:
        qs_ = _query(f, **o)
        return f"{request.path}?{qs_}" if qs_ else request.path

    other_n = sum(n for a, n in counts.items() if a not in STANDARD)
    pills = []
    for label, key in PILLS:
        n = total_base if not key else other_n if key == OTHER else counts.get(key, 0)
        if n or key == f["action"] or not key:
            pills.append({"label": label, "key": key, "count": n, "url": url(action=key, page=1)})
    users_seen = base.exclude(user=None).values("user").distinct().count()
    tiles = [{"label": "Total Entries", "value": total_base, "key": "",
              "sub": f"{users_seen} unique user{'s' if users_seen != 1 else ''}"}]
    if counts.get("LOGIN"):
        tiles.append({"label": "Logins", "value": counts["LOGIN"], "sub": "successful sign-ins", "key": "LOGIN"})
    if counts.get("LOGIN_FAILED"):
        tiles.append({"label": "Failed Logins", "value": counts["LOGIN_FAILED"], "sub": "auth attempts denied",
                      "key": "LOGIN_FAILED"})
    top = [(a, n) for a, n in counts.most_common() if a not in ("LOGIN", "LOGOUT", "LOGIN_FAILED")][:1]
    for action, n in top:
        tiles.append({"label": action.replace("_", " ").title(), "value": n, "sub": "filter to action", "key": action})
    for t in tiles:
        t["url"] = url(action=t["key"], page=1)

    all_actions = sorted(set(AuditLog.objects.order_by().values_list("action", flat=True).distinct()))
    if f["action"] and f["action"] != OTHER and f["action"] not in all_actions:
        all_actions.append(f["action"])
    resource_types = sorted(
        r for r in AuditLog.objects.order_by().values_list("resource_type", flat=True).distinct() if r)
    return {
        "page_title": "Audit Log", "f": f, "entries": entries, "pills": pills, "tiles": tiles,
        "total": AuditLog.objects.count(), "filtered": filtered, "page": page, "pages": pages,
        "start": start + 1 if filtered else 0, "end": min(start + PAGE_SIZE, filtered),
        "prev": url(page=page - 1) if page > 1 else "", "next": url(page=page + 1) if page < pages else "",
        "self_url": url(page=page), "clear_url": request.path,
        "actions": all_actions, "resource_types": resource_types,
        "users": list(User.objects.order_by("username").values("id", "username")),
        "date_from": f["date_from"].isoformat() if f["date_from"] else "",
        "date_to": f["date_to"].isoformat() if f["date_to"] else "",
        "has_filters": any(f[k] not in (None, "") for k in FILTER_KEYS),
    }


@require_GET
def audit_view(request):
    require_admin(request)
    ctx = build(request)
    if is_htmx(request) and request.headers.get("HX-Target") == "audit-results":
        return render(request, "admin_people/_audit_results.html", ctx)
    return render(request, "admin_people/audit.html", ctx)


@require_GET
def audit_detail_view(request, entry_id: int):
    """Details JSON viewer (dialog)."""
    require_admin(request)
    entry = AuditLog.objects.select_related("user").filter(pk=entry_id).first()
    if entry is None:
        raise Http404("Audit entry not found")
    pretty = json.dumps(entry.details or {}, indent=2, sort_keys=True, default=str, ensure_ascii=False)
    return render(request, "admin_people/_audit_detail_modal.html", {"entry": entry, "pretty": pretty})
