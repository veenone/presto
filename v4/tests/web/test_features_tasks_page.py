"""Task Templates page: list, filters, CRUD with feature links and per-feature hours."""

import json

import pytest

from apps.catalog.models import Feature, TaskTemplate, TaskTemplateFeature

pytestmark = pytest.mark.django_db
HX = {"HX-Request": "true"}


def names(resp) -> list[str]:
    return [r["name"] for r in resp.context["results"].rows]


@pytest.fixture
def client_as(web_login, make_user, seeded):
    return lambda role, **kw: web_login(make_user(role, **kw))


@pytest.fixture
def approver(client_as):
    return client_as("APPROVER")


@pytest.fixture
def fids(seeded):
    return list(Feature.objects.filter(is_global=True).order_by("id").values_list("id", flat=True)[:3])


def payload(**kw):
    data = {"name": "Fuzzing", "task_type": "EXECUTION", "base_effort_hours": "6", "product_type": "",
            "description": ""}
    data.update(kw)
    return data


class TestList:
    def test_any_role_sees_list_without_write_actions(self, client_as):
        resp = client_as("VIEWER").get("/tasks")
        assert resp.status_code == 200 and b"Task Templates" in resp.content
        assert resp.context["results"].total == TaskTemplate.objects.count()
        assert b"Add Task Template" not in resp.content and b'title="Edit"' not in resp.content

    def test_estimator_cannot_write(self, client_as):
        assert b"Add Task Template" not in client_as("ESTIMATOR").get("/tasks").content

    def test_tiles_and_type_segments(self, approver):
        TaskTemplate.objects.create(name="PR retest", task_type="EXECUTION", base_effort_hours=2, is_pr_fix=True)
        resp = approver.get("/tasks?seg=execution")
        assert names(resp) and all(r["task_type"] == "EXECUTION" for r in resp.context["results"].rows)
        tiles = resp.context["results"].tiles
        pr = TaskTemplate.objects.filter(is_pr_fix=True).count()
        assert tiles[0].label == "Total Templates" and tiles[0].sub == f"{pr} PR-fix templates"
        assert {s["key"] for s in resp.context["results"].segments} >= {"all", "setup", "execution", "study"}

    def test_product_type_filter_and_search(self, approver):
        TaskTemplate.objects.create(name="Payment cert", task_type="ANALYSIS", base_effort_hours=3,
                                    product_type="Payment", description="EMV level 2")
        assert names(approver.get("/tasks?product_type=Payment")) == ["Payment cert"]
        assert names(approver.get("/tasks?q=emv")) == ["Payment cert"]
        links = approver.get("/tasks?product_type=Payment").context["links"]
        assert links["self"] == "/tasks?product_type=Payment" and "product_type=Payment" in links["sort"]["name"]

    def test_feature_column(self, approver, fids):
        t = TaskTemplate.objects.create(name="Linked", task_type="SETUP", base_effort_hours=1)
        for fid in fids[:2]:
            TaskTemplateFeature.objects.create(task_template=t, feature_id=fid)
        row = next(r for r in approver.get("/tasks?q=linked").context["results"].rows)
        expected = ", ".join(Feature.objects.get(pk=f).name for f in fids[:2])
        assert row["feature"] == expected
        assert any(r["feature"] == "Global" for r in approver.get("/tasks").context["results"].rows)


