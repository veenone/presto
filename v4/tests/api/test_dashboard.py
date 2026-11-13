"""GET /dashboard/stats (port of v3 test_phase6_api.py TestDashboardStats, plus breakdowns)."""

from __future__ import annotations

from itertools import count

from apps.catalog.models import Feature, RiskItem, TaskTemplate
from apps.estimations.models import Estimation

_counter = count(1)


def _create_request(api) -> dict:
    resp = api.post("/api/requests", json={
        "request_number": f"REQ_26/{next(_counter):04d}",
        "title": "Test Request",
        "requester_name": "John Doe",
        "priority": "HIGH",
        "received_date": "2026-02-27",
    })
    assert resp.status_code == 201
    return resp.json()


class TestDashboardStats:
    def test_empty_dashboard(self, api):
        resp = api.get("/api/dashboard/stats")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total_requests"] == 0
        assert data["total_estimations"] == 0
        assert data["avg_grand_total_hours"] == 0
        assert data["recent_estimations"] == []

    def test_dashboard_with_data(self, api):
        _create_request(api)
        _create_request(api)
        Estimation.objects.create(project_name="Test Project", project_type="NEW", grand_total_hours=100.04)
        Estimation.objects.create(
            project_name="Other", project_type="NEW", status="FINAL", grand_total_hours=50,
        )

        data = api.get("/api/dashboard/stats").json()
        assert data["total_requests"] == 2
        assert data["requests_new"] == 2
        assert data["total_estimations"] == 2
        assert data["estimations_draft"] == 1
        assert data["estimations_final"] == 1
        assert data["avg_grand_total_hours"] == 75.0
        recent = data["recent_estimations"]
        assert {e["project_name"] for e in recent} == {"Test Project", "Other"}
        assert any(e["grand_total_hours"] == 100.0 for e in recent)
        assert len(recent[0]["created_at"]) == 16  # "YYYY-MM-DD HH:MM"
        assert data["recent_requests"][0]["priority"] == "HIGH"

    def test_dashboard_request_counts(self, api):
        _create_request(api)
        req2 = _create_request(api)
        req3 = _create_request(api)
        api.put(f"/api/requests/{req2['id']}", json={"status": "COMPLETED"})
        api.put(f"/api/requests/{req3['id']}", json={"status": "IN_ESTIMATION"})

        data = api.get("/api/dashboard/stats").json()
        assert data["total_requests"] == 3
        assert data["requests_new"] == 1
        assert data["requests_completed"] == 1
        assert data["requests_in_progress"] == 1

    def test_breakdowns(self, api):
        Feature.objects.all().delete()
        TaskTemplate.objects.all().delete()
        RiskItem.objects.all().delete()
        Feature.objects.create(name="A", category="Telecom")
        Feature.objects.create(name="B", category=None)
        TaskTemplate.objects.create(
            name="T1", task_type="EXECUTION", base_effort_hours=1, product_type="Telco",
        )
        TaskTemplate.objects.create(name="T2", task_type="EXECUTION", base_effort_hours=1)
        RiskItem.objects.create(name="R1", category="Schedule", likelihood="HIGH")
        RiskItem.objects.create(name="R2", likelihood="LOW")

        data = api.get("/api/dashboard/stats").json()
        assert data["features_by_category"] == {"Telecom": 1, "Uncategorized": 1}
        assert data["tasks_by_type"] == {"EXECUTION": 2}
        assert data["tasks_by_product_type"] == {"Telco": 1, "Unspecified": 1}
        assert data["risks_by_category"] == {"Schedule": 1, "General": 1}
        assert data["risks_by_likelihood"] == {"HIGH": 1, "LOW": 1}

    def test_seeded_catalog_breakdowns_present(self, api):
        data = api.get("/api/dashboard/stats").json()
        assert sum(data["features_by_category"].values()) == Feature.objects.count()

    def test_requires_auth(self, anon_api):
        assert anon_api.get("/api/dashboard/stats").status_code == 401

    def test_viewer_allowed(self, api_as, make_user):
        assert api_as(make_user("VIEWER")).get("/api/dashboard/stats").status_code == 200
