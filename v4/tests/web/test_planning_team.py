"""Team Management page (/team): members, user links and teams."""

import json

import pytest

from apps.accounts.models import AuditLog, User
from apps.core.errors import ServiceError
from apps.teams.models import Team, TeamMember

pytestmark = pytest.mark.django_db
HX = {"HX-Request": "true"}
HX_RESULTS = {"HX-Request": "true", "HX-Target": "crud-results"}


def names(resp) -> list[str]:
    return [r["name"] for r in resp.context["results"].rows]


@pytest.fixture
def members(seeded):
    return [
        TeamMember.objects.create(name="Alice Martin", role="TESTER", skills=["Automation", "Security"]),
        TeamMember.objects.create(name="Bob Chen", role="TESTER", available_hours_per_day=6),
        TeamMember.objects.create(name="Dan Okafor", role="TEST_LEADER", skills=["Fuzzing"]),
    ]


@pytest.fixture
def approver(web_login, make_user, seeded):
    return web_login(make_user("APPROVER"))


class TestList:
    @pytest.mark.parametrize("role", ["VIEWER", "ESTIMATOR"])
    def test_any_user_sees_members_and_teams_read_only(self, web_login, make_user, members, role):
        Team.objects.create(name="Payments")
        resp = web_login(make_user(role)).get("/team")
        body = resp.content.decode()
        assert resp.status_code == 200 and "Team Management" in body and names(resp) == [
            "Alice Martin", "Bob Chen", "Dan Okafor"]
        assert "Payments" in body and "Add Team Member" not in body and "Create Team" not in body
        assert 'title="Edit"' not in body and "Manage</button>" not in body

    def test_approver_manages_teams_but_not_members(self, approver, members):
        Team.objects.create(name="Payments")
        body = approver.get("/team").content.decode()
        assert "Create Team" in body and "Manage</button>" in body
        assert "Add Team Member" not in body and 'title="Edit"' not in body

    def test_admin_sees_everything(self, admin_client, members):
        body = admin_client.get("/team").content.decode()
        assert "Add Team Member" in body and "Create Team" in body and 'title="Edit"' in body
        assert 'class="chip">Automation<' in body and "role-test_leader" in body
        assert "No teams created yet" in body

    def test_tiles_segments_and_search(self, admin_client, members):
        User.objects.filter(username="admin").update(team_member=members[0])
        resp = admin_client.get("/team?seg=TEST_LEADER")
        assert names(resp) == ["Dan Okafor"]
        tiles = resp.context["results"].tiles
        assert [t.label for t in tiles] == ["Total Members", "Tester", "Test Leader"]
        assert tiles[0].sub == "1 linked to a user · 20.0h/day capacity"
        assert {s["key"]: s["count"] for s in resp.context["results"].segments} == {
            "all": 3, "TESTER": 2, "TEST_LEADER": 1}
        assert names(admin_client.get("/team?q=fuzz")) == ["Dan Okafor"]
        assert names(admin_client.get("/team?q=administrator")) == ["Alice Martin"]

    def test_team_column_and_partial(self, admin_client, members):
        team = Team.objects.create(name="Payments")
        members[1].team = team
        members[1].save()
        resp = admin_client.get("/team?q=payments", headers=HX_RESULTS)
        assert names(resp) == ["Bob Chen"] and b"ed-side-nav" not in resp.content
        assert b"teams-section" not in resp.content


