"""Integrations page (/integrations): access, tabs, every field type, secrets, test/sync, webhook."""

import json
from unittest import mock

import pytest

from apps.accounts.models import AuditLog
from apps.core import config
from apps.integrations import config_pages
from apps.integrations.adapters import (
    ConnectionTestResult,
    JiraAdapter,
    RedmineAdapter,
    SnipeItAdapter,
    SyncResult,
    SyncStatus,
)
from apps.integrations.models import IntegrationConfig
from tests.conftest import ApiClient

pytestmark = pytest.mark.django_db
HX = {"HX-Request": "true"}
SECRET_VALUES = ("api-key-SECRET-1", "pr-key-SECRET-2", "hook-SECRET-3")


@pytest.fixture
def viewer(web_login, make_user, seeded):
    return web_login(make_user("VIEWER"))


@pytest.fixture
def api_get(client, admin_user):
    """GET /api/integrations/{system} as admin (separate client: keeps the web session)."""
    from django.test import Client

    api = ApiClient(Client(), admin_user)
    return lambda system: api.get(f"/api/integrations/{system}")


def form_data(system: str, **overrides) -> dict:
    """The POST a browser sends for the untouched panel (rendered defaults)."""
    data: dict = {"enabled": ["false"]}
    for spec in config_pages.specs_for(system):
        if spec.kind == "bool":
            data[spec.key] = ["false", "true"] if spec.default else ["false"]
        elif spec.kind in ("secret", "watchers", "categories"):
            continue
        else:
            data[spec.key] = str(spec.default)
    data.update(overrides)
    return data


def save(client, system: str, data: dict):
    return client.post(f"/integrations/{system}/save", data, headers=HX)


@pytest.fixture
def secrets_set(seeded):
    IntegrationConfig.objects.create(system_name="REDMINE", base_url="https://redmine.invalid",
                                     api_key=SECRET_VALUES[0], enabled=True,
                                     additional_config={"webhook_secret": SECRET_VALUES[2], "project_id": "7"})
    IntegrationConfig.objects.create(system_name="JIRA", base_url="https://jira.invalid", api_key=SECRET_VALUES[0],
                                     enabled=True, additional_config={"pr_api_key": SECRET_VALUES[1]})


# ── Access ──────────────────────────────────────────────────────────────────

class TestAccess:
    def test_admin_sees_editable_page(self, admin_client):
        resp = admin_client.get("/integrations")
        body = resp.content.decode()
        assert resp.status_code == 200
        assert "This page is being rebuilt" not in body
        assert "Test Connection" in body and "/integrations/REDMINE/sync" in body
        assert 'class="ic-fieldset" disabled' not in body
        assert "0 OF 5 ENABLED" in body
        for system in config_pages.SYSTEMS:
            assert f"?tab={system}" in body

    @pytest.mark.parametrize("role", ["VIEWER", "ESTIMATOR", "APPROVER"])
    def test_non_admin_gets_read_only_view(self, web_login, make_user, seeded, role):
        resp = web_login(make_user(role)).get("/integrations")
        body = resp.content.decode()
        assert resp.status_code == 200
        assert "READ ONLY" in body and 'class="ic-fieldset" disabled' in body
        assert "Test Connection" not in body and "Generate" not in body
        assert '<button type="submit" class="btn btn-primary"' not in body
        assert "Base URL" in body and "Webhook Notification Watchers" in body

    def test_anonymous_redirected(self, client, seeded):
        resp = client.get("/integrations")
        assert resp.status_code == 302 and "/login" in resp["Location"]

    @pytest.mark.parametrize("url", [
        "/integrations/REDMINE/save", "/integrations/REDMINE/test", "/integrations/REDMINE/sync",
        "/integrations/redmine/webhook-secret", "/integrations/jira/pr-test",
    ])
    @pytest.mark.parametrize("role", ["VIEWER", "ESTIMATOR", "APPROVER"])
    def test_write_actions_refused_for_non_admin(self, web_login, make_user, seeded, url, role):
        client = web_login(make_user(role))
        with mock.patch.object(config_pages.service, "test_integration") as t, \
                mock.patch.object(config_pages.service, "sync_import") as s:
            resp = client.post(url, {"base_url": "https://evil.invalid", "enabled": "true"}, headers=HX)
        assert resp.status_code == 403
        assert not t.called and not s.called
        assert not IntegrationConfig.objects.exists()

    def test_get_only_and_post_only(self, admin_client):
        assert admin_client.post("/integrations").status_code == 405
        assert admin_client.get("/integrations/REDMINE/save").status_code == 405
        assert admin_client.get("/integrations/REDMINE/test").status_code == 405

    def test_parity_with_api_roles(self):
        """Edit / test / sync need the role the API asks for on PUT/POST /integrations/... (ADMIN)."""
        from apps.api.routers import integrations as router_mod

        assert config_pages.WRITE_ROLE == "ADMIN"
        src = open(router_mod.__file__).read()
        assert '@router.put("/integrations/{system_name}", auth=role("ADMIN"))' in src
        assert '@router.post("/integrations/{system_name}/test", auth=role("ADMIN"))' in src
        assert '@router.post("/integrations/{system_name}/sync", auth=role("ADMIN"))' in src


