"""Catalog endpoints: features, presets, task templates, DUTs, profiles, registries."""

from __future__ import annotations

from apps.catalog.models import DutType, Feature, TaskTemplate, TaskTemplateFeature
from apps.estimations.models import Estimation


def _first_feature_id(api) -> int:
    return api.get("/api/features").json()[0]["id"]


def _project_feature(owner, name="Proj F", estimation=None) -> Feature:
    estimation = estimation or Estimation.objects.create(project_name="P", project_type="NEW", creator=owner)
    return Feature.objects.create(name=name, category="Test", is_global=False, owner_estimation=estimation)


# ── Ported from v3 TestFeatureEndpoints ──────────────────


class TestFeatureEndpoints:
    def test_list_features(self, api):
        resp = api.get("/api/features")
        assert resp.status_code == 200
        assert len(resp.json()) >= 5  # seed data

    def test_create_feature(self, api):
        resp = api.post(
            "/api/features",
            json={
                "name": "API Test Feature",
                "category": "Test",
                "complexity_weight": 1.5,
                "has_existing_tests": False,
            },
        )
        assert resp.status_code == 201
        assert resp.json()["name"] == "API Test Feature"
        assert resp.json()["is_global"] is True

    def test_get_feature(self, api):
        fid = _first_feature_id(api)
        resp = api.get(f"/api/features/{fid}")
        assert resp.status_code == 200
        assert resp.json()["id"] == fid

    def test_update_feature(self, api):
        fid = _first_feature_id(api)
        resp = api.put(f"/api/features/{fid}", json={"complexity_weight": 2.0})
        assert resp.status_code == 200
        assert resp.json()["complexity_weight"] == 2.0

    def test_delete_feature(self, api):
        fid = api.post("/api/features", json={"name": "To Delete", "category": "Test"}).json()["id"]
        assert api.delete(f"/api/features/{fid}").status_code == 204
        assert api.get(f"/api/features/{fid}").status_code == 404

    def test_feature_not_found(self, api):
        resp = api.get("/api/features/9999")
        assert resp.status_code == 404
        assert resp.json() == {"detail": "Feature not found"}


class TestFeatureRules:
    def test_unauthenticated(self, anon_api):
        assert anon_api.get("/api/features").status_code == 401

    def test_create_duplicate_global_409(self, api):
        api.post("/api/features", json={"name": "Dup", "category": "X"})
        resp = api.post("/api/features", json={"name": "Dup", "category": "X"})
        assert resp.status_code == 409
        assert "already exists in category 'X'" in resp.json()["detail"]

    def test_create_requires_approver(self, api_as, make_user):
        resp = api_as(make_user("ESTIMATOR")).post("/api/features", json={"name": "N"})
        assert resp.status_code == 403

    def test_update_rename_clash_409(self, api):
        a = api.post("/api/features", json={"name": "A1", "category": "C"}).json()["id"]
        api.post("/api/features", json={"name": "B1", "category": "C"})
        resp = api.put(f"/api/features/{a}", json={"name": "B1"})
        assert resp.status_code == 409
        assert resp.json()["detail"].startswith("Feature 'B1' already exists in category 'C'")

    def test_estimator_cannot_edit_or_delete_global(self, api, api_as, make_user):
        fid = _first_feature_id(api)
        est = api_as(make_user("ESTIMATOR"))
        assert est.put(f"/api/features/{fid}", json={"name": "x"}).json()["detail"] == (
            "Editing a global feature requires APPROVER role"
        )
        assert est.delete(f"/api/features/{fid}").status_code == 403

    def test_project_feature_author_rules(self, api_as, make_user):
        author, other = make_user("ESTIMATOR"), make_user("ESTIMATOR")
        f = _project_feature(author)
        resp = api_as(other).put(f"/api/features/{f.id}", json={"description": "d"})
        assert resp.status_code == 403
        assert api_as(author).put(f"/api/features/{f.id}", json={"description": "d"}).status_code == 200
        assert api_as(other).delete(f"/api/features/{f.id}").status_code == 403
        assert api_as(author).delete(f"/api/features/{f.id}").status_code == 204

    def test_list_includes_project_features_on_request(self, api, admin_user):
        f = _project_feature(admin_user)
        ids = {x["id"] for x in api.get("/api/features").json()}
        assert f.id not in ids
        ids = {
            x["id"] for x in api.get("/api/features", params={"estimation_id": f.owner_estimation_id}).json()
        }
        assert f.id in ids
        ids = {
            x["id"]
            for x in api.get(
                "/api/features", params={"estimation_id": f.owner_estimation_id, "include_project": "false"}
            ).json()
        }
        assert f.id not in ids

    def test_product_type_filter(self, api):
        api.post("/api/features", json={"name": "Pay only", "product_type": "Payment"})
        api.post("/api/features", json={"name": "Telco only", "product_type": "Telco"})
        names = {f["name"] for f in api.get("/api/features", params={"product_type": "Payment"}).json()}
        assert "Pay only" in names and "Telco only" not in names

    def test_feature_out_lists_linked_templates(self, api):
        fid = _first_feature_id(api)
        tmpl = api.post(
            "/api/task-templates",
            json={
                "name": "Linked",
                "task_type": "EXECUTION",
                "base_effort_hours": 2,
                "feature_ids": [fid],
                "feature_hours": {str(fid): 5.0},
            },
        ).json()
        data = api.get(f"/api/features/{fid}").json()
        nested = [t for t in data["task_templates"] if t["id"] == tmpl["id"]]
        assert nested and nested[0]["feature_ids"] == [fid] and nested[0]["feature_hours"] == {}


