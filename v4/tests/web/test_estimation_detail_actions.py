"""Estimation detail actions and report downloads (session-authenticated)."""

from __future__ import annotations

import io
import json
import re
from unittest import mock

import pytest
from docx import Document
from openpyxl import load_workbook

from apps.estimations.models import Estimation, EstimationVersionSnapshot, HistoricalProject
from apps.estimations.versions import snapshot_estimation
from apps.integrations.adapters.base import SyncResult, SyncStatus
from tests.reports.factories import make_estimation, make_historical

pytestmark = pytest.mark.django_db
HX = {"HX-Request": "true"}
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
NOTIFY_STATUS = "apps.notifications.service.NotificationService.notify_estimation_status_changed"


def events(resp) -> dict:
    return json.loads(resp.get("HX-Trigger") or "{}")


def toast(resp) -> str:
    return events(resp)["toast"]["message"]


def filename(resp) -> str:
    match = re.fullmatch(r'attachment; filename="(.+)"', resp["Content-Disposition"])
    assert match, resp["Content-Disposition"]
    return match.group(1)


def sync_result(**kw) -> SyncResult:
    base = {"system": "REDMINE", "direction": "EXPORT", "status": SyncStatus.SUCCESS}
    base.update(kw)
    return SyncResult(**base)


@pytest.fixture
def est(seeded):
    return make_estimation(with_request=True)


@pytest.fixture
def as_role(web_login, make_user):
    def _as(role: str, **extra):
        return web_login(make_user(role, **extra))
    return _as


# ── Status workflow ──────────────────────────────────────


class TestStatus:
    def test_full_workflow(self, as_role, est):
        client = as_role("ESTIMATOR", display_name="Est One")
        with mock.patch(NOTIFY_STATUS):
            for target in ("FINAL", "APPROVED", "REVISED", "DRAFT"):
                resp = client.post(f"/estimation/{est.id}/status", {"status": target}, headers=HX)
                assert resp.status_code == 204, resp.content
                assert toast(resp) == f"Status changed to {target}." and events(resp)["est-refresh"]
                est.refresh_from_db()
                assert est.status == target
                if target == "APPROVED":
                    assert est.approved_by == "Est One"

    def test_draft_to_revised(self, admin_client, est):
        with mock.patch(NOTIFY_STATUS):
            assert admin_client.post(f"/estimation/{est.id}/status", {"status": "REVISED"}).status_code == 204
        est.refresh_from_db()
        assert est.status == "REVISED"

    def test_invalid_transition_shows_service_message(self, admin_client, est):
        resp = admin_client.post(f"/estimation/{est.id}/status", {"status": "APPROVED"}, headers=HX)
        assert resp.status_code == 400
        assert resp.json()["detail"] == (
            "Status update failed: Invalid status transition: DRAFT -> APPROVED. Allowed: ['FINAL', 'REVISED']")
        assert events(resp)["est-refresh"]  # v3 rebuilds the buttons after a failure
        est.refresh_from_db()
        assert est.status == "DRAFT"

    def test_viewer_refused(self, as_role, est):
        assert as_role("VIEWER").post(f"/estimation/{est.id}/status", {"status": "FINAL"}).status_code == 403
        est.refresh_from_db()
        assert est.status == "DRAFT"

    def test_get_not_allowed_and_404(self, admin_client, est):
        assert admin_client.get(f"/estimation/{est.id}/status").status_code == 405
        assert admin_client.post("/estimation/99999/status", {"status": "FINAL"}).status_code == 404


# ── Assignment ───────────────────────────────────────────


class TestAssign:
    def test_approver_assigns(self, as_role, make_user, est):
        target = make_user("ESTIMATOR", username="bob", display_name="Bob B")
        with mock.patch("apps.notifications.service.NotificationService.notify_user_assigned") as notify:
            resp = as_role("APPROVER").post(f"/estimation/{est.id}/assign", {"assigned_to_id": target.id})
        assert resp.status_code == 204 and toast(resp) == "Assigned to Bob B."
        assert notify.called
        est.refresh_from_db()
        assert est.assigned_to_id == target.id

    def test_estimator_refused(self, as_role, est):
        assert as_role("ESTIMATOR").post(f"/estimation/{est.id}/assign", {"assigned_to_id": 1}).status_code == 403

    def test_errors(self, admin_client, est):
        resp = admin_client.post(f"/estimation/{est.id}/assign", {"assigned_to_id": ""})
        assert resp.status_code == 400 and resp.json()["detail"] == "Select a user first."
        resp = admin_client.post(f"/estimation/{est.id}/assign", {"assigned_to_id": 9999})
        assert resp.status_code == 404 and resp.json()["detail"] == "Assignment failed: Target user not found"