# ── Tabs ────────────────────────────────────────────────────────────────────

class TestTabs:
    @pytest.mark.parametrize("system,marker", [
        ("REDMINE", "Webhook Notification Watchers"), ("JIRA", "X-Ray Integration"),
        ("EMAIL", "SMTP Settings"), ("OUTLINE", "Publishing Settings"), ("SNIPE_IT", "Asset Settings"),
    ])
    def test_each_tab_full_page_and_partial(self, admin_client, system, marker):
        full = admin_client.get(f"/integrations?tab={system}")
        assert full.status_code == 200 and marker in full.content.decode()
        active = f'class="ic-tab active" aria-selected="true"\n       href="/integrations?tab={system}"'
        assert active in full.content.decode()
        part = admin_client.get(f"/integrations/tab/{system}", headers=HX)
        body = part.content.decode()
        assert part.status_code == 200 and marker in body
        assert "<html" not in body and 'id="ic-panel"' in body
        assert f'hx-push-url="/integrations?tab={system}"' in body

    def test_lowercase_tab_accepted(self, admin_client):
        assert admin_client.get("/integrations/tab/jira", headers=HX).status_code == 200

    def test_unknown_system_404(self, admin_client):
        assert admin_client.get("/integrations?tab=NOPE").status_code == 404
        assert admin_client.get("/integrations/tab/NOPE", headers=HX).status_code == 404
        assert save(admin_client, "NOPE", {}).status_code == 404
        assert admin_client.post("/integrations/NOPE/test", headers=HX).status_code == 404
        assert admin_client.post("/integrations/NOPE/sync", headers=HX).status_code == 404

    def test_v3_fields_present(self, admin_client):
        for system in config_pages.SYSTEMS:
            body = admin_client.get(f"/integrations/tab/{system}", headers=HX).content.decode()
            for spec in config_pages.specs_for(system):
                assert spec.label in body.replace("&#x27;", "'"), (system, spec.label)
            assert f"Enable {config_pages.SYSTEM_LABELS[system]} Integration" in body


# ── Saving: API format ──────────────────────────────────────────────────────