class TestWrite:
    def test_create_with_feature_hours(self, approver, fids):
        form = approver.get("/tasks/new", headers=HX)
        assert form.status_code == 200 and f'name="hours_{fids[0]}"'.encode() in form.content
        resp = approver.post("/tasks/new", payload(
            feature_ids=[str(fids[0]), str(fids[1])], **{f"hours_{fids[0]}": "12.5", f"hours_{fids[1]}": "",
                                                         f"hours_{fids[2]}": "99"},
            scales_with_dut="on", is_pr_fix="on", product_type="Telco"), headers=HX)
        assert resp.status_code == 204
        assert json.loads(resp["HX-Trigger"])["toast"]["message"] == "Task Template created."
        t = TaskTemplate.objects.get(name="Fuzzing")
        links = {lnk.feature_id: lnk.base_hours_override for lnk in t.feature_links.all()}
        assert links == {fids[0]: 12.5, fids[1]: None}
        assert t.scales_with_dut and t.is_pr_fix and not t.scales_with_profile and t.product_type == "Telco"
        assert t.feature_id is None and t.base_effort_hours == 6

    def test_validation(self, approver, fids):
        resp = approver.post("/tasks/new", payload(name=" ", base_effort_hours="0", feature_ids=[str(fids[0])],
                                                   **{f"hours_{fids[0]}": "-3"}), headers=HX)
        assert resp.status_code == 422
        body = resp.content.decode()
        assert "Name is required." in body or "This field is required." in body
        assert "greater than or equal to 0.1" in body and "must be a positive number" in body
        assert not TaskTemplate.objects.filter(name="Fuzzing").exists()

    def test_unknown_feature_shows_service_message(self, approver):
        resp = approver.post("/tasks/new", payload(feature_ids=["4242"]), headers=HX)
        assert resp.status_code == 422 and b"Unknown feature id(s): 4242" in resp.content

    def test_edit_shows_overrides_and_updates_links(self, approver, fids):
        t = TaskTemplate.objects.create(name="Edit me", task_type="STUDY", base_effort_hours=4)
        TaskTemplateFeature.objects.create(task_template=t, feature_id=fids[0], base_hours_override=7)
        form = approver.get(f"/tasks/{t.pk}/edit", headers=HX).content.decode()
        assert "Edit me" in form and f'name="hours_{fids[0]}" value="7"' in form
        resp = approver.post(f"/tasks/{t.pk}/edit", payload(name="Edited", task_type="STUDY",
                                                             feature_ids=[str(fids[1])],
                                                             **{f"hours_{fids[1]}": "3"}), headers=HX)
        assert resp.status_code == 204
        t.refresh_from_db()
        assert t.name == "Edited" and t.feature_id == fids[1]
        assert [(lnk.feature_id, lnk.base_hours_override) for lnk in t.feature_links.all()] == [(fids[1], 3.0)]

    def test_edit_to_global_removes_links(self, approver, fids):
        t = TaskTemplate.objects.create(name="Unlink", task_type="SETUP", base_effort_hours=1)
        TaskTemplateFeature.objects.create(task_template=t, feature_id=fids[0])
        assert approver.post(f"/tasks/{t.pk}/edit", payload(name="Unlink"), headers=HX).status_code == 204
        assert not t.feature_links.exists()

    def test_edit_keeps_unknown_task_type(self, approver):
        t = TaskTemplate.objects.create(name="Odd", task_type="CUSTOM", base_effort_hours=1)
        assert b'value="CUSTOM" selected' in approver.get(f"/tasks/{t.pk}/edit", headers=HX).content

    def test_delete_and_404(self, approver):
        t = TaskTemplate.objects.first()
        assert b"This cannot be undone" in approver.get(f"/tasks/{t.pk}/delete", headers=HX).content
        assert approver.post(f"/tasks/{t.pk}/delete", headers=HX).status_code == 204
        assert not TaskTemplate.objects.filter(pk=t.pk).exists()
        assert approver.get("/tasks/9999/edit").status_code == 404

    @pytest.mark.parametrize("role", ["VIEWER", "ESTIMATOR"])
    def test_write_requires_approver(self, client_as, role):
        client = client_as(role)
        t = TaskTemplate.objects.first()
        assert client.get("/tasks/new").status_code == 403
        assert client.post("/tasks/new", payload()).status_code == 403
        assert client.post(f"/tasks/{t.pk}/edit", payload()).status_code == 403
        assert client.post(f"/tasks/{t.pk}/delete").status_code == 403
        assert TaskTemplate.objects.filter(pk=t.pk, name=t.name).exists()
