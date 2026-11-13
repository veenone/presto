"""Feature Catalog page: list, filters, CRUD, promotion actions, categories dialog."""

import json

import pytest

from apps.catalog.models import Feature
from apps.core import config
from apps.estimations.models import Estimation

pytestmark = pytest.mark.django_db
HX = {"HX-Request": "true"}
HX_RESULTS = {"HX-Request": "true", "HX-Target": "crud-results"}


def names(resp) -> list[str]:
    return [r["name"] for r in resp.context["results"].rows]


def events(resp) -> dict:
    return json.loads(resp["HX-Trigger"])


@pytest.fixture
def client_as(web_login, make_user, seeded):
    return lambda role, **kw: web_login(make_user(role, **kw))


@pytest.fixture
def project_feature(make_user, seeded):
    author = make_user("ESTIMATOR", username="author")
    est = Estimation.objects.create(project_name="Apollo", project_type="NEW", estimation_number="EST-7",
                                    creator=author)
    feat = Feature.objects.create(name="Custom NFC", category="Telecom", is_global=False, owner_estimation=est)
    return feat, author


def payload(**kw):
    data = {"name": "Roaming", "category": "Telecom", "complexity_weight": "2", "product_type": "Telco",
            "study_effort_hours": ""}
    data.update(kw)
    return data


class TestList:
    def test_page_lists_global_features_for_any_role(self, client_as):
        resp = client_as("VIEWER").get("/features")
        assert resp.status_code == 200 and b"Feature Catalog" in resp.content
        assert resp.context["results"].total == Feature.objects.filter(is_global=True).count()
        assert b"Add Feature" not in resp.content and b'title="Edit"' not in resp.content
        assert b"Manage Categories" not in resp.content

    def test_approver_sees_add_and_row_actions(self, client_as):
        body = client_as("APPROVER").get("/features").content
        assert b"Add Feature" in body and b'title="Edit"' in body and b'title="Delete"' in body
        assert b"Manage Categories" not in body  # ADMIN only (PUT /api/configuration)

    def test_estimator_has_no_catalog_write_actions(self, client_as):
        body = client_as("ESTIMATOR").get("/features").content
        assert b"Add Feature" not in body and b'title="Edit"' not in body

    def test_admin_sees_manage_categories(self, admin_client):
        assert b"Manage Categories" in admin_client.get("/features").content

    def test_tiles_segments_and_category_filter(self, admin_client):
        resp = admin_client.get("/features")
        tiles = resp.context["results"].tiles
        assert tiles[0].label == "Total Features" and "with tests" in tiles[0].sub
        segs = {s["key"]: s["count"] for s in resp.context["results"].segments}
        assert segs["all"] == resp.context["results"].total
        telecom = admin_client.get("/features?seg=cat:Telecom")
        assert names(telecom) and all(r["category"] == "Telecom" for r in telecom.context["results"].rows)

    def test_unlisted_category_gets_a_segment(self, admin_client):
        Feature.objects.create(name="Odd", category="Legacy")
        segs = {s["key"] for s in admin_client.get("/features").context["results"].segments}
        assert "cat:Legacy" in segs

    def test_product_type_filter_keeps_counts_on_all_rows(self, admin_client):
        Feature.objects.create(name="Only Telco", category="Other", product_type="Telco")
        resp = admin_client.get("/features?product_type=Telco")
        assert names(resp) and all(r["product_type"] == "Telco" for r in resp.context["results"].rows)
        assert resp.context["results"].total == Feature.objects.filter(is_global=True).count()
        assert 'value="Telco" selected' in resp.content.decode()
        # links keep the filter
        assert "product_type=Telco" in resp.context["links"]["segments"]["all"]
        assert admin_client.get("/features?product_type=Bogus").context["state"].extra["product_type"] == "all"

    def test_search(self, admin_client):
        Feature.objects.create(name="Bluetooth LE", category="Platform")
        assert names(admin_client.get("/features?q=bluetooth")) == ["Bluetooth LE"]

    def test_study_effort_default_label_and_sort(self, admin_client):
        Feature.objects.create(name="Zeta", category="Other", study_effort_hours=12)
        rows = admin_client.get("/features?sort=study_effort_hours").context["results"].rows
        assert rows[0]["study_effort_hours"] == 12 and rows[-1]["study_effort_hours"] == "Default"
        assert b"12h" in admin_client.get("/features?q=zeta").content

    def test_htmx_partial(self, admin_client):
        resp = admin_client.get("/features?scope=project", headers=HX_RESULTS)
        assert b'id="crud-results"' in resp.content and b"ed-side-nav" not in resp.content


