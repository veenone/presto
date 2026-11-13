"""Settings page (/settings): access, typed inputs, secrets, logo, connection tests, maintenance."""

import json
from datetime import date
from unittest import mock

import pytest

from apps.accounts.models import AuditLog, User
from apps.core import config
from apps.core.models import Configuration
from apps.core.pages import SECTION_KEYS, classify, spec_for

pytestmark = pytest.mark.django_db
HX = {"HX-Request": "true"}


def value(key: str) -> str:
    return Configuration.objects.get(key=key).value


def save(client, data: dict, htmx: bool = True):
    return client.post("/settings/save", data, headers=HX if htmx else {})


@pytest.fixture
def viewer(web_login, make_user, seeded):
    return web_login(make_user("VIEWER"))


@pytest.fixture
def approver(web_login, make_user, seeded):
    return web_login(make_user("APPROVER"))


@pytest.fixture
def uploads(settings, tmp_path):
    settings.UPLOADS_DIR = tmp_path / "uploads"
    return settings.UPLOADS_DIR


@pytest.fixture
def secrets_set(seeded):
    for key, val in {"smtp_password": "smtp-pw-SECRET1", "ldap_bind_password": "ldap-pw-SECRET2",
                     "oidc_client_secret": "oidc-SECRET3", "jwt_secret": "jwt-SECRET4"}.items():
        config.set_value(key, val)


# ── Access ──────────────────────────────────────────────────────────────────

class TestAccess:
    def test_admin_sees_editable_page(self, admin_client):
        resp = admin_client.get("/settings")
        assert resp.status_code == 200
        body = resp.content.decode()
        assert 'id="settings-form"' in body and "Save Changes" in body
        assert "Test SMTP" in body and "Reinitialize Estimations" in body
        assert "<fieldset class=\"st-fieldset\" disabled" not in body

    @pytest.mark.parametrize("role", ["VIEWER", "ESTIMATOR", "APPROVER"])
    def test_non_admin_gets_read_only_page(self, web_login, make_user, seeded, role):
        resp = web_login(make_user(role)).get("/settings")
        body = resp.content.decode()
        assert resp.status_code == 200
        assert "READ ONLY" in body and 'class="st-fieldset" disabled' in body
        assert "Test SMTP" not in body and "Reinitialize" not in body and "Upload logo" not in body
        assert "working_hours_per_day" in body

    def test_anonymous_redirected_to_login(self, client, seeded):
        resp = client.get("/settings")
        assert resp.status_code == 302 and "/login" in resp["Location"]

    @pytest.mark.parametrize("url", [
        "/settings/save", "/settings/logo", "/settings/test-smtp", "/settings/test-ldap",
        "/settings/ldap-sync", "/settings/maintenance/reinit-estimations",
    ])
    def test_admin_only_actions_refuse_approver(self, approver, url):
        assert approver.post(url, {"working_hours_per_day": "1"}, headers=HX).status_code == 403
        assert value("working_hours_per_day") == "7.0"

    def test_maintenance_dialog_refuses_viewer(self, viewer):
        assert viewer.get("/settings/maintenance/ldap-reset", headers=HX).status_code == 403

    def test_get_on_save_not_allowed(self, admin_client):
        assert admin_client.get("/settings/save").status_code == 405


# ── Layout ─────────────────────────────────────────────────────────────────

