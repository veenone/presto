"""Configuration, branding and logo services (v3 ``/configuration`` endpoints).

Also holds ``ServiceError``, the exception services raise for HTTP-mappable
failures (status code + v3 message). Routers translate it to ``HttpError``.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

from django.conf import settings

from . import config
from .errors import ServiceError  # noqa: F401  (re-exported for callers)
from .models import Configuration

LOGO_EXTENSIONS = (".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp")
DEFAULT_TEAM_SKILLS = (
    '["Test Execution","Test Design","Automation","Performance","Security",'
    '"API Testing","Mobile Testing","Regression"]'
)


def get_branding() -> dict[str, str]:
    return {
        "logo_url": config.get_str("logo_url", ""),
        "logo_height": config.get_str("logo_height", "50"),
    }


def list_configuration():
    return Configuration.objects.all()


def _csv(key: str, default: str) -> list[str]:
    return [c.strip() for c in config.get_str(key, default).split(",") if c.strip()]


def dut_categories() -> list[str]:
    return _csv("dut_categories", "SIM,eSIM,UICC,IoT Device,Mobile Device,Other")


def feature_categories() -> list[str]:
    return _csv("feature_categories", "Telecom,Security,Platform,Other")


def project_types() -> list[str]:
    return _csv("project_types", "NEW,EVOLUTION,SUPPORT,CHANGE_REQUEST")


def _json_list(key: str, default: str) -> list:
    try:
        return json.loads(config.get_str(key, default))
    except (json.JSONDecodeError, TypeError):
        return []


def product_types() -> list:
    return _json_list("product_types", '["Payment", "Telco"]')


def team_skills() -> list:
    return _json_list("team_skills", DEFAULT_TEAM_SKILLS)


def update_configuration(key: str, value: str) -> Configuration:
    """Set a key's value, creating it (empty description) if missing."""
    cfg = Configuration.objects.filter(key=key).first()
    if cfg is None:
        return Configuration.objects.create(key=key, value=value, description="")
    cfg.value = value
    cfg.save(update_fields=["value"])
    return cfg


def uploads_dir() -> Path:
    return Path(settings.UPLOADS_DIR)


def save_logo(filename: str | None, content: bytes) -> dict:
    """Store the logo as ``logo.<ext>`` and point ``logo_url`` at it."""
    ext = os.path.splitext(filename or "logo.png")[1].lower()
    if ext not in LOGO_EXTENSIONS:
        raise ServiceError(400, f"Invalid file type '{ext}'. Allowed: {', '.join(LOGO_EXTENSIONS)}")

    upload_dir = uploads_dir()
    upload_dir.mkdir(parents=True, exist_ok=True)
    target = f"logo{ext}"
    # Keep only the current logo (a previous one may have another extension).
    for existing in upload_dir.glob("logo.*"):
        if existing.name != target:
            try:
                existing.unlink()
            except OSError:
                pass
    (upload_dir / target).write_bytes(content)

    # Cache-busting stamp so browsers refetch a logo whose filename is unchanged.
    logo_url = f"/api/static/uploads/{target}?v={int(time.time())}"
    cfg = Configuration.objects.filter(key="logo_url").first()
    if cfg is None:
        Configuration.objects.create(key="logo_url", value=logo_url, description="Logo image URL")
    else:
        cfg.value = logo_url
        cfg.save(update_fields=["value"])
    return {"status": "ok", "logo_url": logo_url, "size": len(content)}
