"""Schemas for /import/* (v3 returned the importer dicts without a response model)."""

from __future__ import annotations

from typing import Any

from ninja import Schema


class ImportPreviewOut(Schema):
    rows: list[dict[str, Any]] = []
    errors: list[str] = []
    total_parsed: int = 0
    valid_count: int = 0


class ImportResultOut(Schema):
    imported: int = 0
    skipped: int = 0
    errors: list[str] = []
    total_parsed: int = 0
