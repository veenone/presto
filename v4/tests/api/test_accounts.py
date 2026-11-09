"""API tests for /auth/*, /users/* and /audit-log."""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest

from apps.accounts import tokens
from apps.accounts.audit import log_action
from apps.accounts.ldap_provider import LDAPConnectionError
from apps.accounts.models import AuditLog, User, UserSession
from apps.core import config
from apps.teams.models import TeamMember

pytestmark = pytest.mark.django_db

USER_KEYS = {
    "id", "username", "email", "display_name", "role", "auth_provider", "is_active",
    "team_member_id", "last_login_at", "created_at",
}


def _login(anon_api, username="admin", password="admin", headers=None, **extra):
    body = {"username": username, "password": password, **extra}
    return anon_api.post("/api/auth/login", json=body, headers=headers)


# -- /auth/login -------------------------------------------------------------------


class TestLogin:
    def test_local_login(self, anon_api, seeded):
        resp = _login(anon_api, headers={"X-Forwarded-For": "10.1.2.3, 10.0.0.1"})
        assert resp.status_code == 200
        body = resp.json()
        assert body["token_type"] == "bearer" and body["access_token"] and body["refresh_token"]
        assert set(body["user"]) == USER_KEYS
        assert body["user"]["username"] == "admin" and body["user"]["last_login_at"]
        entry = AuditLog.objects.get(action="LOGIN")
        assert entry.user == seeded and entry.ip_address == "10.1.2.3"

    def test_wrong_password(self, anon_api, seeded):
        resp = _login(anon_api, password="nope")
        assert resp.status_code == 401
        assert resp.json() == {"detail": "Invalid username or password"}
        entry = AuditLog.objects.get(action="LOGIN_FAILED")
        assert entry.user is None and entry.resource_type == "auth"
        assert entry.details == {
            "username": "admin", "auth_method": "auto", "reason": "Invalid username or password",
        }

    def test_missing_field_is_422(self, anon_api, seeded):
        assert anon_api.post("/api/auth/login", json={"username": "admin"}).status_code == 422

    def test_ldap_method_not_configured(self, anon_api, seeded):
        resp = _login(anon_api, auth_method="ldap")
        assert resp.status_code == 400
        assert resp.json()["detail"] == "LDAP authentication is not configured"

    def test_ldap_method_skips_local(self, anon_api, seeded):
        config.set_value("ldap_url", "ldap://ldap.test")
        with patch("apps.accounts.ldap_provider.LDAPProvider.authenticate", return_value=None) as auth:
            resp = _login(anon_api, auth_method="ldap")
        assert resp.status_code == 401
        auth.assert_called_once_with("admin", "admin")
        assert not AuditLog.objects.filter(action="LOGIN").exists()

    def test_local_method_skips_ldap(self, anon_api, seeded):
        config.set_value("ldap_url", "ldap://ldap.test")
        with patch("apps.accounts.ldap_provider.LDAPProvider.authenticate") as auth:
            resp = _login(anon_api, password="bad", auth_method="local")
        assert resp.status_code == 401 and not auth.called
        assert AuditLog.objects.get(action="LOGIN_FAILED").details["auth_method"] == "local"

    def test_ldap_fallback_success(self, anon_api, seeded):
        config.set_value("ldap_url", "ldap://ldap.test")
        ldap_user = User.objects.create_user("jdoe", None, auth_provider="ldap", role="ESTIMATOR")
        with patch("apps.accounts.ldap_provider.LDAPProvider.authenticate", return_value=ldap_user):
            resp = _login(anon_api, username="jdoe", password="adpw")
        assert resp.status_code == 200
        assert resp.json()["user"]["auth_provider"] == "ldap"
        assert AuditLog.objects.filter(action="LOGIN", user=ldap_user).exists()
        assert UserSession.objects.filter(user=ldap_user).count() == 1

    def test_ldap_unreachable_is_502(self, anon_api, seeded):
        config.set_value("ldap_url", "ldap://ldap.test")
        err = LDAPConnectionError("Cannot reach LDAP server: timed out")
        with patch("apps.accounts.ldap_provider.LDAPProvider.authenticate", side_effect=err):
            resp = _login(anon_api, username="jdoe", password="x")
        assert resp.status_code == 502
        assert resp.json()["detail"] == "LDAP server unreachable: Cannot reach LDAP server: timed out"
        entry = AuditLog.objects.get(action="LOGIN_FAILED")
        assert entry.details["auth_method"] == "ldap"
        assert entry.details["reason"] == "LDAP unreachable: Cannot reach LDAP server: timed out"

    def test_inactive_user_rejected(self, anon_api, make_user):
        make_user("ADMIN", username="gone", password="pw", is_active=False)
        assert _login(anon_api, "gone", "pw").status_code == 401


