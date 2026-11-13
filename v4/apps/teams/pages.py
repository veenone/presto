"""Team Management page (v3 ``pages/team.py``): team members and teams.

Members: list + add/edit/delete through apps.web.crud; writes need ADMIN like
``/api/team-members``. Linking a member to a user account needs ADMIN too (v3
did it with ``PUT /api/users/{id}``). Teams: create, manage membership and
delete; writes need APPROVER like ``/api/teams``.
"""

from __future__ import annotations

from django import forms
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.http import Http404
from django.shortcuts import render
from django.urls import path as url_path
from django.urls import reverse
from django.views.decorators.http import require_GET, require_http_methods

from apps.accounts import services as account_services
from apps.accounts.audit import log_action
from apps.accounts.models import User
from apps.accounts.roles import has_role
from apps.api.common import client_ip
from apps.core import services as core_services
from apps.core.errors import ServiceError
from apps.teams import services
from apps.teams.models import Team, TeamMember
from apps.web.crud import Column, CrudPage, Segment, Tile
from apps.web.forms import StyledForm

ROLES = ["TESTER", "LEADER", "MANAGER"]
TEAM_WRITE_ROLE = "APPROVER"  # same as POST/PUT/DELETE /api/teams


def _user_label(u: User) -> str:
    return u.display_name or u.username


class ChipCheckboxes(forms.CheckboxSelectMultiple):
    """Checkbox list rendered as toggle chips (see planning/_chips.html)."""


class TeamMemberForm(StyledForm):
    name = forms.CharField(max_length=255)
    role = forms.ChoiceField(initial="TESTER")
    available_hours_per_day = forms.FloatField(
        min_value=0.0, max_value=24.0, initial=7.0, label="Available Hours / Day",
        widget=forms.NumberInput(attrs={"step": "0.5"}))
    skills = forms.MultipleChoiceField(required=False, widget=ChipCheckboxes)
    new_skills = forms.CharField(required=False, max_length=500, label="Add other skills",
                                 help_text="Comma separated, e.g. Fuzzing, Localization")
    linked_user = forms.ChoiceField(required=False, label="Linked User (optional)")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        initial = self.initial or {}
        roles = list(ROLES)
        if initial.get("role") and initial["role"] not in roles:
            roles.append(initial["role"])  # keep roles written by the API (e.g. TEST_LEADER)
        self.fields["role"].choices = [(r, r) for r in roles]

        skills = list(core_services.team_skills())
        for s in initial.get("skills") or []:
            if s not in skills:
                skills.append(s)
        # Skills typed in a re-rendered form stay selectable.
        posted = self.data.getlist("skills") if hasattr(self.data, "getlist") else []
        skills += [s for s in posted if s not in skills]
        self.fields["skills"].choices = [(s, s) for s in skills]
        self.fields["skills"].widget.attrs["class"] = "chip-input"

        users = sorted(account_services.list_assignable_users(), key=lambda u: _user_label(u).lower())
        choices = [("", "(None)")] + [(str(u.pk), _user_label(u)) for u in users]
        current = str(initial.get("linked_user") or "")
        if current and current not in dict(choices):
            linked = User.objects.filter(pk=current).first()  # an inactive user stays selectable
            if linked is not None:
                choices.append((current, _user_label(linked)))
        self.fields["linked_user"].choices = choices

    def clean_name(self):
        name = self.cleaned_data["name"].strip()
        if not name:
            raise forms.ValidationError("Name is required.")
        return name

    def clean(self):
        data = super().clean()
        skills = list(data.get("skills") or [])
        for s in (data.get("new_skills") or "").split(","):
            s = s.strip()
            if s and s not in skills:
                skills.append(s)
        data["skills"] = skills
        return data


class TeamForm(StyledForm):
    name = forms.CharField(max_length=255, label="Team Name")
    description = forms.CharField(required=False, widget=forms.Textarea(attrs={"rows": 3}))

    def clean_name(self):
        name = self.cleaned_data["name"].strip()
        if not name:
            raise forms.ValidationError("Team name is required.")
        return name


