"""Schemas for /auth/*, /users/* and /audit-log (v3 ``auth/schemas.py``)."""

from __future__ import annotations

from datetime import datetime

from ninja import Schema
from pydantic import AliasChoices, BaseModel, Field

from apps.api.common import JsonText

# -- requests ----------------------------------------------------------------


class LoginRequest(BaseModel):
    username: str
    password: str
    auth_method: str = "auto"  # "auto", "local" or "ldap"


class RefreshRequest(BaseModel):
    refresh_token: str


class UserCreate(BaseModel):
    username: str
    email: str | None = None
    display_name: str
    password: str | None = None
    role: str = "VIEWER"
    auth_provider: str = "local"
    team_member_id: int | None = None


class UserUpdate(BaseModel):
    """Partial update: only the supplied fields are applied."""

    email: str | None = None
    display_name: str | None = None
    role: str | None = None
    is_active: bool | None = None
    team_member_id: int | None = None


class PasswordChange(BaseModel):
    current_password: str
    new_password: str


# -- responses ---------------------------------------------------------------


class UserOut(Schema):
    id: int
    username: str
    email: str | None = None
    display_name: str
    role: str
    auth_provider: str
    is_active: bool
    team_member_id: int | None = None
    # Django's ``last_login`` is the v3 ``last_login_at`` column.
    last_login_at: datetime | None = Field(
        None, validation_alias=AliasChoices("last_login_at", "last_login"),
    )
    created_at: datetime | None = None


class TokenResponse(Schema):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    user: UserOut


class AssignableUserOut(Schema):
    id: int
    display_name: str
    username: str
    role: str


class AuthProvidersOut(Schema):
    providers: list[str]


class StatusOut(Schema):
    status: str


class LdapTestOut(Schema):
    success: bool
    message: str


class LdapResetOut(Schema):
    deleted: int


class AuditLogOut(Schema):
    id: int
    user_id: int | None = None
    username: str | None = None
    action: str
    resource_type: str | None = None
    resource_id: int | None = None
    details_json: JsonText = "{}"
    ip_address: str | None = None
    # v3 returned ``str(created_at)`` ("YYYY-MM-DD HH:MM:SS.ffffff").
    created_at: str | None = None
