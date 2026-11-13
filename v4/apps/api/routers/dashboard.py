"""API routes: /dashboard/stats."""

from __future__ import annotations

from ninja import Router

from apps.api.auth import authenticated
from apps.api.schemas.dashboard import DashboardStatsOut
from apps.core.dashboard import dashboard_stats

router = Router(auth=authenticated, tags=["dashboard"])


@router.get("/dashboard/stats", response=DashboardStatsOut)
def get_dashboard_stats(request):
    """Aggregate statistics for the dashboard."""
    return dashboard_stats()
