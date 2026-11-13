"""RBAC page (/rbac): matrix display, save, reset to defaults, effect on navigation."""

import json

import pytest

from apps.accounts.permissions import DEFAULT_MATRIX, PERMISSION_KEYS, get_matrix
from apps.core import config

pytestmark = pytest.mark.django_db
HX = {"HX-Request": "true"}


def nav_labels(client) -> set[str]:
    return {item["label"] for s in client.get("/").context["navigation"] for item in s["items"]}


def matrix_post(matrix: dict) -> dict:
    return {f"perm_{role}": perms for role, perms in matrix.items()}


class TestAccess:
    @pytest.mark.parametrize("role", ["VIEWER", "ESTIMATOR", "APPROVER"])
    def test_non_admin_403(self, web_login, make_user, seeded, role):
        client = web_login(make_user(role))
        assert client.get("/rbac").status_code == 403
        assert client.post("/rbac", {"perm_VIEWER": PERMISSION_KEYS}).status_code == 403
        assert client.get("/rbac/reset").status_code == 403
        assert client.post("/rbac/reset", {"confirm": "1"}).status_code == 403
        assert get_matrix() == {r: list(p) for r, p in DEFAULT_MATRIX.items()}


class TestMatrix:
    def test_renders_18_by_4_with_descriptions_and_locked_admin(self, admin_client):
        resp = admin_client.get("/rbac")
        body = resp.content.decode()
        assert resp.status_code == 200 and "RBAC Management" in body
        assert len(resp.context["rows"]) == 18 and resp.context["roles"] == ["VIEWER", "ESTIMATOR", "APPROVER", "ADMIN"]
        assert 'title="Create, edit and delete user accounts."' in body
        assert body.count('name="perm_ADMIN"') == 0 and body.count("(always granted)") == 18
        assert body.count('name="perm_VIEWER"') == 18
        counts = {c["role"]: c["count"] for c in resp.context["counts"]}
        assert counts == {"VIEWER": 2, "ESTIMATOR": 9, "APPROVER": 12, "ADMIN": 18}

    def test_save_stores_v3_json_and_rerenders(self, admin_client):
        new = {"VIEWER": ["view_estimations", "view_requests"], "ESTIMATOR": ["view_estimations"],
               "APPROVER": ["view_audit_log", "bogus"]}
        resp = admin_client.post("/rbac", matrix_post(new), headers=HX)
        assert resp.status_code == 200 and b'id="rbac-body"' in resp.content and b"ed-side-nav" not in resp.content
        assert json.loads(resp["HX-Trigger"])["toast"]["message"] == "RBAC matrix saved successfully."
        stored = config.get_json("rbac_matrix")
        assert stored["VIEWER"] == ["view_estimations", "view_requests"]
        assert stored["APPROVER"] == ["view_audit_log"] and stored["ADMIN"] == PERMISSION_KEYS

    def test_plain_post_redirects(self, admin_client):
        resp = admin_client.post("/rbac", {"perm_VIEWER": ["view_estimations"]})
        assert resp.status_code == 302 and resp["Location"] == "/rbac"
        assert get_matrix()["ESTIMATOR"] == []

    def test_save_drives_navigation_immediately(self, admin_client, web_login, make_user, client):
        est = make_user("ESTIMATOR", username="est")
        matrix = {r: list(p) for r, p in DEFAULT_MATRIX.items()}
        matrix["ESTIMATOR"].remove("manage_presets")
        matrix["VIEWER"].append("view_requests")
        admin_client.post("/rbac", matrix_post(matrix), headers=HX)
        labels = nav_labels(web_login(est))
        assert "Feature Presets" not in labels and "Request Inbox" in labels
        assert web_login(est).get("/feature-presets").status_code == 403
        assert "Request Inbox" in nav_labels(web_login(make_user("VIEWER")))

    def test_reset_dialog_needs_ack_then_restores_defaults(self, admin_client):
        admin_client.post("/rbac", {"perm_VIEWER": PERMISSION_KEYS})
        assert len(get_matrix()["VIEWER"]) == 18
        assert b"I understand this overwrites" in admin_client.get("/rbac/reset", headers=HX).content
        assert admin_client.post("/rbac/reset", headers=HX).status_code == 422
        resp = admin_client.post("/rbac/reset", {"confirm": "1"}, headers=HX)
        ev = json.loads(resp["HX-Trigger"])
        assert resp.status_code == 204 and ev["rbac-refresh"] and ev["close-modal"]
        assert ev["toast"]["message"] == "RBAC matrix reset to defaults."
        assert get_matrix() == {r: list(p) for r, p in DEFAULT_MATRIX.items()}

    def test_reload_partial(self, admin_client):
        resp = admin_client.get("/rbac", headers={"HX-Request": "true", "HX-Target": "rbac-body"})
        assert b'id="rbac-body"' in resp.content and b"ed-side-nav" not in resp.content
