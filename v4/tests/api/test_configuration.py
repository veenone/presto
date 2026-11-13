"""Configuration, branding, category lists and logo upload."""

from __future__ import annotations

import pytest

from apps.core import config


@pytest.fixture
def uploads(settings, tmp_path):
    settings.UPLOADS_DIR = tmp_path / "uploads"
    return settings.UPLOADS_DIR


# ── Ported from v3 TestConfigurationEndpoints ────────────


class TestConfigurationEndpoints:
    def test_list_config(self, api):
        resp = api.get("/api/configuration")
        assert resp.status_code == 200
        keys = [c["key"] for c in resp.json()]
        assert "leader_effort_ratio" in keys

    def test_update_config(self, api):
        resp = api.put("/api/configuration/leader_effort_ratio", json={"value": "0.6"})
        assert resp.status_code == 200
        assert resp.json()["value"] == "0.6"


class TestConfiguration:
    def test_upsert_new_key(self, api):
        resp = api.put("/api/configuration/brand_new_key", json={"value": "x"})
        assert resp.status_code == 200
        assert resp.json() == {"key": "brand_new_key", "value": "x", "description": ""}

    def test_update_requires_admin(self, api_as, make_user):
        resp = api_as(make_user("APPROVER")).put(
            "/api/configuration/leader_effort_ratio", json={"value": "1"}
        )
        assert resp.status_code == 403

    def test_list_requires_auth(self, anon_api):
        assert anon_api.get("/api/configuration").status_code == 401

    @pytest.mark.parametrize("key", ["product_types", "team_skills", "branding"])
    def test_put_on_literal_get_paths(self, api, anon_api, key):
        # These paths also serve a public GET; PUT must still behave like PUT /configuration/{key}.
        resp = api.put(f"/api/configuration/{key}", json={"value": '["A", "B"]'})
        assert resp.status_code == 200
        assert resp.json()["key"] == key
        assert anon_api.put(f"/api/configuration/{key}", json={"value": "x"}).status_code == 401

    def test_product_types_and_team_skills_public(self, anon_api, api):
        api.put("/api/configuration/product_types", json={"value": '["Payment", "Telco", "IoT"]'})
        assert anon_api.get("/api/configuration/product_types").json() == ["Payment", "Telco", "IoT"]
        api.put("/api/configuration/team_skills", json={"value": "not json"})
        resp = anon_api.get("/api/configuration/team_skills")
        assert resp.status_code == 200 and resp.json() == []

    def test_branding_public(self, anon_api, api):
        api.put("/api/configuration/logo_url", json={"value": "/api/static/uploads/logo.png"})
        resp = anon_api.get("/api/configuration/branding")
        assert resp.status_code == 200
        assert resp.json()["logo_url"] == "/api/static/uploads/logo.png"
        assert "logo_height" in resp.json()

    def test_category_lists(self, api):
        api.put("/api/configuration/dut_categories", json={"value": "SIM, eSIM ,,IoT"})
        assert api.get("/api/dut-categories").json() == ["SIM", "eSIM", "IoT"]
        api.put("/api/configuration/feature_categories", json={"value": "A,B"})
        assert api.get("/api/feature-categories").json() == ["A", "B"]
        api.put("/api/configuration/project_types", json={"value": "NEW"})
        assert api.get("/api/project-types").json() == ["NEW"]

    def test_category_lists_require_auth(self, anon_api):
        assert anon_api.get("/api/dut-categories").status_code == 401

    def test_unknown_get_key_405(self, api):
        assert api.get("/api/configuration/leader_effort_ratio").status_code == 405


class TestLogoUpload:
    def test_upload(self, api, uploads):
        (uploads).mkdir(parents=True)
        (uploads / "logo.jpg").write_bytes(b"old")
        resp = api.post(
            "/api/configuration/logo/upload", files={"file": ("brand.PNG", b"\x89PNGdata", "image/png")}
        )
        assert resp.status_code == 200, resp.content
        data = resp.json()
        assert data["status"] == "ok" and data["size"] == 8
        assert data["logo_url"].startswith("/api/static/uploads/logo.png?v=")
        assert config.get_str("logo_url") == data["logo_url"]
        assert (uploads / "logo.png").read_bytes() == b"\x89PNGdata"
        assert not (uploads / "logo.jpg").exists()

    def test_invalid_extension(self, api, uploads):
        resp = api.post(
            "/api/configuration/logo/upload", files={"file": ("evil.exe", b"x", "application/octet-stream")}
        )
        assert resp.status_code == 400
        assert resp.json()["detail"].startswith("Invalid file type '.exe'")

    def test_admin_only(self, api_as, make_user, uploads):
        resp = api_as(make_user("APPROVER")).post(
            "/api/configuration/logo/upload", files={"file": ("a.png", b"x", "image/png")}
        )
        assert resp.status_code == 403