class TestSections:
    def test_v3_section_classification(self):
        assert classify("buffer_percentage") == "Estimation Parameters"
        assert classify("logo_height") == "Branding"
        assert classify("smtp_from") == "Email / SMTP"
        assert classify("ldap_url") == "LDAP"
        assert classify("oidc_issuer") == "OIDC"
        assert classify("team_skills") == "Other"
        assert list(SECTION_KEYS)[0] == "Estimation Parameters"

    def test_input_kinds(self):
        assert spec_for("buffer_percentage").kind == "float"
        assert spec_for("smtp_port").kind == "int"
        assert spec_for("smtp_tls").kind == "bool"
        assert spec_for("sidebar_bg_dark").kind == "color"
        assert spec_for("dut_categories").kind == "list"
        assert spec_for("product_types").kind == "jsonlist"
        assert spec_for("webhook_watchers").kind == "json"
        assert spec_for("smtp_password").kind == "secret"
        assert spec_for("ldap_group_mapping_json").kind == "roles"
        assert spec_for("some_new_api_secret").kind == "secret"

    def test_page_renders_sections_and_typed_inputs(self, admin_client):
        resp = admin_client.get("/settings")
        names = [s.name for s in resp.context["sections"]]
        assert names[:6] == ["Estimation Parameters", "Workflow Automation", "Branding", "Appearance",
                             "Data Management", "Field Labels"]
        assert names[-2:] == ["LDAP Group Mapping", "OIDC Role Mapping"]
        body = resp.content.decode()
        assert 'type="color"' in body and 'name="button_color_light"' in body
        assert 'type="number"' in body and 'name="smtp_port"' in body
        assert 'name="smtp_tls" value="true" checked' in body
        assert 'name="ldap_group_mapping_json__ADMIN"' in body
        assert f'{Configuration.objects.count()} CONFIG KEYS' in body


# ── Secrets ────────────────────────────────────────────────────────────────

class TestSecrets:
    SECRETS = ("smtp-pw-SECRET1", "ldap-pw-SECRET2", "oidc-SECRET3", "jwt-SECRET4")

    @pytest.mark.parametrize("role", ["ADMIN", "VIEWER"])
    def test_secret_values_never_rendered(self, web_login, make_user, secrets_set, admin_user, role):
        user = admin_user if role == "ADMIN" else make_user(role)
        body = web_login(user).get("/settings").content.decode()
        for secret in self.SECRETS:
            assert secret not in body
        assert body.count(">set</span>") >= 4

    def test_not_set_state(self, admin_client):
        body = admin_client.get("/settings").content.decode()
        assert "not set" in body

    def test_validation_rerender_does_not_echo_secrets(self, admin_client, secrets_set):
        resp = save(admin_client, {"buffer_percentage": "abc", "smtp_password": "typed-NEW-pw"})
        body = resp.content.decode()
        assert resp.status_code == 422
        assert "typed-NEW-pw" not in body
        for secret in self.SECRETS:
            assert secret not in body
        assert value("smtp_password") == "smtp-pw-SECRET1"

    def test_blank_secret_keeps_value(self, admin_client, secrets_set):
        save(admin_client, {"smtp_password": "", "smtp_host": "mail.example.test"})
        assert value("smtp_password") == "smtp-pw-SECRET1"
        assert value("smtp_host") == "mail.example.test"

    def test_typed_secret_replaces_value(self, admin_client, secrets_set):
        resp = save(admin_client, {"ldap_bind_password": "brand-new"})
        assert resp.status_code == 204 and resp["HX-Redirect"] == "/settings"
        assert value("ldap_bind_password") == "brand-new"

    def test_clear_secret(self, admin_client, secrets_set):
        save(admin_client, {"oidc_client_secret": "", "oidc_client_secret__clear": "1"})
        assert value("oidc_client_secret") == ""

    def test_jwt_secret_cannot_be_cleared(self, admin_client, secrets_set):
        save(admin_client, {"jwt_secret__clear": "1"})
        assert value("jwt_secret") == "jwt-SECRET4"

    def test_audit_entry_lists_keys_without_values(self, admin_client, secrets_set):
        save(admin_client, {"smtp_password": "zzz-secret"})
        log = AuditLog.objects.filter(resource_type="configuration").latest("id")
        assert log.details == {"keys": ["smtp_password"]}


# ── Saving typed values ──────────────────────────────────────────────────────

