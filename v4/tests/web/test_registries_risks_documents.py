"""Risk Registry and Document Types pages."""

import json

import pytest

from apps.catalog.models import DocumentType, RiskItem, TaskTemplate

pytestmark = pytest.mark.django_db
HX = {"HX-Request": "true"}


def names(resp) -> list[str]:
    return [r["name"] for r in resp.context["results"].rows]


def toast(resp) -> dict:
    return json.loads(resp["HX-Trigger"])["toast"]


@pytest.fixture
def estimator(web_login, make_user, seeded):
    return web_login(make_user("ESTIMATOR"))


# ── Risks ────────────────────────────────────────────────────────────────────

class TestRisks:
    def test_list_badges_segments_tiles(self, admin_client):
        RiskItem.objects.all().delete()
        RiskItem.objects.create(name="Late DUTs", category="Supply", likelihood="HIGH", impact="HIGH")
        RiskItem.objects.create(name="Lab outage", category="Infra", likelihood="LOW", impact="MEDIUM")
        RiskItem.objects.create(name="Retired", likelihood="HIGH", is_active=False)
        resp = admin_client.get("/risks")
        assert resp.status_code == 200 and sorted(names(resp)) == ["Lab outage", "Late DUTs"]
        assert b'class="badge reg-badge tone-negative">HIGH' in resp.content
        assert b'tone-positive">LOW' in resp.content
        tiles = resp.context["results"].tiles
        assert [(t.label, t.value) for t in tiles] == [
            ("Total Risks", "2"), ("High Likelihood", "1"), ("Medium", "0"), ("Low", "1")]
        assert tiles[0].sub == "2 categories · 1 high impact"
        assert names(admin_client.get("/risks?seg=high")) == ["Late DUTs"]
        assert names(admin_client.get("/risks?q=infra")) == ["Lab outage"]
        assert names(admin_client.get("/risks?sort=likelihood&dir=desc"))[0] == "Lab outage"

    def test_estimator_can_add_and_edit_but_not_delete(self, estimator):
        RiskItem.objects.create(name="Seeded risk")
        body = estimator.get("/risks").content
        assert b"Add Risk" in body and b'title="Edit"' in body and b'title="Delete"' not in body
        resp = estimator.post("/risks/new", {"name": " Scope creep ", "category": "Project", "likelihood": "HIGH",
                                             "impact": "LOW", "description": "d", "mitigation": "m"}, headers=HX)
        assert resp.status_code == 204 and toast(resp)["message"] == "Risk created."
        r = RiskItem.objects.get(name="Scope creep")
        assert (r.likelihood, r.impact, r.mitigation) == ("HIGH", "LOW", "m")
        resp = estimator.post(f"/risks/{r.pk}/edit", {"name": "Scope creep", "likelihood": "MEDIUM", "impact": "HIGH"})
        assert resp.status_code == 204
        r.refresh_from_db()
        assert (r.likelihood, r.impact, r.category, r.description) == ("MEDIUM", "HIGH", "", "")
        assert estimator.get(f"/risks/{r.pk}/delete").status_code == 403
        assert estimator.post(f"/risks/{r.pk}/delete").status_code == 403
        assert RiskItem.objects.filter(pk=r.pk).exists()

    def test_validation_and_duplicates(self, estimator):
        existing = RiskItem.objects.create(name="Dup")
        resp = estimator.post("/risks/new", {"name": "Dup", "likelihood": "LOW", "impact": "LOW"}, headers=HX)
        assert resp.status_code == 422 and b"Risk item &#x27;Dup&#x27; already exists" in resp.content
        resp = estimator.post("/risks/new", {"name": " ", "likelihood": "EXTREME", "impact": "LOW"})
        assert resp.status_code == 422 and b"Select a valid choice" in resp.content
        assert estimator.get(f"/risks/{existing.pk + 999}/edit").status_code == 404

    def test_approver_deletes(self, web_login, make_user, seeded):
        client = web_login(make_user("APPROVER"))
        r = RiskItem.objects.create(name="Gone soon")
        assert b'title="Delete"' in client.get("/risks").content
        resp = client.post(f"/risks/{r.pk}/delete", headers=HX)
        assert resp.status_code == 204 and toast(resp)["message"] == "Risk deleted."
        assert not RiskItem.objects.filter(pk=r.pk).exists()

    def test_viewer_read_only(self, web_login, make_user, seeded):
        client = web_login(make_user("VIEWER"))
        body = client.get("/risks").content
        assert b"Add Risk" not in body and b'title="Edit"' not in body
        assert client.post("/risks/new", {"name": "x", "likelihood": "LOW", "impact": "LOW"}).status_code == 403


