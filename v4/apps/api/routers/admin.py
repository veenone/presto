"""API routes: /admin/backup, /admin/restore."""

from __future__ import annotations

import json
from datetime import UTC, datetime

from django.http import HttpResponse
from ninja import File, Router, UploadedFile
from ninja.errors import HttpError

from apps.accounts.audit import log_action
from apps.api.auth import authenticated, role
from apps.api.common import client_ip
from apps.api.schemas.admin import RestoreResult
from apps.core import backup as backup_service

router = Router(auth=authenticated, tags=["admin"])


@router.get("/admin/backup", auth=role("ADMIN"))
def admin_backup(request):
    """Export the configuration tables as a downloadable JSON file (v3 format)."""
    document, counts = backup_service.build_backup()
    log_action(
        request.auth, "BACKUP", resource_type="system",
        details={"tables": counts}, ip_address=client_ip(request),
    )
    stamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    resp = HttpResponse(json.dumps(document, indent=2, default=str), content_type="application/json")
    resp["Content-Disposition"] = f'attachment; filename="presto_backup_{stamp}.json"'
    return resp


@router.post("/admin/restore", response=RestoreResult, auth=role("ADMIN"))
def admin_restore(request, file: File[UploadedFile]):
    """Restore the configuration tables from a backup file (v3 or v4)."""
    try:
        document = json.loads(file.read())
    except Exception as exc:
        raise HttpError(400, f"Invalid JSON file: {exc}") from exc
    try:
        tables_restored, rows_restored = backup_service.restore_backup(document)
    except ValueError as exc:
        raise HttpError(400, str(exc)) from exc
    except backup_service.RestoreError as exc:
        raise HttpError(500, f"Restore failed: {exc}") from exc
    log_action(
        request.auth, "RESTORE", resource_type="system",
        details={"tables_restored": tables_restored, "rows_restored": rows_restored},
        ip_address=client_ip(request),
    )
    return {"status": "ok", "tables_restored": tables_restored, "rows_restored": rows_restored}
