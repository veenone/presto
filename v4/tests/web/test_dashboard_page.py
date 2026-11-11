"""Dashboard page (/): numbers from the shared stats service, charts payload, links, permissions."""

from __future__ import annotations

import json
import re
from datetime import datetime, timedelta

import pytest

from apps.catalog.models import Feature, RiskItem, TaskTemplate
from apps.core import config
from apps.estimations.models import Estimation
from apps.inbox.models import Request
from apps.web.views import format_age
from tests.conftest import ApiClient

pytestmark = pytest.mark.django_db


def _payload(resp) -> list[dict]:
    body = resp.content.decode()
    m = re.search(r'<script id="dash-chart-data" type="application/json">(.*?)</script>', body, re.S)
    assert m, "json_script payload missing"
    return json.loads(m.group(1))


def _sample_data():
    for i, status in enumerate(["NEW", "NEW", "IN_ESTIMATION", "COMPLETED", "REJECTED"]):
        Request.objects.create(request_number=f"REQ_26/{i + 1:04d}", title=f"Request {i}",
                               requester_name="R", received_date="2026-11-01",
                               priority=["HIGH", "LOW", "MEDIUM", "CRITICAL", "LOW"][i], status=status)
    for i, (status, hours) in enumerate([("DRAFT", 100), ("DRAFT", 20), ("FINAL", 300), ("APPROVED", 516)]):
        Estimation.objects.create(project_name=f"Project {i}", project_type="NEW", status=status,
                                  grand_total_hours=hours, estimation_number=f"EST-2026-{i + 1:03d}",
                                  feasibility_status="FEASIBLE")


@pytest.fixture
def empty_db(seeded):
    Feature.objects.all().delete()
    TaskTemplate.objects.all().delete()
    RiskItem.objects.all().delete()
    return seeded


