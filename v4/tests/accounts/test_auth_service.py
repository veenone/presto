"""Port of v3 ``backend/tests/test_auth.py`` (104 tests).

v3 ``AuthService`` is split in v4: user CRUD, login and audit queries live in
``apps.accounts.services``, tokens in ``apps.accounts.tokens``, the role
hierarchy in ``apps.accounts.roles``, ``RequireRole`` is ``apps.api.auth.role``
and ``ensure_default_admin`` is in ``apps.core.bootstrap``.
"""

from __future__ import annotations

import secrets as _secrets
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import bcrypt
import jwt
import pytest
from django.db import IntegrityError
from django.test import RequestFactory
from ninja.errors import HttpError

from apps.accounts import services as svc
from apps.accounts import tokens
from apps.accounts.audit import log_action
from apps.accounts.models import AuditLog, User, UserSession
from apps.accounts.roles import ROLE_HIERARCHY, has_role
from apps.api.auth import role
from apps.core import bootstrap
from apps.core.models import Configuration

pytestmark = pytest.mark.django_db


# ---------------------------------------------------------------------------
# Fixtures and helpers
# ---------------------------------------------------------------------------


@pytest.fixture
def alice(db) -> User:
    return svc.create_user(
        username="alice", display_name="Alice Smith", password="correct-horse-battery",
        email="alice@example.com", role="ESTIMATOR",
    )


@pytest.fixture
def admin_user(db) -> User:
    return svc.create_user(
        username="sysadmin", display_name="System Admin", password="admin-password-123", role="ADMIN",
    )


@pytest.fixture
def viewer_user(db) -> User:
    return svc.create_user(
        username="viewer", display_name="Read Only", password="viewer-password", role="VIEWER",
    )


def _utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _make_expired_access_token(user: User) -> str:
    with patch("apps.accounts.tokens.ACCESS_TOKEN_EXPIRE_MINUTES", -120):
        return tokens.create_access_token(user)


def _make_expired_session(user: User) -> str:
    token = _secrets.token_urlsafe(64)
    UserSession.objects.create(user=user, refresh_token=token, expires_at=_utcnow() - timedelta(days=1))
    return token


def _deactivate(user: User) -> None:
    user.is_active = False
    user.save()


# ===========================================================================
# 1. create_user()
# ===========================================================================


class TestCreateUser:
    def test_returns_user_with_id(self, db):
        assert svc.create_user(username="bob", display_name="Bob Jones", password="secret").pk is not None

    def test_password_is_hashed(self, db):
        user = svc.create_user(username="charlie", display_name="Charlie", password="plaintext")
        assert user.password is not None
        assert user.password != "plaintext"
        assert bcrypt.checkpw(b"plaintext", user.password.encode())

    def test_default_role_is_viewer(self, db):
        assert svc.create_user(username="defaultrole", display_name="Default").role == "VIEWER"

    def test_custom_role_is_stored(self, db):
        assert svc.create_user(username="approver1", display_name="A", role="APPROVER").role == "APPROVER"

    def test_optional_email_stored(self, db):
        user = svc.create_user(username="withmail", display_name="With Mail", email="wm@example.com")
        assert user.email == "wm@example.com"

    def test_external_provider_no_password(self, db):
        user = svc.create_user(
            username="ldap_user", display_name="LDAP User", auth_provider="ldap",
            external_id="cn=ldap_user,dc=corp,dc=example",
        )
        user.refresh_from_db()
        assert user.password is None
        assert user.auth_provider == "ldap"

    def test_user_is_active_by_default(self, db):
        assert svc.create_user(username="active_default", display_name="Active").is_active is True

    def test_duplicate_username_raises(self, alice):
        with pytest.raises(IntegrityError):
            svc.create_user(username="alice", display_name="Another Alice")


# ===========================================================================
# 2. login()
# ===========================================================================


