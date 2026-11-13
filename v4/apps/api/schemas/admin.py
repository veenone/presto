"""Schemas for /admin/backup and /admin/restore (v3 ``BackupMetadata``/``RestoreResult``)."""

from __future__ import annotations

from ninja import Schema


class BackupMetadata(Schema):
    version: str
    timestamp: str
    tables: dict[str, int]


class RestoreResult(Schema):
    status: str
    tables_restored: list[str]
    rows_restored: dict[str, int]
