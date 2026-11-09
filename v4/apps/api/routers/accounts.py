"""API routes: /auth/*, /users/*, /audit-log (v3 routes.py "Authentication",
"Users", "LDAP Sync" and "Audit Log" sections)."""

from __future__ import annotations

from ninja import Router
from ninja.errors import HttpError

from apps.accounts import services
from apps.accounts.audit import log_action
from apps.accounts.ldap_provider import LDAPProvider
from apps.accounts.models import User
from apps.api.auth import authenticated, role
from apps.api.common import client_ip, get_or_404
from apps.api.schemas.accounts import (
    AssignableUserOut,
    AuditLogOut,
    AuthProvidersOut,
    LdapResetOut,
    LdapTestOut,
    LoginRequest,
    PasswordChange,
    RefreshRequest,
    StatusOut,
    TokenResponse,
    UserCreate,
    UserOut,
    UserUpdate,
)
from apps.teams.models import TeamMember

router = Router(auth=authenticated, tags=["accounts"])


def _check_team_member(team_member_id: int | None) -> None:
    if team_member_id is not None and not TeamMember.objects.filter(pk=team_member_id).exists():
        raise HttpError(404, "Team member not found")


# -- authentication ------------------------------------------------------------

@router.post("/auth/login", auth=None, response=TokenResponse)
def login(request, data: LoginRequest):
    """Authenticate and get JWT tokens."""
    try:
        user, access, refresh = services.authenticate(
            data.username, data.password, data.auth_method, ip_address=client_ip(request),
        )
    except services.AuthError as exc:
        raise HttpError(exc.status_code, exc.detail) from exc
    return {"access_token": access, "refresh_token": refresh, "user": user}


@router.get("/auth/providers", auth=None, response=AuthProvidersOut)
def get_auth_providers(request):
    """Return which authentication providers are available."""
    return {"providers": services.get_auth_providers()}


@router.post("/auth/refresh", auth=None, response=TokenResponse)
def refresh_token(request, data: RefreshRequest):
    result = services.refresh(data.refresh_token)
    if result is None:
        raise HttpError(401, "Invalid or expired refresh token")
    user, access, new_refresh = result
    return {"access_token": access, "refresh_token": new_refresh, "user": user}


@router.post("/auth/logout", auth=None, response=StatusOut)
def logout(request, data: RefreshRequest):
    services.logout(data.refresh_token)
    return {"status": "ok"}


@router.get("/auth/me", response=UserOut)
def get_current_user_info(request):
    return request.auth


@router.post("/auth/change-password", response=StatusOut)
def change_password(request, data: PasswordChange):
    if not services.change_password(request.auth.pk, data.current_password, data.new_password):
        raise HttpError(400, "Current password is incorrect")
    return {"status": "ok"}


# -- users (ADMIN) ---------------------------------------------------------------

@router.get("/users", auth=role("ADMIN"), response=list[UserOut])
def list_users(request, active_only: bool = False):
    return services.list_users(active_only=active_only)


@router.post("/users", auth=role("ADMIN"), response={201: UserOut})
def create_user(request, data: UserCreate):
    if services.get_user_by_username(data.username):
        raise HttpError(400, f"Username '{data.username}' already exists")
    if services.get_user_by_email(data.email):
        raise HttpError(400, f"Email '{data.email}' already exists")
    _check_team_member(data.team_member_id)
    new_user = services.create_user(
        username=data.username,
        display_name=data.display_name,
        password=data.password,
        email=data.email,
        role=data.role,
        auth_provider=data.auth_provider,
        team_member_id=data.team_member_id,
    )
    log_action(request.auth, "CREATE", "user", new_user.pk, ip_address=client_ip(request))
    return 201, new_user


@router.get("/users/assignable", response=list[AssignableUserOut])
def list_assignable_users(request):
    """All active users for assignment dropdowns (any authenticated user)."""
    return services.list_assignable_users()


@router.get("/users/{user_id}", auth=role("ADMIN"), response=UserOut)
def get_user(request, user_id: int):
    return get_or_404(User, user_id, "User not found")


@router.put("/users/{user_id}", auth=role("ADMIN"), response=UserOut)
def update_user(request, user_id: int, data: UserUpdate):
    changes = data.model_dump(exclude_unset=True)
    if services.get_user_by_id(user_id) is None:
        raise HttpError(404, "User not found")
    if "email" in changes:
        other = services.get_user_by_email(changes["email"])
        if other is not None and other.pk != user_id:
            raise HttpError(400, f"Email '{changes['email']}' already exists")
    _check_team_member(changes.get("team_member_id"))
    updated = services.update_user(user_id, **changes)
    log_action(request.auth, "UPDATE", "user", user_id, ip_address=client_ip(request))
    return updated


@router.delete("/users/{user_id}", auth=role("ADMIN"), response={204: None})
def delete_user(request, user_id: int):
    if user_id == request.auth.pk:
        raise HttpError(400, "Cannot delete your own account")
    if not services.delete_user(user_id):
        raise HttpError(404, "User not found")
    log_action(request.auth, "DELETE", "user", user_id, ip_address=client_ip(request))
    return 204, None


# -- LDAP (ADMIN) ------------------------------------------------------------------

@router.post("/auth/ldap/sync", auth=role("ADMIN"))
def ldap_sync(request):
    provider = LDAPProvider()
    if not provider.is_configured:
        raise HttpError(400, "LDAP is not configured")
    result = provider.sync_users()
    log_action(request.auth, "LDAP_SYNC", details=result, ip_address=client_ip(request))
    return result


@router.post("/auth/ldap/test", auth=role("ADMIN"), response=LdapTestOut)
def ldap_test(request):
    """Bind with the service account using the stored ldap_* configuration."""
    ok, message = LDAPProvider().test_connection()
    return {"success": ok, "message": message}


@router.post("/auth/ldap/reset-synced-users", auth=role("ADMIN"), response=LdapResetOut)
def ldap_reset_synced_users(request):
    """Delete all LDAP users (not local ones, not the caller) so a fresh sync can
    repopulate them."""
    deleted = services.reset_ldap_synced_users(keep_user=request.auth)
    log_action(
        request.auth, "LDAP_RESET_USERS", details={"deleted": deleted}, ip_address=client_ip(request),
    )
    return {"deleted": deleted}


# -- audit log (APPROVER+) ------------------------------------------------------------

@router.get("/audit-log", auth=role("APPROVER"), response=list[AuditLogOut])
def list_audit_log(
    request,
    limit: int = 100,
    offset: int = 0,
    action: str | None = None,
    resource_type: str | None = None,
):
    logs = services.get_audit_log(limit=limit, offset=offset, action=action, resource_type=resource_type)
    return [
        {
            "id": log.pk,
            "user_id": log.user_id,
            "username": log.user.username if log.user else None,
            "action": log.action,
            "resource_type": log.resource_type,
            "resource_id": log.resource_id,
            "details_json": log.details,
            "ip_address": log.ip_address,
            "created_at": str(log.created_at) if log.created_at else None,
        }
        for log in logs
    ]
