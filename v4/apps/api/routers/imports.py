"""API routes: /import/*."""

from __future__ import annotations

from ninja import File, Router, UploadedFile

from apps.accounts.audit import log_action
from apps.api.auth import authenticated, role
from apps.api.common import client_ip
from apps.api.schemas.imports import ImportPreviewOut, ImportResultOut
from apps.imports import service

router = Router(auth=authenticated, tags=["imports"])


@router.post("/import/{entity_type}/preview", response=ImportPreviewOut, auth=role("ESTIMATOR"))
def preview_import_endpoint(request, entity_type: str, file: File[UploadedFile]):
    return service.preview_import(file.read(), entity_type, file.name or "data.csv")


@router.post("/import/{entity_type}", response=ImportResultOut, auth=role("APPROVER"))
def execute_import_endpoint(
    request, entity_type: str, file: File[UploadedFile], skip_duplicates: bool = True,
):
    result = service.execute_import(file.read(), entity_type, file.name or "data.csv", skip_duplicates)
    log_action(
        request.auth, "IMPORT", entity_type,
        details={"imported": result["imported"], "skipped": result["skipped"]},
        ip_address=client_ip(request),
    )
    return result
