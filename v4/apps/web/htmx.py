"""Small helpers for htmx requests and responses."""

from __future__ import annotations

import json

from django.http import HttpResponse


def is_htmx(request) -> bool:
    return request.headers.get("HX-Request") == "true"


def trigger(response: HttpResponse, **events) -> HttpResponse:
    """Add ``HX-Trigger`` events, e.g. ``trigger(resp, toast={"message": "Saved"})``."""
    existing = response.get("HX-Trigger")
    payload = json.loads(existing) if existing else {}
    payload.update(events)
    response["HX-Trigger"] = json.dumps(payload)
    return response


def toast(response: HttpResponse, message: str, level: str = "success") -> HttpResponse:
    return trigger(response, toast={"message": message, "level": level})