class TestMemberWrite:
    def test_add_form_lists_configured_skills_and_users(self, admin_client, members):
        body = admin_client.get("/team/new", headers=HX).content.decode()
        assert "Add Team Member" in body and 'value="Test Execution"' in body and "Administrator" in body

    def test_create_with_skills_and_linked_user(self, admin_client, make_user, members):
        user = make_user("ESTIMATOR", username="carla", display_name="Carla D")
        resp = admin_client.post("/team/new", {
            "name": "  Carla Diaz ", "role": "MANAGER", "available_hours_per_day": "6.5",
            "skills": ["Automation", "Performance"], "new_skills": "Fuzzing, Automation , ,Localization",
            "linked_user": str(user.pk)}, headers=HX)
        assert resp.status_code == 204
        assert json.loads(resp["HX-Trigger"])["toast"]["message"] == "Team Member created."
        m = TeamMember.objects.get(name="Carla Diaz")
        assert m.role == "MANAGER" and m.available_hours_per_day == 6.5
        assert m.skills == ["Automation", "Performance", "Fuzzing", "Localization"]
        user.refresh_from_db()
        assert user.team_member_id == m.pk
        assert AuditLog.objects.filter(action="UPDATE", resource_type="user", resource_id=user.pk).exists()

    def test_validation(self, admin_client, members):
        resp = admin_client.post("/team/new", {"name": " ", "role": "CEO", "available_hours_per_day": "30",
                                               "linked_user": "999"}, headers=HX)
        assert resp.status_code == 422
        body = resp.content.decode()
        assert "This field is required." in body and "Select a valid choice" in body
        assert "less than or equal to 24" in body and TeamMember.objects.count() == 3

    def test_edit_keeps_api_role_and_extra_skills(self, admin_client, members):
        dan = members[2]
        form = admin_client.get(f"/team/{dan.pk}/edit", headers=HX).content.decode()
        assert 'value="TEST_LEADER" selected' in form and 'value="Fuzzing"' in form and "checked" in form
        resp = admin_client.post(f"/team/{dan.pk}/edit", {
            "name": "Dan O", "role": "TEST_LEADER", "available_hours_per_day": "7", "skills": ["Fuzzing"]},
            headers=HX)
        assert resp.status_code == 204
        dan.refresh_from_db()
        assert dan.name == "Dan O" and dan.skills == ["Fuzzing"]

    def test_edit_relinks_user(self, admin_client, make_user, members):
        old = make_user("VIEWER", username="old")
        new = make_user("VIEWER", username="new")
        alice = members[0]
        User.objects.filter(pk=old.pk).update(team_member=alice)
        form = admin_client.get(f"/team/{alice.pk}/edit", headers=HX).content.decode()
        assert f'value="{old.pk}" selected' in form
        data = {"name": "Alice Martin", "role": "TESTER", "available_hours_per_day": "7", "linked_user": str(new.pk)}
        assert admin_client.post(f"/team/{alice.pk}/edit", data, headers=HX).status_code == 204
        old.refresh_from_db()
        new.refresh_from_db()
        assert old.team_member_id is None and new.team_member_id == alice.pk
        data["linked_user"] = ""
        assert admin_client.post(f"/team/{alice.pk}/edit", data, headers=HX).status_code == 204
        new.refresh_from_db()
        assert new.team_member_id is None

    def test_inactive_linked_user_stays_selectable(self, admin_client, make_user, members):
        gone = make_user("VIEWER", username="gone", is_active=False)
        User.objects.filter(pk=gone.pk).update(team_member=members[0])
        data = {"name": "Alice Martin", "role": "TESTER", "available_hours_per_day": "7", "linked_user": str(gone.pk)}
        assert admin_client.post(f"/team/{members[0].pk}/edit", data, headers=HX).status_code == 204

    def test_service_error_shown(self, admin_client, members, monkeypatch):
        def boom(member_id, updates):
            raise ServiceError(404, "Team member not found")
        monkeypatch.setattr("apps.teams.services.update_team_member", boom)
        resp = admin_client.post(f"/team/{members[0].pk}/edit", {
            "name": "A", "role": "TESTER", "available_hours_per_day": "7"}, headers=HX)
        assert resp.status_code == 422 and b"Team member not found" in resp.content

    def test_delete(self, admin_client, members):
        bob = members[1]
        assert b"Bob Chen" in admin_client.get(f"/team/{bob.pk}/delete", headers=HX).content
        resp = admin_client.post(f"/team/{bob.pk}/delete", headers=HX)
        assert resp.status_code == 204 and not TeamMember.objects.filter(pk=bob.pk).exists()
        assert admin_client.get("/team/999/edit").status_code == 404

    @pytest.mark.parametrize("role", ["VIEWER", "ESTIMATOR", "APPROVER"])
    def test_member_write_requires_admin(self, web_login, make_user, members, role):
        client = web_login(make_user(role))
        m = members[0]
        data = {"name": "x", "role": "TESTER", "available_hours_per_day": "7"}
        assert client.get("/team/new").status_code == 403
        assert client.post("/team/new", data).status_code == 403
        assert client.post(f"/team/{m.pk}/edit", data).status_code == 403
        assert client.post(f"/team/{m.pk}/delete").status_code == 403
        assert TeamMember.objects.count() == 3 and TeamMember.objects.get(pk=m.pk).name == "Alice Martin"