class TestLogin:
    def test_correct_credentials_return_tuple(self, alice):
        result = svc.login("alice", "correct-horse-battery")
        assert result is not None
        assert result[0].username == "alice"

    def test_returns_non_empty_tokens(self, alice):
        _, access_token, refresh_token = svc.login("alice", "correct-horse-battery")
        assert len(access_token) > 20
        assert len(refresh_token) > 20

    def test_wrong_password_returns_none(self, alice):
        assert svc.login("alice", "wrong-password") is None

    def test_unknown_username_returns_none(self, db):
        assert svc.login("nobody", "whatever") is None

    def test_inactive_user_returns_none(self, db):
        _deactivate(svc.create_user(username="inactive", display_name="Inactive", password="pw"))
        assert svc.login("inactive", "pw") is None

    def test_login_updates_last_login_at(self, alice):
        before = _utcnow()
        svc.login("alice", "correct-horse-battery")
        alice.refresh_from_db()
        assert alice.last_login is not None
        assert alice.last_login >= before - timedelta(seconds=5)

    def test_login_creates_session_record(self, alice):
        _, _, refresh_token = svc.login("alice", "correct-horse-battery")
        record = UserSession.objects.filter(refresh_token=refresh_token).first()
        assert record is not None
        assert record.user_id == alice.pk

    def test_login_writes_audit_entry(self, alice):
        svc.login("alice", "correct-horse-battery")
        assert AuditLog.objects.filter(user=alice, action="LOGIN").exists()

    def test_user_without_password_hash_returns_none(self, db):
        svc.create_user(username="oidc_only", display_name="OIDC Only", auth_provider="oidc")
        assert svc.login("oidc_only", "any-password") is None


# ===========================================================================
# 3. validate_access_token() -> tokens.decode_access_token()
# ===========================================================================


class TestValidateAccessToken:
    def test_valid_token_returns_payload(self, alice):
        _, access_token, _ = svc.login("alice", "correct-horse-battery")
        payload = tokens.decode_access_token(access_token)
        assert payload is not None
        assert payload["username"] == "alice"

    def test_payload_contains_expected_claims(self, alice):
        _, access_token, _ = svc.login("alice", "correct-horse-battery")
        payload = tokens.decode_access_token(access_token)
        assert payload["type"] == "access"
        assert str(payload["sub"]) == str(alice.pk)
        assert payload["role"] == "ESTIMATOR"

    def test_expired_token_returns_none(self, alice):
        assert tokens.decode_access_token(_make_expired_access_token(alice)) is None

    def test_garbage_string_returns_none(self, db):
        assert tokens.decode_access_token("not.a.jwt") is None

    def test_empty_string_returns_none(self, db):
        assert tokens.decode_access_token("") is None

    def test_wrong_type_claim_returns_none(self, alice):
        secret = tokens.jwt_secret()
        payload = jwt.decode(tokens.create_access_token(alice), secret, algorithms=["HS256"])
        payload["type"] = "refresh"
        bad_token = jwt.encode(payload, secret, algorithm="HS256")
        assert tokens.decode_access_token(bad_token) is None

    def test_token_signed_with_wrong_secret_returns_none(self, alice):
        tokens.jwt_secret()  # make sure the real secret exists
        with patch("apps.accounts.tokens.jwt_secret", return_value="completely-wrong-secret-" + "x" * 16):
            bad_token = tokens.create_access_token(alice)
        assert tokens.decode_access_token(bad_token) is None


# ===========================================================================
# 4. refresh()
# ===========================================================================


