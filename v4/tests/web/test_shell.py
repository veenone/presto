import pytest

from apps.accounts.permissions import DEFAULT_MATRIX, get_matrix, user_permissions
from apps.core import config
from apps.notifications.models import WebhookNotification
from apps.web.navigation import ITEMS_BY_NAME
from apps.web.theme import readable_text, theme_variables

pytestmark = pytest.mark.django_db


def nav_labels(client) -> set[str]:
    return {item["label"] for s in client.get("/").context["navigation"] for item in s["items"]}


class TestNavigation:
    def test_admin_sees_everything(self, admin_client):
        assert nav_labels(admin_client) == {i.label for i in ITEMS_BY_NAME.values()}

    def test_viewer_hidden_items(self, web_login, make_user, seeded):
        labels = nav_labels(web_login(make_user("VIEWER")))
        hidden = {"Request Inbox", "Feature Presets", "Users", "RBAC", "Audit Log", "Backup & Restore",
                  "New Estimation"}
        assert hidden.isdisjoint(labels)
        assert {"Dashboard", "Estimations", "Feature Catalog", "Settings"} <= labels

    def test_new_estimation_needs_create_permission(self, web_login, make_user, seeded):
        assert "New Estimation" not in nav_labels(web_login(make_user("VIEWER")))
        assert "New Estimation" in nav_labels(web_login(make_user("ESTIMATOR")))

    def test_estimator_sees_presets_and_inbox(self, web_login, make_user, seeded):
        labels = nav_labels(web_login(make_user("ESTIMATOR")))
        assert {"Feature Presets", "Request Inbox"} <= labels and "Users" not in labels

    def test_saved_matrix_changes_navigation(self, web_login, make_user, seeded):
        config.set_value("rbac_matrix", {"VIEWER": ["view_requests"]})
        assert "Request Inbox" in nav_labels(web_login(make_user("VIEWER")))

    def test_active_item_marked(self, admin_client):
        body = admin_client.get("/duts").content.decode()
        assert 'href="/duts"\n               aria-current="page"' in body or 'aria-current="page"' in body

    def test_every_v3_page_url_exists(self, admin_client):
        from apps.estimations.models import Estimation
        from apps.inbox.models import Request

        est = Estimation.objects.create(project_name="P", project_type="NEW")  # DRAFT: editable
        req = Request.objects.create(request_number="REQ-1", title="T", requester_name="PO",
                                     received_date="2026-11-01")
        details = [f"/requests/{req.pk}", f"/estimation/{est.pk}", f"/estimation/{est.pk}/edit"]
        for path in [i.path for i in ITEMS_BY_NAME.values()] + details:
            assert admin_client.get(path).status_code == 200, path
        assert admin_client.get("/estimation/999999").status_code == 404

    def test_admin_pages_forbidden_for_others(self, web_login, make_user, seeded):
        client = web_login(make_user("APPROVER"))
        for path in ("/users", "/rbac", "/audit", "/admin/backup"):
            resp = client.get(path)
            assert resp.status_code == 403 and b"Access Denied" in resp.content, path
        assert web_login(make_user("VIEWER")).get("/feature-presets").status_code == 403


class TestPermissions:
    def test_admin_always_has_all(self, make_user):
        config.set_value("rbac_matrix", {"ADMIN": []})
        assert len(user_permissions(make_user("ADMIN"))) == 18

    def test_matrix_ignores_unknown_keys_and_keeps_defaults(self, db):
        config.set_value("rbac_matrix", {"VIEWER": ["view_reports", "bogus"]})
        matrix = get_matrix()
        assert matrix["VIEWER"] == ["view_reports"] and matrix["ESTIMATOR"] == DEFAULT_MATRIX["ESTIMATOR"]

    def test_invalid_matrix_falls_back(self, db):
        config.set_value("rbac_matrix", "not json")
        assert get_matrix()["VIEWER"] == DEFAULT_MATRIX["VIEWER"]


