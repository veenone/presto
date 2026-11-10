"""Estimations list page (/estimations)."""

from __future__ import annotations

import pytest

from apps.estimations.models import Estimation
from apps.web.crud import PAGE_SIZE
from tests.reports.factories import make_estimation

pytestmark = pytest.mark.django_db
HX_RESULTS = {"HX-Request": "true", "HX-Target": "crud-results"}


def numbers(resp) -> list[str]:
    return [str(r["number"]) for r in resp.context["results"].rows]


@pytest.fixture
def sample(seeded):
    a = make_estimation(project_name="SIM Toolkit v2.5", estimation_number="EST-2026-001", with_request=True)
    b = make_estimation(project_name="eSIM Profile", estimation_number="EST-2026-002", status="FINAL", version=3)
    c = make_estimation(project_name="Banking <b>Applet</b>", estimation_number="EST-2026-003", status="APPROVED")
    Estimation.objects.filter(pk=b.pk).update(feasibility_status="AT_RISK", grand_total_hours=515.94)
    Estimation.objects.filter(pk=c.pk).update(feasibility_status="NOT_FEASIBLE")
    return a, b, c


class TestAccess:
    def test_every_role_sees_the_list(self, web_login, make_user, sample):
        for role in ("VIEWER", "ESTIMATOR", "APPROVER", "ADMIN"):
            resp = web_login(make_user(role)).get("/estimations")
            assert resp.status_code == 200, role
            assert b"SIM Toolkit v2.5" in resp.content

    def test_new_estimation_button_follows_create_role(self, web_login, make_user, seeded):
        button = b'btn-primary-text" href="/estimation/new"'
        assert button not in web_login(make_user("VIEWER")).get("/estimations").content
        assert button in web_login(make_user("ESTIMATOR")).get("/estimations").content

    def test_no_crud_dialog_routes(self, admin_client, sample):
        assert admin_client.get("/estimations/new").status_code == 404
        assert admin_client.get(f"/estimations/{sample[0].id}/edit").status_code == 404

    def test_anonymous_redirects_to_login(self, client, seeded):
        resp = client.get("/estimations")
        assert resp.status_code == 302 and "/login" in resp["Location"]


class TestContent:
    def test_columns_badges_and_links(self, admin_client, sample):
        a, b, c = sample
        body = admin_client.get("/estimations").content.decode()
        for label in ("Request", "Project", "Total Hours", "Feasibility", "Status", "Assigned To", "Created"):
            assert label in body
        assert f'href="/estimation/{a.id}"' in body
        assert 'class="badge est-badge est-status-FINAL"' in body
        assert 'class="badge est-badge est-feas-AT_RISK"' in body
        assert "EST-2026-002 (v3)" in body  # v3 version label
        assert "REQ_26/0015" in body
        assert "515.9" in body
        assert "Unassigned" in body
        assert "Banking &lt;b&gt;Applet&lt;/b&gt;" in body  # escaped

    def test_default_order_newest_first(self, admin_client, sample):
        assert numbers(admin_client.get("/estimations"))[0].startswith("EST-2026-003")

    def test_empty_state(self, admin_client, seeded):
        assert b"No estimations yet." in admin_client.get("/estimations").content


class TestFilters:
    def test_status_segments_and_tiles(self, admin_client, sample):
        resp = admin_client.get("/estimations?seg=FINAL")
        assert numbers(resp) == ["EST-2026-002 (v3)"]
        segs = {s["key"]: s["count"] for s in resp.context["results"].segments}
        assert segs == {"all": 3, "DRAFT": 1, "FINAL": 1, "APPROVED": 1, "REVISED": 0}
        tiles = {t.label: t.value for t in resp.context["results"].tiles}
        assert tiles["Total Estimations"] == "3" and tiles["At Risk"] == "2"
        assert "Revised" not in tiles

    def test_search(self, admin_client, sample):
        assert numbers(admin_client.get("/estimations?q=esim")) == ["EST-2026-002 (v3)"]
        assert numbers(admin_client.get("/estimations?q=REQ_26")) == ["EST-2026-001"]
        assert numbers(admin_client.get("/estimations?q=not_feasible")) == ["EST-2026-003"]

    def test_sort(self, admin_client, sample):
        resp = admin_client.get("/estimations?sort=grand_total_hours&dir=desc")
        assert numbers(resp)[0] == "EST-2026-002 (v3)"
        asc = numbers(admin_client.get("/estimations?sort=project_name"))
        assert asc == ["EST-2026-003", "EST-2026-002 (v3)", "EST-2026-001"]

    def test_paging(self, admin_client, seeded):
        for i in range(PAGE_SIZE + 2):
            Estimation.objects.create(project_name=f"P{i}", project_type="NEW", estimation_number=f"E-{i:03d}")
        first = admin_client.get("/estimations")
        assert len(first.context["results"].rows) == PAGE_SIZE and first.context["links"]["next"]
        assert len(admin_client.get("/estimations?page=2").context["results"].rows) == 2

    def test_htmx_partial(self, admin_client, sample):
        resp = admin_client.get("/estimations?seg=APPROVED", headers=HX_RESULTS)
        assert resp.status_code == 200
        body = resp.content.decode()
        assert 'id="crud-results"' in body
        assert "<html" not in body and "EST-2026-003" in body
