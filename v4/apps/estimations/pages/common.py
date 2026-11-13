"""Shared bits for the estimation pages: badges, role checks and lookups."""

from __future__ import annotations

from django.core.exceptions import PermissionDenied
from django.http import Http404
from django.utils.html import format_html

from apps.accounts.roles import has_role
from apps.estimations.models import Estimation

STATUSES = ["DRAFT", "FINAL", "APPROVED", "REVISED"]
FEASIBILITY = ["FEASIBLE", "AT_RISK", "NOT_FEASIBLE"]

# Minimum roles: the same as the matching REST endpoints (apps/api/routers/*).
ROLE_CREATE = "ESTIMATOR"        # POST /estimations
ROLE_STATUS = "ESTIMATOR"        # POST /estimations/{id}/status
ROLE_EDIT = "ESTIMATOR"          # PUT /estimations/{id}/draft, /revise
ROLE_EXPORT = "ESTIMATOR"        # POST /estimations/{id}/export, /export-tasks
ROLE_SEND_REPORT = "ESTIMATOR"   # POST /estimations/{id}/send-report
ROLE_ASSIGN = "APPROVER"         # POST /estimations/{id}/assign
ROLE_ARCHIVE = "APPROVER"        # POST /estimations/{id}/archive
# Calibrate, versions, diff, reports and comparison: any signed-in user.


def can(user, role: str) -> bool:
    return has_role(user.role, role)


def require_role(user, role: str) -> None:
    if not can(user, role):
        raise PermissionDenied


def is_author_or_admin(est: Estimation, user) -> bool:
    """Who may edit a DRAFT in place (same rule as ``services.edit_draft``)."""
    return user.role == "ADMIN" or est.creator_id is None or est.creator_id == user.id


def get_estimation_or_404(estimation_id: int) -> Estimation:
    est = (Estimation.objects.select_related("assigned_to", "team", "request", "creator")
           .filter(pk=estimation_id).first())
    if est is None:
        raise Http404("Estimation not found")
    return est


class BadgeCell(str):
    """A table value rendered as a coloured pill (v3 q-badge colours); sorts and searches as text."""

    kind = "status"

    def __str__(self) -> str:
        return self

    def __html__(self) -> str:
        value = str.__str__(self)
        return format_html('<span class="badge est-badge est-{}-{}">{}</span>', self.kind, value, value)


class StatusCell(BadgeCell):
    kind = "status"


class FeasibilityCell(BadgeCell):
    kind = "feas"


class LinkCell(str):
    """A table value rendered as a link to the estimation detail page (covers the whole row)."""

    url = ""

    def __new__(cls, value: str, url: str):
        obj = super().__new__(cls, value)
        obj.url = url
        return obj

    def __str__(self) -> str:
        return self

    def __html__(self) -> str:
        return format_html('<a class="est-row-link" href="{}">{}</a>', self.url, str.__str__(self))


def version_label(est: Estimation) -> str:
    """v3 ``_version_label``: number, plus ``(vN)`` from version 2 on."""
    v = est.version or 1
    num = est.estimation_number or f"EST-{est.id}"
    return f"{num} (v{v})" if v > 1 else num