class TestRefreshToken:
    def test_valid_refresh_returns_new_token_pair(self, alice):
        _, _, old_refresh = svc.login("alice", "correct-horse-battery")
        result = svc.refresh(old_refresh)
        assert result is not None
        user, new_access, new_refresh = result
        assert user.username == "alice"
        assert isinstance(new_access, str) and len(new_access) > 0
        assert new_refresh != old_refresh

    def test_old_refresh_token_is_invalidated(self, alice):
        _, _, old_refresh = svc.login("alice", "correct-horse-battery")
        svc.refresh(old_refresh)
        assert not UserSession.objects.filter(refresh_token=old_refresh).exists()

    def test_new_session_record_exists(self, alice):
        _, _, old_refresh = svc.login("alice", "correct-horse-battery")
        _, _, new_refresh = svc.refresh(old_refresh)
        assert UserSession.objects.filter(refresh_token=new_refresh).exists()

    def test_unknown_refresh_token_returns_none(self, db):
        assert svc.refresh("token-that-does-not-exist") is None

    def test_expired_refresh_token_returns_none(self, alice):
        assert svc.refresh(_make_expired_session(alice)) is None

    def test_expired_session_record_is_deleted(self, alice):
        expired_token = _make_expired_session(alice)
        svc.refresh(expired_token)
        assert not UserSession.objects.filter(refresh_token=expired_token).exists()

    def test_inactive_user_refresh_returns_none(self, db):
        user = svc.create_user(username="will_deactivate", display_name="D", password="pw")
        _, _, refresh_token = svc.login("will_deactivate", "pw")
        _deactivate(user)
        assert svc.refresh(refresh_token) is None


# ===========================================================================
# 5. logout()
# ===========================================================================


class TestLogout:
    def test_logout_returns_true_when_session_exists(self, alice):
        _, _, refresh_token = svc.login("alice", "correct-horse-battery")
        assert svc.logout(refresh_token) is True

    def test_logout_deletes_session_record(self, alice):
        _, _, refresh_token = svc.login("alice", "correct-horse-battery")
        svc.logout(refresh_token)
        assert not UserSession.objects.filter(refresh_token=refresh_token).exists()

    def test_logout_with_unknown_token_returns_false(self, db):
        assert svc.logout("nonexistent-token") is False

    def test_logout_is_idempotent_second_call_returns_false(self, alice):
        _, _, refresh_token = svc.login("alice", "correct-horse-battery")
        svc.logout(refresh_token)
        assert svc.logout(refresh_token) is False

    def test_logout_does_not_affect_other_sessions(self, alice):
        _, _, refresh_a = svc.login("alice", "correct-horse-battery")
        _, _, refresh_b = svc.login("alice", "correct-horse-battery")
        svc.logout(refresh_a)
        assert UserSession.objects.filter(refresh_token=refresh_b).exists()


# ===========================================================================
# 6. change_password()
# ===========================================================================


class TestChangePassword:
    def test_correct_current_password_returns_true(self, alice):
        assert svc.change_password(alice.pk, "correct-horse-battery", "new-secure-pass") is True

    def test_wrong_current_password_returns_false(self, alice):
        assert svc.change_password(alice.pk, "wrong-old-password", "new-secure-pass") is False

    def test_old_password_no_longer_valid_after_change(self, alice):
        svc.change_password(alice.pk, "correct-horse-battery", "brand-new-password")
        assert svc.login("alice", "correct-horse-battery") is None

    def test_new_password_works_after_change(self, alice):
        svc.change_password(alice.pk, "correct-horse-battery", "brand-new-password")
        assert svc.login("alice", "brand-new-password") is not None

    def test_nonexistent_user_returns_false(self, db):
        assert svc.change_password(99999, "any", "other") is False

    def test_user_without_local_password_returns_false(self, db):
        user = svc.create_user(username="ldap_pw_test", display_name="LDAP", auth_provider="ldap")
        assert svc.change_password(user.pk, "", "new-pass") is False


# ===========================================================================
# 7. log_action()
# ===========================================================================


