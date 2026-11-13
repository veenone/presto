"""Exceptions that service code raises for HTTP-mappable failures."""

from __future__ import annotations


class ServiceError(Exception):
    """A business-rule failure carrying the HTTP status and the v3 message.

    Services never import ninja; ``apps.api.api`` turns this into
    ``{"detail": ...}`` with ``status_code`` (and HTML views can do the same).
    """

    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail
