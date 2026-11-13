"""DUT Assets (Snipe-IT) and PR Registry (Jira) pages; the adapters are mocked."""

import json
from unittest import mock

import pytest
from django.core.cache import cache

from apps.catalog.models import DutType
from apps.integrations.adapters import JiraAdapter, SnipeItAdapter
from apps.integrations.models import IntegrationConfig

pytestmark = pytest.mark.django_db
HX = {"HX-Request": "true"}

ASSETS = [
    {"id": 1, "name": "Phone A", "serial": "SN1", "model_name": "M1", "category": "Phones",
     "status": "Ready", "asset_tag": "T1"},
    {"id": 2, "name": "Phone B", "serial": "SN2", "model_name": "M1", "category": "Phones",
     "status": "Deployed", "asset_tag": "T2"},
    {"id": 3, "name": "Modem C", "serial": "SN3", "model_name": "M9", "category": "Modems",
     "status": "Ready", "asset_tag": "T3"},
]


@pytest.fixture(autouse=True)
def clear_cache():
    cache.clear()
    yield
    cache.clear()


@pytest.fixture
def snipe(db):
    IntegrationConfig.objects.update_or_create(system_name="SNIPE_IT", defaults={
        "enabled": True, "base_url": "https://snipe.invalid", "api_key": "k",
        "additional_config": {"categories": "Phones, Modems"}})
    with mock.patch.object(SnipeItAdapter, "get_hardware_by_category", return_value=ASSETS) as m:
        yield m


def names(resp) -> list[str]:
    return [r["name"] for r in resp.context["results"].rows]


# ── Assets ───────────────────────────────────────────────────────────────────

class TestAssets:
    def test_not_configured_banner(self, admin_client):
        IntegrationConfig.objects.filter(system_name="SNIPE_IT").delete()
        resp = admin_client.get("/assets")
        assert resp.status_code == 200
        assert b"The Snipe-IT integration is not configured or not enabled." in resp.content
        assert resp.context["results"].total == 0

    def test_connection_error_banner(self, admin_client, snipe):
        snipe.side_effect = ConnectionError("host unreachable")
        resp = admin_client.get("/assets")
        assert resp.status_code == 200 and b"Failed to load assets: host unreachable" in resp.content
        snipe.side_effect = None  # errors are not cached: the next load succeeds
        assert len(admin_client.get("/assets").context["results"].rows) == 3

    def test_list_filters_and_cache(self, admin_client, snipe):
        resp = admin_client.get("/assets")
        snipe.assert_called_once_with(["Phones", "Modems"])
        assert resp.context["results"].total == 3 and b"Import as DUT" in resp.content
        tiles = resp.context["results"].tiles
        assert [(t.label, t.value) for t in tiles] == [("Total Assets", "3"), ("Ready", "2"), ("Deployed", "1")]
        assert tiles[0].sub == "2 categories tracked"
        assert sorted(names(admin_client.get("/assets?seg=Phones"))) == ["Phone A", "Phone B"]
        assert names(admin_client.get("/assets?q=sn3")) == ["Modem C"]
        assert names(admin_client.get("/assets?sort=serial&dir=desc"))[0] == "Modem C"
        assert snipe.call_count == 1  # filtering reuses the cached rows

    def test_refresh_drops_cache_and_reloads(self, admin_client, snipe):
        admin_client.get("/assets")
        resp = admin_client.post("/assets/refresh", headers=HX)
        assert resp.status_code == 204 and resp["HX-Refresh"] == "true"
        admin_client.get("/assets")
        assert snipe.call_count == 2
        assert admin_client.get("/assets/refresh").status_code == 405

    def test_import_creates_duts_and_skips_duplicates(self, admin_client, snipe):
        admin_client.get("/assets")
        DutType.objects.create(name="phone b")
        resp = admin_client.post("/assets/import", {"ids": ["1", "2", "3"]}, headers=HX)
        assert resp.status_code == 204
        events = json.loads(resp["HX-Trigger"])
        assert events["assets-imported"]
        assert events["toast"] == {
            "message": "Successfully imported 2 asset(s) as DUT entries.\nSkipped 1 duplicate(s): Phone B",
            "level": "warning"}
        d = DutType.objects.get(name="Modem C")
        assert (d.category, d.complexity_multiplier) == ("Modems", 1.0)

    def test_import_needs_selection(self, admin_client, snipe):
        resp = admin_client.post("/assets/import", {}, headers=HX)
        assert resp.status_code == 400 and resp.json()["detail"] == "No assets selected."
        resp = admin_client.post("/assets/import", {"ids": ["999"]}, headers=HX)
        assert resp.status_code == 400

    @pytest.mark.parametrize("role", ["VIEWER", "ESTIMATOR"])
    def test_import_requires_approver(self, web_login, make_user, seeded, snipe, role):
        client = web_login(make_user(role))
        resp = client.get("/assets")
        assert resp.status_code == 200 and b"Import as DUT" not in resp.content
        assert b"data-row-check value=" not in resp.content
        assert client.post("/assets/import", {"ids": ["1"]}).status_code == 403
        assert not DutType.objects.filter(name="Phone A").exists()


