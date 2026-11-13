"""JWT access tokens and server-side refresh tokens (port of v3 ``AuthService``).

Claims, algorithm, lifetimes and the ``jwt_secret`` configuration key are the
same as v3, so tokens issued by one version validate in the other.
"""

from __future__ import annotations

import secrets
from datetime import UTC, datetime, timedelta

import jwt

from apps.core.models import Configuration

from .models import User, UserSession

JWT_ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60
REFRESH_TOKEN_EXPIRE_DAYS = 30
JWT_SECRET_KEY = "jwt_secret"


def _utcnow_naive() -> datetime:
    # Timestamps are stored naive UTC, as in v3 (USE_TZ = False).
    return datetime.now(UTC).replace(tzinfo=None)


def jwt_secret() -> str:
    """Signing secret from configuration; generated and stored on first use."""
    cfg = Configuration.objects.filter(key=JWT_SECRET_KEY).first()
    if cfg and cfg.value:
        return cfg.value
    secret = secrets.token_hex(32)
    Configuration.objects.update_or_create(
        key=JWT_SECRET_KEY,
        defaults={"value": secret, "description": "JWT signing secret (auto-generated)"},
    )
    return secret


def create_access_token(user: User) -> str:
    now = datetime.now(UTC)
    payload = {
        "sub": str(user.pk),
        "username": user.username,
        "role": user.role,
        "exp": now + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES),
        "iat": now,
        "type": "access",
    }
    return jwt.encode(payload, jwt_secret(), algorithm=JWT_ALGORITHM)


def decode_access_token(token: str) -> dict | None:
    """Payload of a valid, unexpired access token; None otherwise."""
    try:
        payload = jwt.decode(token, jwt_secret(), algorithms=[JWT_ALGORITHM])
    except jwt.InvalidTokenError:
        return None
    return payload if payload.get("type") == "access" else None


def create_refresh_token(user: User) -> str:
    token = secrets.token_urlsafe(64)
    UserSession.objects.create(
        user=user, refresh_token=token,
        expires_at=_utcnow_naive() + timedelta(days=REFRESH_TOKEN_EXPIRE_DAYS),
    )
    return token


def issue_token_pair(user: User) -> tuple[str, str]:
    return create_access_token(user), create_refresh_token(user)


def rotate_refresh_token(token: str) -> tuple[User, str, str] | None:
    """Exchange a refresh token for a new pair (the old one is deleted)."""
    record = UserSession.objects.select_related("user").filter(refresh_token=token).first()
    if record is None:
        return None
    if record.expires_at < _utcnow_naive():
        record.delete()
        return None
    user = record.user
    if not user.is_active:
        return None
    record.delete()
    access, refresh = issue_token_pair(user)
    return user, access, refresh


def revoke_refresh_token(token: str) -> bool:
    deleted, _ = UserSession.objects.filter(refresh_token=token).delete()
    return deleted > 0