class TestLogAction:
    def test_creates_audit_log_entry(self, alice):
        log_action(alice.pk, "CUSTOM_ACTION")
        assert AuditLog.objects.filter(action="CUSTOM_ACTION").count() == 1

    def test_entry_has_correct_user_id(self, alice):
        log_action(alice.pk, "CHECK_USER_ID")
        assert AuditLog.objects.get(action="CHECK_USER_ID").user_id == alice.pk

    def test_resource_fields_are_stored(self, alice):
        log_action(alice.pk, "CREATE_ESTIMATION", resource_type="estimation", resource_id=42)
        entry = AuditLog.objects.get(action="CREATE_ESTIMATION")
        assert entry.resource_type == "estimation"
        assert entry.resource_id == 42

    def test_details_dict_serialized_to_json(self, alice):
        details = {"foo": "bar", "count": 7}
        log_action(alice.pk, "WITH_DETAILS", details=details)
        entry = AuditLog.objects.get(action="WITH_DETAILS")
        entry.refresh_from_db()
        assert entry.details == details

    def test_ip_address_stored(self, alice):
        log_action(alice.pk, "IP_TEST", ip_address="192.168.1.100")
        assert AuditLog.objects.get(action="IP_TEST").ip_address == "192.168.1.100"

    def test_null_user_id_allowed_for_system_actions(self, db):
        log_action(None, "SYSTEM_STARTUP")
        assert AuditLog.objects.get(action="SYSTEM_STARTUP").user_id is None

    def test_no_details_defaults_to_empty_json_object(self, alice):
        from apps.api.schemas.accounts import AuditLogOut

        log_action(alice.pk, "NO_DETAILS_ACTION")
        entry = AuditLog.objects.get(action="NO_DETAILS_ACTION")
        assert entry.details == {}
        out = AuditLogOut.model_validate({"id": entry.pk, "action": entry.action, "details_json": entry.details})
        assert out.details_json == "{}"


# ===========================================================================
# 8. get_audit_log()
# ===========================================================================


class TestGetAuditLog:
    @pytest.fixture(autouse=True)
    def _seed_audit_entries(self, alice, viewer_user):
        log_action(alice.pk, "LOGIN", resource_type="session")
        log_action(alice.pk, "CREATE_ESTIMATION", resource_type="estimation", resource_id=1)
        log_action(alice.pk, "CREATE_ESTIMATION", resource_type="estimation", resource_id=2)
        log_action(viewer_user.pk, "LOGIN", resource_type="session")
        log_action(viewer_user.pk, "VIEW_ESTIMATION", resource_type="estimation")

    def test_returns_all_entries_by_default(self):
        assert len(svc.get_audit_log(limit=100)) >= 5

    def test_filter_by_user_id(self, alice):
        entries = svc.get_audit_log(user_id=alice.pk)
        assert all(e.user_id == alice.pk for e in entries)
        assert len(entries) == 3

    def test_filter_by_action(self):
        entries = svc.get_audit_log(action="CREATE_ESTIMATION")
        assert all(e.action == "CREATE_ESTIMATION" for e in entries)
        assert len(entries) == 2

    def test_filter_by_resource_type(self):
        entries = svc.get_audit_log(resource_type="session")
        assert entries and all(e.resource_type == "session" for e in entries)

    def test_combined_filters(self, alice):
        entries = svc.get_audit_log(user_id=alice.pk, resource_type="estimation")
        assert len(entries) == 2
        assert all(e.user_id == alice.pk for e in entries)

    def test_limit_is_respected(self):
        assert len(svc.get_audit_log(limit=2)) <= 2

    def test_offset_paginates_results(self):
        all_entries = svc.get_audit_log(limit=100)
        assert len(svc.get_audit_log(limit=100, offset=2)) == len(all_entries) - 2

    def test_results_ordered_newest_first(self):
        entries = svc.get_audit_log(limit=100)
        timestamps = [e.created_at for e in entries if e.created_at is not None]
        assert timestamps == sorted(timestamps, reverse=True)
        # Same-timestamp rows fall back to id, newest first.
        assert [e.pk for e in entries] == sorted((e.pk for e in entries), reverse=True)


# ===========================================================================
# 9. has_permission() -> roles.has_role()
# ===========================================================================


