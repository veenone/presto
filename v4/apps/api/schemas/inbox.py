"""Schemas for /requests/* (v3 ``RequestBase``/``RequestCreate``/``RequestUpdate``/``RequestOut``)."""

from __future__ import annotations

from datetime import date, datetime

from ninja import Schema
from pydantic import AliasChoices, BaseModel, Field

from apps.api.common import JsonText
from apps.api.schemas.estimations import EstimationOut


class RequestBase(BaseModel):
    request_number: str
    request_source: str = "MANUAL"
    external_id: str | None = None
    title: str
    description: str | None = None
    requester_name: str
    requester_email: str | None = None
    business_unit: str | None = None
    priority: str = "MEDIUM"
    requested_delivery_date: date | None = None
    received_date: date
    notes: str | None = None
    product_type: str | None = None


class RequestCreate(RequestBase):
    pass


class RequestUpdate(BaseModel):
    title: str | None = None
    description: str | None = None
    requester_name: str | None = None
    requester_email: str | None = None
    business_unit: str | None = None
    priority: str | None = None
    status: str | None = None
    requested_delivery_date: date | None = None
    notes: str | None = None
    product_type: str | None = None


class RequestInboxReinit(BaseModel):
    """Confirmation payload for the destructive request-inbox reinitialize."""

    confirm: str


class RequestOut(Schema):
    id: int
    request_number: str
    request_source: str = "MANUAL"
    external_id: str | None = None
    title: str
    description: str | None = None
    requester_name: str
    requester_email: str | None = None
    business_unit: str | None = None
    priority: str = "MEDIUM"
    requested_delivery_date: date | None = None
    received_date: date
    notes: str | None = None
    product_type: str | None = None
    status: str
    attachments_json: JsonText = Field("[]", validation_alias=AliasChoices("attachments_json", "attachments"))
    assigned_to_id: int | None = None
    assigned_to_name: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None

    @staticmethod
    def resolve_assigned_to_name(obj) -> str | None:
        if isinstance(obj, dict):
            return obj.get("assigned_to_name")
        user = obj.assigned_to
        return (user.display_name or user.username) if user is not None else None


class RequestDetailOut(RequestOut):
    """Request with its linked estimations."""

    estimations: list[EstimationOut] = []


class ReinitOut(Schema):
    deleted: int
    message: str


class AttachmentUploadOut(Schema):
    status: str
    filename: str
    size: int


class AssignOut(Schema):
    status: str
    assigned_to_id: int