# ── Archive and calibration ──────────────────────────────


class TestArchive:
    def test_archive_approved(self, as_role, est):
        Estimation.objects.filter(pk=est.pk).update(status="APPROVED")
        resp = as_role("APPROVER").post(f"/estimation/{est.id}/archive")
        assert resp.status_code == 204 and toast(resp) == "Estimation archived to Historical Projects."
        assert HistoricalProject.objects.filter(estimation=est).count() == 1
        again = as_role("ADMIN").post(f"/estimation/{est.id}/archive")
        assert again.status_code == 400
        assert again.json()["detail"] == "Archive failed: This estimation has already been archived"

    def test_only_approved(self, admin_client, est):
        resp = admin_client.post(f"/estimation/{est.id}/archive")
        assert resp.status_code == 400
        assert resp.json()["detail"] == "Archive failed: Only APPROVED estimations can be archived (current: DRAFT)"

    def test_estimator_refused(self, as_role, est):
        Estimation.objects.filter(pk=est.pk).update(status="APPROVED")
        assert as_role("ESTIMATOR").post(f"/estimation/{est.id}/archive").status_code == 403


class TestCalibrate:
    def test_no_references(self, as_role, est):
        resp = as_role("VIEWER").post(f"/estimation/{est.id}/calibrate", headers=HX)
        assert resp.status_code == 400
        assert resp.json()["detail"] == "Calibration failed: No reference projects linked to this estimation."

    def test_result_dialog(self, as_role, est):
        h1, h2 = make_historical("Old A"), make_historical("Old B", estimated_hours=100, actual_hours=90)
        Estimation.objects.filter(pk=est.pk).update(reference_project_ids=[h1.id, h2.id])
        resp = as_role("VIEWER").post(f"/estimation/{est.id}/calibrate", headers=HX)
        body = resp.content.decode()
        assert resp.status_code == 200 and "Calibration" in body
        assert "Old A" in body and "Old B" in body and "Adjustment Factor" in body


# ── External exports ─────────────────────────────────────


def _link_external(est, source="REDMINE", external_id="55"):
    req = est.request
    req.request_source, req.external_id = source, external_id
    req.save()


class TestExport:
    def test_success(self, as_role, est):
        _link_external(est)
        with mock.patch("apps.integrations.service.sync_export", return_value=sync_result(items_updated=1)) as exp:
            resp = as_role("ESTIMATOR").post(f"/estimation/{est.id}/export", headers=HX)
        assert resp.status_code == 204 and toast(resp) == "Export successful (status: SUCCESS)."
        system, data = exp.call_args.args
        assert system == "REDMINE" and data["external_id"] == "55" and data["estimation_number"] == "EST-2026-0001"

    def test_errors_from_adapter(self, admin_client, est):
        _link_external(est)
        result = sync_result(status=SyncStatus.FAILED, errors=["boom"])
        with mock.patch("apps.integrations.service.sync_export", return_value=result):
            resp = admin_client.post(f"/estimation/{est.id}/export")
        assert toast(resp) == "Export completed with errors: boom"
        assert events(resp)["toast"]["level"] == "warning"

    def test_rule_errors(self, admin_client, est):
        resp = admin_client.post(f"/estimation/{est.id}/export")
        assert resp.status_code == 400 and resp.json()["detail"] == "Export failed: Linked request has no external ID"
        _link_external(est, source="MANUAL")
        assert "MANUAL" in admin_client.post(f"/estimation/{est.id}/export").json()["detail"]
        plain = make_estimation(estimation_number="EST-2")
        assert admin_client.post(f"/estimation/{plain.id}/export").json()["detail"] == (
            "Export failed: Estimation is not linked to a request")

    def test_viewer_refused(self, as_role, est):
        assert as_role("VIEWER").post(f"/estimation/{est.id}/export").status_code == 403