class TestHasPermission:
    def test_viewer_satisfies_viewer(self):
        assert has_role("VIEWER", "VIEWER") is True

    def test_viewer_blocked_by_estimator(self):
        assert has_role("VIEWER", "ESTIMATOR") is False

    def test_viewer_blocked_by_approver(self):
        assert has_role("VIEWER", "APPROVER") is False

    def test_viewer_blocked_by_admin(self):
        assert has_role("VIEWER", "ADMIN") is False

    def test_estimator_satisfies_viewer(self):
        assert has_role("ESTIMATOR", "VIEWER") is True

    def test_estimator_satisfies_estimator(self):
        assert has_role("ESTIMATOR", "ESTIMATOR") is True

    def test_estimator_blocked_by_approver(self):
        assert has_role("ESTIMATOR", "APPROVER") is False

    def test_estimator_blocked_by_admin(self):
        assert has_role("ESTIMATOR", "ADMIN") is False

    def test_approver_satisfies_estimator(self):
        assert has_role("APPROVER", "ESTIMATOR") is True

    def test_approver_satisfies_approver(self):
        assert has_role("APPROVER", "APPROVER") is True

    def test_approver_blocked_by_admin(self):
        assert has_role("APPROVER", "ADMIN") is False

    def test_admin_satisfies_viewer(self):
        assert has_role("ADMIN", "VIEWER") is True

    def test_admin_satisfies_estimator(self):
        assert has_role("ADMIN", "ESTIMATOR") is True

    def test_admin_satisfies_approver(self):
        assert has_role("ADMIN", "APPROVER") is True

    def test_admin_satisfies_admin(self):
        assert has_role("ADMIN", "ADMIN") is True

    def test_unknown_user_role_is_blocked(self):
        assert has_role("GHOST", "VIEWER") is False

    def test_unknown_required_role_blocks_all(self):
        assert has_role("ADMIN", "SUPER_ROOT") is False

    def test_role_hierarchy_values_are_monotonically_increasing(self):
        values = [ROLE_HIERARCHY[r] for r in ["VIEWER", "ESTIMATOR", "APPROVER", "ADMIN"]]
        assert values == sorted(values)
        assert len(set(values)) == len(values)


# ===========================================================================
# 10. RequireRole -> apps.api.auth.role() / JWTAuth
# ===========================================================================


class TestRequireRole:
    """Call ``JWTAuth.authenticate`` directly with a real user's token."""

    @staticmethod
    def _call(min_role: str, user: User) -> User:
        request = RequestFactory().get("/api/x")
        return role(min_role).authenticate(request, tokens.create_access_token(user))

    @staticmethod
    def _user(role_name: str) -> User:
        return svc.create_user(username=f"u_{role_name.lower()}", display_name=role_name, role=role_name)

    def test_admin_satisfies_estimator_requirement(self):
        admin = self._user("ADMIN")
        assert self._call("ESTIMATOR", admin) == admin

    def test_admin_satisfies_viewer_requirement(self):
        admin = self._user("ADMIN")
        assert self._call("VIEWER", admin) == admin

    def test_admin_satisfies_admin_requirement(self):
        admin = self._user("ADMIN")
        assert self._call("ADMIN", admin) == admin

    def test_viewer_blocked_by_estimator_requirement(self):
        with pytest.raises(HttpError) as exc_info:
            self._call("ESTIMATOR", self._user("VIEWER"))
        assert exc_info.value.status_code == 403

    def test_viewer_blocked_by_approver_requirement(self):
        with pytest.raises(HttpError) as exc_info:
            self._call("APPROVER", self._user("VIEWER"))
        assert exc_info.value.status_code == 403

    def test_estimator_blocked_by_admin_requirement(self):
        with pytest.raises(HttpError) as exc_info:
            self._call("ADMIN", self._user("ESTIMATOR"))
        assert exc_info.value.status_code == 403

    def test_estimator_satisfies_viewer_requirement(self):
        estimator = self._user("ESTIMATOR")
        assert self._call("VIEWER", estimator) == estimator

    def test_error_detail_mentions_required_role(self):
        with pytest.raises(HttpError) as exc_info:
            self._call("APPROVER", self._user("VIEWER"))
        assert "APPROVER" in str(exc_info.value)

    def test_minimum_role_attribute_stored(self):
        assert role("ESTIMATOR").min_role == "ESTIMATOR"


