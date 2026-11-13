"""Adapter tests ported from v3 ``tests/test_integrations.py`` (HTTP calls mocked)."""

import base64
from unittest.mock import MagicMock, patch

from apps.integrations.adapters.base import (
    SyncStatus,
)
from apps.integrations.adapters.email_adapter import EmailAdapter
from apps.integrations.adapters.jira_adapter import JiraAdapter
from apps.integrations.adapters.redmine_adapter import RedmineAdapter

# ── Adapter construction helpers ─────────────────────────


def _redmine_config(**overrides):
    base = {
        "base_url": "https://redmine.example.com",
        "api_key": "test-key-123",
        "username": "",
        "additional_config": {"project_id": "myproject", "timeout": 5},
    }
    base.update(overrides)
    return base


def _jira_config(**overrides):
    base = {
        "base_url": "https://jira.example.com",
        "api_key": "test-token",
        "username": "user@example.com",
        "additional_config": {
            "jql_filter": "project = TEST",
            "project_key": "TEST",
            "is_cloud": True,
            "timeout": 5,
        },
    }
    base.update(overrides)
    return base


def _email_config(**overrides):
    base = {
        "base_url": "",
        "api_key": "smtp-password",
        "username": "noreply@example.com",
        "additional_config": {
            "smtp_host": "smtp.example.com",
            "smtp_port": 587,
            "smtp_use_tls": True,
            "sender_email": "noreply@example.com",
            "sender_name": "Test Tool",
        },
    }
    base.update(overrides)
    return base


# ── RedmineAdapter tests ────────────────────────────────


