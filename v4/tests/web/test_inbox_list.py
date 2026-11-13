"""Request Inbox list page: access, table, filters, Add Request dialog and import from Redmine / Jira."""

from __future__ import annotations

import json
from datetime import date, timedelta
from unittest import mock

import pytest

from apps.inbox.models import Request
from apps.integrations.adapters.base import ExternalRequest, SyncResult, SyncStatus
from apps.integrations.models import IntegrationConfig

pytestmark = pytest.mark.django_db
HX = {"HX-Request": "true"}
HX_RESULTS = {"HX-Request": "true", "HX-Target": "crud-results"}
HX_MODAL = {"HX-Request": "true", "HX-Target": "modal"}


def events(resp) -> dict:
    return json.loads(resp.get("HX-Trigger") or "{}")


def numbers(resp) -> list[str]:
    return [str(r["request_number"]) for r in resp.context["results"].rows]


def make_request(number: str, **kw) -> Request:
    data = {"title": f"Title {number}", "requester_name": "Ops", "received_date": date(2026, 1, 5)}
    data.update(kw)
    return Request.objects.create(request_number=number, **data)


@pytest.fixture
def as_role(web_login, make_user, seeded):
    def _as(role: str, **extra):
        return web_login(make_user(role, **extra))
    return _as


@pytest.fixture
def sample(seeded):
    return [
        make_request("REQ-1", priority="HIGH", status="NEW", request_source="MANUAL"),
        make_request("REQ-2", priority="LOW", status="ESTIMATED", request_source="REDMINE", external_id="77"),
        make_request("REQ-3", priority="CRITICAL", status="COMPLETED", request_source="JIRA",
                     title="eSIM regression", requester_name="Customer Ops"),
    ]


# ── Access ───────────────────────────────────────────────


class TestAccess:
    def test_viewer_refused_by_default_matrix(self, as_role):
        assert as_role("VIEWER").get("/requests").status_code == 403

    def test_viewer_allowed_when_matrix_grants_view_requests(self, as_role):
        from apps.core import config
        config.set_value("rbac_matrix", json.dumps({"VIEWER": ["view_requests"]}))
        client = as_role("VIEWER")
        resp = client.get("/requests")
        assert resp.status_code == 200
        body = resp.content.decode()
        assert "Add Request" not in body and "Import" not in body
        assert client.get("/requests/new").status_code == 403

    def test_estimator_can_add_but_not_import_or_assign(self, as_role, sample):
        body = as_role("ESTIMATOR").get("/requests").content.decode()
        assert "Add Request" in body
        assert 'data-testid="import-requests"' not in body
        assert "/assign" not in body
        assert "/estimation/new?request_id=" in body

    def test_approver_sees_assign_buttons(self, as_role, sample):
        body = as_role("APPROVER").get("/requests").content.decode()
        assert f'hx-get="/requests/{sample[0].id}/assign"' in body
        assert 'data-testid="import-requests"' not in body

    def test_admin_sees_import(self, admin_client):
        assert 'data-testid="import-requests"' in admin_client.get("/requests").content.decode()

    def test_anonymous_redirected_to_login(self, client, seeded):
        resp = client.get("/requests")
        assert resp.status_code == 302 and "/login" in resp["Location"]

    def test_no_delete_route(self, admin_client, sample):
        assert admin_client.get(f"/requests/{sample[0].id}/delete").status_code == 404


# ── Table ────────────────────────────────────────────────


