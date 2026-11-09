"""LDAP / Active Directory authentication and user synchronisation (port of v3).

``LDAPProvider`` handles two use cases:

1. Interactive login (``authenticate``): bind with the service account, find
   the user entry, re-bind as the user to verify the password, then create or
   update the local ``User`` row.
2. Bulk sync (``sync_users``): enumerate directory entries and create local
   users for the ones that do not exist yet (existing users are never changed).

Configuration keys (``configuration`` table, edited on the Settings page):
``ldap_url``, ``ldap_bind_dn``, ``ldap_bind_password``, ``ldap_search_base``,
``ldap_user_filter`` (``{username}`` is replaced at runtime) and
``ldap_group_mapping_json`` (``{"ADMIN": "CN=Admins,OU=Groups,DC=example,DC=com"}``).

Role rule (v3 3.3.0): a group mapping only changes an existing user's role when
it explicitly matches; otherwise the admin-assigned role is kept.

``ldap3`` is imported lazily so the module loads without it.
"""

from __future__ import annotations

import json

from django.db import transaction

from apps.core import config

from .models import Role, User

CONFIG_KEYS = (
    "ldap_url",
    "ldap_bind_dn",
    "ldap_bind_password",
    "ldap_search_base",
    "ldap_user_filter",
    "ldap_group_mapping_json",
)
ROLE_PRIORITY = ("ADMIN", "APPROVER", "ESTIMATOR", "VIEWER")


class LDAPConnectionError(Exception):
    """The LDAP server is unreachable (or ldap3 is missing)."""


def _attr(entry, name: str) -> str | None:
    value = getattr(entry, name, None) if hasattr(entry, name) else None
    return str(value) if value else None


