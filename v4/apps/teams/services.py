"""Team and team-member services (v3 ``/teams`` and ``/team-members``)."""

from __future__ import annotations

from typing import Any

from django.db import transaction
from django.db.models import Count

from apps.accounts.models import User
from apps.core.services import ServiceError

from .models import Team, TeamMember

# ── Team members ─────────────────────────────────────────


def member_out(m: TeamMember, linked: User | None = None) -> dict:
    return {
        "id": m.id, "name": m.name, "role": m.role,
        "available_hours_per_day": m.available_hours_per_day,
        "skills_json": m.skills, "team_id": m.team_id,
        "linked_user_id": linked.id if linked else None,
        "linked_user_name": (linked.display_name or linked.username) if linked else None,
    }


def list_team_members() -> list[dict]:
    linked: dict[int, User] = {}
    for u in User.objects.filter(team_member__isnull=False).order_by("id"):
        linked.setdefault(u.team_member_id, u)
    return [member_out(m, linked.get(m.id)) for m in TeamMember.objects.order_by("id")]


def _get_member(member_id: int) -> TeamMember:
    member = TeamMember.objects.filter(pk=member_id).first()
    if member is None:
        raise ServiceError(404, "Team member not found")
    return member


def create_team_member(data: dict[str, Any]) -> dict:
    return member_out(TeamMember.objects.create(**data))


def update_team_member(member_id: int, updates: dict[str, Any]) -> dict:
    member = _get_member(member_id)
    for key, val in updates.items():
        setattr(member, key, val)
    member.save()
    return member_out(member)


def delete_team_member(member_id: int) -> None:
    _get_member(member_id).delete()


# ── Teams ────────────────────────────────────────────────

def team_out(t: Team, member_count: int | None = None) -> dict:
    if member_count is None:
        member_count = TeamMember.objects.filter(team=t).count()
    return {
        "id": t.id, "name": t.name, "description": t.description,
        "created_at": t.created_at, "member_count": member_count,
    }


def list_teams() -> list[dict]:
    teams = Team.objects.annotate(n=Count("members")).order_by("id")
    return [team_out(t, t.n) for t in teams]


def _get_team(team_id: int) -> Team:
    team = Team.objects.filter(pk=team_id).first()
    if team is None:
        raise ServiceError(404, "Team not found")
    return team


def create_team(name: str, description: str | None) -> dict:
    if Team.objects.filter(name=name).exists():
        raise ServiceError(400, f"Team '{name}' already exists")
    return team_out(Team.objects.create(name=name, description=description), 0)


def update_team(team_id: int, name: str | None, description: str | None) -> dict:
    team = _get_team(team_id)
    if name is not None:
        if Team.objects.filter(name=name).exclude(pk=team_id).exists():
            raise ServiceError(400, f"Team '{name}' already exists")
        team.name = name
    if description is not None:
        team.description = description
    team.save()
    return team_out(team)


@transaction.atomic
def delete_team(team_id: int) -> None:
    team = _get_team(team_id)
    TeamMember.objects.filter(team=team).update(team=None)
    team.delete()


@transaction.atomic
def update_team_members(team_id: int, member_ids: list[int]) -> dict:
    team = _get_team(team_id)
    TeamMember.objects.filter(team=team).update(team=None)
    TeamMember.objects.filter(pk__in=member_ids).update(team=team)
    return {"status": "ok", "team_id": team_id, "member_count": len(member_ids)}
