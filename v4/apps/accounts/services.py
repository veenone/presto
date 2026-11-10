"""User accounts, login flows and audit-log queries (port of v3 ``AuthService``).

Token handling lives in :mod:`apps.accounts.tokens`, the audit writer in
:mod:`apps.accounts.audit` and the role hierarchy in :mod:`apps.accounts.roles`.
This module holds everything else the v3 service did, minus the SQLAlchemy
session argument. It has no django-ninja imports so the HTML views can reuse it.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import bcrypt
from django.db import transaction

from apps.core import config

from . import tokens
from .audit import log_action
from .models import AuditLog, Role, User

LOGIN_FAILED_MESSAGE = "Invalid username or password"


from apps.core.errors import ServiceError  # noqa: E402


class AuthError(ServiceError):
    """A login failure with the HTTP status and message v3 answered with."""


def _utcnow_naive() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _blank_to_none(value: Any) -> Any:
    return None if isinstance(value, str) and not value.strip() else value


# -- passwords ---------------------------------------------------------------

def hash_password(password: str) -> str:
    """bcrypt hash in the v3 storage format."""
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def verify_password(password: str, password_hash: str) -> bool:
    return bcrypt.checkpw(password.encode(), password_hash.encode())


# -- login flows ---------------------------------------------------------------

def login(username: str, password: str, ip_address: str | None = None) -> tuple[User, str, str] | None:
    """Local username/password login. ``(user, access, refresh)`` or None.

    Updates ``last_login`` and writes a LOGIN audit entry on success.
    """
    user = _check_local(username, password)
    if user is None:
        return None
    user.last_login = _utcnow_naive()
    user.save(update_fields=["last_login"])
    access, refresh_token = tokens.issue_token_pair(user)
    log_action(user, "LOGIN", ip_address=ip_address)
    return user, access, refresh_token


def refresh(refresh_token: str) -> tuple[User, str, str] | None:
    """Rotate a refresh token (v3 ``AuthService.refresh``)."""
    return tokens.rotate_refresh_token(refresh_token)


def logout(refresh_token: str) -> bool:
    return tokens.revoke_refresh_token(refresh_token)


def _check_local(username: str, password: str) -> User | None:
    user = User.objects.filter(username=username, is_active=True).first()
    if user is None or not user.password or not user.check_password(password):
        return None
    return user


def verify_credentials(
    username: str, password: str, auth_method: str = "auto", ip_address: str | None = None,
) -> User:
    """Check credentials: local first, then LDAP (``auth_method`` auto|local|ldap).

    Shared by the API login and the web login. On success updates
    ``last_login`` and writes a LOGIN audit entry; issues no tokens. Raises
    :class:`AuthError` with the v3 status and message on failure: 502 when
    LDAP is unreachable, 400 when ``ldap`` is requested but not configured,
    401 otherwise. Every failure writes a LOGIN_FAILED entry.
    """
    method = auth_method or "auto"
    user: User | None = None

    if method in ("auto", "local"):
        user = _check_local(username, password)

    if user is None and method in ("auto", "ldap"):
        from .ldap_provider import LDAPConnectionError, LDAPProvider

        ldap = LDAPProvider()
        if ldap.is_configured:
            try:
                user = ldap.authenticate(username, password)
            except LDAPConnectionError as exc:
                log_action(
                    None, "LOGIN_FAILED", "auth",
                    details={
                        "username": username, "auth_method": "ldap",
                        "reason": f"LDAP unreachable: {exc}",
                    },
                    ip_address=ip_address,
                )
                raise AuthError(502, f"LDAP server unreachable: {exc}") from exc
        elif method == "ldap":
            raise AuthError(400, "LDAP authentication is not configured")

    if user is None:
        log_action(
            None, "LOGIN_FAILED", "auth",
            details={"username": username, "auth_method": method, "reason": LOGIN_FAILED_MESSAGE},
            ip_address=ip_address,
        )
        raise AuthError(401, LOGIN_FAILED_MESSAGE)

    user.last_login = _utcnow_naive()
    user.save(update_fields=["last_login"])
    log_action(user, "LOGIN", ip_address=ip_address)
    return user


def authenticate(
    username: str, password: str, auth_method: str = "auto", ip_address: str | None = None,
) -> tuple[User, str, str]:
    """``POST /auth/login``: :func:`verify_credentials`, then a new token pair."""
    user = verify_credentials(username, password, auth_method, ip_address)
    access, refresh_token = tokens.issue_token_pair(user)
    return user, access, refresh_token


def get_auth_providers() -> list[str]:
    """Providers offered on the login page (``GET /auth/providers``)."""
    providers = ["local"]
    if config.get_str("ldap_url").strip():
        providers.append("ldap")
    if config.get_str("oidc_issuer").strip():
        providers.append("oidc")
    return providers


def change_password(user_id: int, current_password: str, new_password: str) -> bool:
    """Self-service password change. False if the user has no local password
    or ``current_password`` is wrong."""
    user = User.objects.filter(pk=user_id).first()
    if user is None or not user.password or not user.check_password(current_password):
        return False
    user.set_password(new_password)
    user.save(update_fields=["password"])
    return True


# -- user CRUD -------------------------------------------------------------------

def get_user_by_id(user_id: int) -> User | None:
    return User.objects.filter(pk=user_id).first()


def get_user_by_username(username: str) -> User | None:
    return User.objects.filter(username=username).first()


def get_user_by_email(email: str | None) -> User | None:
    email = _blank_to_none(email)
    return User.objects.filter(email=email).first() if email else None


def create_user(
    username: str,
    display_name: str,
    password: str | None = None,
    email: str | None = None,
    role: str = Role.VIEWER,
    auth_provider: str = "local",
    external_id: str | None = None,
    team_member_id: int | None = None,
) -> User:
    """Create a user; external-provider users have no password (NULL)."""
    user = User(
        username=username, display_name=display_name, email=_blank_to_none(email),
        auth_provider=auth_provider, external_id=external_id, role=role,
        team_member_id=team_member_id,
    )
    if password:
        user.set_password(password)
    else:
        user.password = None
    user.save()
    return user


# Columns that are NOT NULL: a null in a partial update is ignored instead of
# failing with an integrity error.
_NOT_NULL_FIELDS = {"username", "display_name", "role", "is_active", "auth_provider"}


def update_user(user_id: int, **kwargs: Any) -> User | None:
    """Partial update by column name; ``password=`` is hashed. None if not found."""
    user = User.objects.filter(pk=user_id).first()
    if user is None:
        return None
    for key, value in kwargs.items():
        if key == "password":
            if value:
                user.set_password(str(value))
        elif key == "email":
            user.email = _blank_to_none(value)
        elif key == "last_login_at":
            user.last_login = value
        elif key in _NOT_NULL_FIELDS and value is None:
            continue
        elif key not in ("id", "pk") and hasattr(user, key):
            setattr(user, key, value)
    user.save()
    return user


def delete_user(user_id: int) -> bool:
    deleted, _ = User.objects.filter(pk=user_id).delete()
    return deleted > 0


def list_users(active_only: bool = False) -> list[User]:
    qs = User.objects.all()
    if active_only:
        qs = qs.filter(is_active=True)
    return list(qs.order_by("username"))


def list_assignable_users() -> list[User]:
    """Active users for assignment dropdowns (``GET /users/assignable``)."""
    return list(User.objects.filter(is_active=True))


@transaction.atomic
def reset_ldap_synced_users(keep_user: User | None = None) -> int:
    """Delete every ``auth_provider='ldap'`` user except ``keep_user``.

    Sessions cascade; audit and estimation references are set NULL.
    """
    qs = User.objects.filter(auth_provider="ldap")
    if keep_user is not None:
        qs = qs.exclude(pk=keep_user.pk)
    victims = list(qs)
    for u in victims:
        u.delete()
    return len(victims)


# -- audit trail -------------------------------------------------------------------

def get_audit_log(
    limit: int = 100,
    offset: int = 0,
    user_id: int | None = None,
    action: str | None = None,
    resource_type: str | None = None,
) -> list[AuditLog]:
    """Audit entries, newest first, with optional filters."""
    qs = AuditLog.objects.select_related("user")
    if user_id is not None:
        qs = qs.filter(user_id=user_id)
    if action:
        qs = qs.filter(action=action)
    if resource_type:
        qs = qs.filter(resource_type=resource_type)
    qs = qs.order_by("-created_at", "-id")
    offset = max(offset, 0)
    # SQLite treats a negative LIMIT as "no limit"; v3 passed it straight through.
    return list(qs[offset:] if limit < 0 else qs[offset:offset + limit])
