"""Schemas for /integrations/*, /webhooks/redmine and estimation export (v3 routes.py classes)."""

from __future__ import annotations

import json
from datetime import datetime

from ninja import Schema
from pydantic import BaseModel


class IntegrationConfigOut(Schema):
    id: int
    system_name: str
    base_url: str | None = None
    username: str | None = None
    additional_config_json: str = "{}"
    enabled: bool = False
    last_sync_at: datetime | None = None
    has_api_key: bool = False


def integration_out(row) -> dict:
    """v3 ``IntegrationConfigOut.from_model`` (the API key itself is never returned)."""
    additional = row.additional_config
    if isinstance(additional, str):
        text = additional or "{}"
    else:
        text = json.dumps(additional) if additional else "{}"
    return IntegrationConfigOut(
        id=row.id,
        system_name=row.system_name,
        base_url=row.base_url,
        username=row.username,
        additional_config_json=text,
        enabled=row.enabled,
        last_sync_at=row.last_sync_at,
        has_api_key=bool(row.api_key),
    ).model_dump()


class IntegrationConfigUpdate(BaseModel):
    base_url: str | None = None
    api_key: str | None = None
    username: str | None = None
    additional_config_json: str | None = None
    enabled: bool | None = None


class SyncResultOut(Schema):
    system: str
    direction: str
    status: str
    items_processed: int = 0
    items_created: int = 0
    items_updated: int = 0
    items_failed: int = 0
    errors: list[str] = []


class ConnectionTestOut(Schema):
    success: bool
    message: str
    details: dict = {}


class ExportTasksInput(BaseModel):
    target_system: str | None = None
    issue_key: str | None = None
