"""API routes: /requests/*."""

from __future__ import annotations

from ninja import File, Router, UploadedFile
from ninja.errors import HttpError

from apps.accounts.audit import log_action
from apps.accounts.models import User
from apps.api.auth import authenticated, role
from apps.api.common import client_ip, get_or_404
from apps.api.schemas.inbox import (
    AssignOut,
    AttachmentUploadOut,
    ReinitOut,
    RequestCreate,
    RequestDetailOut,
    RequestInboxReinit,
    RequestOut,
    RequestUpdate,
)
from apps.inbox import services
from apps.inbox.models import Request

router = Router(auth=authenticated, tags=["inbox"])

NOT_FOUND = "Request not found"


@router.get("/requests", response=list[RequestOut])
def list_requests(request, status: str | None = None):
    return list(services.list_requests(status))


@router.post("/requests", response={201: RequestOut}, auth=role("ESTIMATOR"))
def create_request(request, data: RequestCreate):
    return 201, services.create_request(data.model_dump())


@router.post("/requests/reinit", response=ReinitOut, auth=role("ADMIN"))
def reinit_request_inbox(request, data: RequestInboxReinit):
    """Delete every request and reset the id counter (estimations are kept)."""
    token = services.REQUEST_REINIT_TOKEN
    if (data.confirm or "").strip().upper() != token:
        raise HttpError(400, f"Confirmation token mismatch. Type '{token}' to proceed.")
    deleted = services.reinit_inbox()
    log_action(
        request.auth, "REQUEST_INBOX_REINIT", resource_type="request",
        details={"deleted": deleted}, ip_address=client_ip(request),
    )
    return {"deleted": deleted, "message": f"Request inbox cleared ({deleted} removed), ID counter reset to 1."}


@router.get("/requests/{request_id}", response=RequestOut)
def get_request(request, request_id: int):
    return get_or_404(Request, request_id, NOT_FOUND)


@router.put("/requests/{request_id}", response=RequestOut, auth=role("ESTIMATOR"))
def update_request(request, request_id: int, data: RequestUpdate):
    req = get_or_404(Request, request_id, NOT_FOUND)
    return services.update_request(req, data.model_dump(exclude_unset=True))


@router.get("/requests/{request_id}/detail", response=RequestDetailOut)
def get_request_detail(request, request_id: int):
    """Request with all linked estimations."""
    from apps.api.schemas.estimations import serialize_estimation

    req = get_or_404(Request, request_id, NOT_FOUND)
    out = RequestOut.from_orm(req).model_dump()
    out["estimations"] = [serialize_estimation(e) for e in req.estimations.order_by("id")]
    return out


@router.post("/requests/{request_id}/attachments", response=AttachmentUploadOut, auth=role("ESTIMATOR"))
def upload_attachment(request, request_id: int, file: UploadedFile = File(...)):
    req = get_or_404(Request, request_id, NOT_FOUND)
    return services.save_attachment(req, file.name, file.read(), file.content_type)


@router.post("/requests/{request_id}/assign", response=AssignOut, auth=role("APPROVER"))
def assign_request(request, request_id: int, assigned_to_id: int):
    req = get_or_404(Request, request_id, NOT_FOUND)
    target_user = get_or_404(User, assigned_to_id, "Target user not found")
    services.assign_request(req, target_user)
    log_action(
        request.auth, "ASSIGN", "request", request_id,
        details={"assigned_to_id": assigned_to_id}, ip_address=client_ip(request),
    )
    return {"status": "ok", "assigned_to_id": assigned_to_id}