class TestSave:
    def test_redmine_every_field_saves_in_api_format(self, admin_client, api_get, make_user):
        w1, w2 = make_user("ESTIMATOR"), make_user("VIEWER")
        data = form_data(
            "REDMINE", base_url=" https://redmine.invalid ", api_key="rm-key", project_id=" 3 ", tracker_id="2",
            poll_interval_minutes="15", webhook_secret="hook-1", effort_hours_field_id="estimated_hours",
            feasibility_field_id="11", estimation_number_field_id="12", subtask_tracker_id="5",
            ssl_verify=["false"], enabled=["false", "true"], webhook_watchers=[str(w1.pk), str(w2.pk)],
        )
        resp = save(admin_client, "REDMINE", data)
        assert resp.status_code == 200
        assert "Credentials saved securely." in resp["HX-Trigger"]
        out = api_get("REDMINE").json()
        assert out["base_url"] == "https://redmine.invalid" and out["username"] is None
        assert out["enabled"] is True and out["has_api_key"] is True
        assert json.loads(out["additional_config_json"]) == {
            "project_id": "3", "tracker_id": "2", "poll_interval_minutes": 15, "webhook_secret": "hook-1",
            "effort_hours_field_id": "estimated_hours", "feasibility_field_id": "11",
            "estimation_number_field_id": "12", "subtask_tracker_id": "5", "ssl_verify": False,
        }
        assert IntegrationConfig.objects.get(system_name="REDMINE").api_key == "rm-key"
        assert json.loads(config.get_str("webhook_watchers")) == [w1.pk, w2.pk]
        # the response is the refreshed panel plus out-of-band strip/status
        body = resp.content.decode()
        assert 'id="ic-strip" class="ed-strip ic-strip" hx-swap-oob="true"' in body
        assert "1 OF 5 ENABLED" in body
        assert "rm-key" not in body and "hook-1" not in body

    def test_jira_every_field(self, admin_client, api_get):
        data = form_data(
            "JIRA", base_url="https://jira.invalid", api_key="j-key", username="bot", jql_filter='type = "FR"',
            project_key="PROJ", is_cloud=["false", "true"], auth_mode="pat", issue_type="Story",
            ssl_verify=["false", "true"], bypass_proxy=["false", "true"], effort_hours_field="originalEstimate",
            feasibility_field="customfield_1", estimation_number_field="customfield_2",
            xray_enabled=["false", "true"], xray_project_key="XR", task_export_type="Task", subtask_type="",
            pr_api_key="pr-key", pr_bypass_proxy=["false", "true"], pr_jql_filter="type = Bug",
            pr_fields="priority,components",
        )
        assert save(admin_client, "JIRA", data).status_code == 200
        out = api_get("JIRA").json()
        assert out["username"] == "bot" and out["has_api_key"] is True and out["enabled"] is False
        assert json.loads(out["additional_config_json"]) == {
            "jql_filter": 'type = "FR"', "project_key": "PROJ", "is_cloud": True, "auth_mode": "pat",
            "issue_type": "Story", "ssl_verify": True, "bypass_proxy": True,
            "effort_hours_field": "originalEstimate", "feasibility_field": "customfield_1",
            "estimation_number_field": "customfield_2", "xray_enabled": True, "xray_project_key": "XR",
            "task_export_type": "Task", "subtask_type": "Sub-task", "pr_api_key": "pr-key",
            "pr_bypass_proxy": True, "pr_jql_filter": "type = Bug", "pr_fields": "priority,components",
        }

    def test_email_top_level_and_ints(self, admin_client, api_get):
        IntegrationConfig.objects.create(system_name="EMAIL", base_url="https://stale.invalid")
        data = form_data("EMAIL", smtp_host="smtp.invalid", smtp_port="465", smtp_use_tls=["false"],
                         username="me@example.com", api_key="pw", sender_email="no@example.com", sender_name="P")
        assert save(admin_client, "EMAIL", data).status_code == 200
        out = api_get("EMAIL").json()
        assert out["base_url"] is None and out["username"] == "me@example.com" and out["has_api_key"]
        assert json.loads(out["additional_config_json"]) == {
            "smtp_host": "smtp.invalid", "smtp_port": 465, "smtp_use_tls": False,
            "sender_email": "no@example.com", "sender_name": "P",
        }

    def test_outline_and_snipeit(self, admin_client, api_get):
        assert save(admin_client, "OUTLINE", form_data(
            "OUTLINE", base_url="https://wiki.invalid", collection_id="uuid-1", auto_publish=["false", "true"],
        )).status_code == 200
        assert json.loads(api_get("OUTLINE").json()["additional_config_json"]) == {
            "collection_id": "uuid-1", "auto_publish": True, "ssl_verify": True}
        assert save(admin_client, "SNIPE_IT", form_data(
            "SNIPE_IT", base_url="https://snipe.invalid", categories=["Phones", "Modems"], timeout="60",
        )).status_code == 200
        out = api_get("SNIPE_IT").json()
        assert json.loads(out["additional_config_json"]) == {
            "categories": "Phones, Modems", "timeout": 60, "ssl_verify": True}
        assert out["has_api_key"] is False

    def test_unmanaged_keys_kept(self, admin_client):
        IntegrationConfig.objects.create(system_name="JIRA",
                                         additional_config={"timeout": 99, "field_mappings": {"a": 1}})
        assert save(admin_client, "JIRA", form_data("JIRA")).status_code == 200
        extra = IntegrationConfig.objects.get(system_name="JIRA").additional_config
        assert extra["timeout"] == 99 and extra["field_mappings"] == {"a": 1}

    def test_saved_values_reshown(self, admin_client):
        save(admin_client, "JIRA", form_data("JIRA", project_key="ZZTOP", auth_mode="basic",
                                             is_cloud=["false", "true"]))
        body = admin_client.get("/integrations/tab/JIRA", headers=HX).content.decode()
        assert 'value="ZZTOP"' in body
        assert '<option value="basic" selected>' in body
        assert 'name="is_cloud" value="true" checked' in body

    @pytest.mark.parametrize("system,field,value,error", [
        ("EMAIL", "smtp_port", "abc", "Enter a whole number."),
        ("EMAIL", "smtp_port", "70000", "Must be at most 65535."),
        ("SNIPE_IT", "timeout", "1", "Must be at least 5."),
        ("REDMINE", "poll_interval_minutes", "-5", "Must be at least 0."),
        ("JIRA", "auth_mode", "oauth", "Choose one of: auto, basic, pat."),
    ])
    def test_validation_errors_save_nothing(self, admin_client, system, field, value, error):
        resp = save(admin_client, system, form_data(system, base_url="https://x.invalid", **{field: value}))
        assert resp.status_code == 422
        body = resp.content.decode()
        assert error in body and "Nothing was saved" in body
        assert "error" in resp["HX-Trigger"]
        assert not IntegrationConfig.objects.filter(system_name=system).exists()

    def test_unknown_watcher_rejected(self, admin_client):
        resp = save(admin_client, "REDMINE", form_data("REDMINE", webhook_watchers=["99999"]))
        assert resp.status_code == 422 and "Choose users from the list." in resp.content.decode()

    def test_watchers_cleared_and_preselected(self, admin_client, make_user):
        user = make_user("ESTIMATOR", username="watcher_x")
        config.set_value("webhook_watchers", json.dumps([user.pk]))
        body = admin_client.get("/integrations/tab/REDMINE", headers=HX).content.decode()
        assert f'name="webhook_watchers" value="{user.pk}" checked' in body
        save(admin_client, "REDMINE", form_data("REDMINE"))
        assert json.loads(config.get_str("webhook_watchers")) == []

    def test_save_is_audited_without_values(self, admin_client):
        save(admin_client, "REDMINE", form_data("REDMINE", api_key="audit-SECRET"))
        entry = AuditLog.objects.filter(resource_type="integration").latest("id")
        assert entry.details["system"] == "REDMINE" and "api_key" in entry.details["fields"]
        assert "audit-SECRET" not in json.dumps(entry.details)


