"""Request inbox business logic (ports of the v3 /requests route bodies)."""

from __future__ import annotations

import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Any

from django.conf import settings
from django.db import connection, transaction

from apps.accounts.models import User

from .models import Request

logger = logging.getLogger(__name__)

REQUEST_REINIT_TOKEN = "REINITIALIZE"


def list_requests(status: str | None = None):
    qs = Request.objects.select_related("assigned_to")
    if status:
        qs = qs.filter(status=status)
    return qs.order_by("-created_at", "-id")


def create_request(data: dict[str, Any]) -> Request:
    return Request.objects.create(**data)


def update_request(req: Request, changes: dict[str, Any]) -> Request:
    for key, val in changes.items():
        setattr(req, key, val)
    req.save()
    return req


def _reset_request_sequence() -> None:
    """Restart request ids at 1 (v3 reset sqlite_sequence / AUTO_INCREMENT)."""
    vendor = connection.vendor
    try:
        with transaction.atomic(), connection.cursor() as cursor:
            if vendor == "sqlite":
                cursor.execute("DELETE FROM sqlite_sequence WHERE name='requests'")
            elif vendor == "mysql":
                cursor.execute("ALTER TABLE requests AUTO_INCREMENT = 1")
            elif vendor == "postgresql":
                cursor.execute("SELECT setval(pg_get_serial_sequence('requests', 'id'), 1, false)")
    except Exception:  # best effort, as in v3
        logger.warning("Could not reset the requests id counter", exc_info=True)


def reinit_inbox() -> int:
    """Delete every request and reset the id counter. Estimations keep their rows (FK SET NULL)."""
    with transaction.atomic():
        _, per_model = Request.objects.all().delete()
        _reset_request_sequence()
    return per_model.get(Request._meta.label, 0)


def save_attachment(req: Request, filename: str, content: bytes, content_type: str | None) -> dict:
    """Store ``content`` under ATTACHMENTS_DIR/<request number>/ and record it on the request."""
    safe_name = os.path.basename((filename or "").replace("\\", "/")) or "attachment"
    root = Path(settings.ATTACHMENTS_DIR).resolve()
    attach_dir = (root / req.request_number).resolve()
    if root not in attach_dir.parents:  # a request number like "../x" must not escape the root
        attach_dir = root / req.request_number.replace("/", "_").replace("\\", "_").replace("..", "_")
    attach_dir.mkdir(parents=True, exist_ok=True)
    filepath = attach_dir / safe_name
    filepath.write_bytes(content)

    attachments = list(req.attachments or [])
    attachments.append({
        "filename": safe_name,
        "filepath": str(filepath),
        "file_size_bytes": len(content),
        "mime_type": content_type or "application/octet-stream",
        "uploaded_at": datetime.now().isoformat(),
        "source": "MANUAL",
        "external_url": None,
    })
    req.attachments = attachments
    req.save(update_fields=["attachments", "updated_at"])
    return {"status": "ok", "filename": safe_name, "size": len(content)}


def assign_request(req: Request, target_user: User) -> None:
    """Assign locally, then push the assignee to Redmine for Redmine requests (best effort)."""
    req.assigned_to = target_user
    req.save(update_fields=["assigned_to", "updated_at"])

    if req.request_source == "REDMINE" and req.external_id:
        try:
            from apps.integrations.service import get_adapter

            adapter = get_adapter("REDMINE")
            if adapter and hasattr(adapter, "update_assignee"):
                adapter.update_assignee(req.external_id, target_user.username)
        except Exception:  # never break the local assignment
            logger.warning("Redmine assignee sync failed", exc_info=True)


# ── Helpers for the web pages (no change to the API behaviour above) ──

def next_request_number() -> str:
    """Next free ``<request_number_prefix>-<year>-<NNN>`` number (v3 numbering, skipping taken ones)."""
    from apps.integrations.service import generate_request_number

    number = generate_request_number()
    if not Request.objects.filter(request_number=number).exists():
        return number
    head, _, tail = number.rpartition("-")
    n = int(tail) if tail.isdigit() else Request.objects.count() + 1
    while True:
        n += 1
        candidate = f"{head}-{n:03d}"
        if not Request.objects.filter(request_number=candidate).exists():
            return candidate


def create_manual_request(data: dict[str, Any]) -> Request:
    """``create_request`` for the inbox dialog: a blank number gets the next one; duplicates are refused."""
    from apps.core.errors import ServiceError

    data = dict(data)
    number = (data.get("request_number") or "").strip()
    if not number:
        number = next_request_number()
    elif Request.objects.filter(request_number=number).exists():
        raise ServiceError(400, f"Request number '{number}' already exists.")
    data["request_number"] = number
    return create_request(data)


def attachment_file(req: Request, index: int) -> tuple[dict, Path] | None:
    """The ``index``-th stored attachment and its file, only when the file lies under ATTACHMENTS_DIR."""
    items = req.attachments if isinstance(req.attachments, list) else []
    if index < 0 or index >= len(items) or not isinstance(items[index], dict):
        return None
    att = items[index]
    root = Path(settings.ATTACHMENTS_DIR).resolve()
    candidates = []
    if att.get("filepath"):
        candidates.append(Path(str(att["filepath"])))
    name = os.path.basename(str(att.get("filename") or "").replace("\\", "/"))
    if name:
        candidates.append(root / req.request_number / name)
    for candidate in candidates:
        try:
            resolved = candidate.resolve()
        except (OSError, ValueError):
            continue
        if root in resolved.parents and resolved.is_file():
            return att, resolved
    return None
