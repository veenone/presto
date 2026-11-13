"""OpenID Connect provider, Authorization Code flow (port of v3 ``OIDCProvider``).

No API endpoint uses it yet; it is kept for the web UI login:

1. The view calls ``get_authorization_url(state)`` and redirects the browser.
2. The IdP redirects back to ``oidc_redirect_uri`` with ``code`` and ``state``.
3. The view checks ``state`` and calls ``handle_callback(code, state)``, which
   exchanges the code, reads ``userinfo``, and creates or updates the local user.

Configuration keys: ``oidc_issuer``, ``oidc_client_id``, ``oidc_client_secret``,
``oidc_redirect_uri``, ``oidc_scopes`` (default ``openid profile email``),
``oidc_role_claim`` (default ``roles``) and ``oidc_role_mapping_json``
(``{"ADMIN": "app-admin"}``).

Role rule (v3 3.3.0): a role mapping only changes an existing user's role when
it explicitly matches; otherwise the admin-assigned role is kept.

``authlib`` and ``requests`` are imported lazily; every method returns None
when they are missing, the provider is not configured, or the IdP fails.
"""

from __future__ import annotations

import json

from django.db import transaction

from apps.core import config

from .models import Role, User

CONFIG_KEYS = (
    "oidc_issuer",
    "oidc_client_id",
    "oidc_client_secret",
    "oidc_redirect_uri",
    "oidc_scopes",
    "oidc_role_claim",
    "oidc_role_mapping_json",
)
ROLE_PRIORITY = ("ADMIN", "APPROVER", "ESTIMATOR", "VIEWER")
DEFAULT_SCOPES = "openid profile email"


class OIDCProvider:
    """OpenID Connect authentication provider."""

    def __init__(self) -> None:
        self._config: dict[str, str] | None = None

    # -- configuration -------------------------------------------------------
    @property
    def config(self) -> dict[str, str]:
        if self._config is None:
            self._config = {key: config.get_str(key) for key in CONFIG_KEYS}
        return self._config

    @property
    def is_configured(self) -> bool:
        return bool(self.config.get("oidc_issuer") and self.config.get("oidc_client_id"))

    def _discovery_url(self) -> str:
        return f"{self.config['oidc_issuer'].rstrip('/')}/.well-known/openid-configuration"

    # -- authorization URL -----------------------------------------------------
    def get_authorization_url(self, state: str) -> str | None:
        """IdP authorization URL embedding ``state`` (CSRF protection), or None."""
        if not self.is_configured:
            return None
        try:
            import requests as http_requests
            from authlib.integrations.requests_client import OAuth2Session

            client = OAuth2Session(
                client_id=self.config["oidc_client_id"],
                client_secret=self.config.get("oidc_client_secret") or None,
                scope=self.config.get("oidc_scopes") or DEFAULT_SCOPES,
                redirect_uri=self.config.get("oidc_redirect_uri") or "",
            )
            discovery = http_requests.get(self._discovery_url(), timeout=10).json()
            uri, _ = client.create_authorization_url(discovery["authorization_endpoint"], state=state)
            return uri
        except Exception:
            return None

    # -- callback ---------------------------------------------------------------
    def handle_callback(self, code: str, state: str) -> User | None:
        """Exchange ``code`` for tokens, read userinfo and upsert the local user.

        The caller must verify ``state`` against the value it generated.
        """
        if not self.is_configured:
            return None
        try:
            import requests as http_requests
            from authlib.integrations.requests_client import OAuth2Session

            discovery = http_requests.get(self._discovery_url(), timeout=10).json()
            client = OAuth2Session(
                client_id=self.config["oidc_client_id"],
                client_secret=self.config.get("oidc_client_secret") or None,
                redirect_uri=self.config.get("oidc_redirect_uri") or "",
            )
            client.fetch_token(discovery["token_endpoint"], code=code)
            userinfo: dict[str, object] = client.get(discovery["userinfo_endpoint"]).json()

            username = (
                str(userinfo.get("preferred_username"))
                if userinfo.get("preferred_username")
                else str(userinfo.get("sub", ""))
            )
            if not username:
                return None
            email = str(userinfo["email"]) if userinfo.get("email") else None
            display_name = str(userinfo.get("name")) if userinfo.get("name") else username
            subject = str(userinfo["sub"]) if userinfo.get("sub") else None
            role = self._map_role(userinfo)

            with transaction.atomic():
                local_user = User.objects.filter(username=username).first()
                if local_user is not None:
                    local_user.display_name = display_name
                    local_user.email = email
                    local_user.external_id = subject
                    if role is not None:
                        local_user.role = role
                    local_user.is_active = True
                else:
                    local_user = User(
                        username=username, display_name=display_name, email=email,
                        auth_provider="oidc", external_id=subject,
                        role=role or Role.VIEWER, is_active=True, password=None,
                    )
                local_user.save()
            return local_user
        except Exception:
            return None

    # -- helpers ----------------------------------------------------------------
    def _map_role(self, userinfo: dict[str, object]) -> str | None:
        """Highest role whose mapped value appears in the role claim (a string
        or a list); None when nothing matches (keep the current role)."""
        role_claim = self.config.get("oidc_role_claim") or "roles"
        raw_roles = userinfo.get(role_claim, [])
        if isinstance(raw_roles, str):
            roles_in_token = [raw_roles]
        elif isinstance(raw_roles, list):
            roles_in_token = [str(r) for r in raw_roles]
        else:
            roles_in_token = []

        mapping_str = self.config.get("oidc_role_mapping_json") or "{}"
        try:
            mapping = json.loads(mapping_str)
        except (json.JSONDecodeError, TypeError):
            mapping = {}
        if not isinstance(mapping, dict):
            mapping = {}
        for role in ROLE_PRIORITY:
            mapped_value = mapping.get(role)
            if mapped_value and mapped_value in roles_in_token:
                return role
        return None
