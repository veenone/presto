"""Integration endpoints, the webhook and estimation export.

Ported from v3 tests/test_integrations.py (TestIntegrationAPI), plus tests for
the endpoints v3 left untested. External systems are mocked.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from apps.integrations.adapters import (
    ConnectionTestResult,
    JiraAdapter,
    OutlineAdapter,
    SnipeItAdapter,
    SyncResult,
    SyncStatus,
)
from apps.integrations.models import IntegrationConfig
from apps.notifications.models import WebhookNotification


def _create_integration_config(api, system_name="REDMINE"):
    return api.put(
        f"/api/integrations/{system_name}",
        json={"base_url": "https://redmine.example.com", "api_key": "test-key", "enabled": True},
    )


def _config(system_name, **extra) -> IntegrationConfig:
    defaults = {"base_url": "https://x.example.com", "api_key": "k", "enabled": True}
    defaults.update(extra)
    return IntegrationConfig.objects.create(system_name=system_name, **defaults)


def _sync_result(**kw) -> SyncResult:
    base = {"system": "REDMINE", "direction": "IMPORT", "status": SyncStatus.SUCCESS}
    base.update(kw)
    return SyncResult(**base)


class TestIntegrationAPI:
    def test_list_integrations_empty(self, api):
        resp = api.get("/api/integrations")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_create_and_get_integration(self, api):
        resp = _create_integration_config(api)
        assert resp.status_code == 200
        data = resp.json()
        assert data["system_name"] == "REDMINE"
        assert data["enabled"] is True
        assert data["has_api_key"] is True
        assert "api_key" not in data

        resp2 = api.get("/api/integrations/REDMINE")
        assert resp2.status_code == 200
        assert resp2.json()["system_name"] == "REDMINE"

    def test_get_is_case_insensitive(self, api):
        _create_integration_config(api)
        assert api.get("/api/integrations/redmine").json()["system_name"] == "REDMINE"

    def test_get_integration_not_found(self, api):
        resp = api.get("/api/integrations/NONEXISTENT")
        assert resp.status_code == 404
        assert resp.json()["detail"] == "Integration NONEXISTENT not found"

    def test_update_integration(self, api):
        _create_integration_config(api)
        resp = api.put("/api/integrations/REDMINE", json={"enabled": False})
        assert resp.status_code == 200
        assert resp.json()["enabled"] is False
        assert resp.json()["base_url"] == "https://redmine.example.com"

    def test_update_additional_config_json(self, api):
        resp = api.put("/api/integrations/jira", json={"additional_config_json": '{"project_key": "T"}'})
        assert resp.status_code == 200
        assert resp.json()["system_name"] == "JIRA"
        assert resp.json()["additional_config_json"] == '{"project_key": "T"}'
        assert IntegrationConfig.objects.get(system_name="JIRA").additional_config == {"project_key": "T"}

    def test_update_rejects_invalid_additional_config(self, api):
        resp = api.put("/api/integrations/JIRA", json={"additional_config_json": "not json"})
        assert resp.status_code == 400

    def test_update_requires_admin(self, api_as, make_user):
        resp = api_as(make_user("APPROVER")).put("/api/integrations/REDMINE", json={"enabled": True})
        assert resp.status_code == 403

    def test_list_integrations_after_create(self, api):
        _create_integration_config(api, "REDMINE")
        _create_integration_config(api, "JIRA")
        resp = api.get("/api/integrations")
        assert resp.status_code == 200
        names = [i["system_name"] for i in resp.json()]
        assert "REDMINE" in names
        assert "JIRA" in names

    def test_list_requires_auth(self, anon_api):
        assert anon_api.get("/api/integrations").status_code == 401

    def test_integration_status(self, api):
        _create_integration_config(api)
        resp = api.get("/api/integrations/REDMINE/status")
        assert resp.status_code == 200
        data = resp.json()
        assert data["system_name"] == "REDMINE"
        assert data["enabled"] is True
        assert data["configured"] is True
        assert data["last_sync_at"] is None

    def test_integration_status_not_found(self, api):
        assert api.get("/api/integrations/NONEXISTENT/status").status_code == 404

    def test_test_integration_not_configured(self, api):
        resp = api.post("/api/integrations/REDMINE/test")
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is False
        assert data["message"] == "REDMINE is not configured or not enabled."

    def test_test_integration_success(self, api):
        _config("REDMINE")
        with patch.object(
            __import__("apps.integrations.adapters", fromlist=["RedmineAdapter"]).RedmineAdapter,
            "test_connection", return_value=ConnectionTestResult(True, "ok", {"user": "x"}),
        ):
            resp = api.post("/api/integrations/redmine/test")
        assert resp.json() == {"success": True, "message": "ok", "details": {"user": "x"}}

    def test_test_integration_requires_admin(self, api_as, make_user):
        assert api_as(make_user("APPROVER")).post("/api/integrations/REDMINE/test").status_code == 403

    def test_sync_not_configured(self, api):
        resp = api.post("/api/integrations/REDMINE/sync")
        assert resp.status_code == 200
        assert resp.json()["status"] == "FAILED"

    def test_sync_import_creates_requests(self, api):
        from apps.integrations.adapters import ExternalRequest, RedmineAdapter

        _config("REDMINE")
        items = [ExternalRequest(external_id="7", title="From Redmine", requester_name="PO", priority="HIGH")]
        result = _sync_result(items_processed=1, imported_items=items)
        with patch.object(RedmineAdapter, "import_requests", return_value=result):
            resp = api.post("/api/integrations/REDMINE/sync")
        data = resp.json()
        assert data["status"] == "SUCCESS"
        assert data["items_created"] == 1
        reqs = api.get("/api/requests").json()
        assert reqs[0]["title"] == "From Redmine"
        assert reqs[0]["request_source"] == "REDMINE"
        assert reqs[0]["request_number"].startswith("REQ-")
        assert IntegrationConfig.objects.get(system_name="REDMINE").last_sync_at is not None

    def test_sync_outline_exports_all(self, api):
        _config("OUTLINE")
        with patch("apps.integrations.service.sync_export_all", return_value=_sync_result(
            system="OUTLINE", direction="EXPORT", items_processed=0,
        )) as export_all:
            resp = api.post("/api/integrations/outline/sync")
        export_all.assert_called_once_with("OUTLINE")
        assert resp.json()["direction"] == "EXPORT"

    def test_sync_requires_admin(self, api_as, make_user):
        assert api_as(make_user("APPROVER")).post("/api/integrations/REDMINE/sync").status_code == 403


class TestRedmineWebhook:
    def test_disabled(self, anon_api):
        resp = anon_api.post("/api/webhooks/redmine")
        assert resp.status_code == 400
        assert resp.json()["detail"] == "Redmine integration is not enabled"

    def test_rejects_bad_token(self, anon_api):
        _config("REDMINE", additional_config={"webhook_secret": "s3cret"})
        assert anon_api.post("/api/webhooks/redmine").status_code == 403
        resp = anon_api.post("/api/webhooks/redmine?token=wrong")
        assert resp.status_code == 403
        assert resp.json()["detail"] == "Invalid webhook token"

    def test_accepts_token_and_notifies_watchers(self, anon_api, admin_user):
        from apps.core import config

        _config("REDMINE", additional_config={"webhook_secret": "s3cret"})
        config.set_value("webhook_watchers", [admin_user.id])
        with patch("apps.integrations.service.sync_import", return_value=_sync_result(
            items_created=2, items_updated=1,
        )):
            resp = anon_api.post("/api/webhooks/redmine?token=s3cret")
        assert resp.status_code == 200
        assert resp.json() == {"status": "ok", "items_created": 2, "items_updated": 1}
        notif = WebhookNotification.objects.get(user=admin_user)
        assert notif.title == "2 new request(s) imported"

    def test_no_secret_needs_no_token(self, anon_api):
        _config("REDMINE")
        with patch("apps.integrations.service.sync_import", return_value=_sync_result()):
            assert anon_api.post("/api/webhooks/redmine").status_code == 200

    def test_bad_watcher_does_not_break_webhook(self, anon_api):
        from apps.core import config

        _config("REDMINE")
        config.set_value("webhook_watchers", [99999])
        with patch("apps.integrations.service.sync_import", return_value=_sync_result(items_created=1)):
            resp = anon_api.post("/api/webhooks/redmine")
        assert resp.status_code == 200
        assert WebhookNotification.objects.count() == 0

    def test_sync_error_is_500(self, anon_api):
        _config("REDMINE")
        with patch("apps.integrations.service.sync_import", side_effect=RuntimeError("boom")):
            resp = anon_api.post("/api/webhooks/redmine")
        assert resp.status_code == 500
        assert resp.json()["detail"] == "Webhook processing failed: boom"


@pytest.fixture
def estimation(db):
    from apps.estimations.models import Estimation, EstimationTask

    est = Estimation.objects.create(
        project_name="Proj", project_type="NEW", estimation_number="EST-2026-001",
        grand_total_hours=100, grand_total_days=10,
        wizard_inputs={"pr_fixes": {"simple": 2}, "pr_details": []},
    )
    EstimationTask.objects.create(estimation=est, task_name="Exec", task_type="EXECUTION", calculated_hours=8)
    return est


def _linked_request(estimation, source="REDMINE", external_id="55"):
    from apps.inbox.models import Request

    req = Request.objects.create(
        request_number="REQ-1", title="T", requester_name="X", received_date="2026-01-01",
        request_source=source, external_id=external_id,
    )
    estimation.request = req
    estimation.save()
    return req


class TestEstimationExport:
    def test_not_found(self, api):
        assert api.post("/api/estimations/9999/export").status_code == 404

    def test_not_linked(self, api, estimation):
        resp = api.post(f"/api/estimations/{estimation.id}/export")
        assert resp.status_code == 400
        assert resp.json()["detail"] == "Estimation is not linked to a request"

    def test_no_external_id(self, api, estimation):
        _linked_request(estimation, external_id=None)
        resp = api.post(f"/api/estimations/{estimation.id}/export")
        assert resp.json()["detail"] == "Linked request has no external ID"

    def test_manual_source(self, api, estimation):
        _linked_request(estimation, source="MANUAL")
        resp = api.post(f"/api/estimations/{estimation.id}/export")
        assert resp.status_code == 400
        assert "MANUAL" in resp.json()["detail"]

    def test_export_success(self, api, estimation):
        _linked_request(estimation)
        with patch("apps.integrations.service.sync_export", return_value=_sync_result(
            direction="EXPORT", items_updated=1,
        )) as export:
            resp = api.post(f"/api/estimations/{estimation.id}/export")
        assert resp.status_code == 200
        assert resp.json() == {"status": "SUCCESS", "system": "REDMINE", "items_updated": 1, "errors": []}
        system, data = export.call_args.args
        assert system == "REDMINE"
        assert data["external_id"] == "55"
        assert data["estimation_number"] == "EST-2026-001"

    def test_requires_estimator(self, api_as, make_user, estimation):
        resp = api_as(make_user("VIEWER")).post(f"/api/estimations/{estimation.id}/export")
        assert resp.status_code == 403


class TestExportTasks:
    def test_no_system(self, api, estimation):
        resp = api.post(f"/api/estimations/{estimation.id}/export-tasks")
        assert resp.status_code == 400
        assert resp.json()["detail"].startswith("No target system specified")

    def test_not_found(self, api):
        assert api.post("/api/estimations/9999/export-tasks").status_code == 404

    def test_explicit_target(self, api, estimation):
        with patch("apps.integrations.service.sync_export_task_breakdown", return_value=_sync_result(
            system="JIRA", direction="EXPORT", items_created=1, items_processed=1,
        )) as export:
            resp = api.post(
                f"/api/estimations/{estimation.id}/export-tasks",
                json={"target_system": "jira", "issue_key": "TEST-1"},
            )
        assert resp.status_code == 200
        assert resp.json()["items_created"] == 1
        system, data = export.call_args.args
        assert system == "JIRA"
        assert data["external_id"] == "TEST-1"
        assert data["tasks"][0]["task_name"] == "Exec"
        assert data["pr_simple"] == 2

    def test_from_linked_request(self, api, estimation):
        _linked_request(estimation)
        with patch("apps.integrations.service.sync_export_task_breakdown",
                   return_value=_sync_result(direction="EXPORT")) as export:
            api.post(f"/api/estimations/{estimation.id}/export-tasks", json={})
        system, data = export.call_args.args
        assert system == "REDMINE"
        assert data["external_id"] == "55"

    def test_invalid_body_is_422(self, api, estimation):
        resp = api.post(f"/api/estimations/{estimation.id}/export-tasks", json={"target_system": 5})
        assert resp.status_code == 422

    def test_unconfigured_target_fails(self, api, estimation):
        resp = api.post(f"/api/estimations/{estimation.id}/export-tasks", json={"target_system": "JIRA"})
        assert resp.status_code == 200
        assert resp.json()["status"] == "FAILED"


class TestOutline:
    def test_publish_not_configured(self, api, estimation):
        resp = api.post(f"/api/integrations/outline/publish/{estimation.id}")
        assert resp.status_code == 400
        assert resp.json()["detail"] == "Outline integration is not configured"

    def test_publish_not_found(self, api):
        assert api.post("/api/integrations/outline/publish/9999").status_code == 404

    def test_publish_success(self, api, estimation):
        _config("OUTLINE")
        with patch.object(OutlineAdapter, "export_estimation", return_value=_sync_result(
            system="OUTLINE", direction="EXPORT", items_created=1,
        )) as export:
            resp = api.post(f"/api/integrations/outline/publish/{estimation.id}")
        assert resp.status_code == 200, resp.content
        assert resp.json() == {"status": "SUCCESS", "items_created": 1, "items_updated": 0, "errors": []}
        data = export.call_args.args[0]
        assert data["working_weeks"] == 2.0
        assert data["working_hours_per_day"] == 7.0
        assert isinstance(data["risk_messages"], list)

    def test_publish_requires_estimator(self, api_as, make_user, estimation):
        resp = api_as(make_user("VIEWER")).post(f"/api/integrations/outline/publish/{estimation.id}")
        assert resp.status_code == 403

    def test_search_not_configured(self, api):
        resp = api.get("/api/integrations/outline/search", params={"query": "x"})
        assert resp.status_code == 400

    def test_search(self, api):
        _config("OUTLINE")
        with patch.object(OutlineAdapter, "search_documents", return_value=[{"title": "Doc"}]) as search:
            resp = api.get("/api/integrations/outline/search", params={"query": "sim", "limit": 3})
        assert resp.status_code == 200
        assert resp.json() == [{"title": "Doc"}]
        search.assert_called_once_with("sim", 3)

    def test_search_requires_query(self, api):
        assert api.get("/api/integrations/outline/search").status_code == 422


class TestSnipeIt:
    def test_assets_not_configured(self, api):
        resp = api.get("/api/integrations/SNIPE_IT/assets")
        assert resp.status_code == 400
        assert resp.json()["detail"] == "Snipe-IT integration is not configured or not enabled."

    def test_assets(self, api):
        _config("SNIPE_IT")
        with patch.object(SnipeItAdapter, "get_hardware_by_category", return_value=[{"id": 1}]) as get:
            resp = api.get("/api/integrations/SNIPE_IT/assets", params={"categories": "SIM, eSIM,"})
        assert resp.status_code == 200
        assert resp.json() == [{"id": 1}]
        get.assert_called_once_with(["SIM", "eSIM"])

    def test_categories(self, api):
        _config("SNIPE_IT")
        with patch.object(SnipeItAdapter, "get_categories", return_value=[{"name": "SIM"}]):
            resp = api.get("/api/integrations/SNIPE_IT/categories")
        assert resp.status_code == 200
        assert resp.json() == [{"name": "SIM"}]

    def test_categories_not_configured(self, api):
        assert api.get("/api/integrations/SNIPE_IT/categories").status_code == 400

    def test_requires_auth(self, anon_api):
        assert anon_api.get("/api/integrations/SNIPE_IT/assets").status_code == 401


class TestJiraPr:
    def test_pr_items_not_configured(self, api):
        resp = api.get("/api/integrations/JIRA/pr-items")
        assert resp.status_code == 400
        assert resp.json()["detail"] == "JIRA integration not configured or not enabled."

    def test_pr_items_no_jql(self, api):
        _config("JIRA")
        resp = api.get("/api/integrations/JIRA/pr-items")
        assert resp.status_code == 400
        assert resp.json()["detail"] == "No PR JQL filter provided."

    def test_pr_items(self, api):
        _config("JIRA", base_url="https://jira.example.com",
                additional_config={"pr_jql_filter": "type = Bug"})
        resp_mock = MagicMock(status_code=200)
        resp_mock.json.return_value = {"issues": [{
            "key": "BUG-1",
            "fields": {
                "summary": "Crash", "priority": {"name": "High"}, "status": {"name": "Open"},
                "issuetype": {"name": "Bug"}, "created": "2026-01-01", "description": {"type": "doc"},
            },
        }]}
        with patch.object(JiraAdapter, "_pr_request", return_value=resp_mock) as req:
            resp = api.get("/api/integrations/JIRA/pr-items")
        assert resp.status_code == 200
        assert resp.json() == [{
            "key": "BUG-1", "summary": "Crash", "priority": "High", "status": "Open",
            "issue_type": "Bug", "created": "2026-01-01", "description": "",
            "link": "https://jira.example.com/browse/BUG-1",
        }]
        assert req.call_args.kwargs["params"]["jql"] == "type = Bug"

    def test_pr_items_jira_error(self, api):
        _config("JIRA")
        resp_mock = MagicMock(status_code=400, text="bad")
        resp_mock.json.return_value = {"errorMessages": ["Field 'x' does not exist"]}
        with patch.object(JiraAdapter, "_pr_request", return_value=resp_mock):
            resp = api.get("/api/integrations/JIRA/pr-items", params={"jql": "x = 1"})
        assert resp.status_code == 400
        assert resp.json()["detail"] == "Jira returned HTTP 400: Field 'x' does not exist"

    def test_pr_items_exception(self, api):
        _config("JIRA")
        with patch.object(JiraAdapter, "_pr_request", side_effect=RuntimeError("net")):
            resp = api.get("/api/integrations/JIRA/pr-items", params={"jql": "x"})
        assert resp.status_code == 500
        assert resp.json()["detail"] == "Failed to fetch PR items: net"

    def test_pr_test_connection(self, api):
        _config("JIRA")
        with patch.object(JiraAdapter, "test_pr_connection", return_value=ConnectionTestResult(True, "fine")):
            resp = api.post("/api/integrations/JIRA/pr-test-connection")
        assert resp.status_code == 200
        assert resp.json() == {"success": True, "message": "fine", "details": {}}

    def test_pr_test_connection_not_configured(self, api):
        assert api.post("/api/integrations/JIRA/pr-test-connection").status_code == 400

    def test_pr_test_connection_requires_admin(self, api_as, make_user):
        resp = api_as(make_user("APPROVER")).post("/api/integrations/JIRA/pr-test-connection")
        assert resp.status_code == 403


class TestRouting:
    """Literal /integrations/... paths are not shadowed by /integrations/{system_name}/... routes."""

    @pytest.mark.parametrize("method,path", [
        ("get", "/api/integrations/SNIPE_IT/assets"),
        ("get", "/api/integrations/SNIPE_IT/categories"),
        ("get", "/api/integrations/JIRA/pr-items"),
        ("post", "/api/integrations/JIRA/pr-test-connection"),
        ("get", "/api/integrations/outline/search?query=x"),
        ("post", "/api/integrations/outline/publish/9999"),
    ])
    def test_literal_paths_reach_their_view(self, api, method, path):
        resp = getattr(api, method)(path)
        assert resp.status_code != 405, path
        # Unrouted paths give Django's HTML 404; our views answer JSON.
        assert resp["Content-Type"].startswith("application/json"), (path, resp.status_code)
        if resp.status_code == 404:
            assert resp.json()["detail"] == "Estimation not found"

    def test_parameterised_paths_still_work_for_snipe_it(self, api):
        _config("SNIPE_IT")
        assert api.get("/api/integrations/SNIPE_IT").status_code == 200
        assert api.get("/api/integrations/SNIPE_IT/status").status_code == 200
        assert api.put("/api/integrations/SNIPE_IT", json={"enabled": False}).status_code == 200