class TestScopes:
    def test_project_scope_lists_project_features_with_estimation(self, admin_client, project_feature):
        resp = admin_client.get("/features?scope=project")
        assert names(resp) == ["Custom NFC"]
        assert b"EST-7 \xc2\xb7 Apollo" in resp.content and b"Estimation" in resp.content
        assert "Custom NFC" not in names(admin_client.get("/features"))

    def test_requested_scope(self, admin_client, project_feature):
        assert names(admin_client.get("/features?scope=requested")) == []
        Feature.objects.filter(pk=project_feature[0].pk).update(promotion_requested=True)
        resp = admin_client.get("/features?scope=requested")
        assert names(resp) == ["Custom NFC"] and b"Requested" in resp.content
        assert any(t.label == "Promotion Requests" for t in admin_client.get("/features").context["results"].tiles)

    def test_row_actions_per_role(self, web_login, make_user, project_feature):
        feat, author = project_feature
        body = web_login(author).get("/features?scope=project").content.decode()
        assert f"/features/{feat.pk}/request-promotion" in body and f"/features/{feat.pk}/edit" in body
        assert f"/features/{feat.pk}/promote" not in body
        other = web_login(make_user("ESTIMATOR")).get("/features?scope=project").content.decode()
        assert f"/features/{feat.pk}/edit" not in other and "request-promotion" not in other
        appr = web_login(make_user("APPROVER")).get("/features?scope=project").content.decode()
        assert f"/features/{feat.pk}/promote" in appr and f"/features/{feat.pk}/edit" not in appr


class TestWrite:
    def test_create(self, client_as):
        client = client_as("APPROVER")
        assert b"Add Feature" in client.get("/features/new", headers=HX).content
        resp = client.post("/features/new", payload(name="  Roaming  ", has_existing_tests="on"), headers=HX)
        assert resp.status_code == 204 and events(resp)["toast"]["message"] == "Feature created."
        f = Feature.objects.get(name="Roaming")
        assert f.is_global and f.complexity_weight == 2 and f.has_existing_tests and f.study_effort_hours is None

    def test_validation(self, client_as):
        resp = client_as("APPROVER").post("/features/new", payload(name=" ", complexity_weight="20",
                                                                   study_effort_hours="500"), headers=HX)
        assert resp.status_code == 422
        body = resp.content.decode()
        assert "Name is required." in body or "This field is required." in body
        assert "less than or equal to 10" in body and "less than or equal to 200" in body

    def test_duplicate_shows_service_message(self, client_as):
        f = Feature.objects.filter(is_global=True).first()
        resp = client_as("APPROVER").post("/features/new", payload(name=f.name, category=f.category), headers=HX)
        assert resp.status_code == 422 and b"already exists in category" in resp.content

    def test_edit_keeps_removed_category_and_clears_optional_fields(self, client_as):
        f = Feature.objects.create(name="Legacy one", category="Legacy", product_type="Telco", study_effort_hours=3)
        client = client_as("APPROVER")
        form = client.get(f"/features/{f.pk}/edit", headers=HX)
        assert form.status_code == 200 and b'value="Legacy" selected' in form.content
        resp = client.post(f"/features/{f.pk}/edit", payload(name="Legacy two", category="Legacy",
                                                               product_type=""), headers=HX)
        assert resp.status_code == 204 and events(resp)["toast"]["message"] == "Feature updated."
        f.refresh_from_db()
        assert (f.name, f.product_type, f.study_effort_hours) == ("Legacy two", None, None)

    def test_edit_unknown_is_404(self, admin_client):
        assert admin_client.get("/features/9999/edit").status_code == 404

    def test_delete(self, client_as):
        f = Feature.objects.filter(is_global=True).first()
        client = client_as("APPROVER")
        assert b"This cannot be undone" in client.get(f"/features/{f.pk}/delete", headers=HX).content
        resp = client.post(f"/features/{f.pk}/delete", headers=HX)
        assert resp.status_code == 204 and not Feature.objects.filter(pk=f.pk).exists()

    @pytest.mark.parametrize("role", ["VIEWER", "ESTIMATOR"])
    def test_catalog_writes_need_approver(self, client_as, role):
        client = client_as(role)
        f = Feature.objects.filter(is_global=True).first()
        assert client.get("/features/new").status_code == 403
        assert client.post("/features/new", payload()).status_code == 403
        assert client.post(f"/features/{f.pk}/edit", payload()).status_code == 403
        assert client.post(f"/features/{f.pk}/delete").status_code == 403
        assert Feature.objects.filter(pk=f.pk, name=f.name).exists()

    def test_project_author_can_edit_and_delete(self, web_login, make_user, project_feature):
        feat, author = project_feature
        client = web_login(author)
        resp = client.post(f"/features/{feat.pk}/edit", payload(name="Custom NFC 2"), headers=HX)
        assert resp.status_code == 204
        assert web_login(make_user("ESTIMATOR")).post(f"/features/{feat.pk}/delete").status_code == 403
        assert web_login(author).post(f"/features/{feat.pk}/delete", headers=HX).status_code == 204


