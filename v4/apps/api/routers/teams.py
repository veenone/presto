"""API routes: /team-members/*, /teams/*."""

from ninja import Router, Status

from apps.api.auth import authenticated, role
from apps.api.common import parse_json_text
from apps.api.schemas.teams import (
    TeamCreate,
    TeamMemberCreate,
    TeamMemberOut,
    TeamMembersResult,
    TeamMembersUpdate,
    TeamMemberUpdate,
    TeamOut,
    TeamUpdate,
)
from apps.teams import services

from .catalog import call

router = Router(auth=authenticated, tags=["teams"])


def _member_fields(data, exclude_unset: bool = False) -> dict:
    fields = data.model_dump(exclude_unset=exclude_unset)
    if "skills_json" in fields:
        fields["skills"] = parse_json_text(fields.pop("skills_json"), [])
    return fields


# ── Team members ─────────────────────────────────────────

@router.get("/team-members", response=list[TeamMemberOut])
def list_team_members(request):
    return services.list_team_members()


@router.post("/team-members", response={201: TeamMemberOut}, auth=role("ADMIN"))
def create_team_member(request, data: TeamMemberCreate):
    return Status(201, services.create_team_member(_member_fields(data)))


@router.put("/team-members/{member_id}", response=TeamMemberOut, auth=role("ADMIN"))
def update_team_member(request, member_id: int, data: TeamMemberUpdate):
    return call(services.update_team_member, member_id, _member_fields(data, exclude_unset=True))


@router.delete("/team-members/{member_id}", response={204: None}, auth=role("ADMIN"))
def delete_team_member(request, member_id: int):
    call(services.delete_team_member, member_id)
    return Status(204, None)


# ── Teams ────────────────────────────────────────────────

@router.get("/teams", response=list[TeamOut])
def list_teams(request):
    return services.list_teams()


@router.post("/teams", response={201: TeamOut}, auth=role("APPROVER"))
def create_team(request, data: TeamCreate):
    return Status(201, call(services.create_team, data.name, data.description))


@router.put("/teams/{team_id}", response=TeamOut, auth=role("APPROVER"))
def update_team(request, team_id: int, data: TeamUpdate):
    return call(services.update_team, team_id, data.name, data.description)


@router.delete("/teams/{team_id}", response={204: None}, auth=role("APPROVER"))
def delete_team(request, team_id: int):
    call(services.delete_team, team_id)
    return Status(204, None)


@router.put("/teams/{team_id}/members", response=TeamMembersResult, auth=role("APPROVER"))
def update_team_members(request, team_id: int, data: TeamMembersUpdate):
    return call(services.update_team_members, team_id, data.member_ids)