# ── PR registry ──────────────────────────────────────────────────────────────

ISSUES = {"issues": [
    {"key": "PR-1", "fields": {"summary": "Crash on boot", "priority": {"name": "Critical"},
                               "status": {"name": "Open"}, "issuetype": {"name": "Bug"},
                               "created": "2026-01-02T10:00:00.000+0000",
                               "description": "Line one\n{color:#ff0000}red{color} <b>x</b>"}},
    {"key": "PR-2", "fields": {"summary": "Slow menu", "priority": {"name": "Low"},
                               "status": {"name": "Done"}, "issuetype": {"name": "Bug"},
                               "created": "2026-02-03T10:00:00.000+0000", "description": {"type": "doc"}}},
    {"key": "PR-3", "fields": {"summary": "Typo", "priority": {"name": "High"},
                               "status": {"name": "In Progress"}, "issuetype": {"name": "Defect"},
                               "created": "2026-03-04T10:00:00.000+0000"}},
]}


def _resp(status=200, payload=None, text=""):
    r = mock.Mock(status_code=status, text=text)
    r.json.return_value = payload if payload is not None else {}
    return r


@pytest.fixture
def jira(db):
    IntegrationConfig.objects.update_or_create(system_name="JIRA", defaults={
        "enabled": True, "base_url": "https://jira.invalid", "api_key": "k",
        "additional_config": {"pr_jql_filter": "project = PR"}})
    with mock.patch.object(JiraAdapter, "_pr_request", return_value=_resp(payload=ISSUES)) as m:
        yield m


class TestPrRegistry:
    def test_not_configured(self, admin_client):
        IntegrationConfig.objects.filter(system_name="JIRA").delete()
        resp = admin_client.get("/pr-registry")
        assert resp.status_code == 200 and b"Jira integration is not configured or not enabled." in resp.content
        assert b'id="crud-search"' not in resp.content and b"Refresh" not in resp.content

    def test_empty_until_refresh(self, admin_client, jira):
        resp = admin_client.get("/pr-registry")
        assert resp.status_code == 200 and resp.context["results"].total == 0
        assert resp.context["results"].tiles[0].sub == "click Refresh to load"
        jira.assert_not_called()

    def test_refresh_loads_and_lists(self, admin_client, jira):
        resp = admin_client.post("/pr-registry/refresh", headers=HX)
        assert resp.status_code == 204 and resp["HX-Refresh"] == "true"
        assert jira.call_args.kwargs["params"]["jql"] == "project = PR"
        page = admin_client.get("/pr-registry")
        assert b"Loaded 3 PR item(s)." in page.content  # flash message shown as a toast after reload
        results = page.context["results"]
        assert results.total == 3
        assert [(t.label, t.value) for t in results.tiles] == [
            ("Total PRs", "3"), ("High / Critical", "2"), ("Low", "1")]
        assert results.tiles[0].sub == "2 open"
        assert b'hx-get="/pr-registry/item/PR-1"' in page.content
        assert b'tone-negative">Critical' in page.content and b"2026-01-02<" in page.content
        keys = [str(r["key"]) for r in admin_client.get("/pr-registry?seg=high").context["results"].rows]
        assert sorted(keys) == ["PR-1", "PR-3"]
        keys = [str(r["key"]) for r in admin_client.get("/pr-registry?q=slow").context["results"].rows]
        assert keys == ["PR-2"]

    def test_refresh_errors_show_detail(self, admin_client, jira):
        jira.return_value = _resp(400, {"errorMessages": ["Bad JQL"]}, "x")
        resp = admin_client.post("/pr-registry/refresh", headers=HX)
        assert resp.status_code == 400
        assert resp.json()["detail"] == "Failed to load PR items: Jira returned HTTP 400: Bad JQL"
        jira.side_effect = OSError("timeout")
        resp = admin_client.post("/pr-registry/refresh", headers=HX)
        assert resp.status_code == 500 and "timeout" in resp.json()["detail"]
        IntegrationConfig.objects.filter(system_name="JIRA").update(additional_config={})
        resp = admin_client.post("/pr-registry/refresh", headers=HX)
        assert resp.json()["detail"] == "Failed to load PR items: No PR JQL filter provided."

    def test_detail_dialog_escapes_description(self, web_login, make_user, seeded, jira):
        client = web_login(make_user("VIEWER"))  # read-only page: any signed-in user
        assert client.post("/pr-registry/refresh", headers=HX).status_code == 204
        resp = client.get("/pr-registry/item/PR-1", headers=HX)
        assert resp.status_code == 200
        body = resp.content
        assert b"Crash on boot" in body and b"https://jira.invalid/browse/PR-1" in body
        assert b'<span style="color:#ff0000">red</span>' in body and b"&lt;b&gt;x&lt;/b&gt;" in body
        assert b"Description \xc2\xb7 2 lines" in body
        assert b"No description provided." in client.get("/pr-registry/item/PR-2").content
        missing = client.get("/pr-registry/item/NOPE-1")
        assert missing.status_code == 404 and "Refresh" in missing.json()["detail"]