# -- /auth/providers, refresh, logout, me, change-password ------------------------------


class TestAuthMisc:
    def test_providers_local_only(self, anon_api, seeded):
        assert anon_api.get("/api/auth/providers").json() == {"providers": ["local"]}

    def test_providers_with_ldap_and_oidc(self, anon_api, seeded):
        config.set_value("ldap_url", "ldap://ldap.test")
        config.set_value("oidc_issuer", "https://idp.test")
        assert anon_api.get("/api/auth/providers").json() == {"providers": ["local", "ldap", "oidc"]}

    def test_refresh(self, anon_api, seeded):
        refresh = _login(anon_api).json()["refresh_token"]
        resp = anon_api.post("/api/auth/refresh", json={"refresh_token": refresh})
        assert resp.status_code == 200
        body = resp.json()
        assert body["refresh_token"] != refresh and body["user"]["username"] == "admin"
        again = anon_api.post("/api/auth/refresh", json={"refresh_token": refresh})
        assert again.status_code == 401
        assert again.json()["detail"] == "Invalid or expired refresh token"

    def test_logout(self, anon_api, seeded):
        refresh = _login(anon_api).json()["refresh_token"]
        resp = anon_api.post("/api/auth/logout", json={"refresh_token": refresh})
        assert resp.status_code == 200 and resp.json() == {"status": "ok"}
        assert not UserSession.objects.exists()
        # Unknown tokens are accepted silently, as in v3.
        assert anon_api.post("/api/auth/logout", json={"refresh_token": "x"}).json() == {"status": "ok"}

    def test_me(self, api, seeded):
        body = api.get("/api/auth/me").json()
        assert set(body) == USER_KEYS and body["id"] == seeded.pk and body["role"] == "ADMIN"

    def test_me_requires_auth(self, anon_api, seeded):
        resp = anon_api.get("/api/auth/me")
        assert resp.status_code == 401
        assert resp.json() == {"detail": "Missing or invalid Authorization header"}

    def test_me_bad_token(self, anon_api, seeded):
        resp = anon_api.get("/api/auth/me", headers={"Authorization": "Bearer junk"})
        assert resp.status_code == 401 and resp.json()["detail"] == "Invalid or expired token"

    def test_change_password(self, api_as, anon_api, make_user):
        user = make_user("VIEWER", username="v", password="old")
        resp = api_as(user).post(
            "/api/auth/change-password", json={"current_password": "old", "new_password": "new"},
        )
        assert resp.status_code == 200 and resp.json() == {"status": "ok"}
        assert _login(anon_api, "v", "new").status_code == 200

    def test_change_password_wrong_current(self, api_as, make_user):
        user = make_user("VIEWER", password="old")
        resp = api_as(user).post(
            "/api/auth/change-password", json={"current_password": "bad", "new_password": "new"},
        )
        assert resp.status_code == 400 and resp.json()["detail"] == "Current password is incorrect"


# -- /users ------------------------------------------------------------------------------


