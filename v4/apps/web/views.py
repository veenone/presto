"""Shell pages: dashboard, not-yet-ported pages and error pages."""

from __future__ import annotations

from datetime import datetime

from django.contrib.auth.decorators import login_not_required
from django.core.exceptions import PermissionDenied
from django.http import HttpResponseServerError
from django.shortcuts import render
from django.template import loader
from django.views.decorators.http import require_GET

from apps.core.dashboard import dashboard_stats

from .navigation import ITEMS_BY_NAME, allowed

ERROR_PAGES: dict[int, dict[str, str]] = {
    400: {"icon": "report_problem", "title": "Bad Request", "tone": "warning",
          "message": "The server could not process the request. This usually means invalid or missing "
                     "data was sent. Please check your input and try again."},
    403: {"icon": "block", "title": "Access Denied", "tone": "negative",
          "message": "You do not have the required permissions to perform this action. Your current role "
                     "may not be authorized. Contact your administrator if you believe this is an error."},
    404: {"icon": "search_off", "title": "Not Found", "tone": "info",
          "message": "The resource you're looking for doesn't exist or has been moved."},
    422: {"icon": "edit_off", "title": "Validation Error", "tone": "warning",
          "message": "The submitted data did not pass validation. Please review your input and try again."},
    500: {"icon": "error_outline", "title": "Server Error", "tone": "negative",
          "message": "Something went wrong on the server. Please try again later or contact support."},
}


def render_error(request, status: int, detail: str = ""):
    page = ERROR_PAGES.get(status, ERROR_PAGES[500])
    return render(request, "errors/error.html", {"status": status, "page": page, "detail": detail},
                  status=status)


# ── Dashboard ─────────────────────────────────────────────────────────────

RECENT_SHOWN = 6  # rows per recent list (v3)

# Semantic colours (v3): they carry meaning, so they stay the same in both themes.
EST_COLORS = {"Draft": "#78909C", "Final": "#1976D2", "Approved": "#4CAF50"}
REQ_COLORS = {"New": "#26A69A", "In Progress": "#FFA726", "Completed": "#66BB6A", "Other": "#BDBDBD"}
LIKELIHOOD_COLORS = {"LOW": "#66BB6A", "MEDIUM": "#FFA726", "HIGH": "#EF5350", "CRITICAL": "#B71C1C"}
# Muted palette for category charts (v3).
PALETTE = ["#5B8DB0", "#79A88C", "#C9A66B", "#C08497", "#9A8CB5",
           "#6FA8AE", "#B58AA0", "#9A8F84", "#8B97A3", "#C2BC7E"]
FEASIBILITY_STRIPE = {"FEASIBLE": "positive", "AT_RISK": "warning", "NOT_FEASIBLE": "negative"}
PRIORITY_STRIPE = {"CRITICAL": "negative", "HIGH": "negative", "MEDIUM": "warning", "LOW": "positive"}


def format_age(stamp: str | None, now: datetime | None = None) -> str:
    """Compact relative age of a ``YYYY-MM-DD HH:MM`` stamp: ``today`` / ``3d`` / ``2w`` / ``5mo`` (v3)."""
    if not stamp:
        return ""
    try:
        then = datetime.strptime(stamp, "%Y-%m-%d %H:%M")
    except ValueError:
        return stamp[:10]
    days = ((now or datetime.now()) - then).days
    if days < 1:
        return "today"
    if days < 7:
        return f"{days}d"
    if days < 30:
        return f"{days // 7}w"
    return f"{days // 30}mo"


def _slices(counts: dict[str, int], colors: dict[str, str] | None = None) -> list[dict]:
    return [{"name": name, "value": value,
             "color": (colors or {}).get(name) or PALETTE[i % len(PALETTE)]}
            for i, (name, value) in enumerate(counts.items())]


def _chart(chart_id: str, title: str, name: str, data: list[dict], *, kind: str = "donut",
           nav: str | None = None, empty: str = "No data yet", empty_link: tuple | None = None) -> dict:
    total = sum(d["value"] for d in data)
    return {"id": chart_id, "title": title, "name": name, "kind": kind, "data": data, "total": total,
            "nav": nav, "empty": empty, "empty_link": empty_link}


