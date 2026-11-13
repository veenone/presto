"""Bulk import (/import/{entity_type}/preview and /import/{entity_type}) from CSV and Excel."""

from __future__ import annotations

from io import BytesIO

import openpyxl

from apps.accounts.models import AuditLog
from apps.catalog.models import DutType, Feature, TaskTemplate
from apps.estimations.models import HistoricalProject

FEATURES_CSV = (
    b"name,category,complexity_weight,has_existing_tests\n"
    b"Import Feature A,Telecom,1.5,yes\n"
    b"Import Feature B,,,\n"
    b",Missing name,1,no\n"
)


def _xlsx(rows: list[list]) -> bytes:
    wb = openpyxl.Workbook()
    ws = wb.active
    for row in rows:
        ws.append(row)
    buf = BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _post(api, path, filename, content, **kw):
    return api.post(path, files={"file": (filename, content, "application/octet-stream")}, **kw)


class TestPreview:
    def test_csv_preview_does_not_write(self, api):
        before = Feature.objects.count()
        resp = _post(api, "/api/import/features/preview", "features.csv", FEATURES_CSV)
        assert resp.status_code == 200
        data = resp.json()
        assert data["total_parsed"] == 3
        assert data["valid_count"] == 2
        assert len(data["errors"]) == 1
        assert data["rows"][0] == {
            "name": "Import Feature A", "category": "Telecom", "complexity_weight": 1.5,
            "has_existing_tests": True, "description": None,
        }
        assert Feature.objects.count() == before

    def test_excel_preview(self, api):
        content = _xlsx([["name", "category", "complexity_multiplier"], ["XL DUT", "SIM", 2]])
        resp = _post(api, "/api/import/dut_types/preview", "duts.xlsx", content)
        assert resp.status_code == 200
        assert resp.json()["valid_count"] == 1
        assert resp.json()["rows"][0]["name"] == "XL DUT"

    def test_unknown_entity_type(self, api):
        resp = _post(api, "/api/import/widgets/preview", "x.csv", b"name\nA\n")
        assert resp.status_code == 200
        assert resp.json()["valid_count"] == 0
        assert resp.json()["errors"]

    def test_preview_requires_estimator(self, api_as, make_user):
        resp = _post(api_as(make_user("VIEWER")), "/api/import/features/preview", "f.csv", FEATURES_CSV)
        assert resp.status_code == 403

    def test_preview_requires_file(self, api):
        assert api.post("/api/import/features/preview").status_code == 422


class TestExecute:
    def test_csv_import_and_skip_duplicates(self, api):
        resp = _post(api, "/api/import/features", "features.csv", FEATURES_CSV)
        assert resp.status_code == 200
        data = resp.json()
        assert data["imported"] == 2
        assert data["skipped"] == 0
        assert data["total_parsed"] == 3
        assert len(data["errors"]) == 1
        feat = Feature.objects.get(name="Import Feature A")
        assert feat.complexity_weight == 1.5 and feat.has_existing_tests is True
        assert AuditLog.objects.filter(action="IMPORT", resource_type="features").exists()

        again = _post(api, "/api/import/features", "features.csv", FEATURES_CSV).json()
        assert again["imported"] == 0
        assert again["skipped"] == 2

    def test_skip_duplicates_false_inserts_again(self, api):
        csv = b"name,category\nDup DUT,SIM\n"
        _post(api, "/api/import/dut_types", "d.csv", csv)
        resp = _post(api, "/api/import/dut_types", "d.csv", csv, params={"skip_duplicates": "false"})
        data = resp.json()
        # dut_types.name is unique: the row is reported as an insert error, not skipped.
        assert data["imported"] == 0 and data["skipped"] == 0
        assert data["errors"] and data["errors"][0].startswith("Insert failed for")
        assert DutType.objects.filter(name="Dup DUT").count() == 1

    def test_excel_task_templates(self, api):
        content = _xlsx([
            ["name", "task_type", "base_effort_hours", "scales_with_dut"],
            ["XL Template", "EXECUTION", 4, "true"],
        ])
        data = _post(api, "/api/import/task_templates", "t.xlsx", content).json()
        assert data["imported"] == 1
        tpl = TaskTemplate.objects.get(name="XL Template")
        assert tpl.base_effort_hours == 4 and tpl.scales_with_dut is True

    def test_historical_projects(self, api):
        csv = b"project_name,project_type,actual_hours,completion_date\nOld,NEW,120,2025-06-30\n"
        data = _post(api, "/api/import/historical_projects", "h.csv", csv).json()
        assert data["imported"] == 1
        assert str(HistoricalProject.objects.get(project_name="Old").completion_date) == "2025-06-30"

    def test_unknown_entity_type(self, api):
        data = _post(api, "/api/import/widgets", "x.csv", b"name\nA\n").json()
        assert data["imported"] == 0
        assert "Unknown entity type: widgets" in data["errors"]

    def test_execute_requires_approver(self, api_as, make_user):
        resp = _post(api_as(make_user("ESTIMATOR")), "/api/import/features", "f.csv", FEATURES_CSV)
        assert resp.status_code == 403
