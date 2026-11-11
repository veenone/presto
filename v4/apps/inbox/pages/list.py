"""Request Inbox list page (v3 ``/requests``): KPI strip, status segments, source/priority filters,
search, sortable paged table, Add Request dialog and import from Redmine / Jira."""

from __future__ import annotations

from datetime import date

from django.urls import path as url_path

from apps.catalog.pages.filtering import FilteredCrudPage, SelectFilter
from apps.inbox import services
from apps.web.crud import Column, ListState, Segment, Tile

from .common import (
    CLOSED,
    PRIORITIES,
    ROLE_ASSIGN,
    ROLE_CREATE,
    ROLE_EDIT,
    ROLE_ESTIMATE,
    ROLE_SYNC,
    SOURCES,
    STATUS_LABELS,
    STATUSES,
    ActionsCell,
    AgeCell,
    AssigneeCell,
    NumberCell,
    PriorityCell,
    SourceCell,
    StatusCell,
    TitleCell,
    can,
    check_view,
    get_request_or_404,
    user_label,
)
from .forms import RequestCreateForm, RequestEditForm


class InboxPage(FilteredCrudPage):
    url_name = "requests"
    title = "Request Inbox"
    noun = "Request"
    write_role = ROLE_CREATE  # POST /requests and PUT /requests/{id} (both ESTIMATOR)
    can_delete = False        # v3 has no request delete
    template_name = "inbox/list.html"
    form_template = "inbox/_request_modal.html"
    default_sort = "created_at"
    search_placeholder = "Search number, title, requester…"
    columns = [
        Column("request_number", "Number", sortable=True, kind="mono"),
        Column("request_source", "Source", align="center"),
        Column("title", "Title", sortable=True),
        Column("priority", "Priority", sortable=True),
        Column("status", "Status", sortable=True),
        Column("requester_name", "Requester", sortable=True),
        Column("assigned", "Assigned", sortable=True),
        Column("created_at", "Age", sortable=True, align="right"),
        Column("actions", "", align="right"),
    ]
    search_fields = ["request_number", "title", "requester_name", "external_id"]
    select_filters = [SelectFilter("source", "Source"), SelectFilter("priority", "Priority")]

    # -- access -----------------------------------------------------------------
    def check_view(self, request) -> None:
        check_view(request.user)

    def context(self, request, **extra) -> dict:
        ctx = super().context(request, **extra)
        user = request.user
        ctx.update({
            # The generic Actions column stays off: the row buttons live in the "actions" cell.
            "can_write": False,
            "can_create": can(user, ROLE_CREATE),
            "can_sync": can(user, ROLE_SYNC),
        })
        return ctx

    # -- filters ----------------------------------------------------------------
    def filter_choices(self, request, name: str) -> list[tuple[str, str]]:
        values = SOURCES if name == "source" else list(reversed(PRIORITIES))
        return [("all", "All")] + [(v, v) for v in values]

    def filter_match(self, row: dict, name: str, value: str) -> bool:
        return row["_source" if name == "source" else "_priority"] == value

    def parse_state(self, request) -> ListState:
        state = super().parse_state(request)
        if not state.sort:  # newest first, as the API lists them
            state.sort, state.dir = "created_at", "desc"
        return state

    # -- rows -------------------------------------------------------------------
    def get_rows(self, request) -> list[dict]:
        user = request.user
        estimate, assign = can(user, ROLE_ESTIMATE), can(user, ROLE_ASSIGN)
        rows = []
        for r in services.list_requests():
            status, priority, source = r.status or "", r.priority or "", r.request_source or ""
            assignee = user_label(r.assigned_to)
            rows.append({
                "id": r.id,
                "request_number": NumberCell(r.request_number, f"/requests/{r.id}", priority),
                "request_source": SourceCell(source),
                "title": TitleCell(r.title or ""),
                "priority": PriorityCell(priority),
                "status": StatusCell(status),
                "requester_name": r.requester_name or "",
                "assigned": AssigneeCell(assignee),
                "created_at": AgeCell(r.created_at),
                "actions": ActionsCell(r.id, estimate=estimate and status not in CLOSED, assign=assign),
                "external_id": r.external_id or "",
                "_status": status, "_priority": priority, "_source": source,
                "_assigned": bool(r.assigned_to_id),
                "_created": r.created_at.date() if r.created_at else None,
            })
        return rows

    def get_segments(self, rows):
        return [Segment("all", "All")] + [
            Segment(s, STATUS_LABELS[s], lambda r, s=s: r["_status"] == s) for s in STATUSES
        ]

    def get_tiles(self, rows):
        today = date.today()
        open_rows = [r for r in rows if r["_status"] not in CLOSED]
        return [
            Tile("Open", str(len(open_rows)), "in progress"),
            Tile("New today", str(sum(1 for r in rows if r["_created"] == today)), "received today"),
            Tile("High priority", str(sum(1 for r in rows if r["_priority"] in ("HIGH", "CRITICAL"))),
                 "HIGH / CRITICAL"),
            Tile("Unassigned", str(sum(1 for r in open_rows if not r["_assigned"])), "need an owner"),
        ]

    # -- add / edit dialogs -------------------------------------------------------
    def get_form(self, request, data=None, initial=None, obj_id=None):
        if obj_id is None:
            if data is None and initial is None:
                initial = {"request_number": services.next_request_number()}
            return RequestCreateForm(data=data, initial=initial)
        return RequestEditForm(data=data, initial=initial)

    def get_initial(self, request, obj_id: int) -> dict:
        req = get_request_or_404(obj_id)
        return {
            "title": req.title, "description": req.description or "", "priority": req.priority,
            "status": req.status, "requester_name": req.requester_name,
            "requester_email": req.requester_email or "", "business_unit": req.business_unit or "",
            "product_type": req.product_type or "", "requested_delivery_date": req.requested_delivery_date,
            "notes": req.notes or "",
        }

    def create(self, request, data: dict):
        return services.create_manual_request(RequestCreateForm.payload(data))

    def update(self, request, obj_id: int, data: dict):
        return services.update_request(get_request_or_404(obj_id), RequestEditForm.payload(data))

    def user_can_write(self, request) -> bool:
        return can(request.user, ROLE_EDIT)

    @classmethod
    def urls(cls, prefix: str) -> list:
        page = cls()
        prefix = prefix.strip("/")
        return [
            url_path(prefix, page.list_view, name=cls.url_name),
            url_path(f"{prefix}/new", page.new_view, name="requests_new"),
            url_path(f"{prefix}/<int:obj_id>/edit", page.edit_view, name="requests_edit"),
        ]
