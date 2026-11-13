"""Core models and field helpers shared across apps.

Every model maps onto the existing v3 table (``Meta.db_table``) with the same
column names, so a v3 database can be adopted with ``migrate --fake-initial``.
"""

from __future__ import annotations

from datetime import datetime

from django.db import models
from django.db.models.functions import Now


def created_at_field(**kwargs) -> models.DateTimeField:
    """``created_at`` as in v3: DB default CURRENT_TIMESTAMP, set in Python too."""
    return models.DateTimeField(default=datetime.now, db_default=Now(), **kwargs)


def updated_at_field(**kwargs) -> models.DateTimeField:
    return models.DateTimeField(auto_now=True, db_default=Now(), **kwargs)


class Configuration(models.Model):
    """Key-value runtime settings (v3 ``configuration`` table).

    Values are stored as strings exactly as in v3; use ``apps.core.config``
    for typed access.
    """

    key = models.CharField(max_length=255, primary_key=True)
    value = models.TextField()
    description = models.TextField(null=True, blank=True)

    class Meta:
        db_table = "configuration"
        ordering = ["key"]

    def __str__(self) -> str:
        return self.key
