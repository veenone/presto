"""User Management page (/users): ADMIN only, CRUD, search, bulk role, active toggle, LDAP actions."""

import json
from unittest.mock import patch

import pytest

from apps.accounts.models import AuditLog, User
from apps.teams.models import TeamMember

pytestmark = pytest.mark.django_db
HX = {"HX-Request": "true"}
HX_RESULTS = {"HX-Request": "true", "HX-Target": "crud-results"}


def usernames(resp) -> list[str]:
    return [r["username"] for r in resp.context["results"].rows]


def events(resp) -> dict:
    return json.loads(resp["HX-Trigger"])


def audit(action, resource_id=None):
    return AuditLog.objects.filter(action=action, resource_type="user", resource_id=resource_id)


@pytest.fixture
def people(make_user):
    return {
        "ann": make_user("APPROVER", username="ann", display_name="Ann Approver", email="ann@example.test"),
        "eve": make_user("ESTIMATOR", username="eve", display_name="Eve Estimator", auth_provider="ldap"),
        "vic": make_user("VIEWER", username="vic", display_name="Vic Viewer", is_active=False),
    }


class TestAccess:
    @pytest.mark.parametrize("role", ["VIEWER", "ESTIMATOR", "APPROVER"])
    def test_non_admin_gets_403_everywhere(self, web_login, make_user, seeded, role):
        client = web_login(make_user(role))
        target = make_user("VIEWER", username="target")
        gets = ["/users", "/users/new", f"/users/{target.pk}/edit", f"/users/{target.pk}/delete",
                f"/users/bulk-role?ids={target.pk}", "/users/ldap/reset"]
        for url in gets:
            assert client.get(url).status_code == 403, url
        posts = {
            "/users/new": {"username": "x", "display_name": "x", "password": "pw", "role": "ADMIN",
                           "auth_provider": "local"},
            f"/users/{target.pk}/edit": {"display_name": "x", "role": "ADMIN"},
            f"/users/{target.pk}/delete": {},
            f"/users/{target.pk}/toggle-active": {},
            "/users/bulk-role": {"ids": [target.pk], "role": "ADMIN", "confirm": "1"},
            "/users/ldap/test": {}, "/users/ldap/sync": {}, "/users/ldap/reset": {"confirm": "1"},
        }
        for url, data in posts.items():
            assert client.post(url, data).status_code == 403, url
        target.refresh_from_db()
        assert target.role == "VIEWER" and target.is_active and not User.objects.filter(username="x").exists()

    def test_anonymous_redirected_to_login(self, client, seeded):
        resp = client.get("/users")
        assert resp.status_code == 302 and "/login" in resp["Location"]

    def test_toggle_requires_post(self, admin_client, people):
        assert admin_client.get(f"/users/{people['ann'].pk}/toggle-active").status_code == 405


