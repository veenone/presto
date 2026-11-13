"""Schemas for /notifications/* (v3 ``WebhookNotificationOut`` and ``UnreadCountOut``)."""

from __future__ import annotations

from datetime import datetime

from ninja import Schema


class WebhookNotificationOut(Schema):
    id: int
    user_id: int
    title: str
    message: str = ""
    source: str = "REDMINE"
    request_id: int | None = None
    is_read: bool = False
    created_at: datetime | None = None


class UnreadCountOut(Schema):
    unread_count: int


class OkOut(Schema):
    ok: bool


class EmailTestOut(Schema):
    success: bool
    message: str