# ── Secrets ─────────────────────────────────────────────────────────────────

class TestSecrets:
    @pytest.mark.parametrize("role", ["ADMIN", "VIEWER"])
    def test_secrets_never_rendered(self, web_login, make_user, secrets_set, admin_user, role):
        client = web_login(admin_user if role == "ADMIN" else make_user(role))
        for system in ("REDMINE", "JIRA"):
            body = client.get(f"/integrations?tab={system}").content.decode()
            for secret in SECRET_VALUES:
                assert secret not in body
            assert ">set</span>" in body
        body = client.get("/integrations?tab=REDMINE").content.decode()
        assert "?token=&lt;webhook_secret&gt;" in body

    def test_blank_keeps_secret(self, admin_client, secrets_set):
        save(admin_client, "REDMINE", form_data("REDMINE", base_url="https://redmine.invalid"))
        row = IntegrationConfig.objects.get(system_name="REDMINE")
        assert row.api_key == SECRET_VALUES[0] and row.additional_config["webhook_secret"] == SECRET_VALUES[2]
        save(admin_client, "JIRA", form_data("JIRA"))
        assert IntegrationConfig.objects.get(system_name="JIRA").additional_config["pr_api_key"] == SECRET_VALUES[1]

    def test_new_value_replaces_secret(self, admin_client, secrets_set):
        save(admin_client, "REDMINE", form_data("REDMINE", api_key="new-key", webhook_secret="new-hook"))
        row = IntegrationConfig.objects.get(system_name="REDMINE")
        assert row.api_key == "new-key" and row.additional_config["webhook_secret"] == "new-hook"

    def test_clear_secrets(self, admin_client, secrets_set, api_get):
        resp = save(admin_client, "REDMINE", form_data("REDMINE", api_key__clear="1", webhook_secret__clear="1"))
        assert resp.status_code == 200
        assert api_get("REDMINE").json()["has_api_key"] is False
        assert IntegrationConfig.objects.get(system_name="REDMINE").additional_config["webhook_secret"] == ""
        save(admin_client, "JIRA", form_data("JIRA", pr_api_key__clear="1"))
        assert "pr_api_key" not in IntegrationConfig.objects.get(system_name="JIRA").additional_config
        assert ">not set</span>" in resp.content.decode()

    def test_read_only_view_has_no_secret_inputs(self, viewer, secrets_set):
        body = viewer.get("/integrations?tab=JIRA").content.decode()
        assert 'type="password"' not in body and "__clear" not in body