class TestSave:
    def test_number_saved_and_redirects(self, admin_client):
        resp = save(admin_client, {"working_hours_per_day": "7.5", "logo_height": "60"})
        assert resp.status_code == 204 and resp["HX-Redirect"] == "/settings"
        assert value("working_hours_per_day") == "7.5" and value("logo_height") == "60"
        page = admin_client.get("/settings")
        assert any("Saved 2 configuration key(s)." in str(m) for m in page.context["messages"])

    @pytest.mark.parametrize("key,bad,message", [
        ("buffer_percentage", "lots", "Enter a number."),
        ("buffer_percentage", "150", "Must be at most 100."),
        ("working_hours_per_day", "", "A value is required."),
        ("smtp_port", "25.5", "Enter a whole number."),
        ("smtp_port", "70000", "Must be at most 65535."),
        ("button_color_dark", "blue", "Enter a hex colour"),
        ("auto_create_historical_project", "sometimes", "Choose one of"),
        ("dut_categories", " , ,", "Enter at least one value."),
        ("product_types", "", "A value is required."),
        ("webhook_watchers", "[1, ", "Invalid JSON"),
        ("webhook_watchers", '["bob"]', "JSON array of user IDs"),
        ("label_deadline", "  ", "A value is required."),
        ("ldap_user_filter", "(uid=x)", "{username}"),
        ("pr_no_test_task_template_id", "999999", "existing task template"),
    ])
    def test_validation_errors(self, admin_client, key, bad, message):
        before = value(key)
        resp = save(admin_client, {key: bad, "leader_effort_ratio": "0.7"})
        assert resp.status_code == 422
        body = resp.content.decode()
        assert message in body and 'id="settings-form"' in body
        assert "<html" not in body  # htmx partial
        assert value(key) == before
        assert value("leader_effort_ratio") == "0.5"  # nothing saved when any field fails
        assert json.loads(resp["HX-Trigger"])["toast"]["level"] == "error"

    def test_error_section_opens_and_keeps_typed_values(self, admin_client):
        body = save(admin_client, {"buffer_percentage": "x", "leader_effort_ratio": "0.9"}).content.decode()
        assert 'st-section has-error" open' in body
        assert 'value="0.9"' in body

    def test_non_htmx_error_renders_full_page(self, admin_client):
        resp = save(admin_client, {"buffer_percentage": "x"}, htmx=False)
        assert resp.status_code == 422 and b"<html" in resp.content

    def test_non_htmx_save_redirects(self, admin_client):
        resp = save(admin_client, {"buffer_percentage": "12"}, htmx=False)
        assert resp.status_code == 302 and resp["Location"] == "/settings"
        assert value("buffer_percentage") == "12"

    def test_boolean_hidden_false_then_checkbox(self, admin_client):
        save(admin_client, {"smtp_tls": ["false"]})
        assert value("smtp_tls") == "false"
        save(admin_client, {"smtp_tls": ["false", "true"], "pr_scales_with_profile": ["false", "true"]})
        assert value("smtp_tls") == "true" and value("pr_scales_with_profile") == "true"

    def test_lists_normalised(self, admin_client):
        save(admin_client, {"dut_categories": " SIM ,eSIM,, Watch ", "product_types": "Payment, Telco, Transit"})
        assert value("dut_categories") == "SIM,eSIM,Watch"
        assert json.loads(value("product_types")) == ["Payment", "Telco", "Transit"]

    def test_formatting_only_change_is_no_change(self, admin_client):
        resp = save(admin_client, {"product_types": "Payment,Telco",
                                   "dut_categories": "SIM, eSIM, UICC, IoT Device, Mobile Device, Other"})
        assert resp.status_code == 204 and "HX-Redirect" not in resp
        assert json.loads(resp["HX-Trigger"])["toast"]["message"] == "No changes detected."
        assert value("product_types") == '["Payment", "Telco"]'

    def test_json_and_template_choice(self, admin_client):
        from apps.catalog.models import TaskTemplate

        tpl = TaskTemplate.objects.first()
        save(admin_client, {"webhook_watchers": "[1, 2]", "pr_no_test_task_template_id": str(tpl.pk)})
        assert value("webhook_watchers") == "[1, 2]"
        assert value("pr_no_test_task_template_id") == str(tpl.pk)
        save(admin_client, {"pr_no_test_task_template_id": ""})
        assert value("pr_no_test_task_template_id") == ""

    def test_role_mapping_matrix(self, admin_client):
        data = {f"ldap_group_mapping_json__{r}": "" for r in ("ADMIN", "APPROVER", "ESTIMATOR", "VIEWER")}
        data["ldap_group_mapping_json__ADMIN"] = "CN=Admins,DC=example,DC=com"
        save(admin_client, data)
        assert json.loads(value("ldap_group_mapping_json")) == {"ADMIN": "CN=Admins,DC=example,DC=com"}
        body = admin_client.get("/settings").content.decode()
        assert 'value="CN=Admins,DC=example,DC=com"' in body

    def test_date_labels(self, admin_client):
        save(admin_client, {"label_deadline": "Go-live date"})
        assert value("label_deadline") == "Go-live date"

    def test_schema_version_is_read_only(self, admin_client):
        before = value("schema_version")
        save(admin_client, {"schema_version": "99"})
        assert value("schema_version") == before

    def test_theme_colour_applies_on_next_load(self, admin_client):
        save(admin_client, {"button_color_dark": "#ff5722", "content_bg_light": "#abc"})
        assert value("button_color_dark") == "#ff5722" and value("content_bg_light") == "#abc"
        body = admin_client.get("/settings").content.decode()
        assert "--primary: #ff5722" in body and "--content-bg: #abc" in body

    def test_colour_case_change_is_no_change(self, admin_client):
        assert value("calendar_today_bg_light") == "#e0e0e0"
        resp = save(admin_client, {"calendar_today_bg_light": "#E0E0E0", "button_color_light": "#1976d2"})
        assert "HX-Redirect" not in resp and value("calendar_today_bg_light") == "#e0e0e0"

    def test_unknown_keys_editable_as_text(self, admin_client):
        Configuration.objects.create(key="custom_flag", value="a", description="")
        body = admin_client.get("/settings").content.decode()
        assert 'name="custom_flag"' in body
        save(admin_client, {"custom_flag": "b"})
        assert value("custom_flag") == "b"


