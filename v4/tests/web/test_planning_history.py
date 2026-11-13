"""Historical Projects page (/history)."""

import json
from datetime import date, timedelta

import pytest

from apps.catalog.pages.history import accuracy_ratio, format_age
from apps.core.errors import ServiceError
from apps.estimations.models import HistoricalProject as Project

pytestmark = pytest.mark.django_db
HX = {"HX-Request": "true"}
HX_RESULTS = {"HX-Request": "true", "HX-Target": "crud-results"}


def names(resp) -> list[str]:
    return [r["project_name"] for r in resp.context["results"].rows]


@pytest.fixture
def projects(seeded):
    return [
        Project.objects.create(project_name="SIM Toolkit", project_type="EVOLUTION", estimated_hours=420,
                               actual_hours=465, completion_date=date.today() - timedelta(days=120)),
        Project.objects.create(project_name="Wallet", project_type="NEW", estimated_hours=100, actual_hours=150,
                               notes="overran"),
        Project.objects.create(project_name="Hotline", project_type="SUPPORT"),
    ]


@pytest.fixture
def approver(web_login, make_user, seeded):
    return web_login(make_user("APPROVER"))


VALID = {"project_name": "  Contactless  ", "project_type": "NEW", "estimated_hours": "200",
         "actual_hours": "180", "dut_count": "3", "profile_count": "2", "pr_count": "5",
         "completion_date": "2026-03-01", "features_json": '["NFC", "EMV"]', "notes": "ok"}


class TestHelpers:
    def test_accuracy_ratio(self):
        assert accuracy_ratio(100, 130) == 1.3 and accuracy_ratio(0, 10) is None
        assert accuracy_ratio(None, 5) is None and accuracy_ratio(10, None) == 0

    def test_format_age(self):
        today = date(2026, 6, 1)
        assert format_age(None) == ""
        assert [format_age(today - timedelta(days=d), today) for d in (0, 3, 14, 120)] == ["today", "3d", "2w", "4mo"]


class TestList:
    @pytest.mark.parametrize("role", ["VIEWER", "ESTIMATOR"])
    def test_any_user_sees_list_without_write_actions(self, web_login, make_user, projects, role):
        resp = web_login(make_user(role)).get("/history")
        assert resp.status_code == 200 and b"Historical Projects" in resp.content
        assert len(resp.context["results"].rows) == 3
        assert b"Add Project" not in resp.content and b'title="Edit"' not in resp.content

    def test_admin_sees_write_actions_and_ratio_colours(self, admin_client, projects):
        body = admin_client.get("/history").content.decode()
        assert "Add Project" in body and 'title="Edit"' in body and "Refresh" in body
        assert 'tone-negative">1.50' in body and 'tone-positive">1.11' in body and "N/A" in body
        assert 'title="' + (date.today() - timedelta(days=120)).strftime("%d %b %Y") in body and ">4mo<" in body

    def test_tiles_and_segments(self, admin_client, projects):
        resp = admin_client.get("/history?seg=NEW")
        assert names(resp) == ["Wallet"]
        tiles = {t.label: t for t in resp.context["results"].tiles}
        assert list(tiles) == ["Total Projects", "Avg Accuracy", "New", "Evolution", "Support"]
        assert tiles["Total Projects"].sub == "615h actual"
        assert tiles["Avg Accuracy"].value == "1.30×" and tiles["Avg Accuracy"].sub == "1 over 1.3× ratio"
        segs = {s["key"]: s["count"] for s in resp.context["results"].segments}
        assert segs == {"all": 3, "NEW": 1, "EVOLUTION": 1, "SUPPORT": 1}

    def test_empty_state(self, admin_client):
        resp = admin_client.get("/history")
        assert b"No historical projects yet" in resp.content
        assert resp.context["results"].tiles[1].value == "—"

    def test_search_and_sort(self, admin_client, projects):
        assert names(admin_client.get("/history?q=OVERRAN")) == ["Wallet"]
        assert names(admin_client.get("/history?sort=accuracy_ratio")) == ["SIM Toolkit", "Wallet", "Hotline"]

    def test_htmx_partial(self, admin_client, projects):
        resp = admin_client.get("/history?seg=SUPPORT", headers=HX_RESULTS)
        assert b'id="crud-results"' in resp.content and b"ed-side-nav" not in resp.content
        assert names(resp) == ["Hotline"]


