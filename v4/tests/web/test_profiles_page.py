"""Test Profiles page; also covers the shared apps.web.crud behaviour."""

import json

import pytest

from apps.catalog.models import TestProfile as Profile
from apps.web.crud import PAGE_SIZE, ListState

pytestmark = pytest.mark.django_db
HX = {"HX-Request": "true"}
HX_RESULTS = {"HX-Request": "true", "HX-Target": "crud-results"}


def names(resp) -> list[str]:
    return [r["name"] for r in resp.context["results"].rows]


@pytest.fixture
def approver(web_login, make_user, seeded):
    return web_login(make_user("APPROVER"))


class TestList:
    def test_page_renders_seed_profiles(self, admin_client):
        resp = admin_client.get("/profiles")
        assert resp.status_code == 200 and b"Test Profiles" in resp.content
        assert len(resp.context["results"].rows) == Profile.objects.count() == 4
        assert b"Add Profile" in resp.content

    def test_viewer_sees_list_without_write_actions(self, web_login, make_user, seeded):
        body = web_login(make_user("VIEWER")).get("/profiles").content
        assert b"Add Profile" not in body and b'title="Edit"' not in body

    def test_search(self, admin_client):
        Profile.objects.create(name="Overnight soak", description="long run", effort_multiplier=3)
        assert names(admin_client.get("/profiles?q=soak")) == ["Overnight soak"]
        assert names(admin_client.get("/profiles?q=LONG RUN")) == ["Overnight soak"]

    def test_segments_and_tiles(self, admin_client):
        Profile.objects.create(name="Retired", is_active=False)
        resp = admin_client.get("/profiles?seg=inactive")
        assert names(resp) == ["Retired"]
        segs = {s["key"]: s["count"] for s in resp.context["results"].segments}
        assert segs == {"all": 5, "active": 4, "inactive": 1}
        assert [t.label for t in resp.context["results"].tiles][:3] == ["Total Profiles", "Active", "Inactive"]
        assert names(admin_client.get("/profiles?seg=bogus")) == names(admin_client.get("/profiles"))

    def test_sort_and_reject_unknown_sort_keys(self, admin_client):
        desc = names(admin_client.get("/profiles?sort=name&dir=desc"))
        assert desc == sorted(desc, key=str.lower, reverse=True)
        assert admin_client.get("/profiles?sort=__class__").status_code == 200

    def test_pagination(self, admin_client):
        for i in range(PAGE_SIZE + 3):
            Profile.objects.create(name=f"P{i:03d}")
        first = admin_client.get("/profiles")
        assert len(first.context["results"].rows) == PAGE_SIZE and first.context["links"]["next"]
        last = admin_client.get("/profiles?page=99")
        assert last.context["results"].page == 2 and len(last.context["results"].rows) == 7

    def test_htmx_returns_results_partial_only(self, admin_client):
        resp = admin_client.get("/profiles?q=smoke", headers=HX_RESULTS)
        assert resp.status_code == 200 and b'id="crud-results"' in resp.content
        assert b"ed-side-nav" not in resp.content and b"hx-swap-oob" in resp.content

    def test_query_string_round_trip(self):
        state = ListState(q="a b", seg="active", sort="name", dir="desc", page=2)
        assert state.query() == "q=a+b&seg=active&sort=name&dir=desc&page=2"
        assert ListState().query() == "" and state.query(page=1, seg="all") == "q=a+b&sort=name&dir=desc"


class TestWrite:
    def test_add_form_and_create(self, approver):
        assert b"Add Profile" in approver.get("/profiles/new", headers=HX).content
        resp = approver.post("/profiles/new", {"name": "  Soak  ", "effort_multiplier": "2.5",
                                               "product_type": "Telco", "is_active": "on"}, headers=HX)
        assert resp.status_code == 204
        events = json.loads(resp["HX-Trigger"])
        assert events["crud-refresh"] and events["close-modal"] and events["toast"]["message"] == "Profile created."
        p = Profile.objects.get(name="Soak")
        assert p.effort_multiplier == 2.5 and p.product_type == "Telco" and p.is_active

    def test_validation_error_rerenders_form_with_422(self, approver):
        resp = approver.post("/profiles/new", {"name": " ", "effort_multiplier": "50"}, headers=HX)
        assert resp.status_code == 422
        assert b"Name is required." in resp.content or b"This field is required." in resp.content
        assert b"less than or equal to 10" in resp.content

    def test_duplicate_name_shows_service_message(self, approver):
        existing = Profile.objects.first()
        resp = approver.post("/profiles/new", {"name": existing.name, "effort_multiplier": "1"}, headers=HX)
        assert resp.status_code == 422 and b"already exists" in resp.content

    def test_unknown_product_type_rejected(self, approver):
        resp = approver.post("/profiles/new", {"name": "X", "effort_multiplier": "1", "product_type": "Nope"})
        assert resp.status_code == 422

    def test_edit(self, approver):
        p = Profile.objects.first()
        form = approver.get(f"/profiles/{p.pk}/edit", headers=HX)
        assert form.status_code == 200 and p.name.encode() in form.content
        resp = approver.post(f"/profiles/{p.pk}/edit", {"name": "Renamed", "effort_multiplier": "1.5"}, headers=HX)
        assert resp.status_code == 204
        p.refresh_from_db()
        assert p.name == "Renamed" and p.is_active is False and p.product_type is None

    def test_edit_unknown_is_404(self, approver):
        assert approver.get("/profiles/9999/edit").status_code == 404

    def test_delete_confirm_and_delete(self, approver):
        p = Profile.objects.first()
        assert b"This cannot be undone" in approver.get(f"/profiles/{p.pk}/delete", headers=HX).content
        resp = approver.post(f"/profiles/{p.pk}/delete", headers=HX)
        assert resp.status_code == 204 and not Profile.objects.filter(pk=p.pk).exists()
        assert json.loads(resp["HX-Trigger"])["toast"]["message"] == "Profile deleted."

    @pytest.mark.parametrize("role", ["VIEWER", "ESTIMATOR"])
    def test_write_requires_approver(self, web_login, make_user, seeded, role):
        client = web_login(make_user(role))
        p = Profile.objects.first()
        assert client.get("/profiles/new").status_code == 403
        assert client.post("/profiles/new", {"name": "x", "effort_multiplier": "1"}).status_code == 403
        assert client.post(f"/profiles/{p.pk}/edit", {"name": "x", "effort_multiplier": "1"}).status_code == 403
        assert client.post(f"/profiles/{p.pk}/delete").status_code == 403
        assert Profile.objects.filter(pk=p.pk, name=p.name).exists()