# ===========================================================================
# 11. ensure_default_admin() -> apps.core.bootstrap
# ===========================================================================


class TestEnsureDefaultAdmin:
    def test_creates_admin_when_table_is_empty(self, db):
        assert User.objects.count() == 0
        bootstrap.ensure_default_admin()
        assert User.objects.count() == 1

    def test_created_admin_has_admin_role(self, db):
        bootstrap.ensure_default_admin()
        assert User.objects.get(username="admin").role == "ADMIN"

    def test_created_admin_can_login(self, db):
        bootstrap.ensure_default_admin()
        assert svc.login("admin", "admin") is not None

    def test_does_not_create_admin_when_users_exist(self, alice):
        count_before = User.objects.count()
        assert count_before >= 1
        bootstrap.ensure_default_admin()
        assert User.objects.count() == count_before

    def test_calling_twice_does_not_duplicate_admin(self, db):
        bootstrap.ensure_default_admin()
        bootstrap.ensure_default_admin()
        assert User.objects.filter(username="admin").count() == 1


# ===========================================================================
# 12. JWT secret management -> tokens.jwt_secret()
# ===========================================================================


class TestJwtSecret:
    def test_secret_is_auto_generated_and_persisted(self, db):
        secret = tokens.jwt_secret()
        assert Configuration.objects.get(key="jwt_secret").value == secret

    def test_secret_is_stable_within_service_instance(self, db):
        assert tokens.jwt_secret() == tokens.jwt_secret()

    def test_new_service_instance_shares_same_secret(self, db):
        secret_a = tokens.jwt_secret()
        Configuration.objects.get(key="jwt_secret").refresh_from_db()
        assert tokens.jwt_secret() == secret_a

    def test_secret_has_adequate_length(self, db):
        assert len(tokens.jwt_secret()) >= 32


# ===========================================================================
# 13. Auxiliary user management
# ===========================================================================


class TestUserManagement:
    def test_get_user_by_id_returns_correct_user(self, alice):
        fetched = svc.get_user_by_id(alice.pk)
        assert fetched is not None and fetched.username == "alice"

    def test_get_user_by_id_returns_none_for_unknown(self, db):
        assert svc.get_user_by_id(99999) is None

    def test_get_user_by_username(self, alice):
        fetched = svc.get_user_by_username("alice")
        assert fetched is not None and fetched.pk == alice.pk

    def test_get_user_by_username_returns_none_for_unknown(self, db):
        assert svc.get_user_by_username("ghost") is None

    def test_list_users_returns_all(self, alice, admin_user, viewer_user):
        usernames = {u.username for u in svc.list_users()}
        assert {"alice", "sysadmin", "viewer"}.issubset(usernames)

    def test_list_users_active_only_excludes_inactive(self, db):
        _deactivate(svc.create_user(username="deactivated", display_name="D", password="pw"))
        active = svc.list_users(active_only=True)
        assert all(u.is_active for u in active)
        assert not any(u.username == "deactivated" for u in active)

    def test_update_user_display_name(self, alice):
        updated = svc.update_user(alice.pk, display_name="Alicia Smith")
        assert updated is not None and updated.display_name == "Alicia Smith"

    def test_update_user_password_via_keyword(self, alice):
        svc.update_user(alice.pk, password="updated-password")
        assert svc.login("alice", "updated-password") is not None
        assert svc.login("alice", "correct-horse-battery") is None

    def test_update_nonexistent_user_returns_none(self, db):
        assert svc.update_user(99999, display_name="Ghost") is None

    def test_delete_user_removes_record(self, db):
        user = svc.create_user(username="to_delete", display_name="Delete Me", password="pw")
        assert svc.delete_user(user.pk) is True
        assert svc.get_user_by_id(user.pk) is None

    def test_delete_nonexistent_user_returns_false(self, db):
        assert svc.delete_user(99999) is False