class TestUsers:
    def test_list(self, api, make_user):
        make_user("VIEWER", username="zed", is_active=False)
        names = [u["username"] for u in api.get("/api/users").json()]
        assert names == sorted(names) and "zed" in names
        active = [u["username"] for u in api.get("/api/users", params={"active_only": "true"}).json()]
        assert "zed" not in active

    def test_list_requires_admin(self, api_as, make_user):
        resp = api_as(make_user("APPROVER")).get("/api/users")
        assert resp.status_code == 403
        assert resp.json()["detail"] == "Insufficient permissions. Required role: ADMIN"

    def test_create(self, api, anon_api, seeded):
        member = TeamMember.objects.create(name="Bob")
        resp = api.post("/api/users", json={
            "username": "bob", "display_name": "Bob", "password": "pw", "email": "bob@example.com",
            "role": "ESTIMATOR", "team_member_id": member.pk,
        })
        assert resp.status_code == 201
        body = resp.json()
        assert body["username"] == "bob" and body["role"] == "ESTIMATOR"
        assert body["team_member_id"] == member.pk and body["auth_provider"] == "local"
        assert body["last_login_at"] is None and body["is_active"] is True
        log = AuditLog.objects.get(action="CREATE", resource_type="user")
        assert log.resource_id == body["id"] and log.user == seeded
        assert _login(anon_api, "bob", "pw").status_code == 200

    def test_create_external_without_password(self, api):
        resp = api.post("/api/users", json={"username": "ext", "display_name": "E", "auth_provider": "ldap"})
        assert resp.status_code == 201
        assert User.objects.get(username="ext").password is None

    def test_create_duplicate_username(self, api):
        resp = api.post("/api/users", json={"username": "admin", "display_name": "X"})
        assert resp.status_code == 400 and resp.json()["detail"] == "Username 'admin' already exists"

    def test_create_duplicate_email(self, api):
        resp = api.post("/api/users", json={"username": "x", "display_name": "X", "email": "admin@localhost"})
        assert resp.status_code == 400 and resp.json()["detail"] == "Email 'admin@localhost' already exists"

    def test_create_blank_email_stored_as_null(self, api):
        for name in ("a1", "a2"):
            resp = api.post("/api/users", json={"username": name, "display_name": name, "email": ""})
            assert resp.status_code == 201
        assert User.objects.get(username="a2").email is None

    def test_create_unknown_team_member(self, api):
        resp = api.post("/api/users", json={"username": "x", "display_name": "X", "team_member_id": 999})
        assert resp.status_code == 404 and resp.json()["detail"] == "Team member not found"

    def test_create_requires_admin(self, api_as, make_user):
        resp = api_as(make_user("ESTIMATOR")).post("/api/users", json={"username": "x", "display_name": "X"})
        assert resp.status_code == 403

    def test_assignable(self, api_as, make_user):
        viewer = make_user("VIEWER", username="vv")
        make_user("VIEWER", username="off", is_active=False)
        resp = api_as(viewer).get("/api/users/assignable")
        assert resp.status_code == 200
        rows = resp.json()
        assert {"vv"} <= {r["username"] for r in rows} and "off" not in {r["username"] for r in rows}
        assert set(rows[0]) == {"id", "display_name", "username", "role"}

    def test_assignable_requires_auth(self, anon_api, seeded):
        assert anon_api.get("/api/users/assignable").status_code == 401

    def test_get(self, api, seeded):
        resp = api.get(f"/api/users/{seeded.pk}")
        assert resp.status_code == 200 and resp.json()["username"] == "admin"

    def test_get_missing(self, api):
        resp = api.get("/api/users/9999")
        assert resp.status_code == 404 and resp.json()["detail"] == "User not found"

    def test_get_requires_admin(self, api_as, make_user):
        user = make_user("APPROVER")
        assert api_as(user).get(f"/api/users/{user.pk}").status_code == 403

    def test_update(self, api, make_user):
        user = make_user("VIEWER", email="old@example.com")
        resp = api.put(f"/api/users/{user.pk}", json={"role": "APPROVER", "is_active": False})
        assert resp.status_code == 200
        body = resp.json()
        assert body["role"] == "APPROVER" and body["is_active"] is False
        assert body["email"] == "old@example.com"  # unset fields untouched
        assert AuditLog.objects.filter(action="UPDATE", resource_type="user", resource_id=user.pk).exists()

    def test_update_null_display_name_ignored(self, api, make_user):
        user = make_user("VIEWER", display_name="Keep")
        resp = api.put(f"/api/users/{user.pk}", json={"display_name": None, "email": None})
        assert resp.status_code == 200 and resp.json()["display_name"] == "Keep"

    def test_update_missing(self, api):
        resp = api.put("/api/users/9999", json={"role": "ADMIN"})
        assert resp.status_code == 404 and resp.json()["detail"] == "User not found"
        assert not AuditLog.objects.filter(action="UPDATE").exists()

    def test_update_email_conflict(self, api, make_user):
        user = make_user("VIEWER")
        resp = api.put(f"/api/users/{user.pk}", json={"email": "admin@localhost"})
        assert resp.status_code == 400

    def test_update_requires_admin(self, api_as, make_user):
        user = make_user("APPROVER")
        assert api_as(user).put(f"/api/users/{user.pk}", json={"role": "ADMIN"}).status_code == 403

    def test_delete(self, api, make_user):
        user = make_user("VIEWER")
        log_action(user, "LOGIN")
        resp = api.delete(f"/api/users/{user.pk}")
        assert resp.status_code == 204
        assert not User.objects.filter(pk=user.pk).exists()
        assert AuditLog.objects.get(action="LOGIN").user_id is None
        assert AuditLog.objects.filter(action="DELETE", resource_type="user", resource_id=user.pk).exists()

    def test_delete_self(self, api, seeded):
        resp = api.delete(f"/api/users/{seeded.pk}")
        assert resp.status_code == 400 and resp.json()["detail"] == "Cannot delete your own account"

    def test_delete_missing(self, api):
        resp = api.delete("/api/users/9999")
        assert resp.status_code == 404 and resp.json()["detail"] == "User not found"

    def test_delete_requires_admin(self, api_as, make_user):
        user = make_user("APPROVER")
        assert api_as(user).delete(f"/api/users/{user.pk}").status_code == 403


