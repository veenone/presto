"""LDAP and OIDC providers with ldap3 / authlib / requests mocked (no network)."""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from apps.accounts.ldap_provider import LDAPConnectionError, LDAPProvider
from apps.accounts.models import User
from apps.accounts.oidc_provider import OIDCProvider
from apps.core import config

pytestmark = pytest.mark.django_db

ADMIN_GROUP = "CN=EstimationAdmins,OU=Groups,DC=example,DC=com"
APPROVER_GROUP = "CN=EstimationApprovers,OU=Groups,DC=example,DC=com"


def _entry(sam="jdoe", cn="John Doe", mail="jdoe@example.com", groups=(), uac="512"):
    return SimpleNamespace(
        entry_dn=f"CN={cn},OU=Users,DC=example,DC=com", sAMAccountName=sam, cn=cn, mail=mail,
        memberOf=list(groups), userAccountControl=uac,
    )


@pytest.fixture
def ldap_config(db):
    for key, value in {
        "ldap_url": "ldap://ldap.test",
        "ldap_bind_dn": "CN=svc,DC=example,DC=com",
        "ldap_bind_password": "svc-pw",
        "ldap_search_base": "OU=Users,DC=example,DC=com",
        "ldap_user_filter": "(sAMAccountName={username})",
        "ldap_group_mapping_json": json.dumps({"ADMIN": ADMIN_GROUP, "APPROVER": APPROVER_GROUP}),
    }.items():
        config.set_value(key, value)


class FakeLdap:
    """Patches ldap3.Server / ldap3.Connection. The first Connection (service
    bind) returns ``entries``; later ones (user binds) answer ``user_bind``."""

    def __init__(self, entries=(), user_bind=True, service_error=None):
        self.entries = list(entries)
        self.user_bind = user_bind
        self.service_error = service_error
        self.connections: list[dict] = []
        self.searches: list[tuple] = []

    def _connection(self, server, **kwargs):
        self.connections.append(kwargs)
        if kwargs.get("auto_bind"):
            if self.service_error:
                raise self.service_error
            conn = MagicMock()
            conn.entries = self.entries
            conn.search.side_effect = lambda *a, **kw: self.searches.append((a, kw))
            return conn
        conn = MagicMock()
        conn.bind.return_value = self.user_bind
        return conn

    def __enter__(self):
        self._patches = [
            patch("ldap3.Server", MagicMock()),
            patch("ldap3.Connection", side_effect=self._connection),
        ]
        for p in self._patches:
            p.start()
        return self

    def __exit__(self, *exc):
        for p in self._patches:
            p.stop()


# -- LDAP: configuration ---------------------------------------------------------


class TestLdapConfig:
    def test_not_configured_by_default(self, db):
        provider = LDAPProvider()
        assert not provider.is_configured
        assert provider.authenticate("x", "y") is None
        assert provider.sync_users()["created"] == 0

    def test_test_connection_not_configured(self, db):
        ok, message = LDAPProvider().test_connection()
        assert not ok and message == "LDAP is not configured. Set ldap_url in Settings and save."

    def test_test_connection_success(self, ldap_config):
        with FakeLdap():
            ok, message = LDAPProvider().test_connection()
        assert ok and message == "Bound to ldap://ldap.test as CN=svc,DC=example,DC=com."

    def test_test_connection_failure(self, ldap_config):
        with FakeLdap(service_error=RuntimeError("invalidCredentials")):
            ok, message = LDAPProvider().test_connection()
        assert not ok and message == "LDAP connection failed: invalidCredentials"


# -- LDAP: interactive login ------------------------------------------------------