class TestTheme:
    def test_saved_preference_rendered(self, admin_client, admin_user):
        assert b'data-theme-server=""' in admin_client.get("/").content
        assert admin_client.post("/me/theme", {"theme": "light"}).status_code == 204
        admin_user.refresh_from_db()
        assert admin_user.theme_preference == "light"
        assert b'data-theme-server="light"' in admin_client.get("/").content

    def test_rejects_unknown_theme(self, admin_client):
        assert admin_client.post("/me/theme", {"theme": "pink"}).status_code == 400

    def test_requires_sign_in(self, client, seeded):
        assert client.post("/me/theme", {"theme": "dark"}).status_code == 302

    def test_config_colours_become_css_variables(self, admin_client):
        config.set_value("sidebar_bg_light", "#0A2540")
        body = admin_client.get("/").content.decode()
        assert "--sidebar-bg: #0A2540;" in body and "--sidebar-fg: #FFFFFF;" in body

    def test_unsafe_colour_values_are_dropped(self, db):
        config.set_value("button_color_dark", "red; } body { display:none")
        assert theme_variables()["dark"]["--primary"] == "#90CAF9"

    def test_readable_text(self):
        assert readable_text("#FFFFFF", "x") == "#212121" and readable_text("#1D1D1D", "x") == "#FFFFFF"
        assert readable_text("nonsense", "fallback") == "fallback"


class TestNotifications:
    @pytest.fixture
    def notes(self, admin_user):
        return [
            WebhookNotification.objects.create(user=admin_user, title=f"Issue {i}", message="m",
                                               source="REDMINE")
            for i in range(3)
        ]

    def test_actions_are_post_only(self, admin_client, notes):
        assert admin_client.get(f"/notifications/{notes[0].pk}/read").status_code == 405
        assert admin_client.get("/notifications/read-all").status_code == 405

    def test_badge_counts_unread(self, admin_client, notes):
        assert b">3</span>" in admin_client.get("/notifications/badge").content
        WebhookNotification.objects.update(is_read=True)
        assert b"ed-side-bell-badge" not in admin_client.get("/notifications/badge").content

    def test_panel_lists_only_own(self, admin_client, notes, make_user):
        WebhookNotification.objects.create(user=make_user("VIEWER"), title="Not yours")
        body = admin_client.get("/notifications/panel").content.decode()
        assert "Issue 0" in body and "Not yours" not in body

    def test_mark_one_read(self, admin_client, notes):
        resp = admin_client.post(f"/notifications/{notes[0].pk}/read")
        notes[0].refresh_from_db()
        assert resp.status_code == 200 and b"Issue 1" in resp.content
        assert notes[0].is_read and "notifications-changed" in resp["HX-Trigger"]

    def test_cannot_mark_someone_elses(self, admin_client, make_user):
        other = WebhookNotification.objects.create(user=make_user("VIEWER"), title="x")
        admin_client.post(f"/notifications/{other.pk}/read")
        other.refresh_from_db()
        assert not other.is_read

    def test_mark_all_read(self, admin_client, notes):
        resp = admin_client.post("/notifications/read-all")
        assert resp.status_code == 200
        assert not WebhookNotification.objects.filter(is_read=False).exists()
        assert "toast" in resp["HX-Trigger"]


class TestPasswordChange:
    def test_change(self, admin_client, admin_user):
        resp = admin_client.post("/account/password", {
            "current_password": "admin", "new_password": "n3w-pass", "confirm_password": "n3w-pass"})
        assert resp.status_code == 302
        admin_user.refresh_from_db()
        assert admin_user.check_password("n3w-pass")
        assert admin_client.get("/").status_code == 200  # session survives the change

    def test_wrong_current_password(self, admin_client):
        resp = admin_client.post("/account/password", {
            "current_password": "nope", "new_password": "n3w-pass", "confirm_password": "n3w-pass"})
        assert resp.status_code == 200 and b"Current password is incorrect" in resp.content

    def test_mismatch(self, admin_client):
        resp = admin_client.post("/account/password", {
            "current_password": "admin", "new_password": "aaaa1", "confirm_password": "bbbb1"})
        assert b"do not match" in resp.content

    def test_external_account_has_no_form(self, web_login, make_user, seeded):
        client = web_login(make_user("VIEWER", password=None, auth_provider="ldap"))
        body = client.get("/account/password").content
        assert b"signs in through LDAP" in body and b"current_password" not in body


class TestErrorPages:
    def test_404_inside_shell(self, admin_client):
        resp = admin_client.get("/no-such-page")
        assert resp.status_code == 404 and b"Not Found" in resp.content and b"ed-side-nav" in resp.content

    def test_404_anonymous_is_standalone(self, client, seeded):
        resp = client.get("/no-such-page")
        assert resp.status_code == 404 and b"Go to Login" in resp.content
        assert b"ed-side-nav" not in resp.content

    def test_500_page_needs_no_database(self, rf):
        from apps.web.views import error_500
        resp = error_500(rf.get("/"))
        assert resp.status_code == 500 and b"Server Error" in resp.content