class TestFeaturePromotion:
    def test_promote(self, api, admin_user):
        f = _project_feature(admin_user)
        resp = api.post(f"/api/features/{f.id}/promote")
        assert resp.status_code == 200
        assert resp.json()["is_global"] is True
        assert resp.json()["owner_estimation_id"] is None

    def test_promote_already_global(self, api):
        resp = api.post(f"/api/features/{_first_feature_id(api)}/promote")
        assert resp.status_code == 400
        assert resp.json()["detail"] == "Feature is already global"

    def test_promote_clash(self, api, admin_user):
        api.post("/api/features", json={"name": "Same", "category": "Test"})
        f = _project_feature(admin_user, name="Same")
        assert api.post(f"/api/features/{f.id}/promote").status_code == 409

    def test_promote_requires_approver(self, api_as, make_user):
        user = make_user("ESTIMATOR")
        f = _project_feature(user)
        assert api_as(user).post(f"/api/features/{f.id}/promote").status_code == 403

    def test_request_promotion(self, api_as, make_user, monkeypatch):
        calls = []

        class FakeService:
            def notify_feature_promotion_requested(self, **kw):
                calls.append(kw)
                raise RuntimeError("smtp down")  # must not break the request

        import apps.notifications.service as notif

        monkeypatch.setattr(notif, "NotificationService", FakeService, raising=False)
        user = make_user("ESTIMATOR", display_name="Est One")
        f = _project_feature(user)
        resp = api_as(user).post(f"/api/features/{f.id}/request-promotion")
        assert resp.status_code == 200
        assert resp.json()["promotion_requested"] is True
        assert calls == [{"feature_name": f.name, "category": "Test", "requested_by": "Est One"}]

    def test_request_promotion_other_user_forbidden(self, api_as, make_user):
        f = _project_feature(make_user("ESTIMATOR"))
        resp = api_as(make_user("ESTIMATOR")).post(f"/api/features/{f.id}/request-promotion")
        assert resp.status_code == 403
        assert resp.json()["detail"] == "Only the project's author or an ADMIN can request promotion"

    def test_request_promotion_missing(self, api):
        assert api.post("/api/features/9999/request-promotion").status_code == 404


