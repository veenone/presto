"""Human-readable document numbers (port of v3 ``_generate_number``)."""

from __future__ import annotations

from datetime import datetime

from django.db import models

from . import config


def generate_number(prefix_key: str, model: type[models.Model], default_prefix: str = "EST") -> str:
    """``<prefix>-<year>-<NNN>`` where NNN is the row count + 1 (v3 behaviour)."""
    prefix = config.get_str(prefix_key, default_prefix)
    return f"{prefix}-{datetime.now().year}-{model.objects.count() + 1:03d}"
