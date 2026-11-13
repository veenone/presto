"""Helpers shared by API routers and schemas."""

from __future__ import annotations

import json
from datetime import date
from typing import Annotated, Any

from django.db import models
from ninja.errors import HttpError
from pydantic import BeforeValidator


def client_ip(request) -> str | None:
    """Client address, preferring the left-most X-Forwarded-For entry (as v3)."""
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR")


def get_or_404[M: models.Model](model: type[M], pk: Any, detail: str | None = None) -> M:
    obj = model.objects.filter(pk=pk).first()
    if obj is None:
        raise HttpError(404, detail or f"{model._meta.verbose_name.capitalize()} not found")
    return obj


def _coerce_date(v: object) -> object:
    """Accept ISO date strings and treat '' as None (v3 ``OptionalDate``)."""
    if isinstance(v, str):
        v = v.strip()
        return date.fromisoformat(v) if v else None
    return v


def _to_json_text(v: object) -> object:
    """Serialise JSONField values back to the JSON text v3 returned in ``*_json`` fields."""
    if v is None or isinstance(v, str):
        return v
    return json.dumps(v)


OptionalDate = Annotated[date | None, BeforeValidator(_coerce_date)]
# For response fields named ``*_json``: accepts a Python value or JSON text, emits JSON text.
JsonText = Annotated[str, BeforeValidator(_to_json_text)]


def parse_json_text(value: str | None, default: Any) -> Any:
    """Parse a ``*_json`` request field into a Python value for a JSONField."""
    if value is None or value == "":
        return default
    try:
        return json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return default
