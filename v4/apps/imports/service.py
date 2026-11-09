"""Bulk import of catalog entities from CSV/Excel (port of v3 ``imports/service.py``).

- :func:`preview_import` parses and validates a file without persisting anything.
- :func:`execute_import` parses the file and inserts valid rows, optionally
  skipping duplicates.
"""

from __future__ import annotations

from django.db import transaction

from apps.catalog.models import DutType, Feature, TaskTemplate, TestProfile
from apps.estimations.models import HistoricalProject

from .csv_importer import parse_csv, parse_excel

MODEL_MAP: dict[str, type] = {
    "features": Feature,
    "dut_types": DutType,
    "test_profiles": TestProfile,
    "task_templates": TaskTemplate,
    "historical_projects": HistoricalProject,
}

# Field that defines uniqueness for duplicate detection (None = no check).
UNIQUE_FIELDS: dict[str, str | None] = {
    "features": "name",
    "dut_types": "name",
    "test_profiles": "name",
    "task_templates": None,
    "historical_projects": None,
}


def preview_import(content: bytes, entity_type: str, filename: str) -> dict:
    """Parse and validate ``content``; ``.xlsx`` files are Excel, everything else CSV."""
    if filename.lower().endswith(".xlsx"):
        return parse_excel(content, entity_type)
    return parse_csv(content, entity_type)


def execute_import(
    content: bytes,
    entity_type: str,
    filename: str,
    skip_duplicates: bool = True,
) -> dict:
    """Insert valid rows one by one; returns ``imported``, ``skipped``, ``errors``, ``total_parsed``."""
    parsed = preview_import(content, entity_type, filename)
    result: dict = {
        "imported": 0,
        "skipped": 0,
        "errors": list(parsed["errors"]),
        "total_parsed": parsed["total_parsed"],
    }

    model_class = MODEL_MAP.get(entity_type)
    if not model_class:
        result["errors"].append(f"Unknown entity type: {entity_type}")
        return result

    unique_field = UNIQUE_FIELDS.get(entity_type)
    for row in parsed["rows"]:
        if skip_duplicates and unique_field and row.get(unique_field):
            if model_class.objects.filter(**{unique_field: row[unique_field]}).exists():
                result["skipped"] += 1
                continue
        try:
            with transaction.atomic():
                model_class.objects.create(**row)
            result["imported"] += 1
        except Exception as exc:  # one bad row must not block the batch
            result["errors"].append(f"Insert failed for {row}: {exc}")
    return result
