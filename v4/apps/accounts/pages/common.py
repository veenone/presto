"""Shared bits for the admin people pages."""

from __future__ import annotations

from django.core.exceptions import PermissionDenied
from django.http import JsonResponse

from apps.accounts.models import Role

ROLE_CHOICES = [(r, r) for r in (Role.VIEWER, Role.ESTIMATOR, Role.APPROVER, Role.ADMIN)]


def require_admin(request) -> None:
    """Every page and action in this group is ADMIN only (navigation rule "ADMIN")."""
    if getattr(request.user, "role", None) != Role.ADMIN:
        raise PermissionDenied


def detail(message: str, status: int = 400) -> JsonResponse:
    """Error body the htmx error handler turns into a toast (same shape as the API)."""
    return JsonResponse({"detail": message}, status=status)
