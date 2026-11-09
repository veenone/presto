"""Schemas for public holidays and working weeks (v3 ``api/schemas.py``)."""

from __future__ import annotations

import datetime as dt

from ninja import Schema
from pydantic import BaseModel

from apps.api.common import OptionalDate


class PublicHolidayCreate(BaseModel):
    date: dt.date
    name: str
    country: str = ""
    is_recurring: bool = False


class PublicHolidayOut(Schema):
    id: int
    date: dt.date
    name: str
    country: str = ""
    is_recurring: bool = False


class PublicHolidayUpdate(BaseModel):
    date: OptionalDate = None
    name: str | None = None
    country: str | None = None
    is_recurring: bool | None = None


class IcsImportOut(Schema):
    imported: int
    skipped: int
    errors: list[str]


class DeletedOut(Schema):
    detail: str
