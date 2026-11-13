"""Team and team-member endpoints."""

from __future__ import annotations

from apps.teams.models import TeamMember

# ── Ported from v3 TestTeamMemberEndpoints ───────────────


class TestTeamMemberEndpoints:
    def test_create_and_list(self, api):
        api.post(
            "/api/team-members", json={"name": "Alice", "role": "TESTER", "available_hours_per_day": 7.0}
        )
        resp = api.get("/api/team-members")
        assert resp.status_code == 200
        assert len(resp.json()) >= 1


class TestTeamMembers:
    def test_skills_json_roundtrip(self, api):
        resp = api.post(
            "/api/team-members",
            json={"name": "Bob", "role": "LEADER", "skills_json": '["Automation", "API Testing"]'},
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["skills_json"] == '["Automation", "API Testing"]'
        assert TeamMember.objects.get(pk=data["id"]).skills == ["Automation", "API Testing"]

    def test_linked_user(self, api, make_user):
        mid = api.post("/api/team-members", json={"name": "Carol", "role": "TESTER"}).json()["id"]
        make_user("ESTIMATOR", username="carol", display_name="Carol D", team_member_id=mid)
        member = next(m for m in api.get("/api/team-members").json() if m["id"] == mid)
        assert member["linked_user_name"] == "Carol D"
        assert member["linked_user_id"] is not None

    def test_update_and_delete(self, api):
        mid = api.post("/api/team-members", json={"name": "Dan", "role": "TESTER"}).json()["id"]
        resp = api.put(
            f"/api/team-members/{mid}", json={"available_hours_per_day": 6, "skills_json": '["X"]'}
        )
        assert resp.status_code == 200
        assert resp.json()["available_hours_per_day"] == 6
        assert resp.json()["skills_json"] == '["X"]'
        assert api.delete(f"/api/team-members/{mid}").status_code == 204
        resp = api.put(f"/api/team-members/{mid}", json={"name": "x"})
        assert resp.status_code == 404
        assert resp.json()["detail"] == "Team member not found"

    def test_admin_only(self, api_as, make_user):
        client = api_as(make_user("APPROVER"))
        assert client.post("/api/team-members", json={"name": "E", "role": "TESTER"}).status_code == 403
        assert client.get("/api/team-members").status_code == 200


class TestTeams:
    def test_crud_and_members(self, api):
        resp = api.post("/api/teams", json={"name": "QA", "description": "Testers"})
        assert resp.status_code == 201
        team = resp.json()
        assert team["member_count"] == 0
        m1 = api.post("/api/team-members", json={"name": "M1", "role": "TESTER"}).json()["id"]
        m2 = api.post("/api/team-members", json={"name": "M2", "role": "TESTER"}).json()["id"]
        resp = api.put(f"/api/teams/{team['id']}/members", json={"member_ids": [m1, m2]})
        assert resp.json() == {"status": "ok", "team_id": team["id"], "member_count": 2}
        listed = next(t for t in api.get("/api/teams").json() if t["id"] == team["id"])
        assert listed["member_count"] == 2
        api.put(f"/api/teams/{team['id']}/members", json={"member_ids": [m2]})
        assert TeamMember.objects.get(pk=m1).team_id is None
        resp = api.put(f"/api/teams/{team['id']}", json={"description": "New"})
        assert resp.json()["description"] == "New" and resp.json()["member_count"] == 1
        assert api.delete(f"/api/teams/{team['id']}").status_code == 204
        assert TeamMember.objects.get(pk=m2).team_id is None

    def test_duplicate_name_400(self, api):
        api.post("/api/teams", json={"name": "Dup"})
        resp = api.post("/api/teams", json={"name": "Dup"})
        assert resp.status_code == 400
        assert resp.json()["detail"] == "Team 'Dup' already exists"

    def test_404s(self, api):
        assert api.put("/api/teams/999", json={"name": "x"}).json()["detail"] == "Team not found"
        assert api.delete("/api/teams/999").status_code == 404
        assert api.put("/api/teams/999/members", json={"member_ids": []}).status_code == 404

    def test_requires_approver(self, api_as, make_user):
        assert api_as(make_user("ESTIMATOR")).post("/api/teams", json={"name": "T"}).status_code == 403
