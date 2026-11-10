"""Cross-page checks for the Phase 4 pages."""

import pytest

from apps.web.navigation import ITEMS_BY_NAME

pytestmark = pytest.mark.django_db

PHASE4 = ["features", "feature_presets", "tasks", "duts", "assets", "profiles", "history", "team", "risks",
          "pr_registry", "document_types", "public_holidays", "settings", "users", "rbac", "audit", "backup"]


@pytest.mark.parametrize("name", PHASE4)
def test_page_is_built_not_placeholder(admin_client, name):
    item = ITEMS_BY_NAME[name]
    resp = admin_client.get(item.path)
    assert resp.status_code == 200, name
    assert b"This page is being rebuilt" not in resp.content, name
    assert item.phase == 0, name


def test_only_phase6_pages_remain_placeholders(admin_client):
    for name in ("requests", "integrations"):
        assert b"This page is being rebuilt" in admin_client.get(ITEMS_BY_NAME[name].path).content, name
    for name in ("estimations", "estimation_new"):
        resp = admin_client.get(ITEMS_BY_NAME[name].path)
        assert resp.status_code == 200 and b"This page is being rebuilt" not in resp.content, name


def test_page_stylesheets_loaded_on_every_page(admin_client):
    """Boosted sidebar navigation swaps only <body>, so page CSS must be in the base <head>."""
    body = admin_client.get("/").content.decode()
    for css in ("features", "registries", "planning", "admin_people", "admin_system"):
        assert f"/static/css/pages/{css}.css" in body, css
