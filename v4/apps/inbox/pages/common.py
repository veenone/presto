"""Shared bits for the request inbox pages: roles, choices, table cells and lookups."""

from __future__ import annotations

from datetime import datetime

from django.core.exceptions import PermissionDenied
from django.http import Http404
from django.utils import timezone
from django.utils.html import format_html

from apps.accounts.roles import has_role
from apps.inbox.models import Request
from apps.web.navigation import ITEMS_BY_NAME, allowed

# Minimum roles: the same as the matching REST endpoints.
ROLE_CREATE = "ESTIMATOR"      # POST /requests
ROLE_EDIT = "ESTIMATOR"        # PUT /requests/{id}
ROLE_UPLOAD = "ESTIMATOR"      # POST /requests/{id}/attachments
ROLE_ASSIGN = "APPROVER"       # POST /requests/{id}/assign
ROLE_SYNC = "ADMIN"            # POST /integrations/{system}/sync
ROLE_ESTIMATE = "ESTIMATOR"    # the wizard behind /estimation/new?request_id=<id>

SOURCES = ["MANUAL", "REDMINE", "JIRA", "EMAIL"]
PRIORITIES = ["LOW", "MEDIUM", "HIGH", "CRITICAL"]
STATUSES = ["NEW", "IN_ESTIMATION", "ESTIMATED", "COMPLETED", "REJECTED", "DELETED_UPSTREAM"]
EDIT_STATUSES = ["NEW", "IN_ESTIMATION", "ESTIMATED", "COMPLETED", "REJECTED"]  # v3 edit dialog
CLOSED = {"COMPLETED", "REJECTED", "DELETED_UPSTREAM"}
SYNC_SYSTEMS = ["REDMINE", "JIRA"]

STATUS_LABELS = {"NEW": "New", "IN_ESTIMATION": "In Estimation", "ESTIMATED": "Estimated",
                 "COMPLETED": "Completed", "REJECTED": "Rejected", "DELETED_UPSTREAM": "Deleted"}
SOURCE_ICONS = {"JIRA": "integration_instructions", "REDMINE": "rss_feed", "EMAIL": "mail", "MANUAL": "edit"}
PRIORITY_RANK = {"CRITICAL": 0, "HIGH": 1, "MEDIUM": 2, "LOW": 3}


def can(user, role: str) -> bool:
    return has_role(user.role, role)


def require_role(user, role: str) -> None:
    if not can(user, role):
        raise PermissionDenied


def check_view(user) -> None:
    """Page access: the "requests" navigation rule (view_requests or manage_requests)."""
    item = ITEMS_BY_NAME["requests"]
    if not allowed(user, item.requires):
        raise PermissionDenied


def get_request_or_404(request_id: int) -> Request:
    req = Request.objects.select_related("assigned_to").filter(pk=request_id).first()
    if req is None:
        raise Http404("Request not found")
    return req


def user_label(user) -> str:
    return (user.display_name or user.username) if user else ""


def format_age(value: datetime | None) -> str:
    """v3 ``format_age``: ``today`` / ``3d`` / ``2w`` / ``5mo``."""
    if not value:
        return ""
    days = (timezone.now() - value).days if timezone.is_aware(value) else (datetime.now() - value).days
    if days < 1:
        return "today"
    if days < 7:
        return f"{days}d"
    if days < 30:
        return f"{days // 7}w"
    return f"{days // 30}mo"


def status_badge(status: str) -> str:
    status = status or ""
    return format_html('<span class="badge inb-badge inb-status-{}">{}</span>', status,
                       "Deleted" if status == "DELETED_UPSTREAM" else status)


def priority_badge(priority: str) -> str:
    priority = priority or ""
    return format_html('<span class="badge inb-badge inb-pri-{}">{}</span>', priority, priority)


class _Cell(str):
    """A table value with custom HTML; sorts and searches by its string value."""

    def __str__(self) -> str:
        return self


