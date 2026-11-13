"""Template context shared by every page (branding, theme, navigation)."""

from __future__ import annotations

from django.conf import settings

from apps.accounts.permissions import get_matrix, user_permissions
from apps.core import config

from .navigation import build_navigation
from .theme import theme_variables


def shell(request) -> dict:
    user = getattr(request, "user", None)
    signed_in = bool(user and user.is_authenticated)
    perms = user_permissions(user, get_matrix()) if signed_in else frozenset()
    logo_url = config.get_str("logo_url", "")
    return {
        "app_version": settings.APP_VERSION,
        "branding": {
            "logo_url": logo_url,
            "logo_height": config.get_str("logo_height", "40") or "40",
        },
        "theme_vars": theme_variables(),
        "user_theme": (user.theme_preference or "") if signed_in else "",
        "ui_perms": perms,
        "navigation": build_navigation(user, request.path, perms) if signed_in else [],
    }