class TestTeams:
    def test_create_team(self, approver):
        assert b"Create Team" in approver.get("/team/teams/new", headers=HX).content
        resp = approver.post("/team/teams/new", {"name": " Payments ", "description": " cards "}, headers=HX)
        assert resp.status_code == 204
        events = json.loads(resp["HX-Trigger"])
        assert events["crud-refresh"] and events["close-modal"] and events["toast"]["message"] == "Team created."
        t = Team.objects.get()
        assert t.name == "Payments" and t.description == "cards"
        approver.post("/team/teams/new", {"name": "Telco"}, headers=HX)
        assert Team.objects.get(name="Telco").description is None

    def test_create_team_validation_and_duplicate(self, approver):
        Team.objects.create(name="Payments")
        assert approver.post("/team/teams/new", {"name": ""}, headers=HX).status_code == 422
        resp = approver.post("/team/teams/new", {"name": "Payments"}, headers=HX)
        assert resp.status_code == 422 and b"Team &#x27;Payments&#x27; already exists" in resp.content

    def test_teams_partial(self, approver, members):
        team = Team.objects.create(name="Payments", description="cards")
        TeamMember.objects.filter(pk=members[0].pk).update(team=team)
        resp = approver.get("/team/teams", headers=HX)
        body = resp.content.decode()
        assert resp.status_code == 200 and 'id="teams-section"' in body
        assert "1 member<" in body and "cards" in body and "1 team<" in body
        assert f'hx-get="/team/teams/{team.pk}/members"' in body and 'hx-get="/team/teams"' in body
        assert approver.post("/team/teams").status_code == 405

    def test_manage_members(self, approver, members):
        team = Team.objects.create(name="Payments")
        other = Team.objects.create(name="Telco")
        TeamMember.objects.filter(pk=members[0].pk).update(team=team)
        TeamMember.objects.filter(pk=members[2].pk).update(team=other)
        form = approver.get(f"/team/teams/{team.pk}/members", headers=HX).content.decode()
        assert "Manage Team: Payments" in form and "Alice Martin" in form and "Bob Chen" in form
        assert "Dan Okafor" not in form  # member of another team
        resp = approver.post(f"/team/teams/{team.pk}/members", {"member_ids": [members[1].pk]}, headers=HX)
        assert resp.status_code == 204
        assert json.loads(resp["HX-Trigger"])["toast"]["message"] == "Team members updated."
        assert list(team.members.values_list("name", flat=True)) == ["Bob Chen"]
        bad = approver.post(f"/team/teams/{team.pk}/members", {"member_ids": [members[2].pk]}, headers=HX)
        assert bad.status_code == 422 and TeamMember.objects.get(pk=members[2].pk).team_id == other.pk

    def test_delete_team_unassigns_members(self, approver, members):
        team = Team.objects.create(name="Payments")
        TeamMember.objects.filter(pk=members[0].pk).update(team=team)
        assert b"Payments" in approver.get(f"/team/teams/{team.pk}/delete", headers=HX).content
        resp = approver.post(f"/team/teams/{team.pk}/delete", headers=HX)
        assert resp.status_code == 204 and not Team.objects.exists()
        assert TeamMember.objects.get(pk=members[0].pk).team_id is None

    def test_delete_team_service_error(self, approver, monkeypatch):
        team = Team.objects.create(name="Payments")

        def boom(team_id):
            raise ServiceError(404, "Team not found")
        monkeypatch.setattr("apps.teams.services.delete_team", boom)
        resp = approver.post(f"/team/teams/{team.pk}/delete", headers=HX)
        assert resp.status_code == 422 and b"Team not found" in resp.content

    def test_unknown_team_is_404(self, approver):
        assert approver.get("/team/teams/999/members").status_code == 404
        assert approver.post("/team/teams/999/delete").status_code == 404

    @pytest.mark.parametrize("role", ["VIEWER", "ESTIMATOR"])
    def test_team_write_requires_approver(self, web_login, make_user, seeded, role):
        client = web_login(make_user(role))
        team = Team.objects.create(name="Payments")
        assert client.get("/team/teams/new").status_code == 403
        assert client.post("/team/teams/new", {"name": "X"}).status_code == 403
        assert client.post(f"/team/teams/{team.pk}/members", {"member_ids": []}).status_code == 403
        assert client.post(f"/team/teams/{team.pk}/delete").status_code == 403
        assert list(Team.objects.values_list("name", flat=True)) == ["Payments"]
