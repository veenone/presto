"""Estimations list page (v3 ``/estimations``): status tiles and segments, search, sort, paging."""

from __future__ import annotations

from django.urls import path as url_path

from apps.estimations.models import Estimation
from apps.web.crud import Column, CrudPage, ListState, Segment, Tile

from .common import ROLE_CREATE, STATUSES, FeasibilityCell, LinkCell, StatusCell, can, version_label


class EstimationsPage(CrudPage):
    url_name = "estimations"
    title = "Estimations"
    noun = "Estimation"
    can_add = can_edit = can_delete = False  # created in the wizard, changed on the detail page
    template_name = "estimations/list.html"
    default_sort = "created_at"
    search_placeholder = "Search by number, request, project, type, assignee…"
    columns = [
        Column("number", "#", sortable=True, kind="mono"),
        Column("request_number", "Request", sortable=True, kind="mono"),
        Column("project_name", "Project", sortable=True, kind="strong"),
        Column("project_type", "Type", sortable=True),
        Column("grand_total_hours", "Total Hours", sortable=True, kind="mono", align="right", fmt="{:,.1f}"),
        Column("feasibility_status", "Feasibility", sortable=True, align="center"),
        Column("status", "Status", sortable=True, align="center"),
        Column("assigned_to_name", "Assigned To", sortable=True),
        Column("created_at", "Created", sortable=True, kind="mono"),
    ]
    search_fields = ["number", "request_number", "project_name", "project_type", "assigned_to_name",
                     "status", "feasibility_status"]

    def user_can_write(self, request) -> bool:
        return False  # no row actions: rows link to the detail page

    def extra_context(self, request) -> dict:
        return {"can_create": can(request.user, ROLE_CREATE)}

    def parse_state(self, request) -> ListState:
        state = super().parse_state(request)
        if not state.sort:  # newest first, as the API lists them
            state.sort, state.dir = "created_at", "desc"
        return state

    def get_rows(self, request) -> list[dict]:
        rows = []
        qs = Estimation.objects.select_related("assigned_to", "request").order_by("-created_at", "-id")
        for e in qs:
            assigned = e.assigned_to
            url = f"/estimation/{e.id}"
            rows.append({
                "id": e.id,
                "number": LinkCell(version_label(e), url),
                "request_number": e.request.request_number if e.request_id and e.request else "",
                "project_name": LinkCell(e.project_name or "", url),
                "project_type": e.project_type or "",
                "grand_total_hours": round(e.grand_total_hours or 0, 1),
                "feasibility_status": FeasibilityCell(e.feasibility_status or ""),
                "status": StatusCell(e.status or ""),
                "assigned_to_name": (assigned.display_name or assigned.username) if assigned else "Unassigned",
                "created_at": e.created_at.strftime("%Y-%m-%d") if e.created_at else "",
                "_feasibility": e.feasibility_status or "",
                "_hours": e.grand_total_hours or 0,
            })
        return rows

    def get_segments(self, rows):
        return [Segment("all", "All")] + [
            Segment(s, s.title(), lambda r, s=s: r["status"] == s) for s in STATUSES
        ]

    def get_tiles(self, rows):
        def n(status: str) -> int:
            return sum(1 for r in rows if r["status"] == status)

        hours = sum(r["_hours"] for r in rows)
        at_risk = sum(1 for r in rows if r["_feasibility"] == "AT_RISK")
        not_feasible = sum(1 for r in rows if r["_feasibility"] == "NOT_FEASIBLE")
        tiles = [
            Tile("Total Estimations", str(len(rows)), f"{hours:,.0f} h estimated", "all"),
            Tile("Draft", str(n("DRAFT")), "in progress", "DRAFT"),
            Tile("Final", str(n("FINAL")), "awaiting approval", "FINAL"),
            Tile("Approved", str(n("APPROVED")), "signed off", "APPROVED"),
        ]
        if n("REVISED"):
            tiles.append(Tile("Revised", str(n("REVISED")), "being reworked", "REVISED"))
        tiles.append(Tile("At Risk", str(at_risk + not_feasible), f"{not_feasible} not feasible"))
        return tiles

    @classmethod
    def urls(cls, prefix: str) -> list:
        return [url_path(prefix.strip("/"), cls().list_view, name=cls.url_name)]
