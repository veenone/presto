"""Typed access to the ``configuration`` key-value table.

v3 parsed string values at every call site (``float(cfg["buffer_percentage"])``,
``value == "true"``...). These helpers do it once, with defaults.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .models import Configuration

DEFAULTS_PATH = Path(__file__).with_name("config_defaults.json")
SCHEMA_VERSION_KEY = "schema_version"
# Marker written to schema_version once the database is managed by Django.
# v3 parses schema_version as an int, so it refuses to start on it.
DJANGO_SCHEMA_MARKER = "django"

_TRUE = {"1", "true", "yes", "on"}
_MISSING = object()


def load_defaults() -> list[dict[str, str]]:
    """Default keys, values and descriptions (the v3 schema 24 set)."""
    return json.loads(DEFAULTS_PATH.read_text(encoding="utf-8"))


def _raw(key: str) -> str | object:
    value = Configuration.objects.filter(key=key).values_list("value", flat=True).first()
    return _MISSING if value is None else value


def get_str(key: str, default: str = "") -> str:
    value = _raw(key)
    return default if value is _MISSING else str(value)


def get_float(key: str, default: float = 0.0) -> float:
    value = _raw(key)
    try:
        return default if value is _MISSING or value == "" else float(value)
    except (TypeError, ValueError):
        return default


def get_int(key: str, default: int = 0) -> int:
    value = _raw(key)
    try:
        return default if value is _MISSING or value == "" else int(float(value))
    except (TypeError, ValueError):
        return default


def get_bool(key: str, default: bool = False) -> bool:
    value = _raw(key)
    return default if value is _MISSING or value == "" else str(value).strip().lower() in _TRUE


def get_list(key: str, default: list[str] | None = None) -> list[str]:
    """Comma-separated list, or a JSON array (both formats exist in v3)."""
    value = _raw(key)
    if value is _MISSING or str(value).strip() == "":
        return list(default or [])
    text = str(value).strip()
    if text.startswith("["):
        try:
            return [str(v) for v in json.loads(text)]
        except json.JSONDecodeError:
            pass
    return [v.strip() for v in text.split(",") if v.strip()]


def get_json(key: str, default: Any = None) -> Any:
    value = _raw(key)
    if value is _MISSING or str(value).strip() == "":
        return default
    try:
        return json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return default


def set_value(key: str, value: Any, description: str | None = None) -> Configuration:
    """Upsert a key. Non-string values are stored the way v3 stores them."""
    if isinstance(value, bool):
        text = "true" if value else "false"
    elif isinstance(value, (list, dict)):
        text = json.dumps(value)
    else:
        text = "" if value is None else str(value)
    obj, created = Configuration.objects.get_or_create(
        key=key, defaults={"value": text, "description": description},
    )
    if not created:
        obj.value = text
        if description is not None:
            obj.description = description
        obj.save()
    return obj


def ensure_defaults() -> int:
    """Insert missing default keys; never overwrite existing values.

    Returns the number of keys created.
    """
    existing = set(Configuration.objects.values_list("key", flat=True))
    missing = [
        Configuration(key=row["key"], value=row["value"], description=row["description"])
        for row in load_defaults()
        if row["key"] not in existing
    ]
    if SCHEMA_VERSION_KEY not in existing:
        missing.append(Configuration(
            key=SCHEMA_VERSION_KEY, value=DJANGO_SCHEMA_MARKER,
            description="Database schema version",
        ))
    Configuration.objects.bulk_create(missing)
    return len(missing)