# ── Webhook URL and secret ──────────────────────────────────────────────────

class TestWebhook:
    def test_url_built_from_request_host(self, admin_client, seeded, settings):
        settings.ALLOWED_HOSTS = ["presto.test"]
        body = admin_client.get("/integrations?tab=REDMINE", HTTP_HOST="presto.test:8443").content.decode()
        assert "http://presto.test:8443/api/webhooks/redmine" in body
        assert "No secret is set" in body
        assert "hx-confirm" not in body.split('id="ic-webhook"')[1].split("ic-hook")[0]

    def test_generate_reveals_once_and_saves(self, admin_client, secrets_set):
        resp = admin_client.post("/integrations/redmine/webhook-secret", headers=HX)
        assert resp.status_code == 200
        secret = IntegrationConfig.objects.get(system_name="REDMINE").additional_config["webhook_secret"]
        assert secret != SECRET_VALUES[2] and len(secret) >= 32
        body = resp.content.decode()
        assert f"/api/webhooks/redmine?token={secret}" in body and "only time it is shown" in body
        assert "shown only once" in resp["HX-Trigger"]
        # other keys untouched; later renders do not show it
        assert IntegrationConfig.objects.get(system_name="REDMINE").additional_config["project_id"] == "7"
        assert secret not in admin_client.get("/integrations?tab=REDMINE").content.decode()

    def test_generated_secret_works_with_webhook(self, admin_client, secrets_set, client):
        admin_client.post("/integrations/redmine/webhook-secret", headers=HX)
        secret = IntegrationConfig.objects.get(system_name="REDMINE").additional_config["webhook_secret"]
        with mock.patch.object(config_pages.service, "sync_import",
                               return_value=SyncResult(system="REDMINE", direction="IMPORT",
                                                       status=SyncStatus.SUCCESS)):
            from django.test import Client

            anon = Client()
            assert anon.post(f"/api/webhooks/redmine?token={secret}").status_code == 200
            assert anon.post("/api/webhooks/redmine?token=wrong").status_code == 403

    def test_generate_asks_confirmation_when_set(self, admin_client, secrets_set):
        body = admin_client.get("/integrations?tab=REDMINE").content.decode()
        assert "Replace the current webhook secret?" in body


# ── Test connection / sync (mocked adapters) ────────────────────────────────

def _enable(system, **extra):
    defaults = {"base_url": "https://x.invalid", "api_key": "k", "enabled": True}
    defaults.update(extra)
    return IntegrationConfig.objects.create(system_name=system, **defaults)


