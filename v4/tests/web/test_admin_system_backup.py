"""Backup & Restore page (/admin/backup): ADMIN only, download, two-step restore."""

import json

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile

from apps.accounts.models import AuditLog
from apps.catalog.models import DocumentType, Feature, PublicHoliday
from apps.core import backup as backup_service

pytestmark = pytest.mark.django_db
HX = {"HX-Request": "true"}


def upload(document, name="presto_backup.json") -> SimpleUploadedFile:
    body = document if isinstance(document, bytes) else json.dumps(document).encode()
    return SimpleUploadedFile(name, body, "application/json")


# A minimal v3 backup (v3 column names, JSON columns as text).
V3_BACKUP = {
    "version": "3.4.0",
    "timestamp": "2025-11-02T10:00:00",
    "tables": {
        "features": [{"id": 7, "name": "Restored feature", "category": "Telecom", "complexity_weight": 1.5,
                      "description": None, "is_active": True, "owner_estimation_id": None}],
        "document_types": [],
        "public_holidays": [{"id": 1, "date": "2026-01-01", "name": "New Year", "country": "FR"}],
    },
}


@pytest.fixture
def approver(web_login, make_user, seeded):
    return web_login(make_user("APPROVER"))


class TestAccess:
    def test_admin_page(self, admin_client):
        resp = admin_client.get("/admin/backup")
        body = resp.content.decode()
        assert resp.status_code == 200
        assert "Download Backup" in body and 'id="restore-form"' in body and "ADMIN ONLY" in body

    @pytest.mark.parametrize("url,method", [
        ("/admin/backup", "get"), ("/admin/backup/download", "get"), ("/admin/backup/restore", "post"),
    ])
    def test_non_admin_forbidden(self, approver, url, method):
        assert getattr(approver, method)(url).status_code == 403

    def test_viewer_forbidden(self, web_login, make_user, seeded):
        assert web_login(make_user("VIEWER")).get("/admin/backup").status_code == 403

    def test_nav_hides_backup_for_non_admin(self, approver):
        assert b'href="/admin/backup"' not in approver.get("/settings").content


class TestDownload:
    def test_download(self, admin_client):
        resp = admin_client.get("/admin/backup/download")
        assert resp.status_code == 200
        assert resp["Content-Type"] == "application/json"
        disp = resp["Content-Disposition"]
        assert disp.startswith('attachment; filename="presto_backup_') and disp.endswith('.json"')
        doc = json.loads(resp.content)
        assert doc["version"] == backup_service.BACKUP_FORMAT_VERSION
        assert set(doc["tables"]) == {name for name, _ in backup_service.BACKUP_TABLES}
        assert len(doc["tables"]["features"]) == Feature.objects.count()
        assert AuditLog.objects.filter(action="BACKUP").exists()

    def test_post_not_allowed(self, admin_client):
        assert admin_client.post("/admin/backup/download").status_code == 405


class TestRestore:
    def test_step_one_shows_confirmation_and_changes_nothing(self, admin_client):
        before = Feature.objects.count()
        resp = admin_client.post("/admin/backup/restore", {"file": upload(V3_BACKUP)}, headers=HX)
        body = resp.content.decode()
        assert resp.status_code == 200
        assert "Confirm Restore" in body and "presto_backup.json" in body
        assert "<td>features</td><td class=\"align-right\">1</td>" in body
        assert "public_holidays" in body and "Backup version 3.4.0" in body
        assert "task_templates" in body  # listed as kept (not in this file)
        assert 'hx-vals=\'{"confirm": "yes"}\'' in body
        assert Feature.objects.count() == before

    def test_confirmed_restore_of_v3_file(self, admin_client):
        resp = admin_client.post("/admin/backup/restore", {"file": upload(V3_BACKUP), "confirm": "yes"}, headers=HX)
        body = resp.content.decode()
        assert resp.status_code == 200
        assert resp["HX-Retarget"] == "#restore-result"
        trig = json.loads(resp["HX-Trigger"])
        assert trig["close-modal"] is True and trig["toast"]["message"] == "Restore completed successfully!"
        assert "Restore completed successfully!" in body
        assert "features (1)" in body and "public_holidays (1)" in body and "document_types (0)" in body
        assert list(Feature.objects.values_list("name", flat=True)) == ["Restored feature"]
        assert PublicHoliday.objects.get().name == "New Year"
        assert DocumentType.objects.count() == 0
        assert AuditLog.objects.filter(action="RESTORE").exists()

    def test_round_trip_own_backup(self, admin_client):
        data = admin_client.get("/admin/backup/download").content
        count = Feature.objects.count()
        resp = admin_client.post("/admin/backup/restore",
                                 {"file": upload(data), "confirm": "yes"}, headers=HX)
        assert resp.status_code == 200 and Feature.objects.count() == count

    @pytest.mark.parametrize("payload,name,message", [
        (b"{not json", "x.json", "Invalid JSON file"),
        ({"version": "3.4.0"}, "x.json", "missing &#x27;tables&#x27; key"),
        ({"tables": {"users": []}}, "x.json", "none of the tables"),
        ({"tables": {"features": {"a": 1}}}, "x.json", "is not a list of rows"),
        (V3_BACKUP, "backup.txt", "Please upload a JSON file."),
    ])
    def test_invalid_files(self, admin_client, payload, name, message):
        before = Feature.objects.count()
        resp = admin_client.post("/admin/backup/restore", {"file": upload(payload, name), "confirm": "yes"},
                                 headers=HX)
        assert resp.status_code == 422
        assert resp["HX-Retarget"] == "#restore-result"
        assert message in resp.content.decode()
        assert json.loads(resp["HX-Trigger"])["toast"]["level"] == "error"
        assert Feature.objects.count() == before

    def test_missing_file(self, admin_client):
        resp = admin_client.post("/admin/backup/restore", {}, headers=HX)
        assert resp.status_code == 422 and b"Choose a backup file first." in resp.content

    def test_restore_failure_rolls_back(self, admin_client):
        bad = {"tables": {"features": [{"id": 1, "name": None}]}}
        before = Feature.objects.count()
        resp = admin_client.post("/admin/backup/restore", {"file": upload(bad), "confirm": "yes"}, headers=HX)
        assert resp.status_code == 422 and b"Restore failed:" in resp.content
        assert Feature.objects.count() == before