# ── Ported from v3 TestFeaturePresets ────────────────────


class TestFeaturePresets:
    def _feature_ids(self, api):
        return [f["id"] for f in api.get("/api/features").json()[:3]]

    def test_create_and_list_preset(self, api, admin_user):
        fids = self._feature_ids(api)
        resp = api.post(
            "/api/feature-presets", json={"name": "Smoke set", "product_type": "SIM", "feature_ids": fids}
        )
        assert resp.status_code == 201, resp.content
        data = resp.json()
        assert data["name"] == "Smoke set"
        assert sorted(data["feature_ids"]) == sorted(fids)
        assert data["owner_user_id"] == admin_user.id
        assert data["owner_name"] == admin_user.display_name
        assert any(p["name"] == "Smoke set" for p in api.get("/api/feature-presets").json())

    def test_preset_requires_features(self, api):
        resp = api.post("/api/feature-presets", json={"name": "Empty", "feature_ids": []})
        assert resp.status_code == 400
        assert resp.json()["detail"] == "A preset must contain at least one feature."

    def test_preset_requires_name(self, api):
        resp = api.post("/api/feature-presets", json={"name": "  ", "feature_ids": [1]})
        assert resp.json()["detail"] == "Preset name is required."

    def test_product_type_filter(self, api):
        fids = self._feature_ids(api)
        api.post(
            "/api/feature-presets", json={"name": "SIM only", "product_type": "SIM", "feature_ids": fids}
        )
        api.post("/api/feature-presets", json={"name": "Any type", "product_type": None, "feature_ids": fids})
        names = {p["name"] for p in api.get("/api/feature-presets", params={"product_type": "eSIM"}).json()}
        assert "Any type" in names
        assert "SIM only" not in names

    def test_delete_preset(self, api):
        pid = api.post(
            "/api/feature-presets", json={"name": "Temp", "feature_ids": self._feature_ids(api)}
        ).json()["id"]
        assert api.delete(f"/api/feature-presets/{pid}").status_code == 204
        assert all(p["id"] != pid for p in api.get("/api/feature-presets").json())
        assert api.delete(f"/api/feature-presets/{pid}").status_code == 404

    def test_update_preset_and_description(self, api):
        fids = self._feature_ids(api)
        pid = api.post(
            "/api/feature-presets",
            json={"name": "Editable", "description": "first draft", "feature_ids": fids},
        ).json()["id"]
        resp = api.put(
            f"/api/feature-presets/{pid}",
            json={"name": "Renamed", "description": "final", "feature_ids": fids[:1]},
        )
        assert resp.status_code == 200, resp.content
        data = resp.json()
        assert data["name"] == "Renamed"
        assert data["description"] == "final"
        assert data["feature_ids"] == sorted(fids[:1])

    def test_only_owner_or_admin_can_edit(self, api_as, make_user):
        owner, other = make_user("ESTIMATOR"), make_user("ESTIMATOR")
        pid = (
            api_as(owner).post("/api/feature-presets", json={"name": "Mine", "feature_ids": [1]}).json()["id"]
        )
        resp = api_as(other).put(f"/api/feature-presets/{pid}", json={"name": "Hijack"})
        assert resp.status_code == 403
        assert resp.json()["detail"] == "Only the preset's owner or an ADMIN can edit it."
        assert api_as(other).delete(f"/api/feature-presets/{pid}").status_code == 403

    def test_viewer_cannot_create(self, api_as, make_user):
        resp = api_as(make_user("VIEWER")).post(
            "/api/feature-presets", json={"name": "V", "feature_ids": [1]}
        )
        assert resp.status_code == 403


# ── Task templates ───────────────────────────────────────