class TestList:
    def test_rows_badges_and_links(self, admin_client, sample):
        resp = admin_client.get("/requests")
        assert resp.status_code == 200
        body = resp.content.decode()
        assert "Request Inbox" in body and "TOTAL · 3" in body
        assert f'href="/requests/{sample[0].id}"' in body
        assert 'class="badge inb-badge inb-pri-HIGH"' in body
        assert 'class="badge inb-badge inb-status-ESTIMATED"' in body
        assert "inb-stripe-CRITICAL" in body
        assert body.count('class="muted inb-unassigned"') == 3
        assert ">rss_feed<" in body  # Redmine source icon

    def test_estimate_button_hidden_for_closed_requests(self, admin_client, sample):
        body = admin_client.get("/requests").content.decode()
        assert f"/estimation/new?request_id={sample[0].id}" in body
        assert f"/estimation/new?request_id={sample[2].id}" not in body  # COMPLETED

    def test_newest_first_by_default(self, admin_client, sample):
        assert numbers(admin_client.get("/requests")) == ["REQ-3", "REQ-2", "REQ-1"]

    def test_sort_by_priority_is_by_urgency(self, admin_client, sample):
        assert numbers(admin_client.get("/requests?sort=priority")) == ["REQ-3", "REQ-1", "REQ-2"]

    def test_tiles(self, admin_client, sample):
        tiles = {t.label: t.value for t in admin_client.get("/requests").context["results"].tiles}
        assert tiles == {"Open": "2", "New today": "3", "High priority": "2", "Unassigned": "2"}

    def test_status_segments(self, admin_client, sample):
        resp = admin_client.get("/requests?seg=ESTIMATED")
        assert numbers(resp) == ["REQ-2"]
        segs = {s["key"]: s["count"] for s in resp.context["results"].segments}
        assert segs["all"] == 3 and segs["NEW"] == 1 and segs["DELETED_UPSTREAM"] == 0

    def test_source_and_priority_filters(self, admin_client, sample):
        assert numbers(admin_client.get("/requests?source=JIRA")) == ["REQ-3"]
        assert numbers(admin_client.get("/requests?priority=HIGH")) == ["REQ-1"]
        assert numbers(admin_client.get("/requests?source=JIRA&priority=LOW")) == []
        assert numbers(admin_client.get("/requests?source=bogus")) == ["REQ-3", "REQ-2", "REQ-1"]

    def test_search_number_title_requester_external_id(self, admin_client, sample):
        assert numbers(admin_client.get("/requests?q=esim")) == ["REQ-3"]
        assert numbers(admin_client.get("/requests?q=customer ops")) == ["REQ-3"]
        assert numbers(admin_client.get("/requests?q=77")) == ["REQ-2"]
        assert numbers(admin_client.get("/requests?q=req-1")) == ["REQ-1"]

    def test_paging(self, admin_client, seeded):
        for i in range(30):
            make_request(f"P-{i:02d}")
        resp = admin_client.get("/requests?page=2")
        assert len(resp.context["results"].rows) == 5
        assert "26–30 of 30" in resp.content.decode()

    def test_htmx_results_partial(self, admin_client, sample):
        resp = admin_client.get("/requests?seg=NEW", headers=HX_RESULTS)
        body = resp.content.decode()
        assert resp.status_code == 200 and 'id="crud-results"' in body and "<html" not in body

    def test_empty_state(self, admin_client):
        assert "No request inbox yet." in admin_client.get("/requests").content.decode()

    def test_age_column(self, admin_client, seeded):
        old = make_request("OLD")
        Request.objects.filter(pk=old.pk).update(created_at=old.created_at - timedelta(days=10))
        body = admin_client.get("/requests").content.decode()
        assert ">1w<" in body


# ── Add Request dialog ───────────────────────────────────


class TestCreate:
    def test_form_prefills_next_number(self, as_role):
        resp = as_role("ESTIMATOR").get("/requests/new", headers=HX)
        assert resp.status_code == 200
        body = resp.content.decode()
        assert "Add Request" in body and f"REQ-{date.today().year}-001" in body
        assert 'name="received_date"' in body

    def test_create_happy_path(self, as_role):
        resp = as_role("ESTIMATOR").post("/requests/new", {
            "request_number": "REQ_26/0001", "request_source": "MANUAL", "title": " SIM Toolkit ",
            "requester_name": "Product Office", "priority": "HIGH", "received_date": "2026-10-20",
            "description": "Validate", "requester_email": "", "product_type": "",
        }, headers=HX)
        assert resp.status_code == 204, resp.content
        ev = events(resp)
        assert ev["crud-refresh"] and ev["close-modal"] and ev["toast"]["message"] == "Request created."
        req = Request.objects.get(request_number="REQ_26/0001")
        assert req.title == "SIM Toolkit" and req.priority == "HIGH" and req.status == "NEW"
        assert req.requester_email is None and req.product_type is None

    def test_blank_number_uses_numbering_service(self, admin_client):
        make_request(f"REQ-{date.today().year}-002")  # count + 1 = 002 is taken: the next free one is used
        resp = admin_client.post("/requests/new", {
            "request_number": "", "request_source": "MANUAL", "title": "T", "requester_name": "R",
            "priority": "MEDIUM", "received_date": "2026-10-20",
        }, headers=HX)
        assert resp.status_code == 204
        assert Request.objects.filter(request_number=f"REQ-{date.today().year}-003").exists()

    def test_validation_errors_rerender_dialog(self, admin_client):
        resp = admin_client.post("/requests/new", {
            "request_source": "MANUAL", "title": "  ", "requester_name": "", "priority": "MEDIUM",
            "received_date": "", "requester_email": "not-an-email",
        }, headers=HX)
        assert resp.status_code == 422
        body = resp.content.decode()
        assert body.count("This field is required.") == 3  # title (blank after strip), requester, received date
        assert "Enter a valid email address." in body
        assert Request.objects.count() == 0

    def test_duplicate_number_service_error_in_dialog(self, admin_client, sample):
        resp = admin_client.post("/requests/new", {
            "request_number": "REQ-1", "request_source": "MANUAL", "title": "T", "requester_name": "R",
            "priority": "MEDIUM", "received_date": "2026-10-20",
        }, headers=HX)
        assert resp.status_code == 422
        assert "Request number &#x27;REQ-1&#x27; already exists." in resp.content.decode()

    def test_viewer_cannot_create(self, as_role):
        from apps.core import config
        config.set_value("rbac_matrix", json.dumps({"VIEWER": ["view_requests"]}))
        resp = as_role("VIEWER").post("/requests/new", {"title": "x"})
        assert resp.status_code == 403
        assert Request.objects.count() == 0


