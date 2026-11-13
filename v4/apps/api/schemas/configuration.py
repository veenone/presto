"""Schemas for the configuration endpoints (v3 ``api/schemas.py``)."""

from __future__ import annotations

from ninja import Schema
from pydantic import BaseModel


class ConfigurationOut(Schema):
    key: str
    value: str
    description: str | None = None


class ConfigurationUpdate(BaseModel):
    value: str


class BrandingOut(Schema):
    logo_url: str
    logo_height: str


class LogoUploadOut(Schema):
    status: str
    logo_url: str
    size: int