class TeamMembersForm(StyledForm):
    member_ids = forms.TypedMultipleChoiceField(coerce=int, required=False, label="Team Members",
                                                widget=ChipCheckboxes)

    def __init__(self, *args, team: Team, **kwargs):
        super().__init__(*args, **kwargs)
        available = TeamMember.objects.filter(team__isnull=True) | TeamMember.objects.filter(team=team)
        self.fields["member_ids"].choices = [(m.pk, m.name) for m in available.order_by("name")]
        self.fields["member_ids"].widget.attrs["class"] = "chip-input"


class TeamPage(CrudPage):
    url_name = "team"
    title = "Team Management"
    noun = "Team Member"
    empty_text = "No team members yet"
    write_role = "ADMIN"  # same as POST/PUT/DELETE /api/team-members
    form_class = TeamMemberForm
    template_name = "planning/team/page.html"
    results_template = "planning/_results.html"
    form_template = "planning/team/_member_form.html"
    search_placeholder = "Search by name, role, skills, linked user…"
    columns = [
        Column("name", "Name", sortable=True, kind="strong"),
        Column("role", "Role", sortable=True, kind="role"),
        Column("available_hours_per_day", "Hrs/Day", sortable=True, kind="mono", align="right", fmt="{:g}"),
        Column("skills", "Skills", kind="chips"),
        Column("team_name", "Team", sortable=True),
        Column("linked_user_name", "Linked User", sortable=True),
    ]
    search_fields = ["name", "role", "skills_text", "linked_user_name", "team_name", "id"]

    # -- list -----------------------------------------------------------------
    def get_rows(self, request):
        teams = dict(Team.objects.values_list("id", "name"))
        rows = []
        for m in services.list_team_members():
            skills = m["skills_json"] if isinstance(m["skills_json"], list) else []
            rows.append({
                "id": m["id"], "name": m["name"], "role": m["role"],
                "available_hours_per_day": m["available_hours_per_day"],
                "skills": skills, "skills_text": ", ".join(str(s) for s in skills),
                "team_id": m["team_id"], "team_name": teams.get(m["team_id"]),
                "linked_user_id": m["linked_user_id"], "linked_user_name": m["linked_user_name"],
            })
        return rows

    def _roles(self, rows):
        present = {r["role"] for r in rows if r["role"]}
        return [r for r in ROLES if r in present] + sorted(present - set(ROLES))

    def get_segments(self, rows):
        return [Segment("all", "All")] + [
            Segment(role, role.replace("_", " ").title(), lambda r, role=role: r["role"] == role)
            for role in self._roles(rows)
        ]

    def get_tiles(self, rows):
        hours = sum(r["available_hours_per_day"] or 0 for r in rows)
        linked = sum(1 for r in rows if r["linked_user_name"])
        tiles = [Tile("Total Members", str(len(rows)),
                      f"{linked} linked to a user · {hours:.1f}h/day capacity", "all")]
        for role in self._roles(rows):
            n = sum(1 for r in rows if r["role"] == role)
            tiles.append(Tile(role.replace("_", " ").title(), str(n), "filter to role", role))
        return tiles

    def extra_context(self, request):
        ctx = {"can_manage_teams": has_role(request.user.role, TEAM_WRITE_ROLE)}
        match = getattr(request, "resolver_match", None)
        if match is not None and match.url_name == self.url_name:  # full list page: teams section too
            ctx["teams"] = services.list_teams()
        return ctx

    # -- members --------------------------------------------------------------
    def _get(self, obj_id: int) -> TeamMember:
        obj = TeamMember.objects.filter(pk=obj_id).first()
        if obj is None:
            raise Http404
        return obj

    @staticmethod
    def _linked_user_id(member_id: int) -> int | None:
        return User.objects.filter(team_member_id=member_id).order_by("id").values_list("id", flat=True).first()

    def get_initial(self, request, obj_id):
        m = self._get(obj_id)
        linked = self._linked_user_id(obj_id)
        return {"name": m.name, "role": m.role, "available_hours_per_day": m.available_hours_per_day,
                "skills": list(m.skills or []), "linked_user": str(linked) if linked else ""}

    def object_label(self, request, obj_id):
        return f"team member '{self._get(obj_id).name}'"

    @staticmethod
    def _payload(data: dict) -> dict:
        return {"name": data["name"], "role": data["role"],
                "available_hours_per_day": data["available_hours_per_day"], "skills": data["skills"]}

    def _set_user(self, request, user_id: int, member_id: int | None) -> None:
        """Same change as v3's ``PUT /api/users/{id}`` with ``team_member_id`` (audited)."""
        if account_services.update_user(user_id, team_member_id=member_id) is not None:
            log_action(request.user, "UPDATE", "user", user_id, ip_address=client_ip(request))

    def _link(self, request, member_id: int, old: int | None, new_value: str) -> None:
        new = int(new_value) if new_value else None
        if new == old:
            return
        if old:
            self._set_user(request, old, None)
        if new:
            self._set_user(request, new, member_id)

    def create(self, request, data):
        with transaction.atomic():
            member = services.create_team_member(self._payload(data))
            self._link(request, member["id"], None, data.get("linked_user"))
        return member

    def update(self, request, obj_id, data):
        self._get(obj_id)
        with transaction.atomic():
            member = services.update_team_member(obj_id, self._payload(data))
            self._link(request, obj_id, self._linked_user_id(obj_id), data.get("linked_user"))
        return member

    def delete(self, request, obj_id):
        self._get(obj_id)
        services.delete_team_member(obj_id)

    # -- teams ----------------------------------------------------------------
    def check_team_write(self, request) -> None:
        self.check_view(request)
        if not has_role(request.user.role, TEAM_WRITE_ROLE):
            raise PermissionDenied

    @staticmethod
    def _team(team_id: int) -> Team:
        team = Team.objects.filter(pk=team_id).first()
        if team is None:
            raise Http404
        return team

    def teams_view(self, request):
        self.check_view(request)
        return render(request, "planning/team/_teams.html",
                      self.context(request, teams=services.list_teams(), list_path=reverse(self.url_name)))

    def team_new_view(self, request):
        self.check_team_write(request)
        ctx = {"action": request.path, "heading": "Create Team", "submit_label": "Create"}
        if request.method == "POST":
            form = TeamForm(data=request.POST)
            if form.is_valid():
                try:
                    services.create_team(form.cleaned_data["name"], form.cleaned_data["description"].strip() or None)
                except ServiceError as exc:
                    form.add_error(None, exc.detail)
                else:
                    return self._saved("Team created.")
            return render(request, "planning/team/_team_form.html", self.context(request, form=form, **ctx),
                          status=422)
        return render(request, "planning/team/_team_form.html", self.context(request, form=TeamForm(), **ctx))

    def team_members_view(self, request, team_id: int):
        self.check_team_write(request)
        team = self._team(team_id)
        ctx = {"action": request.path, "heading": f"Manage Team: {team.name}", "submit_label": "Save"}
        if request.method == "POST":
            form = TeamMembersForm(data=request.POST, team=team)
            if form.is_valid():
                try:
                    services.update_team_members(team_id, form.cleaned_data["member_ids"])
                except ServiceError as exc:
                    form.add_error(None, exc.detail)
                else:
                    return self._saved("Team members updated.")
            return render(request, "planning/team/_team_form.html", self.context(request, form=form, **ctx),
                          status=422)
        initial = {"member_ids": list(team.members.values_list("pk", flat=True))}
        form = TeamMembersForm(initial=initial, team=team)
        return render(request, "planning/team/_team_form.html", self.context(request, form=form, **ctx))

    def team_delete_view(self, request, team_id: int):
        self.check_team_write(request)
        team = self._team(team_id)
        ctx = {"label": f"team '{team.name}'", "action": request.path, "noun": "Team",
               "note": "Its members are kept and become unassigned."}
        if request.method == "POST":
            try:
                services.delete_team(team_id)
            except ServiceError as exc:
                return render(request, "planning/_confirm_delete.html",
                              self.context(request, error=exc.detail, **ctx), status=422)
            return self._saved("Team deleted.")
        return render(request, "planning/_confirm_delete.html", self.context(request, **ctx))

    @classmethod
    def urls(cls, prefix: str) -> list:
        page = cls()
        prefix = prefix.strip("/")
        both = require_http_methods(["GET", "POST"])
        return [
            *super().urls(prefix),
            url_path(f"{prefix}/teams", require_GET(page.teams_view), name="team_teams"),
            url_path(f"{prefix}/teams/new", both(page.team_new_view), name="team_team_new"),
            url_path(f"{prefix}/teams/<int:team_id>/members", both(page.team_members_view),
                     name="team_team_members"),
            url_path(f"{prefix}/teams/<int:team_id>/delete", both(page.team_delete_view), name="team_team_delete"),
        ]