# ── Import from Redmine / Jira ───────────────────────────


def _enable(system: str) -> None:
    IntegrationConfig.objects.update_or_create(
        system_name=system, defaults={"enabled": True, "base_url": "https://tracker.example.test", "api_key": "k"})


class TestSync:
    def test_modal_lists_systems(self, admin_client, seeded):
        _enable("JIRA")
        resp = admin_client.get("/requests/sync", headers=HX)
        assert resp.status_code == 200
        body = resp.content.decode()
        assert "Import Requests" in body and 'data-testid="sync-redmine"' in body and 'data-testid="sync-jira"' in body
        assert "Enabled" in body and "Not configured" in body

    def test_non_admin_refused(self, as_role):
        client = as_role("APPROVER")
        assert client.get("/requests/sync").status_code == 403
        assert client.post("/requests/sync/redmine").status_code == 403

    def test_not_configured(self, admin_client, seeded):
        resp = admin_client.post("/requests/sync/redmine", headers=HX)
        assert resp.status_code == 200
        body = resp.content.decode()
        assert 'data-testid="sync-not-configured"' in body and "REDMINE is not configured" not in body
        assert "Redmine is not configured or not enabled." in body and 'href="/integrations"' in body
        assert "crud-refresh" not in events(resp)

    def test_configured_success_creates_requests(self, admin_client, seeded):
        _enable("REDMINE")
        result = SyncResult(system="REDMINE", direction="IMPORT", status=SyncStatus.SUCCESS, items_processed=2,
                            imported_items=[ExternalRequest(external_id="101", title="Issue A", priority="HIGH"),
                                            ExternalRequest(external_id="102", title="Issue B")])
        with mock.patch("apps.integrations.adapters.RedmineAdapter.import_requests", return_value=result):
            resp = admin_client.post("/requests/sync/redmine", headers=HX)
        assert resp.status_code == 200
        body = resp.content.decode()
        assert 'data-testid="sync-result"' in body and "inb-sync-SUCCESS" in body
        ev = events(resp)
        assert ev["crud-refresh"] and ev["toast"]["message"] == "Redmine: 2 created, 0 updated."
        ext_ids = Request.objects.filter(request_source="REDMINE").values_list("external_id", flat=True)
        assert set(ext_ids) == {"101", "102"}
        assert IntegrationConfig.objects.get(system_name="REDMINE").last_sync_at is not None

    def test_failed_result_shows_errors(self, admin_client, seeded):
        _enable("JIRA")
        result = SyncResult(system="JIRA", direction="IMPORT", status=SyncStatus.FAILED,
                            errors=["HTTP 401 Unauthorized"])
        with mock.patch("apps.integrations.adapters.JiraAdapter.import_requests", return_value=result):
            resp = admin_client.post("/requests/sync/jira", headers=HX)
        body = resp.content.decode()
        assert "inb-sync-FAILED" in body and "HTTP 401 Unauthorized" in body
        assert "crud-refresh" not in events(resp)

    def test_adapter_exception_shown_in_dialog(self, admin_client, seeded):
        _enable("JIRA")
        with mock.patch("apps.integrations.adapters.JiraAdapter.import_requests",
                        side_effect=ConnectionError("connection refused")):
            resp = admin_client.post("/requests/sync/jira", headers=HX)
        assert resp.status_code == 200
        assert "Sync failed: connection refused" in resp.content.decode()

    def test_unknown_system_and_get(self, admin_client, seeded):
        assert admin_client.post("/requests/sync/outline").status_code == 404
        assert admin_client.get("/requests/sync/redmine").status_code == 405
