"""Configuration backup and restore (port of v3 ``/admin/backup`` and ``/admin/restore``).

The file format is the v3 one: table names as keys and v3 column names per row,
with JSON columns as JSON text (``skills_json``). v3 backups therefore restore
unchanged, which makes this a migration path from v3.
"""

from __future__ import annotations

import json
from datetime import date, datetime
from typing import Any

from django.core.management.color import no_style
from django.db import connection, models, transaction

from apps.catalog.models import (
    DocumentType,
    DutType,
    Feature,
    PublicHoliday,
    RiskItem,
    TaskTemplate,
    TaskTemplateFeature,
    TestProfile,
)
from apps.teams.models import Team, TeamMember

# Version written into backups. v3 wrote "3.4.0"; the format is unchanged.
BACKUP_FORMAT_VERSION = "3.4.0"

# Ordered by FK dependencies (parents before children), as in v3.
BACKUP_TABLES: list[tuple[str, type[models.Model]]] = [
    ("features", Feature),
    ("task_templates", TaskTemplate),
    ("task_template_features", TaskTemplateFeature),
    ("dut_types", DutType),
    ("test_profiles", TestProfile),
    ("teams", Team),
    ("team_members", TeamMember),
    ("risk_items", RiskItem),
    ("document_types", DocumentType),
    ("public_holidays", PublicHoliday),
]


class RestoreError(Exception):
    """Restore failed; nothing was changed."""


def _columns(model: type[models.Model]) -> list[models.Field]:
    """Concrete fields that map to a table column (skips a composite pk)."""
    return [f for f in model._meta.concrete_fields if f.column]


def row_to_dict(obj: models.Model) -> dict[str, Any]:
    """One row as v3 serialised it: column names, ISO dates, JSON columns as text."""
    out: dict[str, Any] = {}
    for f in _columns(obj.__class__):
        val = getattr(obj, f.attname)
        if isinstance(f, models.JSONField):
            val = None if val is None else json.dumps(val)
        elif isinstance(val, (date, datetime)):
            val = val.isoformat()
        out[f.column] = val
    return out


def build_backup() -> tuple[dict, dict[str, int]]:
    """Return (backup document, row count per table)."""
    tables: dict[str, list[dict]] = {}
    counts: dict[str, int] = {}
    for name, model in BACKUP_TABLES:
        rows = [row_to_dict(r) for r in model.objects.all().order_by(*_order(model))]
        tables[name] = rows
        counts[name] = len(rows)
    backup = {
        "version": BACKUP_FORMAT_VERSION,
        "timestamp": datetime.utcnow().isoformat(),
        "tables": tables,
    }
    return backup, counts


def _order(model: type[models.Model]) -> list[str]:
    if model is TaskTemplateFeature:
        return ["task_template_id", "feature_id"]
    return ["pk"]


def _convert(field: models.Field, value: Any) -> Any:
    """Turn a v3 backup value into what the Django field expects."""
    if value is None:
        return None
    if isinstance(field, models.JSONField):
        if isinstance(value, str):
            try:
                return json.loads(value)
            except json.JSONDecodeError:
                return field.get_default()
        return value
    if isinstance(field, models.DateTimeField):
        if isinstance(value, str):
            try:
                return datetime.fromisoformat(value)
            except ValueError:
                return None
        return value
    if isinstance(field, models.DateField):
        if isinstance(value, str):
            try:
                return date.fromisoformat(value[:10])
            except ValueError:
                return None
        return value
    return value


def _build_instance(model: type[models.Model], row: dict[str, Any]) -> models.Model:
    kwargs: dict[str, Any] = {}
    for f in _columns(model):
        # Accept the v3 column name, and the Django field name as a fallback.
        if f.column in row:
            raw = row[f.column]
        elif f.attname in row:
            raw = row[f.attname]
        elif f.name in row:
            raw = row[f.name]
        else:
            continue
        value = _convert(f, raw)
        if value is None and not f.null and f.has_default():
            continue  # let the model default fill a NOT NULL column
        if f.is_relation and value is not None and f.null:
            # Older backups omit parent tables (e.g. ``teams``): drop dangling
            # nullable references instead of failing the whole restore (v3).
            if not f.related_model._default_manager.filter(pk=value).exists():
                value = None
        kwargs[f.attname] = value
    return model(**kwargs)


def _reset_sequences(models_list: list[type[models.Model]]) -> None:
    sql = connection.ops.sequence_reset_sql(no_style(), models_list)
    if sql:
        with connection.cursor() as cursor:
            for stmt in sql:
                cursor.execute(stmt)


def restore_backup(backup: Any) -> tuple[list[str], dict[str, int]]:
    """Replace the backed-up tables present in ``backup``.

    Returns (tables restored, rows per table). Raises ``ValueError`` for a
    malformed document and ``RestoreError`` when the data cannot be inserted.
    """
    if not isinstance(backup, dict) or "tables" not in backup:
        raise ValueError("Invalid backup format: missing 'tables' key")
    tables_map = backup["tables"]
    if not isinstance(tables_map, dict):
        raise ValueError("Invalid backup format: missing 'tables' key")

    restored: list[str] = []
    counts: dict[str, int] = {}
    try:
        with transaction.atomic():
            for name, model in reversed(BACKUP_TABLES):
                if name in tables_map:
                    model.objects.all().delete()
            touched: list[type[models.Model]] = []
            for name, model in BACKUP_TABLES:
                if name not in tables_map:
                    continue
                rows = tables_map[name] or []
                for row in rows:
                    # Saved one by one so FK checks see rows inserted earlier.
                    _build_instance(model, row).save(force_insert=True)
                restored.append(name)
                counts[name] = len(rows)
                touched.append(model)
            _reset_sequences([m for m in touched if m is not TaskTemplateFeature])
    except Exception as exc:
        raise RestoreError(str(exc)) from exc
    return restored, counts
