"""Report endpoints: downloads, executive summary, comparison, trend, send-report."""

from __future__ import annotations

import io
import re
import sys
import types
from unittest import mock

import pytest
from docx import Document
from openpyxl import load_workbook

from tests.reports.factories import make_estimation, make_historical

XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


def _filename(resp) -> str:
    match = re.fullmatch(r'attachment; filename="(.+)"', resp["Content-Disposition"])
    assert match, resp["Content-Disposition"]
    return match.group(1)


class TestReportEndpoints:
    """Ported from v3 tests/test_api.py::TestReportEndpoints, plus file checks."""

    def test_xlsx_report(self, api):
        est = make_estimation()
        resp = api.get(f"/api/estimations/{est.id}/report/xlsx")
        assert resp.status_code == 200
        assert resp["Content-Type"] == XLSX
        assert re.fullmatch(r"EST-2026-0001-Report_Test-\d{12}\.xlsx", _filename(resp))
        assert resp["Access-Control-Expose-Headers"] == "Content-Disposition"
        assert load_workbook(io.BytesIO(resp.content)).sheetnames

    def test_docx_report(self, api):
        est = make_estimation(with_request=True, with_team=True, with_risk=True)
        resp = api.get(f"/api/estimations/{est.id}/report/docx")
        assert resp.status_code == 200
        assert resp["Content-Type"] == DOCX
        assert re.fullmatch(r"REQ_26_0015-Report_Test-\d{12}\.docx", _filename(resp))
        doc = Document(io.BytesIO(resp.content))
        assert any("Report Test" in p.text for p in doc.paragraphs)

    def test_pdf_report(self, api):
        est = make_estimation()
        resp = api.get(f"/api/estimations/{est.id}/report/pdf")
        assert resp.status_code == 200
        assert resp["Content-Type"] == "application/pdf"
        assert _filename(resp).endswith(".pdf")
        assert resp.content[:4] == b"%PDF"

    @pytest.mark.parametrize("fmt", ["xlsx", "docx", "pdf", "executive-summary"])
    def test_unknown_estimation_404(self, api, fmt):
        resp = api.get(f"/api/estimations/99999/report/{fmt}")
        assert resp.status_code == 404
        assert resp.json() == {"detail": "Estimation not found"}

    @pytest.mark.parametrize("fmt", ["xlsx", "docx", "pdf", "executive-summary"])
    def test_auth_required(self, anon_api, fmt):
        assert anon_api.get(f"/api/estimations/1/report/{fmt}").status_code == 401

    def test_viewer_can_download(self, seeded, make_user, api_as):
        est = make_estimation()
        resp = api_as(make_user("VIEWER")).get(f"/api/estimations/{est.id}/report/pdf")
        assert resp.status_code == 200

    def test_generation_failure_is_500(self, api):
        est = make_estimation()
        with mock.patch("apps.reports.excel_report.generate_excel_report", side_effect=RuntimeError("boom")):
            resp = api.get(f"/api/estimations/{est.id}/report/xlsx")
        assert resp.status_code == 500
        assert resp.json() == {"detail": "Excel report generation failed: boom"}


class TestExecutiveSummary:
    def test_download(self, api):
        est = make_estimation()
        resp = api.get(f"/api/estimations/{est.id}/report/executive-summary")
        assert resp.status_code == 200
        assert resp["Content-Type"] == "application/pdf"
        assert _filename(resp) == "EST-2026-0001_summary.pdf"
        assert resp.content[:4] == b"%PDF"

    def test_filename_without_number(self, api):
        est = make_estimation(estimation_number=None)
        resp = api.get(f"/api/estimations/{est.id}/report/executive-summary")
        assert _filename(resp) == f"EST-{est.id}_summary.pdf"


class TestComparison:
    def test_compare(self, api):
        a = make_estimation(estimation_number="EST-A")
        b = make_estimation(estimation_number="EST-B", project_name="Other")
        resp = api.post("/api/reports/comparison", params={"estimation_a_id": a.id, "estimation_b_id": b.id})
        assert resp.status_code == 200
        assert resp["Content-Type"] == XLSX
        assert _filename(resp) == "comparison_EST-A_EST-B.xlsx"
        assert load_workbook(io.BytesIO(resp.content)).sheetnames

    def test_missing_estimation(self, api):
        a = make_estimation()
        resp = api.post("/api/reports/comparison", params={"estimation_a_id": a.id, "estimation_b_id": 99999})
        assert resp.status_code == 404
        assert resp.json() == {"detail": "One or both estimations not found"}

    def test_params_required(self, api):
        assert api.post("/api/reports/comparison").status_code == 422

    def test_auth_required(self, anon_api):
        resp = anon_api.post("/api/reports/comparison", params={"estimation_a_id": 1, "estimation_b_id": 2})
        assert resp.status_code == 401


class TestTrend:
    def test_trend(self, api):
        make_historical("A")
        make_historical("B", actual_hours=380.0, completion_date=None)
        resp = api.get("/api/reports/trend")
        assert resp.status_code == 200
        assert resp["Content-Type"] == XLSX
        assert _filename(resp) == "trend_analysis.xlsx"
        assert load_workbook(io.BytesIO(resp.content)).sheetnames

    def test_trend_empty(self, api):
        assert api.get("/api/reports/trend").status_code == 200

    def test_auth_required(self, anon_api):
        assert anon_api.get("/api/reports/trend").status_code == 401


@pytest.fixture
def email_adapter(monkeypatch):
    """Patch ``apps.integrations.service.get_adapter``; returns a setter for its return value."""
    from apps.integrations.adapters import EmailAdapter

    holder = {"adapter": None}

    def fake_get_adapter(system_name):
        assert system_name == "EMAIL"
        return holder["adapter"]

    try:
        import apps.integrations.service as service
        monkeypatch.setattr(service, "get_adapter", fake_get_adapter)
    except ImportError:  # service module not written yet
        fake = types.ModuleType("apps.integrations.service")
        fake.get_adapter = fake_get_adapter
        monkeypatch.setitem(sys.modules, "apps.integrations.service", fake)

    def _set(result_status: str | None, errors: list[str] | None = None):
        if result_status is None:
            holder["adapter"] = None
            return None
        from apps.integrations.adapters import SyncResult, SyncStatus
        adapter = mock.MagicMock(spec=EmailAdapter)
        adapter.send_estimation_report.return_value = SyncResult(
            system="EMAIL", direction="EXPORT", status=SyncStatus(result_status), errors=errors or [],
        )
        holder["adapter"] = adapter
        return adapter

    return _set


class TestSendReport:
    def test_success(self, api, email_adapter):
        adapter = email_adapter("SUCCESS")
        est = make_estimation()
        resp = api.post(f"/api/estimations/{est.id}/send-report", params={"to_email": "a@b.c"})
        assert resp.status_code == 200
        assert resp.json() == {"status": "ok", "message": "Report sent to a@b.c"}
        kwargs = adapter.send_estimation_report.call_args.kwargs
        assert kwargs["to_email"] == "a@b.c"
        assert kwargs["estimation_number"] == "EST-2026-0001"
        assert kwargs["project_name"] == "Report Test"
        assert kwargs["report_filename"] == "EST-2026-0001.pdf"
        assert kwargs["report_bytes"][:4] == b"%PDF"

    def test_not_configured(self, api, email_adapter):
        email_adapter(None)
        est = make_estimation()
        resp = api.post(f"/api/estimations/{est.id}/send-report", params={"to_email": "a@b.c"})
        assert resp.status_code == 400
        assert resp.json() == {"detail": "Email integration is not configured or not enabled."}

    def test_send_failure(self, api, email_adapter):
        email_adapter("FAILED", ["smtp down", "retry later"])
        est = make_estimation(estimation_number=None)
        resp = api.post(f"/api/estimations/{est.id}/send-report", params={"to_email": "a@b.c"})
        assert resp.status_code == 500
        assert resp.json() == {"detail": "Failed to send: smtp down; retry later"}

    def test_unknown_estimation(self, api, email_adapter):
        email_adapter("SUCCESS")
        resp = api.post("/api/estimations/99999/send-report", params={"to_email": "a@b.c"})
        assert resp.status_code == 404

    def test_requires_estimator(self, seeded, make_user, api_as, email_adapter):
        email_adapter("SUCCESS")
        est = make_estimation()
        resp = api_as(make_user("VIEWER")).post(
            f"/api/estimations/{est.id}/send-report", params={"to_email": "a@b.c"},
        )
        assert resp.status_code == 403

    def test_auth_required(self, anon_api):
        resp = anon_api.post("/api/estimations/1/send-report", params={"to_email": "a@b.c"})
        assert resp.status_code == 401