class TestRedmineAdapter:
    def test_system_name(self):
        adapter = RedmineAdapter(_redmine_config())
        assert adapter.system_name == "REDMINE"

    def test_headers(self):
        adapter = RedmineAdapter(_redmine_config())
        headers = adapter._headers()
        assert headers["X-Redmine-API-Key"] == "test-key-123"
        assert headers["Content-Type"] == "application/json"

    def test_url_construction(self):
        adapter = RedmineAdapter(_redmine_config())
        assert adapter._url("/issues.json") == "https://redmine.example.com/issues.json"

    def test_test_connection_no_url(self):
        adapter = RedmineAdapter(_redmine_config(base_url=""))
        result = adapter.test_connection()
        assert result.success is False
        assert "not configured" in result.message

    def test_test_connection_no_key(self):
        adapter = RedmineAdapter(_redmine_config(api_key=""))
        result = adapter.test_connection()
        assert result.success is False

    @patch("apps.integrations.adapters.redmine_adapter.http_requests.get")
    def test_test_connection_success(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "user": {"id": 1, "login": "admin", "firstname": "John", "lastname": "Doe"}
        }
        mock_get.return_value = mock_resp

        adapter = RedmineAdapter(_redmine_config())
        result = adapter.test_connection()
        assert result.success is True
        assert "John Doe" in result.message

    @patch("apps.integrations.adapters.redmine_adapter.http_requests.get")
    def test_test_connection_failure(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.status_code = 401
        mock_resp.text = "Unauthorized"
        mock_get.return_value = mock_resp

        adapter = RedmineAdapter(_redmine_config())
        result = adapter.test_connection()
        assert result.success is False
        assert "401" in result.message

    def test_import_no_project_id(self):
        config = _redmine_config()
        config["additional_config"]["project_id"] = ""
        adapter = RedmineAdapter(config)
        result = adapter.import_requests()
        assert result.status == SyncStatus.FAILED

    @patch("apps.integrations.adapters.redmine_adapter.http_requests.get")
    def test_import_success(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = {
            "issues": [
                {
                    "id": 101,
                    "subject": "Test Issue",
                    "description": "A test",
                    "author": {"name": "Alice"},
                    "priority": {"name": "High"},
                    "due_date": "2026-03-15",
                }
            ]
        }
        mock_get.return_value = mock_resp

        adapter = RedmineAdapter(_redmine_config())
        result = adapter.import_requests()
        assert result.status == SyncStatus.SUCCESS
        assert result.items_processed == 1
        assert result.items_created == 1

    def test_export_no_external_id(self):
        adapter = RedmineAdapter(_redmine_config())
        result = adapter.export_estimation({"grand_total_hours": 100})
        assert result.status == SyncStatus.SKIPPED

    @patch("apps.integrations.adapters.redmine_adapter.http_requests.put")
    def test_export_success(self, mock_put):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_put.return_value = mock_resp

        adapter = RedmineAdapter(_redmine_config())
        result = adapter.export_estimation({
            "external_id": "101",
            "grand_total_hours": 100,
            "feasibility_status": "FEASIBLE",
            "estimation_number": "EST-2026-001",
        })
        assert result.status == SyncStatus.SUCCESS
        assert result.items_updated == 1


# ── JiraAdapter tests ───────────────────────────────────


class TestJiraAdapter:
    def test_system_name(self):
        adapter = JiraAdapter(_jira_config())
        assert adapter.system_name == "JIRA"

    def test_cloud_auth_header(self):
        adapter = JiraAdapter(_jira_config())
        headers = adapter._headers()
        assert "Basic" in headers["Authorization"]

    def test_server_bearer_auth(self):
        config = _jira_config(username="")
        config["additional_config"]["is_cloud"] = False
        adapter = JiraAdapter(config)
        headers = adapter._headers()
        assert "Bearer" in headers["Authorization"]

    def test_url_construction(self):
        adapter = JiraAdapter(_jira_config())
        url = adapter._url("search")
        assert url == "https://jira.example.com/rest/api/2/search"

    def test_test_connection_no_url(self):
        adapter = JiraAdapter(_jira_config(base_url=""))
        result = adapter.test_connection()
        assert result.success is False

    @patch("apps.integrations.adapters.jira_adapter.http_requests.request")
    def test_test_connection_success(self, mock_request):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"displayName": "John", "accountId": "abc"}
        mock_request.return_value = mock_resp

        adapter = JiraAdapter(_jira_config())
        result = adapter.test_connection()
        assert result.success is True
        assert "John" in result.message

    def test_import_no_jql(self):
        config = _jira_config()
        config["additional_config"]["jql_filter"] = ""
        adapter = JiraAdapter(config)
        result = adapter.import_requests()
        assert result.status == SyncStatus.FAILED
        assert "JQL" in result.errors[0]

    @patch("apps.integrations.adapters.jira_adapter.http_requests.request")
    def test_import_success(self, mock_request):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = {
            "issues": [
                {
                    "key": "TEST-1",
                    "fields": {
                        "summary": "Jira Issue",
                        "description": "A test",
                        "reporter": {"displayName": "Bob", "emailAddress": "bob@test.com"},
                        "priority": {"name": "High"},
                        "duedate": "2026-04-01",
                        "status": {"name": "Open"},
                        "issuetype": {"name": "Task"},
                    },
                }
            ]
        }
        mock_request.return_value = mock_resp

        adapter = JiraAdapter(_jira_config())
        result = adapter.import_requests()
        assert result.status == SyncStatus.SUCCESS
        assert result.items_created == 1

    def test_export_no_external_id(self):
        adapter = JiraAdapter(_jira_config())
        result = adapter.export_estimation({})
        assert result.status == SyncStatus.SKIPPED

    @patch("apps.integrations.adapters.jira_adapter.http_requests.request")
    def test_export_with_field_mappings(self, mock_request):
        # PUT for field update returns 204, POST for comment returns 201
        mock_request.side_effect = [
            MagicMock(status_code=204),
            MagicMock(status_code=201),
        ]

        config = _jira_config()
        config["additional_config"]["field_mappings"] = {
            "effort_hours": "customfield_10001",
        }
        adapter = JiraAdapter(config)
        result = adapter.export_estimation({
            "external_id": "TEST-1",
            "grand_total_hours": 120,
            "feasibility_status": "FEASIBLE",
            "estimation_number": "EST-001",
        })
        assert result.status == SyncStatus.SUCCESS

    def test_xray_disabled(self):
        adapter = JiraAdapter(_jira_config())
        result = adapter.create_xray_test_plan({})
        assert result.status == SyncStatus.SKIPPED

    def test_dc_pat_auth(self):
        """Data Center with Personal Access Token uses Bearer auth."""
        config = _jira_config(username="")
        config["additional_config"]["is_cloud"] = False
        config["additional_config"]["auth_mode"] = "pat"
        adapter = JiraAdapter(config)
        headers = adapter._headers()
        assert headers["Authorization"] == "Bearer test-token"

    def test_dc_basic_auth(self):
        """Data Center with username+password uses Basic auth."""
        config = _jira_config()
        config["additional_config"]["is_cloud"] = False
        config["additional_config"]["auth_mode"] = "basic"
        adapter = JiraAdapter(config)
        headers = adapter._headers()
        assert "Basic" in headers["Authorization"]

    def test_ssl_verify_default(self):
        adapter = JiraAdapter(_jira_config())
        assert adapter.ssl_verify is True

    def test_ssl_verify_disabled(self):
        config = _jira_config()
        config["additional_config"]["ssl_verify"] = False
        adapter = JiraAdapter(config)
        assert adapter.ssl_verify is False

    def test_flat_field_mappings(self):
        """Flat UI keys (effort_hours_field) should map to field_mappings dict."""
        config = _jira_config()
        config["additional_config"]["effort_hours_field"] = "customfield_10001"
        config["additional_config"]["feasibility_field"] = "customfield_10002"
        adapter = JiraAdapter(config)
        assert adapter.field_mappings["effort_hours"] == "customfield_10001"
        assert adapter.field_mappings["feasibility"] == "customfield_10002"

    def test_original_estimate_mapping(self):
        """'originalEstimate' keyword maps to Jira built-in time tracking."""
        config = _jira_config()
        config["additional_config"]["effort_hours_field"] = "originalEstimate"
        adapter = JiraAdapter(config)
        assert adapter.field_mappings["effort_hours"] == "originalEstimate"

    @patch("apps.integrations.adapters.jira_adapter.http_requests.request")
    def test_export_original_estimate(self, mock_request):
        """Export with originalEstimate uses timetracking field."""
        mock_request.side_effect = [
            MagicMock(status_code=204),  # PUT fields
            MagicMock(status_code=201),  # POST comment
        ]
        config = _jira_config()
        config["additional_config"]["effort_hours_field"] = "originalEstimate"
        adapter = JiraAdapter(config)
        result = adapter.export_estimation({
            "external_id": "TEST-1",
            "grand_total_hours": 511.5,
            "feasibility_status": "AT_RISK",
            "estimation_number": "EST-001",
        })
        assert result.status == SyncStatus.SUCCESS
        # Verify the PUT call used timetracking
        put_call = mock_request.call_args_list[0]
        body = put_call.kwargs.get("json") or put_call[1].get("json", {})
        assert "timetracking" in body.get("fields", {})
        assert body["fields"]["timetracking"]["originalEstimate"] == "512h"

    def test_dc_pat_auth_with_username(self):
        """DC with auth_mode=pat and username set must still use Bearer, not Basic."""
        config = _jira_config(username="admin")
        config["additional_config"]["is_cloud"] = False
        config["additional_config"]["auth_mode"] = "pat"
        adapter = JiraAdapter(config)
        headers = adapter._headers()
        assert headers["Authorization"] == "Bearer test-token"

    def test_dc_auto_auth_with_username(self):
        """DC with auth_mode=auto and username set uses Basic auth."""
        config = _jira_config(username="admin")
        config["additional_config"]["is_cloud"] = False
        config["additional_config"]["auth_mode"] = "auto"
        adapter = JiraAdapter(config)
        headers = adapter._headers()
        assert "Basic" in headers["Authorization"]

    def test_dc_auto_auth_no_username(self):
        """DC with auth_mode=auto and no username defaults to Bearer (PAT)."""
        config = _jira_config(username="")
        config["additional_config"]["is_cloud"] = False
        config["additional_config"]["auth_mode"] = "auto"
        adapter = JiraAdapter(config)
        headers = adapter._headers()
        assert headers["Authorization"] == "Bearer test-token"

    def test_test_connection_no_key(self):
        config = _jira_config(api_key="")
        adapter = JiraAdapter(config)
        result = adapter.test_connection()
        assert not result.success
        assert "token" in result.message.lower()

    @patch("apps.integrations.adapters.jira_adapter.http_requests.request")
    def test_test_connection_401(self, mock_request):
        """HTTP 401 gives a clear auth failure message."""
        mock_request.return_value = MagicMock(status_code=401, text="Unauthorized")
        config = _jira_config()
        config["additional_config"]["is_cloud"] = False
        adapter = JiraAdapter(config)
        result = adapter.test_connection()
        assert not result.success
        assert "401" in result.message
        assert "auth" in result.message.lower()

    @patch("apps.integrations.adapters.jira_adapter.http_requests.request")
    def test_test_connection_404(self, mock_request):
        """HTTP 404 hints at context path issue."""
        mock_request.return_value = MagicMock(status_code=404, text="Not Found")
        config = _jira_config()
        config["additional_config"]["is_cloud"] = False
        adapter = JiraAdapter(config)
        result = adapter.test_connection()
        assert not result.success
        assert "404" in result.message
        assert "context path" in result.message.lower()

    @patch("apps.integrations.adapters.jira_adapter.http_requests.request")
    def test_test_connection_ssl_error(self, mock_request):
        """SSL errors produce a clear hint about self-signed certs."""
        import requests as _req
        mock_request.side_effect = _req.exceptions.SSLError("SSL: CERTIFICATE_VERIFY_FAILED")
        config = _jira_config()
        adapter = JiraAdapter(config)
        result = adapter.test_connection()
        assert not result.success
        assert "ssl" in result.message.lower()

    @patch("apps.integrations.adapters.jira_adapter.http_requests.request")
    def test_test_connection_proxy_error(self, mock_request):
        """A proxy tunnel failure hints at the 'Bypass proxy' option."""
        import requests as _req
        mock_request.side_effect = _req.exceptions.ProxyError(
            "Tunnel connection failed: 502 Bad Gateway"
        )
        adapter = JiraAdapter(_jira_config())
        result = adapter.test_connection()
        assert not result.success
        assert "bypass proxy" in result.message.lower()

    @patch("apps.integrations.adapters.jira_adapter.http_requests.request")
    def test_bypass_proxy_forces_direct_connection(self, mock_request):
        """bypass_proxy makes _request override env proxies with a direct route."""
        mock_request.return_value = MagicMock(
            status_code=200,
            json=MagicMock(return_value={"displayName": "John"}),
        )
        config = _jira_config()
        config["additional_config"]["bypass_proxy"] = True
        adapter = JiraAdapter(config)
        assert adapter.bypass_proxy is True

        adapter.test_connection()
        _, kwargs = mock_request.call_args
        assert kwargs["proxies"] == {"http": None, "https": None}

    @patch("apps.integrations.adapters.jira_adapter.http_requests.request")
    def test_no_bypass_proxy_by_default(self, mock_request):
        """Without bypass_proxy, _request does not touch proxy settings."""
        mock_request.return_value = MagicMock(
            status_code=200,
            json=MagicMock(return_value={"displayName": "John"}),
        )
        adapter = JiraAdapter(_jira_config())
        assert adapter.bypass_proxy is False

        adapter.test_connection()
        _, kwargs = mock_request.call_args
        assert "proxies" not in kwargs

    # -- PR-items connection -------------------------------------------------

    def test_pr_headers_use_pr_token_when_set(self):
        config = _jira_config()
        config["additional_config"]["pr_api_key"] = "pr-secret"
        adapter = JiraAdapter(config)
        expected = "Basic " + base64.b64encode(
            b"user@example.com:pr-secret"
        ).decode()
        assert adapter._pr_headers()["Authorization"] == expected

    def test_pr_headers_fall_back_to_main_key(self):
        adapter = JiraAdapter(_jira_config())  # no pr_api_key
        assert adapter._pr_headers() == adapter._headers()

    @patch("apps.integrations.adapters.jira_adapter.http_requests.request")
    def test_pr_request_bypasses_proxy_when_pr_toggle_on(self, mock_request):
        mock_request.return_value = MagicMock(status_code=200)
        config = _jira_config()
        config["additional_config"]["pr_bypass_proxy"] = True
        adapter = JiraAdapter(config)
        adapter._pr_request("GET", adapter._url("myself"))
        _, kwargs = mock_request.call_args
        assert kwargs["proxies"] == {"http": None, "https": None}

    @patch("apps.integrations.adapters.jira_adapter.http_requests.request")
    def test_pr_request_bypasses_proxy_when_main_toggle_on(self, mock_request):
        """Main bypass implies PR bypass (same host)."""
        mock_request.return_value = MagicMock(status_code=200)
        config = _jira_config()
        config["additional_config"]["bypass_proxy"] = True
        adapter = JiraAdapter(config)
        assert adapter.pr_bypass_proxy is False
        adapter._pr_request("GET", adapter._url("myself"))
        _, kwargs = mock_request.call_args
        assert kwargs["proxies"] == {"http": None, "https": None}

    @patch("apps.integrations.adapters.jira_adapter.http_requests.request")
    def test_test_pr_connection_success(self, mock_request):
        mock_request.return_value = MagicMock(
            status_code=200,
            json=MagicMock(return_value={"displayName": "PR Bot"}),
        )
        config = _jira_config()
        config["additional_config"]["pr_api_key"] = "pr-secret"
        adapter = JiraAdapter(config)
        result = adapter.test_pr_connection()
        assert result.success is True
        assert "PR Bot" in result.message
        assert "PR token" in result.message

    @patch("apps.integrations.adapters.jira_adapter.http_requests.request")
    def test_test_pr_connection_proxy_hint(self, mock_request):
        import requests as _req
        mock_request.side_effect = _req.exceptions.ProxyError(
            "Tunnel connection failed: 502 Bad Gateway"
        )
        adapter = JiraAdapter(_jira_config())
        result = adapter.test_pr_connection()
        assert not result.success
        assert "bypass proxy" in result.message.lower()

    def test_test_pr_connection_no_key(self):
        config = _jira_config(api_key="")
        adapter = JiraAdapter(config)
        result = adapter.test_pr_connection()
        assert not result.success
        assert "api key" in result.message.lower()


# ── EmailAdapter tests ──────────────────────────────────


class TestEmailAdapter:
    def test_system_name(self):
        adapter = EmailAdapter(_email_config())
        assert adapter.system_name == "EMAIL"

    def test_test_connection_no_host(self):
        config = _email_config()
        config["additional_config"]["smtp_host"] = ""
        adapter = EmailAdapter(config)
        result = adapter.test_connection()
        assert result.success is False
        assert "not configured" in result.message

    def test_import_not_implemented(self):
        adapter = EmailAdapter(_email_config())
        result = adapter.import_requests()
        assert result.status == SyncStatus.SKIPPED
        assert "not yet implemented" in result.errors[0].lower()

    def test_export_no_email(self):
        adapter = EmailAdapter(_email_config())
        result = adapter.export_estimation({"grand_total_hours": 100})
        assert result.status == SyncStatus.SKIPPED

    def test_send_report_no_host(self):
        config = _email_config()
        config["additional_config"]["smtp_host"] = ""
        adapter = EmailAdapter(config)
        result = adapter.send_report("to@test.com", "Subject", "<p>Body</p>")
        assert result.status == SyncStatus.FAILED
