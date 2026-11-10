"""Configurable theme colours (Settings page keys from v3) as CSS variables."""

from __future__ import annotations

from apps.core import config

# (CSS variable, light config key, light default, dark config key, dark default)
COLOR_KEYS = (
    ("--primary", "button_color_light", "#1976D2", "button_color_dark", "#90CAF9"),
    ("--sidebar-bg", "sidebar_bg_light", "#FFFFFF", "sidebar_bg_dark", "#1D1D1D"),
    ("--content-bg", "content_bg_light", "#FAFAFA", "content_bg_dark", "#121212"),
    ("--table-header-bg", "table_header_bg_light", "#E0E0E0", "table_header_bg_dark", "#424242"),
    ("--calendar-today-bg", "calendar_today_bg_light", "#e0e0e0", "calendar_today_bg_dark", "#37474f"),
    ("--calendar-weekend-bg", "calendar_weekend_bg_light", "#f0f0f0", "calendar_weekend_bg_dark", "#263238"),
)


def readable_text(hex_color: str, fallback: str) -> str:
    """White or near-black text for a background (v3 luminance rule)."""
    try:
        value = hex_color.strip().lstrip("#")
        r, g, b = int(value[0:2], 16), int(value[2:4], 16), int(value[4:6], 16)
    except (ValueError, IndexError):
        return fallback
    return "#FFFFFF" if 0.299 * r + 0.587 * g + 0.114 * b < 140 else "#212121"


def _safe(value: str, default: str) -> str:
    """Only plain colour values reach the stylesheet."""
    value = (value or "").strip()
    return value if value and all(c.isalnum() or c in "#(),.% " for c in value) else default


def theme_variables() -> dict[str, dict[str, str]]:
    themes: dict[str, dict[str, str]] = {"light": {}, "dark": {}}
    for var, light_key, light_default, dark_key, dark_default in COLOR_KEYS:
        themes["light"][var] = _safe(config.get_str(light_key, light_default), light_default)
        themes["dark"][var] = _safe(config.get_str(dark_key, dark_default), dark_default)
    for name, fallback in (("light", "#212121"), ("dark", "#FFFFFF")):
        themes[name]["--sidebar-fg"] = readable_text(themes[name]["--sidebar-bg"], fallback)
        themes[name]["--on-primary"] = readable_text(themes[name]["--primary"], "#FFFFFF")
    return themes