class TestList:
    def test_page_lists_users_with_tiles_and_segments(self, admin_client, people):
        resp = admin_client.get("/users")
        assert resp.status_code == 200 and b"User Management" in resp.content and b"Add User" in resp.content
        assert usernames(resp) == ["admin", "ann", "eve", "vic"]
        tiles = {t.label: t.value for t in resp.context["results"].tiles}
        assert tiles["Total Users"] == "4" and tiles["Admins"] == "1" and tiles["Approvers"] == "1"
        assert tiles["Federated"] == "1"
        segs = {s["key"]: s["count"] for s in resp.context["results"].segments}
        assert segs == {"all": 4, "VIEWER": 1, "ESTIMATOR": 1, "APPROVER": 1, "ADMIN": 1}

    @pytest.mark.parametrize("q,expected", [
        ("ann", ["ann"]), ("EVE ESTIM", ["eve"]), ("example.test", ["ann"]),
        ("viewer", ["vic"]), ("ldap", ["eve"]), ("approver", ["ann"]),
    ])
    def test_search_across_fields(self, admin_client, people, q, expected):
        assert usernames(admin_client.get(f"/users?q={q}")) == expected

    def test_role_segment_and_sort(self, admin_client, people):
        assert usernames(admin_client.get("/users?seg=ESTIMATOR")) == ["eve"]
        assert usernames(admin_client.get("/users?sort=username&dir=desc"))[0] == "vic"

    def test_htmx_partial_has_selection_and_team_member_name(self, admin_client, people):
        member = TeamMember.objects.create(name="Tess Tester", role="Tester")
        people["ann"].team_member = member
        people["ann"].save()
        resp = admin_client.get("/users", headers=HX_RESULTS)
        body = resp.content.decode()
        assert 'id="crud-results"' in body and "ed-side-nav" not in body
        assert f'name="ids" value="{people["ann"].pk}"' in body and "Tess Tester" in body

    def test_own_row_cannot_be_deleted_or_deactivated(self, admin_client, admin_user, people):
        body = admin_client.get("/users").content.decode()
        assert "You cannot delete your own account" in body
        assert f"/users/{admin_user.pk}/delete" not in body and f"/users/{people['ann'].pk}/delete" in body
        assert f"/users/{admin_user.pk}/toggle-active" not in body


class TestCreate:
    def test_create_writes_user_and_audit(self, admin_client, admin_user):
        member = TeamMember.objects.create(name="Tess", role="Tester")
        resp = admin_client.post("/users/new", {
            "username": " newbie ", "display_name": "New Bie", "email": "new@example.test", "password": "s3cret",
            "role": "ESTIMATOR", "auth_provider": "local", "team_member_id": str(member.pk),
        }, headers=HX)
        assert resp.status_code == 204 and events(resp)["toast"]["message"] == "User created."
        user = User.objects.get(username="newbie")
        assert user.role == "ESTIMATOR" and user.team_member_id == member.pk and user.check_password("s3cret")
        entry = audit("CREATE", user.pk).get()
        assert entry.user_id == admin_user.pk and entry.ip_address == "127.0.0.1"

    def test_validation_errors(self, admin_client):
        resp = admin_client.post("/users/new", {"username": "", "display_name": " ", "email": "nope",
                                                "role": "GOD", "auth_provider": "local"}, headers=HX)
        assert resp.status_code == 422
        body = resp.content.decode()
        assert "This field is required." in body and "Enter a valid email address." in body
        assert "Password is required for new users." in body and "Select a valid choice" in body

    def test_external_provider_needs_no_password(self, admin_client):
        resp = admin_client.post("/users/new", {"username": "dirk", "display_name": "Dirk", "role": "VIEWER",
                                                "auth_provider": "ldap"}, headers=HX)
        assert resp.status_code == 204
        assert User.objects.get(username="dirk").password is None

    def test_duplicate_username_and_email_use_api_messages(self, admin_client, people):
        base = {"display_name": "X", "password": "pw", "role": "VIEWER", "auth_provider": "local"}
        resp = admin_client.post("/users/new", {**base, "username": "ann"}, headers=HX)
        assert resp.status_code == 422 and "Username &#x27;ann&#x27; already exists" in resp.content.decode()
        resp = admin_client.post("/users/new", {**base, "username": "zed", "email": "ann@example.test"}, headers=HX)
        assert resp.status_code == 422 and "Email &#x27;ann@example.test&#x27; already exists" in resp.content.decode()
        assert not audit("CREATE").exists()

    def test_unknown_team_member_rejected(self, admin_client):
        resp = admin_client.post("/users/new", {"username": "t", "display_name": "T", "password": "pw",
                                                "role": "VIEWER", "auth_provider": "local", "team_member_id": "999"})
        assert resp.status_code == 422 and not User.objects.filter(username="t").exists()


