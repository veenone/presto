"""Sidebar navigation and page access rules (same items and gates as the v3 UI).

``requires`` is ``None`` (any signed-in user), a UI permission key, a tuple of
keys (any of them), or ``"ADMIN"`` (admin role only).
"""

from __future__ import annotations

from dataclasses import dataclass

from apps.accounts.models import Role, User
from apps.accounts.permissions import user_permissions

Requirement = str | tuple[str, ...] | None


@dataclass(frozen=True)
class NavItem:
    label: str
    icon: str
    url_name: str
    path: str
    requires: Requirement = None
    phase: int = 0  # migration phase that delivers the real page (0 = done)


@dataclass(frozen=True)
class NavSection:
    key: str
    label: str
    items: tuple[NavItem, ...]


NAVIGATION: tuple[NavSection, ...] = (
    NavSection("overview", "Overview", (
        NavItem("Dashboard", "dashboard", "dashboard", "/", phase=6),
        NavItem("Request Inbox", "inbox", "requests", "/requests",
                ("view_requests", "manage_requests"), phase=6),
    )),
    NavSection("estimation", "Estimation", (
        NavItem("Estimations", "list_alt", "estimations", "/estimations", phase=5),
        NavItem("New Estimation", "add_circle", "estimation_new", "/estimation/new", phase=5),
    )),
    NavSection("data_management", "Data Management", (
        NavItem("Feature Catalog", "category", "features", "/features", phase=4),
        NavItem("Feature Presets", "bookmarks", "feature_presets", "/feature-presets",
                "manage_presets", phase=4),
        NavItem("Task Templates", "assignment", "tasks", "/tasks", phase=4),
        NavItem("DUT Registry", "devices", "duts", "/duts", phase=4),
        NavItem("DUT Assets", "inventory", "assets", "/assets", phase=4),
        NavItem("Test Profiles", "tune", "profiles", "/profiles", phase=4),
        NavItem("Historical Projects", "history", "history", "/history", phase=4),
        NavItem("Team Members", "group", "team", "/team", phase=4),
        NavItem("Risk Registry", "warning", "risks", "/risks", phase=4),
        NavItem("PR Registry", "bug_report", "pr_registry", "/pr-registry", phase=4),
        NavItem("Document Types", "description", "document_types", "/document-types", phase=4),
        NavItem("Public Holidays", "event", "public_holidays", "/public-holidays", phase=4),
    )),
    NavSection("administration", "Administration", (
        NavItem("Settings", "settings", "settings", "/settings", phase=4),
        NavItem("Integrations", "sync", "integrations", "/integrations", phase=6),
        NavItem("Users", "manage_accounts", "users", "/users", "ADMIN", phase=4),
        NavItem("RBAC", "admin_panel_settings", "rbac", "/rbac", "ADMIN", phase=4),
        NavItem("Audit Log", "receipt_long", "audit", "/audit", "ADMIN", phase=4),
        NavItem("Backup & Restore", "backup", "backup", "/admin/backup", "ADMIN", phase=4),
    )),
)

ITEMS_BY_NAME: dict[str, NavItem] = {item.url_name: item for s in NAVIGATION for item in s.items}


def allowed(user: User, requires: Requirement, perms: frozenset[str] | None = None) -> bool:
    if requires is None:
        return True
    if requires == Role.ADMIN:
        return user.role == Role.ADMIN
    perms = user_permissions(user) if perms is None else perms
    keys = (requires,) if isinstance(requires, str) else requires
    return any(k in perms for k in keys)


def build_navigation(user: User, current_path: str, perms: frozenset[str]) -> list[dict]:
    """Sections with the items this user may see, marking the active one."""
    sections = []
    for section in NAVIGATION:
        items = [
            {
                "label": item.label, "icon": item.icon, "path": item.path,
                "active": current_path == item.path
                or (item.path != "/" and current_path.startswith(item.path + "/")),
            }
            for item in section.items if allowed(user, item.requires, perms)
        ]
        if items:
            sections.append({"key": section.key, "label": section.label, "items": items})
    return sections