class LDAPProvider:
    """LDAP authentication provider for Active Directory integration."""

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
        return bool(self.config.get("ldap_url"))

    def test_connection(self) -> tuple[bool, str]:
        """Bind with the service account; ``(success, message)`` for the Settings UI."""
        if not self.is_configured:
            return False, "LDAP is not configured. Set ldap_url in Settings and save."
        try:
            import ldap3
        except ImportError:
            return False, "ldap3 library is not installed on the server."
        try:
            server = ldap3.Server(self.config["ldap_url"], get_info=ldap3.ALL, connect_timeout=10)
            bind_dn = self.config.get("ldap_bind_dn") or None
            bind_pw = self.config.get("ldap_bind_password") or None
            conn = ldap3.Connection(
                server, user=bind_dn, password=bind_pw, auto_bind=True, receive_timeout=10,
            )
            conn.unbind()
            who = bind_dn or "anonymous"
            return True, f"Bound to {self.config['ldap_url']} as {who}."
        except Exception as exc:
            return False, f"LDAP connection failed: {exc}"

    # -- interactive login ------------------------------------------------------
    def authenticate(self, username: str, password: str) -> User | None:
        """Verify ``username``/``password`` against LDAP and upsert the local user.

        Returns None on any failure (unknown user, wrong password...). Raises
        :class:`LDAPConnectionError` when the server cannot be reached.
        """
        if not self.is_configured:
            return None
        try:
            import ldap3

            # get_info=NONE: login needs a bind and one search; fetching the
            # schema adds seconds per login against AD.
            server = ldap3.Server(self.config["ldap_url"], get_info=ldap3.NONE, connect_timeout=10)
            search_base = self.config.get("ldap_search_base", "")
            user_filter_tpl = self.config.get("ldap_user_filter") or "(sAMAccountName={username})"
            user_filter = user_filter_tpl.replace("{username}", username)

            conn = ldap3.Connection(
                server,
                user=self.config.get("ldap_bind_dn"),
                password=self.config.get("ldap_bind_password"),
                auto_bind=True,
                receive_timeout=10,
            )
            conn.search(
                search_base, user_filter,
                attributes=["sAMAccountName", "cn", "mail", "memberOf", "distinguishedName"],
            )
            if not conn.entries:
                return None

            entry = conn.entries[0]
            user_dn = str(entry.entry_dn)

            user_conn = ldap3.Connection(server, user=user_dn, password=password, receive_timeout=10)
            if not user_conn.bind():
                return None

            display_name = _attr(entry, "cn") or username
            email = _attr(entry, "mail")
            groups = [str(g) for g in entry.memberOf] if _attr(entry, "memberOf") else []
            role = self._map_groups_to_role(groups)

            with transaction.atomic():
                local_user = User.objects.filter(username=username).first()
                if local_user is not None:
                    local_user.display_name = display_name
                    local_user.email = email
                    local_user.external_id = user_dn
                    local_user.auth_provider = "ldap"
                    if role is not None:
                        local_user.role = role
                    local_user.is_active = True
                else:
                    local_user = User(
                        username=username, display_name=display_name, email=email,
                        auth_provider="ldap", external_id=user_dn,
                        role=role or Role.VIEWER, is_active=True, password=None,
                    )
                local_user.save()
            return local_user

        except ImportError as exc:
            raise LDAPConnectionError("ldap3 library is not installed") from exc
        except LDAPConnectionError:
            raise
        except Exception as exc:
            err_msg = str(exc)
            lowered = err_msg.lower()
            if "connect" in lowered or "timeout" in lowered or "socket" in lowered:
                raise LDAPConnectionError(f"Cannot reach LDAP server: {err_msg}") from exc
            return None

    # -- bulk sync ---------------------------------------------------------------
    def sync_users(self) -> dict[str, object]:
        """Create local users for directory entries that do not exist yet.

        Existing users are skipped (counted in ``skipped``), never modified or
        deactivated; ``POST /auth/ldap/reset-synced-users`` clears a bad sync.
        """
        result: dict[str, object] = {
            "synced": 0, "created": 0, "updated": 0, "skipped": 0, "deactivated": 0, "errors": [],
        }
        if not self.is_configured:
            return result

        try:
            import ldap3

            server = ldap3.Server(self.config["ldap_url"], get_info=ldap3.NONE)
            conn = ldap3.Connection(
                server,
                user=self.config.get("ldap_bind_dn"),
                password=self.config.get("ldap_bind_password"),
                auto_bind=True,
            )

            search_base = (self.config.get("ldap_search_base") or "").strip()
            if not search_base:
                # Without a base DN ldap3 searches the server's default naming
                # context, which returns unrelated entries.
                result["errors"] = [
                    "ldap_search_base is not configured. Set it in "
                    "Settings -> LDAP (e.g. OU=Users,DC=corp,DC=example,DC=com) "
                    "and save before syncing."
                ]
                return result

            user_filter = (self.config.get("ldap_user_filter") or "").strip() or "(objectClass=person)"
            if "{username}" in user_filter:
                user_filter = user_filter.replace("{username}", "*")

            conn.search(
                search_base, user_filter, search_scope=ldap3.SUBTREE,
                attributes=[
                    "sAMAccountName", "cn", "mail", "memberOf", "distinguishedName", "userAccountControl",
                ],
            )

            errors: list[str] = []
            created = skipped = 0
            for entry in conn.entries:
                try:
                    sam = _attr(entry, "sAMAccountName")
                    if not sam:
                        continue
                    if User.objects.filter(username=sam).exists():
                        skipped += 1
                        continue

                    groups = [str(g) for g in entry.memberOf] if _attr(entry, "memberOf") else []
                    try:
                        uac = int(_attr(entry, "userAccountControl") or "0")
                    except ValueError:
                        uac = 0
                    with transaction.atomic():
                        User.objects.create(
                            username=sam,
                            display_name=_attr(entry, "cn") or sam,
                            email=_attr(entry, "mail"),
                            auth_provider="ldap",
                            external_id=str(entry.entry_dn),
                            role=self._map_groups_to_role(groups) or Role.VIEWER,
                            # AD ACCOUNTDISABLE flag (bit 1 of userAccountControl).
                            is_active=not bool(uac & 0x0002),
                            password=None,
                        )
                    created += 1
                except Exception as exc:
                    errors.append(str(exc))

            result.update(created=created, synced=created, skipped=skipped, errors=errors)

        except ImportError:
            result["errors"] = [*result["errors"], "ldap3 library not installed"]  # type: ignore[misc]
        except Exception as exc:
            result["errors"] = [*result["errors"], str(exc)]  # type: ignore[misc]

        return result

    # -- helpers ----------------------------------------------------------------
    def _map_groups_to_role(self, group_dns: list[str]) -> str | None:
        """Highest role whose mapped group DN is a case-insensitive substring of
        one of ``group_dns``; None when nothing matches (keep the current role)."""
        mapping_str = self.config.get("ldap_group_mapping_json") or "{}"
        try:
            mapping = json.loads(mapping_str)
        except (json.JSONDecodeError, TypeError):
            mapping = {}
        if not isinstance(mapping, dict):
            mapping = {}
        for role in ROLE_PRIORITY:
            group_dn = mapping.get(role)
            if group_dn and any(str(group_dn).lower() in g.lower() for g in group_dns):
                return role
        return None
