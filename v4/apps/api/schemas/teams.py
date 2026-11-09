"""Schemas for teams and team members (v3 ``api/schemas.py``)."""

from __future__ import annotations

from datetime import datetime

from ninja import Schema
from pydantic import AliasChoices, BaseModel, Field

from apps.api.common import JsonText


class TeamMemberCreate(BaseModel):
    name: str
    role: str
    available_hours_per_day: float = 7.0
    skills_json: str = "[]"


class TeamMemberUpdate(BaseModel):
    name: str | None = None
    role: str | None = None
    available_hours_per_day: float | None = None
    skills_json: str | None = None


class TeamMemberOut(Schema):
    id: int
    name: str
    role: str
    available_hours_per_day: float = 7.0
    skills_json: JsonText = Field("[]", validation_alias=AliasChoices("skills_json", "skills"))
    team_id: int | None = None
    linked_user_id: int | None = None
    linked_user_name: str | None = None


class TeamCreate(BaseModel):
    name: str
    description: str | None = None


class TeamUpdate(BaseModel):
    name: str | None = None
    description: str | None = None


class TeamOut(Schema):
    id: int
    name: str
    description: str | None = None
    created_at: datetime | None = None
    member_count: int = 0


class TeamMembersUpdate(BaseModel):
    member_ids: list[int] = []


class TeamMembersResult(Schema):
    status: str
    team_id: int
    member_count: int
