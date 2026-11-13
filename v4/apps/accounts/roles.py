"""Role hierarchy used by the API (same rules as v3 ``AuthService.has_permission``)."""

from __future__ import annotations

ROLE_HIERARCHY: dict[str, int] = {"VIEWER": 0, "ESTIMATOR": 1, "APPROVER": 2, "ADMIN": 3}


def has_role(user_role: str | None, required_role: str) -> bool:
    """True when ``user_role`` is at least ``required_role`` in the hierarchy."""
    return ROLE_HIERARCHY.get(user_role or "", -1) >= ROLE_HIERARCHY.get(required_role, 999)
