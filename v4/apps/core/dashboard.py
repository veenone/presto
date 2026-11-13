"""Dashboard statistics: one computation shared by GET /api/dashboard/stats and the dashboard page."""

from __future__ import annotations

from typing import Any

from django.db.models import Avg, Count, Value
from django.db.models.functions import Coalesce

from apps.catalog.models import Feature, RiskItem, TaskTemplate
from apps.estimations.models import Estimation
from apps.inbox.models import Request

RECENT_LIMIT = 50


def _fmt(dt) -> str | None:
    return dt.strftime("%Y-%m-%d %H:%M") if dt else None


def _breakdown(model, field: str, fallback: str | None = None) -> dict[str, int]:
    """``{value: count}`` grouped by ``field``; NULL becomes ``fallback`` when given."""
    qs = model.objects.order_by()
    if fallback is not None:
        rows = qs.annotate(k=Coalesce(field, Value(fallback))).values("k").annotate(n=Count("id")) \
            .values_list("k", "n")
    else:
        rows = qs.values(field).annotate(n=Count("id")).values_list(field, "n")
    return {k: n for k, n in rows if k is not None}


def dashboard_stats(recent_limit: int = RECENT_LIMIT) -> dict[str, Any]:
    """Aggregate statistics for the dashboard (v3 ``DashboardStatsOut`` shape)."""
    requests = Request.objects.all()
    estimations = Estimation.objects.all()
    avg_hours = estimations.aggregate(v=Avg("grand_total_hours"))["v"] or 0

    recent_estimations = [
        {
            "id": e.id,
            "estimation_number": e.estimation_number,
            "project_name": e.project_name,
            "grand_total_hours": round(e.grand_total_hours, 1),
            "feasibility_status": e.feasibility_status,
            "status": e.status,
            "version": e.version or 1,
            "created_at": _fmt(e.created_at),
        }
        for e in estimations.order_by("-created_at", "-id")[:recent_limit]
    ]
    recent_requests = [
        {
            "id": r.id,
            "request_number": r.request_number,
            "title": r.title,
            "priority": r.priority,
            "status": r.status,
            "created_at": _fmt(r.created_at),
        }
        for r in requests.order_by("-created_at", "-id")[:recent_limit]
    ]

    return {
        "total_requests": requests.count(),
        "requests_new": requests.filter(status="NEW").count(),
        "requests_in_progress": requests.filter(status__in=["IN_ESTIMATION", "IN_PROGRESS"]).count(),
        "requests_completed": requests.filter(status="COMPLETED").count(),
        "total_estimations": estimations.count(),
        "estimations_draft": estimations.filter(status="DRAFT").count(),
        "estimations_final": estimations.filter(status="FINAL").count(),
        "estimations_approved": estimations.filter(status="APPROVED").count(),
        "avg_grand_total_hours": round(float(avg_hours), 1),
        # Declared by the v3 schema but never computed by the v3 backend; kept at 0 for parity.
        "avg_utilization_pct": 0.0,
        "recent_estimations": recent_estimations,
        "recent_requests": recent_requests,
        "features_by_category": _breakdown(Feature, "category", "Uncategorized"),
        "tasks_by_type": _breakdown(TaskTemplate, "task_type"),
        "tasks_by_product_type": _breakdown(TaskTemplate, "product_type", "Unspecified"),
        "risks_by_category": _breakdown(RiskItem, "category", "General"),
        "risks_by_likelihood": _breakdown(RiskItem, "likelihood"),
    }