# ── Logo ───────────────────────────────────────────────────────────────────

class TestLogo:
    def test_upload_valid(self, admin_client, uploads):
        from django.core.files.uploadedfile import SimpleUploadedFile

        logo = SimpleUploadedFile("brand.PNG", b"\x89PNGdata", "image/png")
        resp = admin_client.post("/settings/logo", {"file": logo}, headers=HX)
        assert resp.status_code == 200
        assert (uploads / "logo.png").read_bytes() == b"\x89PNGdata"
        assert value("logo_url").startswith("/api/static/uploads/logo.png?v=")
        assert 'id="logo-field"' in resp.content.decode() and value("logo_url") in resp.content.decode()
        assert json.loads(resp["HX-Trigger"])["toast"]["message"] == "Logo uploaded and saved."

    def test_upload_invalid_type(self, admin_client, uploads):
        from django.core.files.uploadedfile import SimpleUploadedFile

        before = value("logo_url")
        resp = admin_client.post("/settings/logo", {"file": SimpleUploadedFile("evil.exe", b"x")}, headers=HX)
        assert resp.status_code == 422 and b"Invalid file type" in resp.content
        assert value("logo_url") == before and not uploads.exists()

    def test_upload_missing_file(self, admin_client, uploads):
        resp = admin_client.post("/settings/logo", {}, headers=HX)
        assert resp.status_code == 422 and b"Choose an image file" in resp.content


# ── Connection tests and LDAP ─────────────────────────────────────────────────