class TestActions:
    def test_test_ok(self, admin_client, seeded):
        _enable("REDMINE")
        with mock.patch.object(RedmineAdapter, "test_connection",
                               return_value=ConnectionTestResult(True, "Connected as bot", {"user": "bot"})):
            resp = admin_client.post("/integrations/REDMINE/test", headers=HX)
        assert resp.status_code == 200
        trig = json.loads(resp["HX-Trigger"])["toast"]
        assert trig == {"message": "Connection OK: Connected as bot", "level": "success"}
        body = resp.content.decode()
        assert "Connection OK" in body and "Connected as bot" in body and "ic-ok" in body

    def test_test_failure(self, admin_client, seeded):
        _enable("JIRA")
        with mock.patch.object(JiraAdapter, "test_connection",
                               return_value=ConnectionTestResult(False, "HTTP 401: Unauthorized")):
            resp = admin_client.post("/integrations/JIRA/test", headers=HX)
        assert resp.status_code == 200
        assert json.loads(resp["HX-Trigger"])["toast"]["level"] == "warning"
        assert "Connection failed" in resp.content.decode() and "HTTP 401" in resp.content.decode()

    def test_test_adapter_exception_clean_message(self, admin_client, seeded):
        _enable("REDMINE")
        with mock.patch.object(RedmineAdapter, "test_connection", side_effect=ConnectionError("refused")):
            resp = admin_client.post("/integrations/REDMINE/test", headers=HX)
        assert resp.status_code == 200 and "Test error: refused" in resp.content.decode()

    def test_test_not_configured(self, admin_client, seeded):
        resp = admin_client.post("/integrations/OUTLINE/test", headers=HX)
        assert resp.status_code == 200
        assert "OUTLINE is not configured or not enabled." in resp.content.decode()
        IntegrationConfig.objects.create(system_name="OUTLINE", enabled=False, base_url="https://w.invalid")
        resp = admin_client.post("/integrations/OUTLINE/test", headers=HX)
        assert "not configured or not enabled" in json.loads(resp["HX-Trigger"])["toast"]["message"]

    def test_missing_base_url_precheck(self, admin_client, seeded):
        _enable("REDMINE", base_url="")
        with mock.patch.object(RedmineAdapter, "test_connection") as t:
            resp = admin_client.post("/integrations/REDMINE/test", headers=HX)
        assert not t.called
        assert "Base URL is required before testing/syncing." in resp.content.decode()
        _enable("EMAIL", base_url=None, additional_config={})
        resp = admin_client.post("/integrations/EMAIL/test", headers=HX)
        assert "SMTP Host is required before testing." in resp.content.decode()

    def test_sync_success_updates_strip(self, admin_client, seeded):
        _enable("REDMINE")
        result = SyncResult(system="REDMINE", direction="IMPORT", status=SyncStatus.SUCCESS,
                            items_processed=3, items_created=2, items_updated=1)
        with mock.patch.object(RedmineAdapter, "import_requests", return_value=result):
            resp = admin_client.post("/integrations/REDMINE/sync", headers=HX)
        assert resp.status_code == 200
        body = resp.content.decode()
        assert "Sync SUCCESS" in body
        assert 'id="ic-strip" class="ed-strip ic-strip" hx-swap-oob="true"' in body
        assert 'id="ic-lastsync" class="ic-lastsync" hx-swap-oob="true"' in body and "Last synced" in body
        toast = json.loads(resp["HX-Trigger"])["toast"]
        assert toast["level"] == "success" and toast["message"].startswith("Sync SUCCESS — 3 processed")
        assert IntegrationConfig.objects.get(system_name="REDMINE").last_sync_at is not None

    def test_sync_failure(self, admin_client, seeded):
        _enable("JIRA")
        result = SyncResult(system="JIRA", direction="IMPORT", status=SyncStatus.FAILED,
                            errors=["Connection refused"])
        with mock.patch.object(JiraAdapter, "import_requests", return_value=result):
            resp = admin_client.post("/integrations/JIRA/sync", headers=HX)
        body = resp.content.decode()
        assert "Sync FAILED" in body and "Connection refused" in body and "ic-fail" in body
        assert json.loads(resp["HX-Trigger"])["toast"]["level"] == "warning"

    def test_sync_exception(self, admin_client, seeded):
        _enable("JIRA")
        with mock.patch.object(JiraAdapter, "import_requests", side_effect=RuntimeError("boom")):
            resp = admin_client.post("/integrations/JIRA/sync", headers=HX)
        assert resp.status_code == 200 and "Sync error: boom" in resp.content.decode()

    def test_sync_not_configured(self, admin_client, seeded):
        resp = admin_client.post("/integrations/SNIPE_IT/sync", headers=HX)
        assert "SNIPE_IT is not configured or not enabled." in resp.content.decode()

    def test_outline_sync_exports(self, admin_client, seeded):
        _enable("OUTLINE")
        with mock.patch.object(config_pages.service, "sync_export_all",
                               return_value=SyncResult(system="OUTLINE", direction="EXPORT",
                                                       status=SyncStatus.SUCCESS)) as m:
            admin_client.post("/integrations/OUTLINE/sync", headers=HX)
        m.assert_called_once_with("OUTLINE")