class TestExportTasks:
    def test_dialog_defaults_to_linked_system(self, admin_client, est):
        _link_external(est, source="REDMINE", external_id="77")
        body = admin_client.get(f"/estimation/{est.id}/export-tasks", headers=HX).content.decode()
        assert "Export Task Breakdown" in body
        assert '<option value="REDMINE" selected>' in body
        assert 'data-value="77"' in body  # "use linked request" button

    def test_export(self, as_role, est):
        result = sync_result(system="JIRA", items_created=3, items_processed=3)
        with mock.patch("apps.integrations.service.sync_export_task_breakdown", return_value=result) as exp:
            resp = as_role("ESTIMATOR").post(f"/estimation/{est.id}/export-tasks",
                                             {"target_system": "JIRA", "issue_key": " PROJ-1 "}, headers=HX)
        assert resp.status_code == 204
        assert toast(resp) == "Task breakdown exported: 3/3 tasks created in JIRA."
        assert events(resp)["close-modal"]
        system, data = exp.call_args.args
        assert system == "JIRA" and data["external_id"] == "PROJ-1" and data["tasks"]

    def test_partial_errors(self, admin_client, est):
        result = sync_result(items_created=1, items_processed=2, errors=["a", "b", "c", "d"])
        with mock.patch("apps.integrations.service.sync_export_task_breakdown", return_value=result):
            resp = admin_client.post(f"/estimation/{est.id}/export-tasks", {"target_system": "REDMINE"})
        assert toast(resp) == "Task export: 1/2 created. Errors: a, b, c"

    def test_invalid_and_failure(self, admin_client, est):
        resp = admin_client.post(f"/estimation/{est.id}/export-tasks", {"target_system": "NOPE"})
        assert resp.status_code == 422 and b"Select a valid choice" in resp.content
        with mock.patch("apps.integrations.service.sync_export_task_breakdown", side_effect=RuntimeError("down")):
            resp = admin_client.post(f"/estimation/{est.id}/export-tasks", {"target_system": "JIRA"})
        assert resp.status_code == 422 and b"Task export failed: down" in resp.content

    def test_viewer_refused(self, as_role, est):
        assert as_role("VIEWER").get(f"/estimation/{est.id}/export-tasks").status_code == 403


# ── Email ────────────────────────────────────────────────


@pytest.fixture
def email_adapter(monkeypatch):
    from apps.integrations import service
    from apps.integrations.adapters import EmailAdapter

    holder: dict = {"adapter": None}
    monkeypatch.setattr(service, "get_adapter", lambda name: holder["adapter"])

    def _set(status: str | None, errors: list[str] | None = None):
        if status is None:
            holder["adapter"] = None
            return None
        adapter = mock.MagicMock(spec=EmailAdapter)
        adapter.send_estimation_report.return_value = SyncResult(
            system="EMAIL", direction="EXPORT", status=SyncStatus(status), errors=errors or [])
        holder["adapter"] = adapter
        return adapter
    return _set


class TestSendReport:
    def test_dialog_prefills_own_email(self, as_role, est):
        body = as_role("ESTIMATOR", email="me@example.com").get(f"/estimation/{est.id}/send-report").content
        assert b"Send Report by Email" in body and b'value="me@example.com"' in body

    def test_success(self, admin_client, est, email_adapter):
        adapter = email_adapter("SUCCESS")
        resp = admin_client.post(f"/estimation/{est.id}/send-report", {"to_email": "a@b.co"}, headers=HX)
        assert resp.status_code == 204 and toast(resp) == "Report sent to a@b.co"
        kwargs = adapter.send_estimation_report.call_args.kwargs
        assert kwargs["report_filename"] == "EST-2026-0001.pdf" and kwargs["report_bytes"][:4] == b"%PDF"

    def test_not_configured_and_failure(self, admin_client, est, email_adapter):
        email_adapter(None)
        resp = admin_client.post(f"/estimation/{est.id}/send-report", {"to_email": "a@b.co"})
        assert resp.status_code == 422 and b"Email integration is not configured or not enabled." in resp.content
        email_adapter("FAILED", ["smtp down"])
        resp = admin_client.post(f"/estimation/{est.id}/send-report", {"to_email": "a@b.co"})
        assert resp.status_code == 422 and b"Failed to send: smtp down" in resp.content

    def test_validation(self, admin_client, est, email_adapter):
        email_adapter("SUCCESS")
        resp = admin_client.post(f"/estimation/{est.id}/send-report", {"to_email": "nope"})
        assert resp.status_code == 422 and b"Enter a valid email address." in resp.content

    def test_viewer_refused(self, as_role, est):
        assert as_role("VIEWER").post(f"/estimation/{est.id}/send-report", {"to_email": "a@b.co"}).status_code == 403