def dashboard_context(request) -> dict:
    """Everything the dashboard templates show, from the same service as GET /api/dashboard/stats."""
    stats = dashboard_stats(recent_limit=RECENT_SHOWN)
    can_requests = allowed(request.user, ITEMS_BY_NAME["requests"].requires)
    req_nav = "/requests" if can_requests else None

    kpis = [
        {"label": "Open Requests", "icon": "inbox",
         "value": str(max(stats["total_requests"] - stats["requests_completed"], 0)),
         "sub": f"{stats['requests_new']} new in inbox"},
        {"label": "Active Estimations", "icon": "edit_note",
         "value": str(stats["estimations_draft"] + stats["estimations_final"]),
         "sub": f"{stats['estimations_final']} awaiting approval"},
        {"label": "Avg Estimation", "icon": "schedule",
         "value": f"{stats['avg_grand_total_hours']:,.0f}h", "sub": "across all estimations"},
        {"label": "Team Utilization", "icon": "speed",
         "value": f"{stats['avg_utilization_pct']:.0f}%", "sub": "of allocated capacity",
         "help": "Average % of available hours consumed by assigned estimators "
                 "across final + approved estimations."},
    ]
    pipeline = [
        {"label": "Inbox · New", "count": stats["requests_new"], "href": req_nav},
        {"label": "Estimating", "count": stats["requests_in_progress"], "href": req_nav},
        {"label": "Draft", "count": stats["estimations_draft"], "href": "/estimations"},
        {"label": "Final", "count": stats["estimations_final"], "href": "/estimations"},
        {"label": "Approved", "count": stats["estimations_approved"], "href": "/estimations"},
    ]

    req_counts = {"New": stats["requests_new"], "In Progress": stats["requests_in_progress"],
                  "Completed": stats["requests_completed"]}
    other = stats["total_requests"] - sum(req_counts.values())
    if other > 0:
        req_counts["Other"] = other
    by_type = dict(sorted(stats["tasks_by_type"].items(), key=lambda kv: kv[1], reverse=True))
    by_product = dict(sorted(stats["tasks_by_product_type"].items(), key=lambda kv: kv[1], reverse=True))
    charts = [
        _chart("estimations", f"Estimations · {stats['total_estimations']}", "Estimations",
               _slices({"Draft": stats["estimations_draft"], "Final": stats["estimations_final"],
                        "Approved": stats["estimations_approved"]}, EST_COLORS),
               nav="/estimations", empty="No estimations yet",
               empty_link=("/estimation/new", "New Estimation", "add_circle")),
        _chart("requests", f"Requests · {stats['total_requests']}", "Requests",
               _slices(req_counts, REQ_COLORS), nav=req_nav, empty="No requests yet"),
        _chart("features", f"Features · {sum(stats['features_by_category'].values())}", "Features",
               _slices(stats["features_by_category"]), nav="/features",
               empty="No features configured yet",
               empty_link=("/features", "Configure Features", "category")),
        _chart("risks", f"Risks · {sum(stats['risks_by_likelihood'].values())}", "Likelihood",
               _slices(stats["risks_by_likelihood"], LIKELIHOOD_COLORS), nav="/risks",
               empty="No risks configured yet", empty_link=("/risks", "Add Risks", "warning")),
        _chart("task-types", "Task Templates · by Type", "Task templates",
               [{"name": k, "value": v, "color": "#5B8DB0"} for k, v in by_type.items()], kind="bar",
               nav="/tasks", empty="No task templates yet"),
        _chart("task-product-types", "Task Templates · by Product Type", "Product Type",
               _slices(by_product), nav="/tasks", empty="No task templates yet"),
    ]
    payload = [{k: c[k] for k in ("id", "kind", "name", "data", "nav")} for c in charts if c["total"]]

    now = datetime.now()
    recent_estimations = [
        {**e, "ident": e["estimation_number"] or f"#{e['id']}", "title": e["project_name"],
         "num": f"{e['grand_total_hours']:,.0f}h",
         "stripe": FEASIBILITY_STRIPE.get(e["feasibility_status"], ""),
         "age": format_age(e["created_at"], now), "href": f"/estimation/{e['id']}"}
        for e in stats["recent_estimations"]
    ]
    recent_requests = [
        {**r, "ident": r["request_number"] or f"#{r['id']}", "num": r["priority"],
         "stripe": PRIORITY_STRIPE.get(r["priority"], ""),
         "age": format_age(r["created_at"], now),
         "href": f"/requests/{r['id']}" if can_requests else None}
        for r in stats["recent_requests"]
    ]
    return {
        "page_title": "Dashboard", "stats": stats, "kpis": kpis, "pipeline": pipeline,
        "charts": charts, "chart_payload": payload, "can_requests": can_requests,
        "recent_estimations": recent_estimations, "recent_requests": recent_requests,
        "requests_see_all": req_nav or "",
    }


@require_GET
def dashboard(request):
    return render(request, "web/dashboard.html", dashboard_context(request))


@require_GET
def dashboard_summary(request):
    """KPI strip + pipeline state, polled every 60 s by the dashboard."""
    return render(request, "web/dashboard/_summary.html", dashboard_context(request))


def placeholder(request, url_name: str, **kwargs):
    """A page that keeps its v3 URL and access rule while its HTML is being rebuilt."""
    item = ITEMS_BY_NAME[url_name]
    if not allowed(request.user, item.requires):
        raise PermissionDenied
    return render(request, "web/placeholder.html", {"item": item, "page_title": item.label})


@login_not_required
def error_400(request, exception=None):
    return render_error(request, 400)


@login_not_required
def error_403(request, exception=None):
    return render_error(request, 403)


@login_not_required
def error_404(request, exception=None):
    return render_error(request, 404)


@login_not_required
def error_500(request):
    # No request context: the shell's context processor needs the database,
    # which may be the reason for the error.
    return HttpResponseServerError(loader.render_to_string("errors/500.html"))


@login_not_required
def csrf_failure(request, reason=""):
    return render_error(request, 403, "Your form expired or was sent from another page. "
                                      "Reload the page and try again.")