class TestJiraPr:
    def test_pr_test_connection(self, admin_client, seeded):
        _enable("JIRA")
        with mock.patch.object(JiraAdapter, "test_pr_connection",
                               return_value=ConnectionTestResult(True, "PR token OK")):
            resp = admin_client.post("/integrations/jira/pr-test", headers=HX)
        assert "PR connection OK: PR token OK" in resp["HX-Trigger"]

    def test_pr_test_not_configured(self, admin_client, seeded):
        resp = admin_client.post("/integrations/jira/pr-test", headers=HX)
        assert "JIRA integration not configured or not enabled." in resp.content.decode()

    def test_pr_fetch(self, admin_client, seeded):
        _enable("JIRA")
        with mock.patch.object(config_pages.service, "jira_pr_items", return_value=[{"key": "A-1"}]) as m:
            resp = admin_client.get("/integrations/jira/pr-fetch", {"pr_jql_filter": "type = Bug"}, headers=HX)
        m.assert_called_once_with("type = Bug")
        assert "Fetched 1 PR item(s)" in resp.content.decode()

    def test_pr_fetch_errors(self, admin_client, seeded):
        resp = admin_client.get("/integrations/jira/pr-fetch", headers=HX)
        assert resp.status_code == 400 and resp.json()["detail"] == "PR JQL Filter is empty."
        resp = admin_client.get("/integrations/jira/pr-fetch", {"pr_jql_filter": "x"}, headers=HX)
        assert resp.status_code == 400 and "not configured" in resp.json()["detail"]


class TestSnipeCategories:
    def test_fetch_merges_selection(self, admin_client, seeded):
        _enable("SNIPE_IT")
        with mock.patch.object(SnipeItAdapter, "get_categories",
                               return_value=[{"name": "Phones"}, {"name": "Modems"}, {"id": 3}]):
            resp = admin_client.get("/integrations/snipe-it/categories", {"categories": ["Custom"]}, headers=HX)
        body = resp.content.decode()
        assert resp.status_code == 200
        assert 'value="Custom" checked' in body and 'value="Modems">' in body and 'value="Phones">' in body
        assert "Loaded 2 categories" in resp["HX-Trigger"]

    def test_fetch_not_configured_and_error(self, admin_client, seeded):
        resp = admin_client.get("/integrations/snipe-it/categories", headers=HX)
        assert resp.status_code == 400
        _enable("SNIPE_IT")
        with mock.patch.object(SnipeItAdapter, "get_categories", side_effect=OSError("timeout")):
            resp = admin_client.get("/integrations/snipe-it/categories", headers=HX)
        assert resp.status_code == 502 and resp.json()["detail"] == "Failed to fetch categories: timeout"


class TestCleanErrors:
    RAW = ("HTTPConnectionPool(host='127.0.0.1', port=9): Max retries exceeded with url: /users/current.json "
           "(Caused by NewConnectionError(\"HTTPConnection(host='127.0.0.1', port=9): Failed to establish a new "
           "connection: [Errno 111] Connection refused\"))")

    def test_clean_error(self):
        assert config_pages.clean_error(self.RAW) == "Cannot reach 127.0.0.1:9 (Connection refused)"
        assert config_pages.clean_error(f"Connection failed: {self.RAW}") == \
            "Connection failed: Cannot reach 127.0.0.1:9 (Connection refused)"
        assert config_pages.clean_error("HTTP 401: Unauthorized") == "HTTP 401: Unauthorized"

    def test_test_message_cleaned_and_not_double_prefixed(self, admin_client, seeded):
        _enable("REDMINE")
        with mock.patch.object(RedmineAdapter, "test_connection",
                               return_value=ConnectionTestResult(False, f"Connection failed: {self.RAW}")):
            resp = admin_client.post("/integrations/REDMINE/test", headers=HX)
        toast = json.loads(resp["HX-Trigger"])["toast"]["message"]
        assert toast == "Connection failed: Cannot reach 127.0.0.1:9 (Connection refused)"
        assert "Max retries" not in resp.content.decode()