class TestTaskTemplates:
    def test_list(self, api):
        resp = api.get("/api/task-templates")
        assert resp.status_code == 200
        assert len(resp.json()) >= 1

    def test_create_with_features_and_hours(self, api):
        fids = [f["id"] for f in api.get("/api/features").json()[:2]]
        resp = api.post(
            "/api/task-templates",
            json={
                "name": "Multi",
                "task_type": "EXECUTION",
                "base_effort_hours": 3,
                "feature_ids": fids,
                "feature_hours": {str(fids[0]): 7.5},
            },
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["feature_ids"] == fids
        assert data["feature_id"] is None
        assert data["feature_hours"] == {str(fids[0]): 7.5}
        listed = api.get("/api/task-templates", params={"feature_id": fids[1]}).json()
        assert [t["id"] for t in listed] == [data["id"]]

    def test_create_legacy_single_feature(self, api):
        fid = _first_feature_id(api)
        data = api.post(
            "/api/task-templates",
            json={"name": "Single", "task_type": "SETUP", "base_effort_hours": 1, "feature_id": fid},
        ).json()
        assert data["feature_id"] == fid and data["feature_ids"] == [fid]

    def test_create_unknown_feature_400(self, api):
        resp = api.post(
            "/api/task-templates",
            json={"name": "Bad", "task_type": "SETUP", "base_effort_hours": 1, "feature_ids": [99999]},
        )
        assert resp.status_code == 400
        assert not TaskTemplate.objects.filter(name="Bad").exists()

    def test_update_and_relink(self, api):
        fid = _first_feature_id(api)
        tid = api.post(
            "/api/task-templates", json={"name": "T", "task_type": "SETUP", "base_effort_hours": 1}
        ).json()["id"]
        resp = api.put(f"/api/task-templates/{tid}", json={"base_effort_hours": 4, "feature_ids": [fid]})
        assert resp.status_code == 200
        assert resp.json()["base_effort_hours"] == 4
        assert resp.json()["feature_id"] == fid
        assert TaskTemplateFeature.objects.filter(task_template_id=tid).count() == 1

    def test_update_delete_404(self, api):
        assert (
            api.put("/api/task-templates/9999", json={"name": "x"}).json()["detail"]
            == "Task template not found"
        )
        assert api.delete("/api/task-templates/9999").status_code == 404

    def test_delete(self, api):
        tid = api.post(
            "/api/task-templates", json={"name": "Del", "task_type": "SETUP", "base_effort_hours": 1}
        ).json()["id"]
        assert api.delete(f"/api/task-templates/{tid}").status_code == 204

    def test_requires_approver(self, api_as, make_user):
        resp = api_as(make_user("ESTIMATOR")).post(
            "/api/task-templates", json={"name": "X", "task_type": "SETUP", "base_effort_hours": 1}
        )
        assert resp.status_code == 403


# ── Ported from v3 TestDutTypeEndpoints / TestProfileEndpoints ──


class TestDutTypeEndpoints:
    def test_list_dut_types(self, api):
        resp = api.get("/api/dut-types")
        assert resp.status_code == 200
        assert len(resp.json()) >= 3

    def test_create_dut_type(self, api):
        resp = api.post(
            "/api/dut-types", json={"name": "Test DUT", "category": "Test", "complexity_multiplier": 1.3}
        )
        assert resp.status_code == 201

    def test_create_duplicate_409(self, api):
        api.post("/api/dut-types", json={"name": "Dup DUT"})
        assert api.post("/api/dut-types", json={"name": "Dup DUT"}).status_code == 409

    def test_update_rename_clash(self, api):
        a = api.post("/api/dut-types", json={"name": "D1"}).json()["id"]
        api.post("/api/dut-types", json={"name": "D2"})
        resp = api.put(f"/api/dut-types/{a}", json={"name": "D2"})
        assert resp.status_code == 409
        assert resp.json()["detail"] == "DUT type with name 'D2' already exists"
        assert (
            api.put(f"/api/dut-types/{a}", json={"complexity_multiplier": 2}).json()["complexity_multiplier"]
            == 2
        )

    def test_delete_and_404(self, api):
        did = api.post("/api/dut-types", json={"name": "Gone"}).json()["id"]
        assert api.delete(f"/api/dut-types/{did}").status_code == 204
        assert api.delete(f"/api/dut-types/{did}").json()["detail"] == "DUT type not found"

    def test_reinit(self, api, api_as, make_user):
        assert api_as(make_user("APPROVER")).post("/api/dut-types/reinit").status_code == 403
        resp = api.post("/api/dut-types/reinit")
        assert resp.status_code == 200
        assert resp.json() == {"status": "ok", "message": "DUT registry cleared, ID counter reset to 1"}
        assert not DutType.objects.exists()

    def test_product_type_filter(self, api):
        api.post("/api/dut-types", json={"name": "PayDUT", "product_type": "Payment"})
        names = {d["name"] for d in api.get("/api/dut-types", params={"product_type": "Telco"}).json()}
        assert "PayDUT" not in names


class TestProfileEndpoints:
    def test_list_profiles(self, api):
        resp = api.get("/api/profiles")
        assert resp.status_code == 200
        assert len(resp.json()) >= 3

    def test_crud(self, api):
        resp = api.post("/api/profiles", json={"name": "Deep", "effort_multiplier": 1.4})
        assert resp.status_code == 201
        pid = resp.json()["id"]
        assert api.put(f"/api/profiles/{pid}", json={"is_active": False}).json()["is_active"] is False
        assert api.delete(f"/api/profiles/{pid}").status_code == 204
        assert api.put(f"/api/profiles/{pid}", json={}).json()["detail"] == "Profile not found"

    def test_duplicate_409(self, api):
        api.post("/api/profiles", json={"name": "Twin"})
        assert api.post("/api/profiles", json={"name": "Twin"}).status_code == 409

    def test_viewer_forbidden(self, api_as, make_user):
        assert api_as(make_user("VIEWER")).post("/api/profiles", json={"name": "V"}).status_code == 403


# ── Historical projects ──────────────────────────────────


class TestHistoricalProjects:
    def test_crud(self, api):
        resp = api.post(
            "/api/historical-projects",
            json={
                "project_name": "Old",
                "project_type": "NEW",
                "estimated_hours": 100,
                "actual_hours": 120,
                "features_json": "[1, 2]",
                "completion_date": "2025-06-30",
            },
        )
        assert resp.status_code == 201, resp.content
        data = resp.json()
        assert data["features_json"] == "[1, 2]"
        assert data["completion_date"] == "2025-06-30"
        pid = data["id"]
        assert len(api.get("/api/historical-projects").json()) == 1
        resp = api.put(
            f"/api/historical-projects/{pid}",
            json={"project_name": "Old2", "project_type": "NEW", "actual_hours": 130},
        )
        assert resp.json()["actual_hours"] == 130
        assert resp.json()["features_json"] == "[1, 2]"
        assert api.delete(f"/api/historical-projects/{pid}").status_code == 204
        assert (
            api.delete(f"/api/historical-projects/{pid}").json()["detail"] == "Historical project not found"
        )

    def test_requires_approver(self, api_as, make_user):
        resp = api_as(make_user("ESTIMATOR")).post(
            "/api/historical-projects", json={"project_name": "X", "project_type": "NEW"}
        )
        assert resp.status_code == 403


# ── Task presets ─────────────────────────────────────────


class TestTaskPresets:
    def test_crud(self, api):
        resp = api.post(
            "/api/task-presets", json={"name": "Basic", "product_type": "Telco", "task_template_ids": [1, 2]}
        )
        assert resp.status_code == 201
        pid = resp.json()["id"]
        assert resp.json()["task_template_ids"] == [1, 2]
        assert [p["id"] for p in api.get("/api/task-presets", params={"product_type": "Telco"}).json()] == [
            pid
        ]
        assert api.get("/api/task-presets", params={"product_type": "Payment"}).json() == []
        resp = api.put(f"/api/task-presets/{pid}", json={"task_template_ids": [3], "description": "d"})
        assert resp.json()["task_template_ids"] == [3]
        assert resp.json()["name"] == "Basic"
        assert api.delete(f"/api/task-presets/{pid}").status_code == 204
        assert api.delete(f"/api/task-presets/{pid}").json()["detail"] == "Task preset not found"

    def test_duplicate_400(self, api):
        api.post("/api/task-presets", json={"name": "Same"})
        resp = api.post("/api/task-presets", json={"name": "Same"})
        assert resp.status_code == 400
        assert resp.json()["detail"] == "Preset 'Same' already exists"

    def test_viewer_forbidden(self, api_as, make_user):
        assert api_as(make_user("VIEWER")).post("/api/task-presets", json={"name": "V"}).status_code == 403


# ── Risk items ───────────────────────────────────────────


class TestRiskItems:
    def test_crud_and_active_filter(self, api):
        resp = api.post("/api/risk-items", json={"name": "Late HW", "likelihood": "HIGH"})
        assert resp.status_code == 201
        rid = resp.json()["id"]
        assert resp.json()["category"] == "General"
        assert rid in {r["id"] for r in api.get("/api/risk-items").json()}
        api.put(f"/api/risk-items/{rid}", json={"is_active": False})
        assert rid not in {r["id"] for r in api.get("/api/risk-items").json()}
        assert api.delete(f"/api/risk-items/{rid}").status_code == 204
        assert api.put(f"/api/risk-items/{rid}", json={}).json()["detail"] == "Risk item not found"

    def test_duplicate_400(self, api):
        api.post("/api/risk-items", json={"name": "R"})
        assert (
            api.post("/api/risk-items", json={"name": "R"}).json()["detail"] == "Risk item 'R' already exists"
        )

    def test_delete_requires_approver(self, api, api_as, make_user):
        rid = api.post("/api/risk-items", json={"name": "R2"}).json()["id"]
        assert api_as(make_user("ESTIMATOR")).delete(f"/api/risk-items/{rid}").status_code == 403


# ── Document types ───────────────────────────────────────


class TestDocumentTypes:
    def test_crud_and_all(self, api):
        tmpl = TaskTemplate.objects.order_by("id").first()
        resp = api.post("/api/document-types", json={"name": "Test Plan", "task_template_id": tmpl.id})
        assert resp.status_code == 201
        data = resp.json()
        assert data["task_template_name"] == tmpl.name
        assert data["category"] == "Report" and data["base_effort_hours"] == 4.0
        did = data["id"]
        api.put(f"/api/document-types/{did}", json={"is_active": False})
        assert did not in {d["id"] for d in api.get("/api/document-types").json()}
        assert did in {d["id"] for d in api.get("/api/document-types/all").json()}
        resp = api.put(f"/api/document-types/{did}", json={"task_template_id": None})
        assert resp.json()["task_template_name"] is None
        assert api.delete(f"/api/document-types/{did}").status_code == 204
        assert api.delete(f"/api/document-types/{did}").json()["detail"] == "Document type not found"

    def test_duplicate_400(self, api):
        api.post("/api/document-types", json={"name": "Report A"})
        resp = api.post("/api/document-types", json={"name": "Report A"})
        assert resp.status_code == 400
        assert resp.json()["detail"] == "Document type 'Report A' already exists"

    def test_sorted_by_name(self, api):
        api.post("/api/document-types", json={"name": "Zeta"})
        api.post("/api/document-types", json={"name": "Alpha"})
        names = [d["name"] for d in api.get("/api/document-types").json()]
        assert names == sorted(names)

    def test_delete_requires_approver(self, api, api_as, make_user):
        did = api.post("/api/document-types", json={"name": "Keep"}).json()["id"]
        assert api_as(make_user("ESTIMATOR")).delete(f"/api/document-types/{did}").status_code == 403