class TestEdit:
    def test_edit_form_and_update(self, admin_client, people):
        ann = people["ann"]
        form = admin_client.get(f"/users/{ann.pk}/edit", headers=HX)
        assert form.status_code == 200 and b"Edit User: ann" in form.content and b"Password" not in form.content
        resp = admin_client.post(f"/users/{ann.pk}/edit", {"display_name": "Ann B", "email": "",
                                                           "role": "ADMIN", "team_member_id": ""}, headers=HX)
        assert resp.status_code == 204 and events(resp)["toast"]["message"] == "User updated."
        ann.refresh_from_db()
        assert (ann.display_name, ann.email, ann.role, ann.is_active) == ("Ann B", None, "ADMIN", False)
        assert audit("UPDATE", ann.pk).count() == 1

    def test_duplicate_email_on_edit(self, admin_client, people):
        resp = admin_client.post(f"/users/{people['eve'].pk}/edit", {
            "display_name": "Eve", "email": "ann@example.test", "role": "ESTIMATOR", "is_active": "on"}, headers=HX)
        assert resp.status_code == 422 and b"already exists" in resp.content
        assert not audit("UPDATE", people["eve"].pk).exists()

    def test_own_role_and_active_are_locked(self, admin_client, admin_user):
        form = admin_client.get(f"/users/{admin_user.pk}/edit", headers=HX).content.decode()
        assert "You cannot change your own role" in form
        resp = admin_client.post(f"/users/{admin_user.pk}/edit", {"display_name": "Boss", "role": "VIEWER"},
                                 headers=HX)
        assert resp.status_code == 204
        admin_user.refresh_from_db()
        assert admin_user.display_name == "Boss" and admin_user.role == "ADMIN" and admin_user.is_active

    def test_unknown_user_is_404(self, admin_client):
        assert admin_client.get("/users/9999/edit").status_code == 404
        assert admin_client.get("/users/9999/delete").status_code == 404


class TestDelete:
    def test_delete_with_audit(self, admin_client, people):
        vic = people["vic"]
        assert b"Delete user &#x27;vic&#x27;?" in admin_client.get(f"/users/{vic.pk}/delete", headers=HX).content
        resp = admin_client.post(f"/users/{vic.pk}/delete", headers=HX)
        assert resp.status_code == 204 and events(resp)["toast"]["message"] == "User deleted."
        assert not User.objects.filter(pk=vic.pk).exists() and audit("DELETE", vic.pk).count() == 1

    def test_cannot_delete_yourself(self, admin_client, admin_user):
        resp = admin_client.post(f"/users/{admin_user.pk}/delete", headers=HX)
        assert resp.status_code == 422 and b"Cannot delete your own account" in resp.content
        assert User.objects.filter(pk=admin_user.pk).exists() and not audit("DELETE").exists()


class TestBulkRole:
    def test_dialog_lists_selection_and_warns_about_self(self, admin_client, admin_user, people):
        resp = admin_client.get(f"/users/bulk-role?ids={people['ann'].pk}&ids={admin_user.pk}", headers=HX)
        body = resp.content.decode()
        assert resp.status_code == 200 and "This will change 2 user(s)" in body
        assert "your role will not be changed" in body and "I understand this changes 2 users" in body

    def test_no_selection(self, admin_client):
        resp = admin_client.get("/users/bulk-role", headers=HX)
        assert resp.status_code == 400 and resp.json()["detail"] == "Select at least one user first."

    def test_requires_confirmation(self, admin_client, people):
        resp = admin_client.post("/users/bulk-role", {"ids": [people["vic"].pk], "role": "APPROVER"}, headers=HX)
        assert resp.status_code == 422 and b"I understand this changes 1 users" in resp.content
        people["vic"].refresh_from_db()
        assert people["vic"].role == "VIEWER"

    def test_apply_skips_self_and_audits_each(self, admin_client, admin_user, people):
        ids = [people["ann"].pk, people["vic"].pk, admin_user.pk]
        resp = admin_client.post("/users/bulk-role", {"ids": ids, "role": "ESTIMATOR", "confirm": "1"}, headers=HX)
        assert resp.status_code == 204
        ev = events(resp)
        assert ev["toast"]["message"] == "Role updated for 2/3 users." and ev["crud-refresh"] and ev["close-modal"]
        assert set(User.objects.filter(pk__in=ids).values_list("username", "role")) == {
            ("ann", "ESTIMATOR"), ("vic", "ESTIMATOR"), ("admin", "ADMIN")}
        assert AuditLog.objects.filter(action="UPDATE", resource_type="user").count() == 2

    def test_invalid_role(self, admin_client, people):
        resp = admin_client.post("/users/bulk-role", {"ids": [people["ann"].pk], "role": "ROOT", "confirm": "1"})
        assert resp.status_code == 422 and b"Choose a valid role." in resp.content


