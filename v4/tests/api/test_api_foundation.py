import pytest
from ninja import Router

from apps.accounts import tokens
from apps.accounts.audit import log_action
from apps.accounts.models import AuditLog, UserSession
from apps.accounts.roles import has_role

pytestmark = pytest.mark.django_db


def test_role_hierarchy():
    assert has_role("ADMIN", "APPROVER") and has_role("ESTIMATOR", "ESTIMATOR")
    assert not has_role("VIEWER", "ESTIMATOR") and not has_role(None, "VIEWER")
    assert not has_role("ADMIN", "UNKNOWN")


def test_access_token_roundtrip_and_claims(admin_user):
    payload = tokens.decode_access_token(tokens.create_access_token(admin_user))
    assert payload["sub"] == str(admin_user.pk) and payload["role"] == "ADMIN" and payload["type"] == "access"
    assert tokens.decode_access_token("garbage") is None


def test_jwt_secret_generated_once(db):
    first = tokens.jwt_secret()
    assert len(first) == 64 and tokens.jwt_secret() == first


def test_refresh_rotation_and_revocation(admin_user):
    _, refresh = tokens.issue_token_pair(admin_user)
    user, _, new_refresh = tokens.rotate_refresh_token(refresh)
    assert user == admin_user and new_refresh != refresh
    assert tokens.rotate_refresh_token(refresh) is None  # old token is gone
    assert tokens.revoke_refresh_token(new_refresh) and not UserSession.objects.exists()


def test_expired_refresh_token_is_rejected(admin_user):
    _, refresh = tokens.issue_token_pair(admin_user)
    UserSession.objects.update(expires_at="2000-01-01 00:00:00")
    assert tokens.rotate_refresh_token(refresh) is None
    assert not UserSession.objects.exists()


def test_log_action(admin_user):
    log_action(admin_user, "LOGIN", ip_address="10.0.0.1")
    log_action(None, "LOGIN_FAILED", "auth", details={"username": "x"})
    assert AuditLog.objects.filter(user=admin_user, action="LOGIN", ip_address="10.0.0.1").exists()
    assert AuditLog.objects.get(action="LOGIN_FAILED").details == {"username": "x"}


def test_public_endpoints(anon_api):
    assert anon_api.get("/api/healthcheck").json()["status"] == "ok"
    assert anon_api.get("/api/host-config").json()["title"] == "Test Effort Estimation Tool"


class TestAuthGuards:
    """JWTAuth as ninja calls it: header present -> authenticate(), else None (401)."""

    @staticmethod
    def call(auth, token=None):
        from django.test import RequestFactory
        headers = {"HTTP_AUTHORIZATION": f"Bearer {token}"} if token else {}
        request = RequestFactory().get("/api/x", **headers)
        return auth(request), request

    def test_missing_header_returns_none(self, db):
        from apps.api.auth import authenticated
        assert self.call(authenticated)[0] is None  # ninja then answers 401

    def test_unauthenticated_handler_message(self):
        from ninja.errors import AuthenticationError

        from apps.api.api import _unauthenticated
        resp = _unauthenticated(None, AuthenticationError())
        assert resp.status_code == 401 and resp["WWW-Authenticate"] == "Bearer"
        assert b"Missing or invalid Authorization header" in resp.content

    def test_bad_token(self, db):
        from ninja.errors import HttpError

        from apps.api.auth import authenticated
        with pytest.raises(HttpError) as exc:
            self.call(authenticated, "nope")
        assert exc.value.status_code == 401 and str(exc.value) == "Invalid or expired token"

    def test_inactive_user(self, make_user):
        from ninja.errors import HttpError

        from apps.api.auth import authenticated
        user = make_user("ADMIN")
        token = tokens.create_access_token(user)
        user.is_active = False
        user.save()
        with pytest.raises(HttpError) as exc:
            self.call(authenticated, token)
        assert exc.value.status_code == 401

    def test_role_guard(self, admin_user, make_user):
        from ninja.errors import HttpError

        from apps.api.auth import authenticated, optional_user, role
        user, request = self.call(authenticated, tokens.create_access_token(admin_user))
        assert user == admin_user and request.user == admin_user
        assert self.call(role("ADMIN"), tokens.create_access_token(admin_user))[0] == admin_user
        with pytest.raises(HttpError) as exc:
            self.call(role("ADMIN"), tokens.create_access_token(make_user("APPROVER")))
        assert exc.value.status_code == 403
        assert str(exc.value) == "Insufficient permissions. Required role: ADMIN"
        assert optional_user(request) == admin_user
