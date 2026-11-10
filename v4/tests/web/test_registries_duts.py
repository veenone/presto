"""DUT Registry page: list, CRUD, bulk edit / delete, reinit."""

import json

import pytest

from apps.catalog.models import DutType
from apps.core import config

pytestmark = pytest.mark.django_db
HX = {"HX-Request": "true"}


def names(resp) -> list[str]:
    return [r["name"] for r in resp.context["results"].rows]


@pytest.fixture
def approver(web_login, make_user, seeded):
    return web_login(make_user("APPROVER"))


def toast(resp) -> dict:
    return json.loads(resp["HX-Trigger"])["toast"]


class TestList:
    def test_renders_with_selection_and_reinit_for_admin(self, admin_client):
        resp = admin_client.get("/duts")
        assert resp.status_code == 200 and b"DUT Registry" in resp.content
        assert len(resp.context["results"].rows) == DutType.objects.count() > 0
        body = resp.content
        assert b"Add DUT Type" in body and b"Reinit Registry" in body and b"data-row-check value=" in body
        assert b"css/pages/registries.css" in body

    def test_viewer_sees_list_only(self, web_login, make_user, seeded):
        body = web_login(make_user("VIEWER")).get("/duts").content
        assert b"Add DUT Type" not in body and b"data-row-check value=" not in body and b"Reinit Registry" not in body

    def test_approver_has_bulk_but_no_reinit(self, approver):
        body = approver.get("/duts").content
        assert b"data-row-check value=" in body and b"Bulk Edit" in body and b"Reinit Registry" not in body

    def test_categories_from_settings_drive_segments_and_tiles(self, admin_client):
        config.set_value("dut_categories", "SIM,Router")
        DutType.objects.all().delete()
        DutType.objects.create(name="R1", category="Router", product_type="Telco")
        DutType.objects.create(name="R2", category="Router")
        DutType.objects.create(name="S1", category="SIM")
        resp = admin_client.get("/duts?seg=Router")
        assert sorted(names(resp)) == ["R1", "R2"]
        segs = {s["key"]: s["count"] for s in resp.context["results"].segments}
        assert segs == {"all": 3, "SIM": 1, "Router": 2}
        tiles = resp.context["results"].tiles
        assert [t.label for t in tiles] == ["Total DUTs", "Router", "SIM"]
        assert tiles[0].sub == "1 have product type set"

    def test_search(self, admin_client):
        DutType.objects.create(name="Weird Modem", category="Other", product_type="Telco")
        assert names(admin_client.get("/duts?q=weird")) == ["Weird Modem"]


class TestCrud:
    def test_create_and_duplicate(self, approver):
        form = approver.get("/duts/new", headers=HX)
        assert form.status_code == 200 and b"Complexity Multiplier" in form.content
        resp = approver.post("/duts/new", {"name": " Router X ", "category": "SIM",
                                           "complexity_multiplier": "1.4", "product_type": "Telco"}, headers=HX)
        assert resp.status_code == 204 and toast(resp)["message"] == "DUT Type created."
        d = DutType.objects.get(name="Router X")
        assert d.complexity_multiplier == 1.4 and d.product_type == "Telco"
        dup = approver.post("/duts/new", {"name": "Router X", "category": "SIM", "complexity_multiplier": "1"})
        assert dup.status_code == 422 and b"already exists" in dup.content

    def test_validation(self, approver):
        resp = approver.post("/duts/new", {"name": "", "category": "Nope", "complexity_multiplier": "11"})
        assert resp.status_code == 422
        assert b"less than or equal to 10" in resp.content and b"Select a valid choice" in resp.content

    def test_edit_keeps_unlisted_category_and_clears_product_type(self, approver):
        d = DutType.objects.create(name="Legacy", category="Retired cat", product_type="Telco")
        form = approver.get(f"/duts/{d.pk}/edit", headers=HX)
        assert b"Retired cat" in form.content
        resp = approver.post(f"/duts/{d.pk}/edit", {"name": "Legacy 2", "category": "Retired cat",
                                                    "complexity_multiplier": "2", "product_type": ""})
        assert resp.status_code == 204
        d.refresh_from_db()
        assert (d.name, d.category, d.complexity_multiplier, d.product_type) == ("Legacy 2", "Retired cat", 2, None)

    def test_delete(self, approver):
        d = DutType.objects.first()
        assert b"This cannot be undone" in approver.get(f"/duts/{d.pk}/delete", headers=HX).content
        assert approver.post(f"/duts/{d.pk}/delete", headers=HX).status_code == 204
        assert not DutType.objects.filter(pk=d.pk).exists()
        assert approver.get("/duts/9999/edit").status_code == 404

    @pytest.mark.parametrize("role", ["VIEWER", "ESTIMATOR"])
    def test_writes_require_approver(self, web_login, make_user, seeded, role):
        client = web_login(make_user(role))
        d = DutType.objects.first()
        assert client.get("/duts").status_code == 200
        new = {"name": "x", "category": "SIM", "complexity_multiplier": "1"}
        assert client.post("/duts/new", new).status_code == 403
        assert client.post(f"/duts/{d.pk}/edit", {"name": "x"}).status_code == 403
        assert client.post(f"/duts/{d.pk}/delete").status_code == 403
        bulk = {"ids": [d.pk], "set_category": "on", "category": "SIM"}
        assert client.post("/duts/bulk-edit", bulk).status_code == 403
        assert client.post("/duts/bulk-delete", {"ids": [d.pk]}).status_code == 403
        assert DutType.objects.filter(pk=d.pk, name=d.name).exists()