class TestPromotion:
    def test_promote(self, client_as, project_feature):
        feat, _ = project_feature
        client = client_as("APPROVER")
        assert b"Promote Feature" in client.get(f"/features/{feat.pk}/promote", headers=HX).content
        resp = client.post(f"/features/{feat.pk}/promote", headers=HX)
        assert resp.status_code == 204
        assert events(resp)["toast"]["message"] == "'Custom NFC' is now a global feature."
        assert events(resp)["crud-refresh"] and events(resp)["close-modal"]
        feat.refresh_from_db()
        assert feat.is_global and feat.owner_estimation_id is None

    def test_promote_clash_shows_service_message(self, client_as, project_feature):
        feat, _ = project_feature
        Feature.objects.create(name="Custom NFC", category="Telecom")
        resp = client_as("APPROVER").post(f"/features/{feat.pk}/promote", headers=HX)
        assert resp.status_code == 422 and b"already exists in category" in resp.content

    def test_promote_already_global(self, client_as):
        f = Feature.objects.filter(is_global=True).first()
        resp = client_as("APPROVER").post(f"/features/{f.pk}/promote", headers=HX)
        assert resp.status_code == 422 and b"Feature is already global" in resp.content

    @pytest.mark.parametrize("role", ["VIEWER", "ESTIMATOR"])
    def test_promote_needs_approver(self, client_as, project_feature, role):
        feat, _ = project_feature
        assert client_as(role).post(f"/features/{feat.pk}/promote").status_code == 403
        feat.refresh_from_db()
        assert not feat.is_global

    def test_request_promotion_by_author(self, web_login, project_feature):
        feat, author = project_feature
        client = web_login(author)
        assert b"Request Promotion" in client.get(f"/features/{feat.pk}/request-promotion", headers=HX).content
        resp = client.post(f"/features/{feat.pk}/request-promotion", headers=HX)
        assert resp.status_code == 204
        assert events(resp)["toast"]["message"] == "Requested adding 'Custom NFC' to the global catalog."
        feat.refresh_from_db()
        assert feat.promotion_requested

    def test_request_promotion_rules(self, client_as, project_feature):
        feat, _ = project_feature
        assert client_as("VIEWER").post(f"/features/{feat.pk}/request-promotion").status_code == 403
        resp = client_as("ESTIMATOR").post(f"/features/{feat.pk}/request-promotion", headers=HX)
        assert resp.status_code == 422 and b"Only the project&#x27;s author or an ADMIN" in resp.content
        assert client_as("ESTIMATOR").post("/features/9999/request-promotion").status_code == 404


class TestCategories:
    def test_admin_only(self, client_as):
        assert client_as("APPROVER").get("/features/categories").status_code == 403
        assert client_as("APPROVER").post("/features/categories", {"op": "add", "name": "X"}).status_code == 403

    def test_add_and_remove(self, admin_client):
        resp = admin_client.get("/features/categories", headers=HX)
        assert b"Manage Feature Categories" in resp.content and b"Telecom" in resp.content
        resp = admin_client.post("/features/categories", {"op": "add", "name": " Radio "}, headers=HX)
        assert resp.status_code == 200 and events(resp)["toast"]["message"] == "Added 'Radio'."
        assert config.get_str("feature_categories").endswith(",Radio")
        resp = admin_client.post("/features/categories", {"op": "remove", "name": "Radio"}, headers=HX)
        assert events(resp)["toast"]["message"] == "Removed 'Radio'." and "Radio" not in config.get_str(
            "feature_categories")

    @pytest.mark.parametrize(("name", "message"), [
        ("", "Enter a category name."), ("Telecom", "&#x27;Telecom&#x27; already exists."),
        ("a,b", "cannot contain commas"),
    ])
    def test_add_errors(self, admin_client, name, message):
        resp = admin_client.post("/features/categories", {"op": "add", "name": name}, headers=HX)
        assert resp.status_code == 422 and message.encode() in resp.content
