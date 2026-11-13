from unittest import mock

import pytest
from django.test import Client

from apps.accounts.models import AuditLog
from apps.core import config

pytestmark = pytest.mark.django_db


def test_anonymous_page_redirects_to_login(client, seeded):
    resp = client.get("/features")
    assert resp.status_code == 302 and resp["Location"] == "/login?next=/features"


def test_anonymous_htmx_request_gets_hx_redirect(client, seeded):
    resp = client.get("/notifications/badge", headers={"HX-Request": "true",
                                                        "HX-Current-URL": "http://testserver/duts"})
    assert resp.status_code == 401 and resp["HX-Redirect"] == "/login?next=/duts"


def test_api_is_not_redirected(client, seeded):
    resp = client.get("/api/features")
    assert resp.status_code == 401 and resp.json()["detail"] == "Missing or invalid Authorization header"


def test_login_page_renders_without_ldap_toggle(client, seeded):
    resp = client.get("/login")
    body = resp.content.decode()
    assert resp.status_code == 200 and "Welcome back" in body
    assert "ed-login-method" not in body and "single sign-on" not in body


def test_login_page_offers_ldap_and_sso_when_configured(client, seeded):
    config.set_value("ldap_url", "ldap://dc.example.com")
    config.set_value("oidc_issuer", "https://id.example.com")
    body = client.get("/login").content.decode()
    assert "ed-login-method" in body and "Sign in with single sign-on" in body


def test_successful_login_starts_session_and_audits(client, seeded):
    resp = client.post("/login", {"username": "admin", "password": "admin", "next": "/duts"})
    assert resp.status_code == 302 and resp["Location"] == "/duts"
    assert client.get("/").status_code == 200
    assert AuditLog.objects.filter(action="LOGIN", user=seeded).exists()
    seeded.refresh_from_db()
    assert seeded.last_login is not None


def test_failed_login_shows_v3_message_and_audits(client, seeded):
    resp = client.post("/login", {"username": "admin", "password": "wrong"})
    assert resp.status_code == 401 and b"Invalid username or password" in resp.content
    assert AuditLog.objects.filter(action="LOGIN_FAILED").exists()
    assert client.get("/").status_code == 302


def test_inactive_user_cannot_log_in(client, make_user):
    make_user("ADMIN", username="gone", password="pw", is_active=False)
    assert client.post("/login", {"username": "gone", "password": "pw"}).status_code == 401


@pytest.mark.parametrize("target", ["https://evil.example/", "//evil.example/x"])
def test_login_ignores_offsite_next(client, seeded, target):
    resp = client.post("/login", {"username": "admin", "password": "admin", "next": target})
    assert resp["Location"] == "/"


def test_ldap_login_path(client, seeded, make_user):
    config.set_value("ldap_url", "ldap://dc.example.com")
    ldap_user = make_user("ESTIMATOR", username="jdoe", password=None, auth_provider="ldap")
    with mock.patch("apps.accounts.ldap_provider.LDAPProvider.authenticate", return_value=ldap_user):
        resp = client.post("/login", {"username": "jdoe", "password": "pw", "auth_method": "ldap"})
    assert resp.status_code == 302 and client.get("/").context["user"] == ldap_user


def test_login_requires_csrf_token(seeded):
    strict = Client(enforce_csrf_checks=True)
    resp = strict.post("/login", {"username": "admin", "password": "admin"})
    assert resp.status_code == 403 and b"Reload the page" in resp.content


def test_logout_is_post_only(admin_client):
    assert admin_client.get("/logout").status_code == 405
    resp = admin_client.post("/logout")
    assert resp.status_code == 302 and resp["Location"] == "/login"
    assert AuditLog.objects.filter(action="LOGOUT").exists()
    assert admin_client.get("/").status_code == 302


def test_signed_in_user_visiting_login_is_redirected(admin_client):
    assert admin_client.get("/login")["Location"] == "/"


class TestOidc:
    def test_start_redirects_to_provider_and_stores_state(self, client, seeded):
        with mock.patch("apps.accounts.oidc_provider.OIDCProvider.is_configured", new=True), \
             mock.patch("apps.accounts.oidc_provider.OIDCProvider.get_authorization_url",
                        side_effect=lambda state: f"https://id.example.com/auth?state={state}"):
            resp = client.get("/auth/oidc/start?next=/duts")
        state = client.session["oidc_state"]
        assert resp.status_code == 302 and resp["Location"].endswith(state)

    def test_start_when_not_configured(self, client, seeded):
        resp = client.get("/auth/oidc/start")
        assert resp["Location"] == "/login"

    def test_callback_rejects_wrong_state(self, client, seeded):
        session = client.session
        session["oidc_state"] = "expected"
        session.save()
        assert client.get("/auth/oidc/callback?state=other&code=x").status_code == 400

    def test_callback_signs_in(self, client, seeded, make_user):
        user = make_user("VIEWER", username="sso", password=None, auth_provider="oidc")
        session = client.session
        session["oidc_state"], session["oidc_next"] = "s1", "/profiles"
        session.save()
        with mock.patch("apps.accounts.oidc_provider.OIDCProvider.handle_callback", return_value=user):
            resp = client.get("/auth/oidc/callback?state=s1&code=abc")
        assert resp["Location"] == "/profiles"
        assert AuditLog.objects.filter(action="LOGIN", user=user).exists()

    def test_callback_failure_returns_to_login(self, client, seeded):
        session = client.session
        session["oidc_state"] = "s1"
        session.save()
        with mock.patch("apps.accounts.oidc_provider.OIDCProvider.handle_callback", return_value=None):
            resp = client.get("/auth/oidc/callback?state=s1&code=abc")
        assert resp["Location"] == "/login" and AuditLog.objects.filter(action="LOGIN_FAILED").exists()