class TestDashboardPage:
    def test_renders_for_viewer(self, web_login, make_user, seeded):
        resp = web_login(make_user("VIEWER")).get("/")
        assert resp.status_code == 200
        body = resp.content.decode()
        assert "Operational summary · live" in body
        assert "vendor/echarts-5.6.0.min.js" in body and "js/pages/dashboard.js" in body
        assert 'x-data="dashboardCharts"' in body
        assert "{#" not in body and "{%" not in body  # no template comment leaks

    def test_requires_login(self, client, db):
        resp = client.get("/")
        assert resp.status_code == 302 and "/login" in resp["Location"]

    def test_post_not_allowed(self, admin_client):
        assert admin_client.post("/").status_code == 405

    def test_numbers_match_api(self, admin_client, admin_user):
        _sample_data()
        api = ApiClient(admin_client, admin_user).get("/api/dashboard/stats").json()
        ctx = admin_client.get("/").context
        stats = ctx["stats"]
        for key, value in api.items():
            if key.startswith("recent_"):
                assert stats[key] == value[:6], key
            else:
                assert stats[key] == value, key
        kpis = {k["label"]: k for k in ctx["kpis"]}
        assert kpis["Open Requests"]["value"] == str(api["total_requests"] - api["requests_completed"]) == "4"
        assert kpis["Open Requests"]["sub"] == "2 new in inbox"
        assert kpis["Active Estimations"]["value"] == "3"
        assert kpis["Active Estimations"]["sub"] == "1 awaiting approval"
        assert kpis["Avg Estimation"]["value"] == "234h"
        assert kpis["Team Utilization"]["value"] == "0%"
        assert [s["count"] for s in ctx["pipeline"]] == [2, 1, 2, 1, 1]

    def test_chart_payload_shape(self, admin_client):
        _sample_data()
        payload = _payload(admin_client.get("/"))
        ids = [c["id"] for c in payload]
        assert ids[:2] == ["estimations", "requests"]
        for c in payload:
            assert set(c) == {"id", "kind", "name", "data", "nav"}
            assert c["kind"] in {"donut", "bar"}
            for d in c["data"]:
                assert set(d) == {"name", "value", "color"} and d["color"].startswith("#")
        by_id = {c["id"]: c for c in payload}
        assert {d["name"]: d["value"] for d in by_id["estimations"]["data"]} == \
            {"Draft": 2, "Final": 1, "Approved": 1}
        assert {d["name"]: d["value"] for d in by_id["requests"]["data"]} == \
            {"New": 2, "In Progress": 1, "Completed": 1, "Other": 1}
        assert by_id["task-types"]["kind"] == "bar"
        values = [d["value"] for d in by_id["task-types"]["data"]]
        assert values == sorted(values, reverse=True)

    def test_catalog_charts_follow_breakdowns(self, admin_client, empty_db):
        Feature.objects.create(name="A", category="Telecom")
        Feature.objects.create(name="B", category=None)
        RiskItem.objects.create(name="R1", likelihood="HIGH")
        payload = {c["id"]: c for c in _payload(admin_client.get("/"))}
        assert {d["name"]: d["value"] for d in payload["features"]["data"]} == {"Telecom": 1, "Uncategorized": 1}
        assert payload["risks"]["data"] == [{"name": "HIGH", "value": 1, "color": "#EF5350"}]
        assert "task-types" not in payload  # no task templates: empty card, no chart

    def test_empty_database(self, admin_client, empty_db):
        resp = admin_client.get("/")
        assert resp.status_code == 200
        body = resp.content.decode()
        assert _payload(resp) == []
        assert "data-chart=" not in body
        for text in ("No estimations yet", "No requests yet", "No features configured yet",
                     "No risks configured yet", "No task templates yet"):
            assert text in body
        assert [s["count"] for s in resp.context["pipeline"]] == [0] * 5
        assert resp.context["kpis"][2]["value"] == "0h"

    def test_recent_lists_link_to_details(self, admin_client):
        _sample_data()
        body = admin_client.get("/").content.decode()
        est = Estimation.objects.order_by("-created_at", "-id").first()
        req = Request.objects.order_by("-created_at", "-id").first()
        assert f'href="/estimation/{est.id}"' in body
        assert f'href="/requests/{req.id}"' in body
        assert 'href="/estimations">see all' in body and 'href="/requests">see all' in body

    def test_recent_lists_capped_at_six(self, admin_client):
        for i in range(8):
            Estimation.objects.create(project_name=f"P{i}", project_type="NEW")
        assert len(admin_client.get("/").context["recent_estimations"]) == 6

    def test_viewer_gets_no_request_links(self, web_login, make_user, seeded):
        _sample_data()
        resp = web_login(make_user("VIEWER")).get("/")
        body = resp.content.decode()
        assert 'href="/requests' not in body
        assert "REQ_26/0005" in body  # still listed, just not linked
        assert all(s["href"] is None for s in resp.context["pipeline"][:2])
        assert next(c for c in _payload(resp) if c["id"] == "requests")["nav"] is None
        assert f'href="/estimation/{Estimation.objects.first().id}"' in body

    def test_matrix_grant_restores_request_links(self, web_login, make_user, seeded):
        _sample_data()
        config.set_value("rbac_matrix", {"VIEWER": ["view_requests"]})
        body = web_login(make_user("VIEWER")).get("/").content.decode()
        assert 'href="/requests">see all' in body

    def test_summary_partial(self, admin_client):
        _sample_data()
        resp = admin_client.get("/dashboard/summary", headers={"HX-Request": "true"})
        assert resp.status_code == 200
        body = resp.content.decode()
        assert 'id="dash-summary"' in body and 'hx-trigger="every 60s"' in body
        assert "Pipeline State" in body and "<html" not in body


class TestFormatAge:
    def test_buckets(self):
        now = datetime(2026, 11, 11, 12, 0)
        stamp = lambda d: (now - timedelta(days=d)).strftime("%Y-%m-%d %H:%M")  # noqa: E731
        assert format_age(stamp(0), now) == "today"
        assert format_age(stamp(3), now) == "3d"
        assert format_age(stamp(15), now) == "2w"
        assert format_age(stamp(65), now) == "2mo"
        assert format_age(None, now) == "" and format_age("bad", now) == "bad"