# ── Document types ───────────────────────────────────────────────────────────

class TestDocumentTypes:
    def test_list_includes_inactive_and_linked_task(self, admin_client):
        DocumentType.objects.all().delete()
        tmpl = TaskTemplate.objects.first()
        DocumentType.objects.create(name="Test Report", category="Report", base_effort_hours=6, task_template=tmpl)
        DocumentType.objects.create(name="Old Plan", category="Planning", base_effort_hours=2, is_active=False)
        resp = admin_client.get("/document-types")
        assert resp.status_code == 200 and b"Manage document types" in resp.content
        assert names(resp) == ["Old Plan", "Test Report"]
        assert tmpl.name.encode() in resp.content
        tiles = resp.context["results"].tiles
        assert [(t.label, t.value) for t in tiles] == [
            ("Total Types", "2"), ("Avg Hours", "4.0h"), ("Planning", "1"), ("Report", "1")]
        assert tiles[0].sub == "1 active · 1 linked to a task"
        assert names(admin_client.get("/document-types?seg=planning")) == ["Old Plan"]
        assert names(admin_client.get(f"/document-types?q={tmpl.name[:5]}")) == ["Test Report"]

    def test_create_without_active_switch(self, estimator):
        form = estimator.get("/document-types/new", headers=HX).content
        assert b"Linked Task Template" in form and b'name="is_active"' not in form
        tmpl = TaskTemplate.objects.first()
        resp = estimator.post("/document-types/new", {"name": "Audit Pack", "category": "Compliance",
                                                      "base_effort_hours": "3.5", "task_template_id": str(tmpl.pk),
                                                      "description": "  "}, headers=HX)
        assert resp.status_code == 204 and toast(resp)["message"] == "Document Type created."
        d = DocumentType.objects.get(name="Audit Pack")
        assert (d.base_effort_hours, d.task_template_id, d.description, d.is_active) == (3.5, tmpl.pk, None, True)

    def test_edit_deactivates_and_unlinks(self, estimator):
        tmpl = TaskTemplate.objects.first()
        d = DocumentType.objects.create(name="Spec", task_template=tmpl)
        form = estimator.get(f"/document-types/{d.pk}/edit", headers=HX).content
        assert b'name="is_active"' in form
        resp = estimator.post(f"/document-types/{d.pk}/edit", {"name": "Spec v2", "category": "Report",
                                                               "base_effort_hours": "4", "task_template_id": ""})
        assert resp.status_code == 204
        d.refresh_from_db()
        assert (d.name, d.is_active, d.task_template_id) == ("Spec v2", False, None)

    def test_validation(self, estimator):
        DocumentType.objects.create(name="Taken")
        resp = estimator.post("/document-types/new", {"name": "Taken", "category": "Report", "base_effort_hours": "4"})
        assert resp.status_code == 422 and b"already exists" in resp.content
        resp = estimator.post("/document-types/new", {"name": "X", "category": "Report", "base_effort_hours": "0.1",
                                                      "task_template_id": "999999"})
        assert resp.status_code == 422
        assert b"greater than or equal to 0.5" in resp.content and b"Select a valid choice" in resp.content

    def test_delete_needs_approver(self, estimator, web_login, make_user):
        d = DocumentType.objects.create(name="Temp")
        assert estimator.post(f"/document-types/{d.pk}/delete").status_code == 403
        approver = web_login(make_user("APPROVER"))
        assert b"Delete &#x27;Temp&#x27;?" in approver.get(f"/document-types/{d.pk}/delete", headers=HX).content
        assert approver.post(f"/document-types/{d.pk}/delete", headers=HX).status_code == 204
        assert not DocumentType.objects.filter(pk=d.pk).exists()

    def test_viewer_cannot_write(self, web_login, make_user, seeded):
        client = web_login(make_user("VIEWER"))
        assert client.get("/document-types").status_code == 200
        assert client.get("/document-types/new").status_code == 403
