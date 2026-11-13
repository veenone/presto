"""UI permission matrix (v3 RBAC page): which role sees which feature.

The REST API enforces the role hierarchy only (``apps.accounts.roles``), as v3
did. This matrix controls navigation and page access in the web UI. The ADMIN
role always has every permission. A saved matrix lives in the ``rbac_matrix``
configuration key as JSON ``{role: [permission, ...]}``.
"""

from __future__ import annotations

from apps.core import config

from .models import Role, User

ROLES: list[str] = [Role.VIEWER, Role.ESTIMATOR, Role.APPROVER, Role.ADMIN]

# (key, label, description) in display order.
PERMISSIONS: list[tuple[str, str, str]] = [
    ("view_estimations", "View Estimations", "View existing estimations."),
    ("create_estimations", "Create Estimations", "Create and edit estimations."),
    ("approve_estimations", "Approve Estimations", "Approve or reject submitted estimations."),
    ("manage_users", "Manage Users", "Create, edit and delete user accounts."),
    ("view_reports", "View Reports", "View generated reports."),
    ("download_reports", "Download Reports", "Download reports as PDF / Word / Excel."),
    ("manage_features", "Manage Features", "Add, edit and remove catalog features."),
    ("manage_presets", "Manage Feature Presets", "Create, edit and delete feature presets."),
    ("manage_duts", "Manage DUTs", "Manage the DUT (device) registry."),
    ("manage_profiles", "Manage Profiles", "Manage test configuration profiles."),
    ("manage_team", "Manage Team", "Manage team members and assignments."),
    ("manage_integrations", "Manage Integrations", "Configure Redmine / JIRA / Outline integrations."),
    ("manage_settings", "Manage Settings", "Change global application settings."),
    ("view_audit_log", "View Audit Log", "View the security / audit log."),
    ("view_requests", "View Requests", "View incoming test requests."),
    ("manage_requests", "Manage Requests", "Triage and manage test requests."),
    ("ldap_sync", "LDAP Sync", "Trigger LDAP user synchronization."),
    ("reinit_registries", "Reinit Registries", "Re-initialize seed registries."),
]
PERMISSION_KEYS: list[str] = [key for key, _, _ in PERMISSIONS]

_ESTIMATOR = [
    "view_estimations", "create_estimations", "view_reports", "download_reports",
    "manage_features", "manage_presets", "manage_duts", "manage_profiles", "view_requests",
]
DEFAULT_MATRIX: dict[str, list[str]] = {
    Role.VIEWER: ["view_estimations", "view_reports"],
    Role.ESTIMATOR: _ESTIMATOR,
    Role.APPROVER: [
        "view_estimations", "create_estimations", "approve_estimations", "view_reports",
        "download_reports", "manage_features", "manage_presets", "manage_duts", "manage_profiles",
        "view_audit_log", "view_requests", "manage_requests",
    ],
    Role.ADMIN: list(PERMISSION_KEYS),
}
MATRIX_CONFIG_KEY = "rbac_matrix"


def get_matrix() -> dict[str, list[str]]:
    """Saved matrix merged over the defaults; ADMIN always has everything."""
    saved = config.get_json(MATRIX_CONFIG_KEY, None)
    matrix = {role: list(perms) for role, perms in DEFAULT_MATRIX.items()}
    if isinstance(saved, dict):
        for role in ROLES:
            perms = saved.get(role)
            if isinstance(perms, list):
                matrix[role] = [p for p in perms if p in PERMISSION_KEYS]
    matrix[Role.ADMIN] = list(PERMISSION_KEYS)
    return matrix


def user_permissions(user: User | None, matrix: dict[str, list[str]] | None = None) -> frozenset[str]:
    if user is None or not getattr(user, "is_authenticated", False) or not user.is_active:
        return frozenset()
    if user.role == Role.ADMIN:
        return frozenset(PERMISSION_KEYS)
    return frozenset((matrix or get_matrix()).get(user.role, []))


def has_permission(user: User | None, key: str) -> bool:
    return key in user_permissions(user)
