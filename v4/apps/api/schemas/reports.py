"""Schemas for the report endpoints (downloads return files; only send-report returns JSON)."""

from ninja import Schema


class SendReportOut(Schema):
    status: str
    message: str