# ── Versions ─────────────────────────────────────────────


class TestVersionDiff:
    def test_diff(self, as_role, est):
        EstimationVersionSnapshot.objects.create(estimation=est, version=1, snapshot=snapshot_estimation(est))
        Estimation.objects.filter(pk=est.pk).update(version=2, grand_total_hours=120.5, project_name="Renamed")
        task = est.tasks.order_by("id").first()
        task.calculated_hours += 5
        task.save()
        body = as_role("VIEWER").get(f"/estimation/{est.id}/versions/diff?a=1&b=2", headers=HX).content.decode()
        assert "Field Changes" in body and "Grand Total Hours" in body and "120.5" in body and "Renamed" in body
        assert "Modified Tasks" in body and "+5.0" in body

    def test_same_or_missing_version(self, admin_client, est):
        resp = admin_client.get(f"/estimation/{est.id}/versions/diff?a=1&b=1")
        assert resp.status_code == 400 and resp.json()["detail"] == "Select two different versions."
        resp = admin_client.get(f"/estimation/{est.id}/versions/diff?a=1&b=7")
        assert resp.status_code == 404 and resp.json()["detail"] == "Diff failed: Version 7 snapshot not found"

    def test_no_differences(self, admin_client, est):
        EstimationVersionSnapshot.objects.create(estimation=est, version=1, snapshot=snapshot_estimation(est))
        Estimation.objects.filter(pk=est.pk).update(version=2)
        body = admin_client.get(f"/estimation/{est.id}/versions/diff?a=1&b=2").content.decode()
        assert "No differences found between the two versions." in body


# ── Downloads ────────────────────────────────────────────


class TestDownloads:
    def test_excel(self, as_role, est):
        resp = as_role("VIEWER").get(f"/estimation/{est.id}/report/xlsx")
        assert resp.status_code == 200 and resp["Content-Type"] == XLSX
        assert re.fullmatch(r"REQ_26_0015-Report_Test-\d{12}\.xlsx", filename(resp)), filename(resp)
        assert load_workbook(io.BytesIO(resp.content)).sheetnames

    def test_word(self, admin_client, est):
        resp = admin_client.get(f"/estimation/{est.id}/report/docx")
        assert resp["Content-Type"] == DOCX and filename(resp).endswith(".docx")
        assert Document(io.BytesIO(resp.content)).paragraphs

    def test_pdf_and_executive_summary(self, admin_client, est):
        resp = admin_client.get(f"/estimation/{est.id}/report/pdf")
        assert resp["Content-Type"] == "application/pdf" and resp.content[:4] == b"%PDF"
        resp = admin_client.get(f"/estimation/{est.id}/report/executive-summary")
        assert resp["Content-Type"] == "application/pdf" and resp.content[:4] == b"%PDF"
        assert filename(resp) == "EST-2026-0001_summary.pdf"

    def test_comparison(self, as_role, est):
        other = make_estimation(estimation_number="EST-2026-0002", project_name="Other")
        client = as_role("VIEWER")
        dialog = client.get(f"/estimation/{est.id}/compare", headers=HX).content.decode()
        assert "Compare Estimations" in dialog and f'value="{other.id}"' in dialog
        resp = client.get(f"/estimation/{est.id}/compare/report?other={other.id}")
        assert resp["Content-Type"] == XLSX
        assert filename(resp) == "comparison_EST-2026-0001_EST-2026-0002.xlsx"
        assert load_workbook(io.BytesIO(resp.content)).sheetnames
        assert client.get(f"/estimation/{est.id}/compare/report?other=99999").status_code == 404
        assert client.get(f"/estimation/{est.id}/compare/report").status_code == 400

    def test_unknown_format_and_id(self, admin_client, est):
        assert admin_client.get(f"/estimation/{est.id}/report/csv").status_code == 404
        assert admin_client.get("/estimation/99999/report/pdf").status_code == 404
        assert admin_client.get("/estimation/99999/report/executive-summary").status_code == 404

    def test_generation_failure(self, admin_client, est):
        with mock.patch("apps.reports.pdf_report.generate_pdf_report", side_effect=ValueError("bad data")):
            resp = admin_client.get(f"/estimation/{est.id}/report/pdf")
        assert resp.status_code == 500 and b"PDF report generation failed: bad data" in resp.content
