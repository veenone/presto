"""Audit trail writer (port of v3 ``AuthService.log_action``)."""

from __future__ import annotations

from typing import Any

from .models import AuditLog, User


def log_action(
    user: User | int | None,
    action: str,
    resource_type: str | None = None,
    resource_id: int | None = None,
    details: dict[str, Any] | None = None,
    ip_address: str | None = None,
) -> AuditLog:
    """Append an audit entry. ``user`` may be a User, a user id, or None (system)."""
    user_id = user.pk if isinstance(user, User) else user
    return AuditLog.objects.create(
        user_id=user_id, action=action, resource_type=resource_type,
        resource_id=resource_id, details=details or {}, ip_address=ip_address,
    )
