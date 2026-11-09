"""Bearer-JWT authentication with the v3 role hierarchy.

Usage in a router::

    router = Router(auth=authenticated)          # any logged-in user

    @router.get("/users", auth=role("ADMIN"))     # ADMIN only
    @router.post("/auth/login", auth=None)        # public
"""

from __future__ import annotations

from functools import cache

from ninja.errors import HttpError
from ninja.security import HttpBearer

from apps.accounts import tokens
from apps.accounts.models import User
from apps.accounts.roles import has_role


class JWTAuth(HttpBearer):
    def __init__(self, min_role: str | None = None) -> None:
        super().__init__()
        self.min_role = min_role

    def authenticate(self, request, token: str) -> User:
        payload = tokens.decode_access_token(token)
        if payload is None:
            raise HttpError(401, "Invalid or expired token")
        user = User.objects.filter(pk=int(str(payload["sub"])), is_active=True).first()
        if user is None:
            raise HttpError(401, "User not found or inactive")
        if self.min_role and not has_role(user.role, self.min_role):
            raise HttpError(403, f"Insufficient permissions. Required role: {self.min_role}")
        request.user = user
        return user


authenticated = JWTAuth()


@cache
def role(min_role: str) -> JWTAuth:
    """Auth requiring at least ``min_role`` (VIEWER < ESTIMATOR < APPROVER < ADMIN)."""
    return JWTAuth(min_role)


def optional_user(request) -> User | None:
    """The bearer user if a valid token is present, else None (v3 ``get_optional_user``)."""
    header = request.headers.get("Authorization", "")
    if not header.startswith("Bearer "):
        return None
    payload = tokens.decode_access_token(header.split(" ", 1)[1])
    if payload is None:
        return None
    return User.objects.filter(pk=int(str(payload["sub"])), is_active=True).first()