class NumberCell(_Cell):
    """Request number linking to the detail page; its link covers the row; priority stripe via CSS."""

    def __new__(cls, value: str, url: str, priority: str):
        obj = super().__new__(cls, value)
        obj.url, obj.priority = url, priority
        return obj

    def __html__(self) -> str:
        return format_html('<a class="inb-row-link inb-stripe-{}" href="{}">{}</a>',
                           self.priority, self.url, str.__str__(self))


class SourceCell(_Cell):
    def __html__(self) -> str:
        value = str.__str__(self)
        return format_html('<span class="icon inb-source-icon" title="{}" aria-label="{}">{}</span>',
                           value or "Unknown source", value or "Unknown source",
                           SOURCE_ICONS.get(value, "help_outline"))


class TitleCell(_Cell):
    def __html__(self) -> str:
        value = str.__str__(self)
        return format_html('<span class="inb-title" title="{}">{}</span>', value, value)


class PriorityCell(_Cell):
    """Sorts by urgency (CRITICAL first) rather than alphabetically."""

    def __new__(cls, priority: str):
        obj = super().__new__(cls, f"{PRIORITY_RANK.get(priority, 9)}{priority}")
        obj.priority = priority
        return obj

    def __html__(self) -> str:
        return priority_badge(self.priority)


class StatusCell(_Cell):
    def __html__(self) -> str:
        return status_badge(str.__str__(self))


class AssigneeCell(_Cell):
    """Assignee name, or a muted "Unassigned" (which is also its value, so it never renders as "—")."""

    def __new__(cls, name: str):
        obj = super().__new__(cls, name or "Unassigned")
        obj.unassigned = not name
        return obj

    def __html__(self) -> str:
        if self.unassigned:
            return format_html('<span class="muted inb-unassigned">{}</span>', "Unassigned")
        return format_html("{}", str.__str__(self))


class AgeCell(_Cell):
    """Sorts by the ISO timestamp; shows the compact age with the full date as a tooltip."""

    def __new__(cls, created: datetime | None):
        obj = super().__new__(cls, created.isoformat() if created else "")
        obj.created = created
        return obj

    def __html__(self) -> str:
        if not self.created:
            return format_html('<span class="muted">{}</span>', "—")
        local = timezone.localtime(self.created) if timezone.is_aware(self.created) else self.created
        return format_html('<span class="ed-mono inb-age" title="{}">{}</span>',
                           local.strftime("%d %b %Y, %H:%M"), format_age(self.created))


class ActionsCell(_Cell):
    """Row buttons (estimate, assign, view); they sit above the row link."""

    def __new__(cls, req_id: int, *, estimate: bool, assign: bool):
        obj = super().__new__(cls, str(req_id))  # non-empty, or the cell renders as "—"
        obj.req_id, obj.estimate, obj.assign = req_id, estimate, assign
        return obj

    def __html__(self) -> str:
        parts = []
        if self.estimate:
            parts.append(format_html(
                '<a class="btn-round sm inb-tone-positive inb-row-btn" href="/estimation/new?request_id={}" '
                'title="Create estimation from this request" aria-label="Create estimation from request {}">'
                '<span class="icon" aria-hidden="true">add_chart</span></a>', self.req_id, self.req_id))
        if self.assign:
            parts.append(format_html(
                '<button type="button" class="btn-round sm inb-row-btn" hx-get="/requests/{}/assign" '
                'hx-target="#modal" hx-swap="innerHTML" title="Assign" aria-label="Assign request {}">'
                '<span class="icon" aria-hidden="true">person_add</span></button>', self.req_id, self.req_id))
        parts.append(format_html(
            '<a class="btn-round sm tone-primary inb-row-btn" href="/requests/{}" title="View request" '
            'aria-label="View request {}"><span class="icon" aria-hidden="true">visibility</span></a>',
            self.req_id, self.req_id))
        return format_html('<span class="inb-actions">{}</span>', format_html("".join(["{}"] * len(parts)), *parts))
