"""Audit Log page (/audit): ADMIN only, filters, paging, details viewer."""

from datetime import datetime

import pytest

from apps.accounts.audit import log_action
from apps.accounts.models import AuditLog
from apps.accounts.pages.audit import PAGE_SIZE

pytestmark = pytest.mark.django_db
HX_RESULTS = {"HX-Request": "true", "HX-Target": "audit-results"}


def actions(resp) -> list[str]:
    return [e["action"] for e in resp.context["entries"]]


@pytest.fixture
def log(admin_user, make_user):
    other = make_user("ESTIMATOR", username="est")
    log_action(admin_user, "LOGIN", ip_address="10.0.0.1")
    log_action(None, "LOGIN_FAILED", "auth", details={"username": "ghost"}, ip_address="10.0.0.9")
    log_action(admin_user, "CREATE", "user", other.pk, ip_address="10.0.0.1")
    log_action(other, "UPDATE", "estimation", 7, details={"status": "APPROVED"})
    log_action(admin_user, "DELETE", "feature", 3)
    log_action(admin_user, "BACKUP_RESTORE", details={"file": "x.db"})
    old = log_action(other, "CREATE", "feature", 4)
    AuditLog.objects.filter(pk=old.pk).update(created_at=datetime(2025, 1, 15, 10, 0))
    return {"admin": admin_user, "est": other}


class TestAccess:
    @pytest.mark.parametrize("role", ["VIEWER", "ESTIMATOR", "APPROVER"])
    def test_non_admin_403(self, web_login, make_user, seeded, role):
        client = web_login(make_user(role))
        entry = log_action(None, "LOGIN")
        assert client.get("/audit").status_code == 403
        assert client.get(f"/audit/{entry.pk}").status_code == 403

    def test_get_only(self, admin_client):
        assert admin_client.post("/audit").status_code == 405


class TestList:
    def test_newest_first_with_ip_tiles_and_pills(self, admin_client, log):
        resp = admin_client.get("/audit")
        assert resp.status_code == 200 and b"Audit Log" in resp.content
        assert actions(resp)[:2] == ["BACKUP_RESTORE", "DELETE"] and actions(resp)[-1] == "CREATE"
        assert b"10.0.0.9" in resp.content
        pills = {p["label"]: p["count"] for p in resp.context["pills"]}
        assert pills == {"All": 7, "Create": 2, "Update": 1, "Delete": 1, "Login": 1, "Failed Login": 1,
                         "Backup/Restore": 1}
        tiles = {t["label"]: t["value"] for t in resp.context["tiles"]}
        assert tiles["Total Entries"] == 7 and tiles["Logins"] == 1 and tiles["Failed Logins"] == 1
        assert tiles["Create"] == 2

    def test_action_filter_and_other_group(self, admin_client, log):
        assert actions(admin_client.get("/audit?action=CREATE")) == ["CREATE", "CREATE"]
        assert actions(admin_client.get("/audit?action=CHANGE")) == ["BACKUP_RESTORE"]

    def test_resource_type_user_and_date_filters(self, admin_client, log):
        assert actions(admin_client.get("/audit?resource_type=feature")) == ["DELETE", "CREATE"]
        resp = admin_client.get(f"/audit?user={log['est'].pk}")
        assert actions(resp) == ["UPDATE", "CREATE"]
        assert actions(admin_client.get("/audit?date_to=2025-12-31")) == ["CREATE"]
        assert len(actions(admin_client.get("/audit?date_from=2026-01-01"))) == 6
        assert actions(admin_client.get("/audit?date_from=2025-01-15&date_to=2025-01-15")) == ["CREATE"]
        # Pills count within the other filters; the action filter combines with them.
        resp = admin_client.get("/audit?resource_type=feature&action=DELETE")
        assert actions(resp) == ["DELETE"] and resp.context["pills"][0]["count"] == 2

    def test_bad_filter_values_are_ignored(self, admin_client, log):
        resp = admin_client.get("/audit?user=abc&date_from=nope&page=-3")
        assert resp.status_code == 200 and len(actions(resp)) == 7

    def test_paging(self, admin_client, admin_user):
        for i in range(PAGE_SIZE + 5):
            log_action(admin_user, "UPDATE", "feature", i)
        first = admin_client.get("/audit")
        assert len(first.context["entries"]) == PAGE_SIZE and first.context["next"].endswith("page=2")
        assert first.context["entries"][0]["resource_id"] == PAGE_SIZE + 4
        last = admin_client.get("/audit?page=99")
        assert last.context["page"] == 2 and len(last.context["entries"]) == 5 and last.context["prev"]

    def test_paging_keeps_filters(self, admin_client, admin_user):
        for i in range(PAGE_SIZE + 1):
            log_action(admin_user, "DELETE", "feature", i)
        assert admin_client.get("/audit?action=DELETE").context["next"] == "/audit?action=DELETE&page=2"

    def test_htmx_partial(self, admin_client, log):
        resp = admin_client.get("/audit?action=LOGIN", headers=HX_RESULTS)
        assert b'id="audit-results"' in resp.content and b"ed-side-nav" not in resp.content
        assert b"hx-swap-oob" in resp.content

    def test_empty(self, admin_client):
        AuditLog.objects.all().delete()
        assert b"No audit entries match" in admin_client.get("/audit").content


class TestDetails:
    def test_details_viewer(self, admin_client, log):
        entry = AuditLog.objects.get(action="UPDATE")
        assert f'/audit/{entry.pk}"'.encode() in admin_client.get("/audit").content
        resp = admin_client.get(f"/audit/{entry.pk}", headers={"HX-Request": "true"})
        body = resp.content.decode()
        assert resp.status_code == 200 and f"Audit Entry #{entry.pk}" in body
        assert "&quot;status&quot;: &quot;APPROVED&quot;" in body and "estimation #7" in body

    def test_unknown_entry_404(self, admin_client):
        assert admin_client.get("/audit/99999").status_code == 404