class TestLdapAuthenticate:
    def test_creates_user_with_mapped_role(self, ldap_config):
        with FakeLdap([_entry(groups=[ADMIN_GROUP])]) as fake:
            user = LDAPProvider().authenticate("jdoe", "pw")
        assert user.username == "jdoe" and user.role == "ADMIN" and user.auth_provider == "ldap"
        assert user.email == "jdoe@example.com" and user.display_name == "John Doe"
        assert user.password is None and user.external_id.startswith("CN=John Doe")
        assert fake.searches[0][0] == ("OU=Users,DC=example,DC=com", "(sAMAccountName=jdoe)")

    def test_new_user_without_matching_group_is_viewer(self, ldap_config):
        with FakeLdap([_entry(groups=["CN=Other,DC=example,DC=com"])]):
            assert LDAPProvider().authenticate("jdoe", "pw").role == "VIEWER"

    def test_existing_role_kept_when_no_group_matches(self, ldap_config):
        """v3 3.3.0: an admin-assigned role is never reset to VIEWER."""
        User.objects.create_user("jdoe", None, role="APPROVER", auth_provider="ldap")
        with FakeLdap([_entry(groups=[])]):
            assert LDAPProvider().authenticate("jdoe", "pw").role == "APPROVER"

    def test_existing_role_overwritten_when_group_matches(self, ldap_config):
        User.objects.create_user("jdoe", None, role="VIEWER", is_active=False)
        with FakeLdap([_entry(groups=[APPROVER_GROUP.lower()])]):
            user = LDAPProvider().authenticate("jdoe", "pw")
        assert user.role == "APPROVER" and user.is_active and user.auth_provider == "ldap"

    def test_highest_role_wins(self, ldap_config):
        with FakeLdap([_entry(groups=[APPROVER_GROUP, ADMIN_GROUP])]):
            assert LDAPProvider().authenticate("jdoe", "pw").role == "ADMIN"

    def test_unknown_user(self, ldap_config):
        with FakeLdap([]):
            assert LDAPProvider().authenticate("ghost", "pw") is None
        assert not User.objects.filter(username="ghost").exists()

    def test_wrong_password(self, ldap_config):
        with FakeLdap([_entry()], user_bind=False):
            assert LDAPProvider().authenticate("jdoe", "bad") is None

    def test_unreachable_server_raises(self, ldap_config):
        with FakeLdap(service_error=OSError("socket connection error while opening")):
            with pytest.raises(LDAPConnectionError, match="Cannot reach LDAP server"):
                LDAPProvider().authenticate("jdoe", "pw")

    def test_service_bind_rejected_returns_none(self, ldap_config):
        with FakeLdap(service_error=RuntimeError("automatic bind not successful - invalidCredentials")):
            assert LDAPProvider().authenticate("jdoe", "pw") is None

    def test_email_conflict_returns_none(self, ldap_config):
        User.objects.create_user("other", "pw", email="jdoe@example.com")
        with FakeLdap([_entry()]):
            assert LDAPProvider().authenticate("jdoe", "pw") is None
        assert User.objects.count() == 1

    def test_bad_group_mapping_json(self, ldap_config):
        config.set_value("ldap_group_mapping_json", "not json")
        assert LDAPProvider()._map_groups_to_role([ADMIN_GROUP]) is None


# -- LDAP: bulk sync ----------------------------------------------------------------


class TestLdapSync:
    def test_creates_new_and_skips_existing(self, ldap_config):
        User.objects.create_user("existing", "pw", role="ADMIN")
        entries = [
            _entry("new1", "New One", "n1@example.com", [APPROVER_GROUP]),
            _entry("existing", "Existing", "e@example.com"),
            _entry("disabled", "Disabled", None, uac="514"),
            SimpleNamespace(entry_dn="CN=nosam", sAMAccountName=None),
        ]
        with FakeLdap(entries) as fake:
            result = LDAPProvider().sync_users()
        assert result == {
            "synced": 2, "created": 2, "updated": 0, "skipped": 1, "deactivated": 0, "errors": [],
        }
        assert User.objects.get(username="new1").role == "APPROVER"
        assert User.objects.get(username="disabled").is_active is False
        assert User.objects.get(username="existing").role == "ADMIN"
        # {username} is neutralised for the enumeration filter.
        assert fake.searches[0][0][1] == "(sAMAccountName=*)"

    def test_missing_search_base(self, ldap_config):
        config.set_value("ldap_search_base", " ")
        with FakeLdap([_entry()]):
            result = LDAPProvider().sync_users()
        assert "ldap_search_base is not configured" in result["errors"][0]
        assert result["created"] == 0

    def test_empty_filter_falls_back_to_person(self, ldap_config):
        config.set_value("ldap_user_filter", "")
        with FakeLdap([]) as fake:
            LDAPProvider().sync_users()
        assert fake.searches[0][0][1] == "(objectClass=person)"

    def test_connection_error_reported(self, ldap_config):
        with FakeLdap(service_error=OSError("socket error")):
            result = LDAPProvider().sync_users()
        assert result["errors"] == ["socket error"]


# -- OIDC ------------------------------------------------------------------------------


@pytest.fixture
def oidc_config(db):
    for key, value in {
        "oidc_issuer": "https://idp.test/realms/x/",
        "oidc_client_id": "presto",
        "oidc_client_secret": "s3cret",
        "oidc_redirect_uri": "https://app.test/callback",
        "oidc_role_claim": "groups",
        "oidc_role_mapping_json": json.dumps({"ADMIN": "app-admin", "ESTIMATOR": "app-est"}),
    }.items():
        config.set_value(key, value)