class TestActiveToggle:
    def test_toggle(self, admin_client, people):
        vic = people["vic"]
        resp = admin_client.post(f"/users/{vic.pk}/toggle-active", headers=HX)
        assert resp.status_code == 204 and events(resp)["toast"]["message"] == "User 'vic' activated."
        vic.refresh_from_db()
        assert vic.is_active and audit("UPDATE", vic.pk).count() == 1
        admin_client.post(f"/users/{vic.pk}/toggle-active", headers=HX)
        vic.refresh_from_db()
        assert not vic.is_active

    def test_cannot_deactivate_yourself(self, admin_client, admin_user):
        resp = admin_client.post(f"/users/{admin_user.pk}/toggle-active", headers=HX)
        assert resp.status_code == 400 and "deactivate your own" in resp.json()["detail"]
        admin_user.refresh_from_db()
        assert admin_user.is_active

    def test_unknown_user(self, admin_client):
        assert admin_client.post("/users/9999/toggle-active").status_code == 404


class TestLdap:
    def test_test_not_configured(self, admin_client):
        resp = admin_client.post("/users/ldap/test", headers=HX)
        assert resp.status_code == 204
        toast = events(resp)["toast"]
        assert toast["level"] == "error" and toast["message"].startswith("LDAP failed: LDAP is not configured")

    def test_test_ok(self, admin_client):
        with patch("apps.accounts.pages.users.LDAPProvider.test_connection", return_value=(True, "Bound.")):
            toast = events(admin_client.post("/users/ldap/test", headers=HX))["toast"]
        assert toast == {"message": "LDAP OK: Bound.", "level": "success"}

    def test_sync_not_configured(self, admin_client):
        resp = admin_client.post("/users/ldap/sync", headers=HX)
        assert resp.status_code == 400 and resp.json()["detail"] == "LDAP is not configured"

    def test_sync_logs_and_reports(self, admin_client):
        result = {"synced": 3, "created": 2, "updated": 0, "skipped": 1, "deactivated": 0, "errors": []}
        with patch("apps.accounts.pages.users.LDAPProvider.is_configured", True), \
             patch("apps.accounts.pages.users.LDAPProvider.sync_users", return_value=result):
            resp = admin_client.post("/users/ldap/sync", headers=HX)
        assert resp.status_code == 204
        assert events(resp)["toast"]["message"] == "LDAP sync: 2 created, 1 skipped (already exist)"
        assert AuditLog.objects.get(action="LDAP_SYNC").details["created"] == 2

    def test_reset_requires_ack_then_deletes_ldap_users(self, admin_client, people):
        assert admin_client.post("/users/ldap/reset", headers=HX).status_code == 422
        assert User.objects.filter(username="eve").exists()
        resp = admin_client.post("/users/ldap/reset", {"confirm": "1"}, headers=HX)
        assert resp.status_code == 204 and "Removed 1 LDAP-synced user(s)" in events(resp)["toast"]["message"]
        assert not User.objects.filter(username="eve").exists() and User.objects.filter(username="ann").exists()
        assert AuditLog.objects.get(action="LDAP_RESET_USERS").details == {"deleted": 1}
