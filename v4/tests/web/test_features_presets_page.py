"""Feature Presets admin page: access, list, details, CRUD with the owner rule."""

import json

import pytest

from apps.catalog.models import Feature, FeaturePreset

pytestmark = pytest.mark.django_db
HX = {"HX-Request": "true"}


def names(resp) -> list[str]:
    return [r["name"] for r in resp.context["results"].rows]


@pytest.fixture
def client_as(web_login, make_user, seeded):
    return lambda role, **kw: web_login(make_user(role, **kw))


@pytest.fixture
def fids(seeded):
    return list(Feature.objects.filter(is_global=True).order_by("id").values_list("id", flat=True)[:3])


@pytest.fixture
def presets(make_user, fids):
    owner = make_user("ESTIMATOR", username="owner", display_name="Olive Owner")
    a = FeaturePreset.objects.create(name="Telco basics", product_type="Telco", feature_ids=fids[:2],
                                     owner_user=owner, description="Starter set")
    b = FeaturePreset.objects.create(name="Shared", feature_ids=fids[:1])
    return a, b, owner


class TestAccess:
    def test_viewer_refused(self, client_as):
        assert client_as("VIEWER").get("/feature-presets").status_code == 403

    def test_estimator_allowed(self, client_as):
        resp = client_as("ESTIMATOR").get("/feature-presets")
        assert resp.status_code == 200 and b"Feature Presets" in resp.content and b"Add Preset" in resp.content

    def test_rbac_permission_without_write_role_hides_add(self, client_as):
        from apps.accounts.permissions import MATRIX_CONFIG_KEY
        from apps.core.services import update_configuration
        update_configuration(MATRIX_CONFIG_KEY, json.dumps({"VIEWER": ["manage_presets"]}))
        client = client_as("VIEWER")
        resp = client.get("/feature-presets")
        assert resp.status_code == 200 and b"Add Preset" not in resp.content
        assert client.get("/feature-presets/new").status_code == 403


class TestList:
    def test_rows_tiles_segments(self, client_as, presets):
        resp = client_as("ADMIN").get("/feature-presets")
        assert names(resp) == ["Shared", "Telco basics"]
        row = resp.context["results"].rows[1]
        assert row["feature_count"] == 2 and row["owner_name"] == "Olive Owner" and row["product_label"] == "Telco"
        assert [t.label for t in resp.context["results"].tiles] == ["Total Presets", "Mine", "Any Product Type"]
        assert names(client_as("ADMIN").get("/feature-presets?seg=any")) == ["Shared"]
        assert names(client_as("ADMIN").get("/feature-presets?seg=pt:Telco")) == ["Telco basics"]

    def test_mine_segment_and_search(self, web_login, presets):
        _, _, owner = presets
        client = web_login(owner)
        assert names(client.get("/feature-presets?seg=mine")) == ["Telco basics"]
        assert names(client.get("/feature-presets?q=starter")) == ["Telco basics"]

    def test_actions_follow_owner_rule(self, client_as, presets):
        a, b, _ = presets
        body = client_as("ESTIMATOR").get("/feature-presets").content.decode()
        assert f"/feature-presets/{a.pk}/edit" not in body and f"/feature-presets/{b.pk}/edit" in body
        assert f'hx-get="/feature-presets/{a.pk}"' in body  # details for everyone

    def test_details(self, client_as, presets):
        a, _, _ = presets
        resp = client_as("ESTIMATOR").get(f"/feature-presets/{a.pk}", headers=HX)
        body = resp.content.decode()
        assert resp.status_code == 200 and "Telco basics" in body and "Starter set" in body
        assert "2 feature(s) · Telco · by Olive Owner" in body
        for f in Feature.objects.filter(pk__in=a.feature_ids):
            assert f.name in body
        assert client_as("ESTIMATOR").get("/feature-presets/9999").status_code == 404


class TestWrite:
    def test_create(self, client_as, fids):
        client = client_as("ESTIMATOR")
        form = client.get("/feature-presets/new", headers=HX)
        assert form.status_code == 200 and b'name="feature_ids"' in form.content
        resp = client.post("/feature-presets/new", {"name": " Mine ", "description": "", "product_type": "Payment",
                                                    "feature_ids": [str(fids[1]), str(fids[0])]}, headers=HX)
        assert resp.status_code == 204 and json.loads(resp["HX-Trigger"])["toast"]["message"] == "Preset created."
        p = FeaturePreset.objects.get(name="Mine")
        assert p.feature_ids == sorted(fids[:2]) and p.product_type == "Payment" and p.owner_user.role == "ESTIMATOR"

    def test_validation(self, client_as):
        resp = client_as("ESTIMATOR").post("/feature-presets/new", {"name": " ", "feature_ids": []}, headers=HX)
        assert resp.status_code == 422
        assert b"Preset name is required." in resp.content and b"Select at least one feature." in resp.content

    def test_edit_clears_optional_fields(self, web_login, presets, fids):
        a, _, owner = presets
        client = web_login(owner)
        form = client.get(f"/feature-presets/{a.pk}/edit", headers=HX).content.decode()
        assert "Telco basics" in form and f'value="{fids[0]}" x-model="on" checked' in form
        resp = client.post(f"/feature-presets/{a.pk}/edit", {"name": "Renamed", "description": "", "product_type": "",
                                                              "feature_ids": [str(fids[2])]}, headers=HX)
        assert resp.status_code == 204
        a.refresh_from_db()
        assert (a.name, a.description, a.product_type, a.feature_ids) == ("Renamed", None, None, [fids[2]])

    def test_non_owner_refused(self, client_as, presets, fids):
        a, _, _ = presets
        client = client_as("ESTIMATOR")
        assert client.get(f"/feature-presets/{a.pk}/edit").status_code == 403
        assert client.post(f"/feature-presets/{a.pk}/edit", {"name": "x", "feature_ids": [fids[0]]}).status_code == 403
        assert client.post(f"/feature-presets/{a.pk}/delete").status_code == 403
        assert FeaturePreset.objects.filter(pk=a.pk, name="Telco basics").exists()

    def test_admin_edits_any_and_deletes(self, admin_client, presets):
        a, _, _ = presets
        assert admin_client.get(f"/feature-presets/{a.pk}/edit", headers=HX).status_code == 200
        assert b"This cannot be undone" in admin_client.get(f"/feature-presets/{a.pk}/delete", headers=HX).content
        resp = admin_client.post(f"/feature-presets/{a.pk}/delete", headers=HX)
        assert resp.status_code == 204 and not FeaturePreset.objects.filter(pk=a.pk).exists()
        assert json.loads(resp["HX-Trigger"])["toast"]["message"] == "Preset deleted."

    def test_unknown_feature_id_is_ignored_by_picker_but_kept(self, admin_client, presets):
        a, _, _ = presets
        FeaturePreset.objects.filter(pk=a.pk).update(feature_ids=[*a.feature_ids, 999])
        body = admin_client.get(f"/feature-presets/{a.pk}", headers=HX).content
        assert b"Feature 999" in body