DISCOVERY = {
    "authorization_endpoint": "https://idp.test/auth",
    "token_endpoint": "https://idp.test/token",
    "userinfo_endpoint": "https://idp.test/userinfo",
}


def _oidc_patches(userinfo=None, discovery=DISCOVERY):
    session = MagicMock()
    session.create_authorization_url.return_value = ("https://idp.test/auth?state=abc", "abc")
    session.get.return_value.json.return_value = userinfo or {}
    session_cls = MagicMock(return_value=session)
    get = MagicMock()
    get.return_value.json.return_value = discovery
    return session_cls, session, get


class TestOidc:
    def test_not_configured(self, db):
        provider = OIDCProvider()
        assert not provider.is_configured
        assert provider.get_authorization_url("s") is None
        assert provider.handle_callback("c", "s") is None

    def test_authorization_url(self, oidc_config):
        session_cls, session, get = _oidc_patches()
        with patch("authlib.integrations.requests_client.OAuth2Session", session_cls), \
                patch("requests.get", get):
            url = OIDCProvider().get_authorization_url("abc")
        assert url == "https://idp.test/auth?state=abc"
        get.assert_called_once_with("https://idp.test/realms/x/.well-known/openid-configuration", timeout=10)
        assert session_cls.call_args.kwargs["scope"] == "openid profile email"
        session.create_authorization_url.assert_called_once_with("https://idp.test/auth", state="abc")

    def test_authorization_url_discovery_failure(self, oidc_config):
        session_cls, _, get = _oidc_patches()
        get.side_effect = ConnectionError("down")
        with patch("authlib.integrations.requests_client.OAuth2Session", session_cls), \
                patch("requests.get", get):
            assert OIDCProvider().get_authorization_url("abc") is None

    def test_callback_creates_user(self, oidc_config):
        info = {"sub": "123", "preferred_username": "jane", "email": "jane@example.com",
                "name": "Jane", "groups": ["app-est", "other"]}
        session_cls, session, get = _oidc_patches(info)
        with patch("authlib.integrations.requests_client.OAuth2Session", session_cls), \
                patch("requests.get", get):
            user = OIDCProvider().handle_callback("code1", "abc")
        session.fetch_token.assert_called_once_with("https://idp.test/token", code="code1")
        assert user.username == "jane" and user.role == "ESTIMATOR" and user.auth_provider == "oidc"
        assert user.external_id == "123" and user.display_name == "Jane" and user.password is None

    def test_callback_keeps_existing_role_without_match(self, oidc_config):
        User.objects.create_user("jane", None, role="APPROVER", auth_provider="oidc")
        session_cls, _, get = _oidc_patches({"sub": "123", "preferred_username": "jane", "groups": "x"})
        with patch("authlib.integrations.requests_client.OAuth2Session", session_cls), \
                patch("requests.get", get):
            assert OIDCProvider().handle_callback("c", "s").role == "APPROVER"

    def test_callback_overwrites_role_on_match(self, oidc_config):
        User.objects.create_user("jane", None, role="VIEWER", auth_provider="oidc")
        session_cls, _, get = _oidc_patches({"sub": "1", "preferred_username": "jane", "groups": "app-admin"})
        with patch("authlib.integrations.requests_client.OAuth2Session", session_cls), \
                patch("requests.get", get):
            assert OIDCProvider().handle_callback("c", "s").role == "ADMIN"

    def test_callback_falls_back_to_sub(self, oidc_config):
        session_cls, _, get = _oidc_patches({"sub": "abc-123"})
        with patch("authlib.integrations.requests_client.OAuth2Session", session_cls), \
                patch("requests.get", get):
            user = OIDCProvider().handle_callback("c", "s")
        assert user.username == "abc-123" and user.role == "VIEWER"

    def test_callback_token_failure(self, oidc_config):
        session_cls, session, get = _oidc_patches({"sub": "1"})
        session.fetch_token.side_effect = RuntimeError("invalid_grant")
        with patch("authlib.integrations.requests_client.OAuth2Session", session_cls), \
                patch("requests.get", get):
            assert OIDCProvider().handle_callback("c", "s") is None
        assert not User.objects.exists()

    def test_map_role_ignores_non_list_claim(self, oidc_config):
        assert OIDCProvider()._map_role({"groups": 42}) is None
