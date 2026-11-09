"""Schemas for /dashboard/stats (v3 ``DashboardStatsOut`` and its recent-item rows)."""

from __future__ import annotations

from ninja import Schema


class RecentEstimationOut(Schema):
    id: int
    estimation_number: str | None = None
    project_name: str
    grand_total_hours: float = 0
    feasibility_status: str = ""
    status: str = ""
    version: int = 1
    created_at: str | None = None


class RecentRequestOut(Schema):
    id: int
    request_number: str = ""
    title: str = ""
    priority: str = ""
    status: str = ""
    created_at: str | None = None


class DashboardStatsOut(Schema):
    total_requests: int = 0
    requests_new: int = 0
    requests_in_progress: int = 0
    requests_completed: int = 0
    total_estimations: int = 0
    estimations_draft: int = 0
    estimations_final: int = 0
    estimations_approved: int = 0
    avg_grand_total_hours: float = 0
    avg_utilization_pct: float = 0
    recent_estimations: list[RecentEstimationOut] = []
    recent_requests: list[RecentRequestOut] = []
    features_by_category: dict[str, int] = {}
    tasks_by_type: dict[str, int] = {}
    tasks_by_product_type: dict[str, int] = {}
    risks_by_category: dict[str, int] = {}
    risks_by_likelihood: dict[str, int] = {}