class TestConnectionActions:
    def test_smtp_ok(self, admin_client):
        with mock.patch("apps.notifications.service.NotificationService.test_connection",
                        return_value=(True, "Connected to mail:587.")):
            resp = admin_client.post("/settings/test-smtp", headers=HX)
        assert resp.status_code == 204
        assert json.loads(resp["HX-Trigger"])["toast"] == {"message": "SMTP OK: Connected to mail:587.",
                                                           "level": "success"}

    def test_smtp_failure(self, admin_client):
        with mock.patch("apps.notifications.service.NotificationService.test_connection",
                        return_value=(False, "refused")):
            resp = admin_client.post("/settings/test-smtp", headers=HX)
        assert json.loads(resp["HX-Trigger"])["toast"] == {"message": "SMTP failed: refused", "level": "warning"}

    def test_smtp_unconfigured_uses_real_service(self, admin_client):
        resp = admin_client.post("/settings/test-smtp", headers=HX)
        assert "SMTP is not configured" in json.loads(resp["HX-Trigger"])["toast"]["message"]

    def test_ldap(self, admin_client):
        with mock.patch("apps.accounts.ldap_provider.LDAPProvider.test_connection",
                        return_value=(True, "Bound.")):
            resp = admin_client.post("/settings/test-ldap", headers=HX)
        assert json.loads(resp["HX-Trigger"])["toast"]["message"] == "LDAP OK: Bound."

    def test_ldap_sync_not_configured(self, admin_client):
        resp = admin_client.post("/settings/ldap-sync", headers=HX)
        assert resp.status_code == 400 and resp.json() == {"detail": "LDAP is not configured"}

    def test_ldap_sync(self, admin_client):
        config.set_value("ldap_url", "ldap://dir.example.test")
        result = {"synced": 3, "created": 2, "updated": 0, "skipped": 1, "deactivated": 0, "errors": []}
        with mock.patch("apps.accounts.ldap_provider.LDAPProvider.sync_users", return_value=result):
            resp = admin_client.post("/settings/ldap-sync", headers=HX)
        toast = json.loads(resp["HX-Trigger"])["toast"]
        assert toast == {"message": "LDAP sync: 2 created, 1 skipped (already exist)", "level": "success"}
        assert AuditLog.objects.filter(action="LDAP_SYNC").exists()


# ── Maintenance (reinit) ──────────────────────────────────────────────────────

class TestMaintenance:
    def test_dialogs(self, admin_client):
        body = admin_client.get("/settings/maintenance/reinit-requests", headers=HX).content.decode()
        assert "Reinitialize request inbox?" in body and 'name="confirm"' in body
        body = admin_client.get("/settings/maintenance/ldap-reset", headers=HX).content.decode()
        assert "Reinitialize LDAP users?" in body and 'name="confirm"' not in body
        assert admin_client.get("/settings/maintenance/bogus", headers=HX).status_code == 404

    def test_ldap_reset_keeps_local_and_self(self, admin_client, make_user):
        make_user("VIEWER", username="dir1", auth_provider="ldap")
        make_user("VIEWER", username="local1")
        resp = admin_client.post("/settings/maintenance/ldap-reset", headers=HX)
        assert resp.status_code == 204
        trig = json.loads(resp["HX-Trigger"])
        assert trig["close-modal"] is True and "Removed 1 LDAP-synced" in trig["toast"]["message"]
        assert not User.objects.filter(username="dir1").exists() and User.objects.filter(username="local1").exists()

    @pytest.mark.parametrize("action", ["reinit-requests", "reinit-estimations"])
    def test_wrong_token_rerenders_dialog(self, admin_client, action):
        resp = admin_client.post(f"/settings/maintenance/{action}", {"confirm": "yes"}, headers=HX)
        assert resp.status_code == 422 and b"Type REINITIALIZE exactly to confirm." in resp.content

    def test_reinit_requests(self, admin_client):
        from apps.inbox.models import Request

        Request.objects.create(request_number="REQ-0001", title="X", requester_name="Ann",
                               received_date=date(2026, 1, 5))
        resp = admin_client.post("/settings/maintenance/reinit-requests", {"confirm": "reinitialize"}, headers=HX)
        assert resp.status_code == 204 and Request.objects.count() == 0
        assert "1 request(s) removed" in json.loads(resp["HX-Trigger"])["toast"]["message"]

    def test_reinit_estimations(self, admin_client):
        from apps.estimations.models import Estimation

        resp = admin_client.post("/settings/maintenance/reinit-estimations", {"confirm": "REINITIALIZE"}, headers=HX)
        assert resp.status_code == 204 and Estimation.objects.count() == 0
        assert AuditLog.objects.filter(action="ESTIMATION_REINIT").exists()