class TestWrite:
    def test_create(self, approver):
        form = approver.get("/history/new", headers=HX)
        assert form.status_code == 200 and b"Features JSON" in form.content
        resp = approver.post("/history/new", VALID, headers=HX)
        assert resp.status_code == 204
        assert json.loads(resp["HX-Trigger"])["toast"]["message"] == "Project created."
        p = Project.objects.get()
        assert p.project_name == "Contactless" and p.features == ["NFC", "EMV"]
        assert (p.dut_count, p.profile_count, p.pr_count, p.estimated_hours) == (3, 2, 5, 200.0)
        assert p.completion_date == date(2026, 3, 1) and p.notes == "ok"

    def test_minimal_create_defaults(self, approver):
        resp = approver.post("/history/new", {"project_name": "X", "project_type": "SUPPORT"}, headers=HX)
        assert resp.status_code == 204
        p = Project.objects.get()
        assert p.features == [] and p.estimated_hours is None and p.notes is None

    def test_validation_errors(self, approver):
        bad = {**VALID, "project_name": " ", "project_type": "BOGUS", "features_json": "[oops",
               "actual_hours": "-1", "completion_date": "31/02/2026"}
        resp = approver.post("/history/new", bad, headers=HX)
        assert resp.status_code == 422
        body = resp.content.decode()
        assert body.count("This field is required.") == 1  # blank name
        for msg in ("Select a valid choice", "Features JSON is not valid JSON.",
                    "greater than or equal to 0", "Enter a valid date"):
            assert msg in body
        assert not Project.objects.exists()

    def test_service_error_shown_in_dialog(self, approver, monkeypatch):
        def boom(data):
            raise ServiceError(404, "Estimation not found")
        monkeypatch.setattr("apps.catalog.services.create_historical_project", boom)
        resp = approver.post("/history/new", VALID, headers=HX)
        assert resp.status_code == 422 and b"Estimation not found" in resp.content

    def test_edit_keeps_unknown_type_and_features(self, approver, projects):
        p = Project.objects.create(project_name="Legacy", project_type="MIGRATION", features=[{"id": 1}])
        form = approver.get(f"/history/{p.pk}/edit", headers=HX).content.decode()
        assert 'value="MIGRATION" selected' in form and "[{&quot;id&quot;: 1}]" in form
        resp = approver.post(f"/history/{p.pk}/edit", {"project_name": "Legacy 2", "project_type": "MIGRATION",
                                                       "features_json": '[{"id": 1}]'}, headers=HX)
        assert resp.status_code == 204
        p.refresh_from_db()
        assert p.project_name == "Legacy 2" and p.features == [{"id": 1}]

    def test_edit_and_delete_unknown_is_404(self, approver):
        assert approver.get("/history/999/edit").status_code == 404
        assert approver.post("/history/999/delete").status_code == 404

    def test_delete(self, approver, projects):
        p = projects[0]
        assert b"SIM Toolkit" in approver.get(f"/history/{p.pk}/delete", headers=HX).content
        resp = approver.post(f"/history/{p.pk}/delete", headers=HX)
        assert resp.status_code == 204 and not Project.objects.filter(pk=p.pk).exists()

    @pytest.mark.parametrize("role", ["VIEWER", "ESTIMATOR"])
    def test_write_requires_approver(self, web_login, make_user, projects, role):
        client = web_login(make_user(role))
        p = projects[0]
        assert client.get("/history/new").status_code == 403
        assert client.post("/history/new", VALID).status_code == 403
        assert client.post(f"/history/{p.pk}/edit", VALID).status_code == 403
        assert client.post(f"/history/{p.pk}/delete").status_code == 403
        assert Project.objects.count() == 3