class TestBulk:
    def test_bulk_edit_dialog_lists_selection(self, approver):
        a, b = DutType.objects.all()[:2]
        resp = approver.get(f"/duts/bulk-edit?ids={a.pk}&ids={b.pk}&ids=junk", headers=HX)
        assert resp.status_code == 200 and b"Update 2 selected DUT type(s)" in resp.content
        assert a.name.encode() in resp.content

    def test_bulk_edit_requires_selection_and_a_field(self, approver):
        resp = approver.get("/duts/bulk-edit", headers=HX)
        assert resp.status_code == 400 and resp.json()["detail"] == "Select at least one DUT type first."
        d = DutType.objects.first()
        resp = approver.post("/duts/bulk-edit", {"ids": [d.pk]}, headers=HX)
        assert resp.status_code == 422 and b"Check at least one field to update." in resp.content
        resp = approver.post("/duts/bulk-edit", {"ids": [d.pk], "set_multiplier": "on", "complexity_multiplier": "0"})
        assert resp.status_code == 422 and b"greater than or equal to 0.1" in resp.content

    def test_bulk_edit_applies_only_checked_fields(self, approver):
        a = DutType.objects.create(name="A1", category="SIM", complexity_multiplier=1, product_type="Telco")
        b = DutType.objects.create(name="B1", category="eSIM", complexity_multiplier=3, product_type="Payment")
        resp = approver.post("/duts/bulk-edit", {
            "ids": [a.pk, b.pk], "set_multiplier": "on", "complexity_multiplier": "2.5",
            "set_product_type": "on", "product_type": "__clear__", "category": "UICC",
        }, headers=HX)
        assert resp.status_code == 204
        events = json.loads(resp["HX-Trigger"])
        assert events["crud-refresh"] and events["close-modal"]
        assert events["toast"] == {"message": "Updated 2/2 DUT types.", "level": "success"}
        a.refresh_from_db()
        b.refresh_from_db()
        assert (a.category, a.complexity_multiplier, a.product_type) == ("SIM", 2.5, None)
        assert (b.category, b.complexity_multiplier, b.product_type) == ("eSIM", 2.5, None)

    def test_bulk_edit_counts_failures(self, approver, monkeypatch):
        from apps.catalog import services
        from apps.core.errors import ServiceError

        a, b = DutType.objects.all()[:2]
        real = services.update_dut_type

        def flaky(dut_id, updates):
            if dut_id == b.pk:
                raise ServiceError(409, "boom")
            return real(dut_id, updates)

        monkeypatch.setattr(services, "update_dut_type", flaky)
        resp = approver.post("/duts/bulk-edit", {"ids": [a.pk, b.pk], "set_category": "on", "category": "UICC"})
        assert toast(resp) == {"message": "Updated 1/2 DUT types. (1 failed)", "level": "warning"}

    def test_bulk_delete(self, approver):
        a, b = DutType.objects.all()[:2]
        confirm = approver.get(f"/duts/bulk-delete?ids={a.pk}&ids={b.pk}", headers=HX)
        assert b"Delete 2 selected DUT type(s)? This cannot be undone." in confirm.content
        resp = approver.post("/duts/bulk-delete", {"ids": [a.pk, b.pk, 99999]}, headers=HX)
        assert toast(resp)["message"] == "Deleted 2/2 DUT types."
        assert not DutType.objects.filter(pk__in=[a.pk, b.pk]).exists()
        assert approver.post("/duts/bulk-delete", {}).status_code == 400


class TestReinit:
    def test_admin_reinit_resets_ids(self, admin_client):
        assert b"delete ALL DUT types" in admin_client.get("/duts/reinit", headers=HX).content
        resp = admin_client.post("/duts/reinit", headers=HX)
        assert resp.status_code == 204 and toast(resp)["message"] == "DUT registry reinitialized."
        assert DutType.objects.count() == 0
        assert DutType.objects.create(name="Fresh").pk == 1

    @pytest.mark.parametrize("role", ["VIEWER", "APPROVER"])
    def test_reinit_is_admin_only(self, web_login, make_user, seeded, role):
        client = web_login(make_user(role))
        assert client.get("/duts/reinit").status_code == 403
        assert client.post("/duts/reinit").status_code == 403
        assert DutType.objects.exists()

    def test_reinit_rejects_other_methods(self, admin_client):
        assert admin_client.put("/duts/reinit").status_code == 405