# -- /auth/ldap/* ---------------------------------------------------------------------------


class TestLdapEndpoints:
    def test_sync_not_configured(self, api):
        resp = api.post("/api/auth/ldap/sync")
        assert resp.status_code == 400 and resp.json()["detail"] == "LDAP is not configured"

    def test_sync(self, api, seeded):
        config.set_value("ldap_url", "ldap://ldap.test")
        result = {"synced": 1, "created": 1, "updated": 0, "skipped": 0, "deactivated": 0, "errors": []}
        with patch("apps.accounts.ldap_provider.LDAPProvider.sync_users", return_value=result):
            resp = api.post("/api/auth/ldap/sync")
        assert resp.status_code == 200 and resp.json() == result
        assert AuditLog.objects.get(action="LDAP_SYNC").details == result

    def test_sync_requires_admin(self, api_as, make_user):
        assert api_as(make_user("APPROVER")).post("/api/auth/ldap/sync").status_code == 403

    def test_test_not_configured(self, api):
        resp = api.post("/api/auth/ldap/test")
        assert resp.status_code == 200
        assert resp.json() == {
            "success": False, "message": "LDAP is not configured. Set ldap_url in Settings and save.",
        }

    def test_test_success(self, api):
        with patch("apps.accounts.ldap_provider.LDAPProvider.test_connection", return_value=(True, "Bound")):
            assert api.post("/api/auth/ldap/test").json() == {"success": True, "message": "Bound"}

    def test_test_requires_admin(self, api_as, make_user):
        assert api_as(make_user("ESTIMATOR")).post("/api/auth/ldap/test").status_code == 403

    def test_reset_synced_users(self, api, api_as, make_user, seeded):
        make_user("VIEWER", username="l1", auth_provider="ldap")
        make_user("VIEWER", username="l2", auth_provider="ldap")
        ldap_admin = make_user("ADMIN", username="ladmin", auth_provider="ldap")
        local = make_user("VIEWER", username="loc")
        resp = api_as(ldap_admin).post("/api/auth/ldap/reset-synced-users")
        assert resp.status_code == 200 and resp.json() == {"deleted": 2}
        assert set(User.objects.values_list("username", flat=True)) == {"admin", "ladmin", local.username}
        assert AuditLog.objects.get(action="LDAP_RESET_USERS").details == {"deleted": 2}

    def test_reset_requires_admin(self, api_as, make_user):
        assert api_as(make_user("APPROVER")).post("/api/auth/ldap/reset-synced-users").status_code == 403


# -- /audit-log ---------------------------------------------------------------------------------


class TestAuditLog:
    def test_list(self, api, seeded):
        log_action(seeded, "CREATE", "feature", 5, {"name": "X"}, "1.2.3.4")
        log_action(None, "LOGIN_FAILED", "auth")
        rows = api.get("/api/audit-log").json()
        assert [r["action"] for r in rows] == ["LOGIN_FAILED", "CREATE"]
        first, second = rows
        assert first["username"] is None and first["details_json"] == "{}"
        assert second == {
            "id": second["id"], "user_id": seeded.pk, "username": "admin", "action": "CREATE",
            "resource_type": "feature", "resource_id": 5, "details_json": json.dumps({"name": "X"}),
            "ip_address": "1.2.3.4", "created_at": second["created_at"],
        }
        # v3 returned str(datetime): "YYYY-MM-DD HH:MM:SS[.ffffff]"
        assert " " in second["created_at"] and "T" not in second["created_at"]

    def test_filters_and_paging(self, api, seeded):
        for i in range(5):
            log_action(seeded, "UPDATE" if i % 2 else "CREATE", "estimation" if i < 3 else "feature", i)
        assert len(api.get("/api/audit-log", params={"action": "UPDATE"}).json()) == 2
        assert len(api.get("/api/audit-log", params={"resource_type": "feature"}).json()) == 2
        page = api.get("/api/audit-log", params={"limit": 2, "offset": 1}).json()
        assert [r["resource_id"] for r in page] == [3, 2]

    def test_approver_allowed(self, api_as, make_user):
        assert api_as(make_user("APPROVER")).get("/api/audit-log").status_code == 200

    def test_estimator_forbidden(self, api_as, make_user):
        resp = api_as(make_user("ESTIMATOR")).get("/api/audit-log")
        assert resp.status_code == 403
        assert resp.json()["detail"] == "Insufficient permissions. Required role: APPROVER"


def test_token_for_deleted_user_rejected(anon_api, make_user):
    user = make_user("ADMIN")
    token = tokens.create_access_token(user)
    user.delete()
    resp = anon_api.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 401 and resp.json()["detail"] == "User not found or inactive"
