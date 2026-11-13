"""Estimation wizard pages: /estimation/new and /estimation/<id>/edit."""

from __future__ import annotations

import json
from datetime import date
from unittest import mock

import pytest

from apps.api.schemas.estimations import EstimationCreate
from apps.catalog.models import DocumentType, DutType, Feature, FeaturePreset, RiskItem, TaskTemplate
from apps.catalog.models import TestProfile as Profile
from apps.estimations import services
from apps.estimations.models import Estimation, EstimationVersionSnapshot, HistoricalProject
from apps.estimations.wizard import state as st
from apps.inbox.models import Request
from apps.teams.models import Team, TeamMember

pytestmark = pytest.mark.django_db

HX = {"HTTP_HX_REQUEST": "true"}
NEW = "/estimation/new"


@pytest.fixture
def catalog(seeded):
    """Seed catalog plus registry rows every wizard step can use."""
    risks = [RiskItem.objects.create(name=f"Risk {i}", category="Planning") for i in range(2)]
    docs = [DocumentType.objects.create(name="Test Plan", base_effort_hours=6),
            DocumentType.objects.create(name="Report", base_effort_hours=4, task_template=TaskTemplate.objects.first())]
    team = Team.objects.create(name="Team A")
    members = [TeamMember.objects.create(name="Alice", role="LEADER", team=team),
               TeamMember.objects.create(name="Bob", role="TESTER", team=team)]
    hist = HistoricalProject.objects.create(project_name="Old", project_type="NEW", estimated_hours=100,
                                            actual_hours=120)
    return {
        "features": list(Feature.objects.order_by("id").values_list("id", flat=True)),
        "duts": list(DutType.objects.order_by("id").values_list("id", flat=True)),
        "profiles": list(Profile.objects.order_by("id").values_list("id", flat=True)),
        "risks": [r.id for r in risks], "docs": [d.id for d in docs], "team": team,
        "members": [m.id for m in members], "hist": hist.id,
    }


@pytest.fixture
def estimator(seeded, make_user):
    return make_user("ESTIMATOR", username="est")


@pytest.fixture
def est_client(web_login, estimator):
    return web_login(estimator)


def post(client, action, step, base=NEW, **fields):
    data = {"_step": step, **fields}
    return client.post(f"{base}/{action}", data, **HX)


def session_state(client, key="wizard:new"):
    return client.session[key]


def fill_all(client, c, base=NEW, name="Wizard Project"):
    """Walk steps 1-6 with every field filled; returns the step-7 response."""
    f, d, p = c["features"], c["duts"], c["profiles"]
    steps = [
        (1, {"project_name": name, "project_type": "EVOLUTION", "project_goals": "Goals",
             "target_customer": "ACME", "product_type_filter": "All", "description": "Scope"}),
        (2, {"feature_ids": [f[0], f[1], f[2]], "new_feature_ids": [f[1]]}),
        (3, {"reference_project_ids": [c["hist"]]}),
        (4, {"dut_ids": [d[0], d[1]], "profile_ids": [p[0], p[1]],
             "matrix": [f"{d[0]}:{p[0]}", f"{d[0]}:{p[1]}", f"{d[1]}:{p[0]}"]}),
        (5, {"pr_simple": 2, "pr_medium": 1, "pr_complex": 1,
             "pr_number": ["PR-1", "PR-2"], "pr_link": ["", "http://x/PR-2"], "pr_priority": ["HIGH", "LOW"],
             "pr_complexity": ["complex", "simple"], "pr_status": ["Open", "Merged"],
             "pr_description": ["d1", ""], "pr_test": ["0", "1"],
             "doc_all": c["docs"], "doc_ids": [c["docs"][0]], f"doc_count_{c['docs'][0]}": 2}),
        (6, {"start_date": "2026-11-02", "testing_start_date": "2026-11-09", "expected_delivery": "2026-12-18",
             "working_days": 30, "team_size": 2, "has_leader": "on", "expected_releases": 2,
             "team_id": c["team"].id, "alloc_member": c["members"], "alloc_role": ["LEADER", "TESTER"],
             "alloc_hours": ["10", "40"], "risk_item_ids": c["risks"][:1]}),
    ]
    resp = None
    for step, fields in steps:
        resp = post(client, "step", step, base, goto=step + 1, **fields)
        assert resp.status_code == 200, (step, resp.content[:500])
    return resp


# ── Access ───────────────────────────────────────────────


class TestAccess:
    def test_viewer_refused(self, web_login, make_user, catalog):
        client = web_login(make_user("VIEWER"))
        assert client.get(NEW).status_code == 403
        assert post(client, "step", 1, goto=2, project_name="x").status_code == 403

    def test_estimator_allowed_and_redirected_to_step_1(self, est_client, catalog):
        page = est_client.get(NEW)
        assert page.status_code == 200 and page["HX-Push-Url"] == "/estimation/new?step=1"
        assert b"New Estimation" in page.content and b"wizard-form" in page.content
        assert b"autosave" in page.content

    def test_unknown_ids_404(self, admin_client, catalog):
        assert admin_client.get("/estimation/999/edit").status_code == 404
        assert admin_client.get("/estimation/999/edit?step=2").status_code == 404
        assert post(admin_client, "step", 1, "/estimation/999/edit", goto=2).status_code == 404
        assert admin_client.get(f"{NEW}?request_id=999").status_code == 404
        assert post(admin_client, "feature/12345/edit", 2).status_code == 404

    def test_draft_edit_only_author_or_admin(self, api_as, web_login, estimator, make_user, catalog):
        other = make_user("ESTIMATOR", username="other")
        resp = api_as(estimator).post("/api/estimations", json={"project_name": "Mine", "project_type": "NEW"})
        eid = resp.json()["id"]
        assert web_login(other).get(f"/estimation/{eid}/edit?step=1").status_code == 403
        assert web_login(estimator).get(f"/estimation/{eid}/edit?step=1").status_code == 200

    def test_get_methods(self, admin_client, catalog):
        assert admin_client.post(NEW).status_code == 405
        assert admin_client.get(f"{NEW}/step").status_code == 405


# ── Steps: validation and state merge ────────────────────


class TestSteps:
    def test_project_name_required(self, admin_client, catalog):
        resp = post(admin_client, "step", 1, goto=2, project_name=" ")
        assert resp.status_code == 422
        assert b"Project Name is required." in resp.content
        assert "HX-Trigger" in resp and "toast" in resp["HX-Trigger"]

    def test_change_request_needs_reference(self, admin_client, catalog):
        resp = post(admin_client, "step", 1, goto=2, project_name="X", project_type="CHANGE_REQUEST")
        assert resp.status_code == 422 and b"Project Reference is required" in resp.content
        resp = post(admin_client, "step", 1, goto=2, project_name="X", project_type="CHANGE_REQUEST",
                    project_reference="EST-1 — Old")
        assert resp.status_code == 200 and resp["HX-Push-Url"] == "/estimation/new?step=2"

    def test_features_required_but_back_is_free(self, admin_client, catalog):
        assert post(admin_client, "step", 2, goto=3).status_code == 422
        resp = post(admin_client, "step", 2, goto=1)
        assert resp.status_code == 200 and resp["HX-Push-Url"].endswith("step=1")

    def test_new_features_subset_of_selected(self, admin_client, catalog):
        f = catalog["features"]
        post(admin_client, "step", 2, goto=3, feature_ids=[f[0]], new_feature_ids=[f[0], f[1]])
        data = session_state(admin_client)["data"]
        assert data["feature_ids"] == [f[0]] and data["new_feature_ids"] == [f[0]]

    def test_matrix_rules(self, admin_client, catalog):
        d, p = catalog["duts"], catalog["profiles"]
        assert b"Select at least one DUT." in post(admin_client, "step", 4, goto=5).content
        assert b"Select at least one Profile." in post(admin_client, "step", 4, goto=5, dut_ids=[d[0]]).content
        resp = post(admin_client, "step", 4, goto=5, dut_ids=[d[0]], profile_ids=[p[0]])
        assert resp.status_code == 422 and b"combination" in resp.content
        # cells outside the selected DUTs/profiles are dropped
        resp = post(admin_client, "step", 4, goto=5, dut_ids=[d[0]], profile_ids=[p[0]],
                    matrix=[f"{d[0]}:{p[0]}", f"{d[1]}:{p[0]}", "junk"])
        assert resp.status_code == 200
        assert session_state(admin_client)["data"]["dut_profile_matrix"] == [[d[0], p[0]]]

    def test_pr_counts_and_details(self, admin_client, catalog):
        resp = post(admin_client, "step", 5, goto=6, pr_simple="-1", pr_medium="x", pr_complex="2")
        assert resp.status_code == 422 and b"whole number" in resp.content
        resp = post(admin_client, "step", 5, goto=6, pr_simple="1", pr_medium="0", pr_complex="0",
                    pr_number=["", "PR-9"], pr_link=["", ""], pr_priority=["LOW", "HIGH"],
                    pr_complexity=["simple", "bogus"], pr_status=["Open", ""], pr_description=["", "x"],
                    pr_test=["1", "0"])
        assert resp.status_code == 200
        data = session_state(admin_client)["data"]
        assert data["pr_fixes"] == {"simple": 1, "medium": 0, "complex": 0}
        assert data["pr_details"] == [{"pr_number": "PR-9", "link": "", "description": "x", "priority": "HIGH",
                                       "complexity": "simple", "status": "Open", "test_available": False}]

    def test_delivery_rules(self, admin_client, catalog):
        resp = post(admin_client, "step", 6, goto=7, team_size="0", working_days="5", expected_releases="1")
        assert resp.status_code == 422 and b"Team size must be at least 1." in resp.content
        resp = post(admin_client, "step", 6, goto=7, team_size="1", working_days="5", expected_releases="1",
                    start_date="2026-13-45")
        assert resp.status_code == 422 and b"valid date" in resp.content

    def test_state_merges_across_steps(self, admin_client, catalog):
        c = catalog
        resp = fill_all(admin_client, c)
        assert resp["HX-Push-Url"] == "/estimation/new?step=7"
        s = session_state(admin_client)
        data, meta = s["data"], s["meta"]
        assert meta["description"] == "Scope"
        assert data["project_name"] == "Wizard Project" and data["target_customer"] == "ACME"
        assert data["feature_ids"] == c["features"][:3] and data["new_feature_ids"] == [c["features"][1]]
        assert data["reference_project_ids"] == [c["hist"]]
        assert len(data["dut_profile_matrix"]) == 3
        assert data["pr_fixes"] == {"simple": 2, "medium": 1, "complex": 1}
        assert [r["pr_number"] for r in data["pr_details"]] == ["PR-1", "PR-2"]
        assert data["document_type_ids"] == [c["docs"][0]]
        assert data["document_counts"] == {str(c["docs"][0]): 2, str(c["docs"][1]): 1}
        assert data["has_leader"] is True and data["team_size"] == 2 and data["expected_releases"] == 2
        assert data["team_allocations"][0] == {"team_member_id": c["members"][0], "role": "LEADER",
                                               "allocated_hours": 10.0}
        assert data["testing_start_date"] == "2026-11-09" and data["expected_delivery"] == "2026-12-18"
        EstimationCreate.model_validate(data)  # the state is the API payload
        # earlier steps keep their values when revisited
        page = admin_client.get(f"{NEW}?step=1")
        assert b'value="Wizard Project"' in page.content and b"Scope" in page.content

    def test_steps_render_saved_values(self, admin_client, catalog):
        fill_all(admin_client, catalog)
        for n in range(1, 8):
            assert admin_client.get(f"{NEW}?step={n}").status_code == 200
        s4 = admin_client.get(f"{NEW}?step=4").content.decode()
        assert f"{catalog['duts'][0]}:{catalog['profiles'][1]}" in s4
        s6 = admin_client.get(f"{NEW}?step=6").content.decode()
        assert 'value="2026-11-02"' in s6 and "Alice" in s6

    def test_fresh_start_resets(self, admin_client, catalog):
        post(admin_client, "step", 1, goto=2, project_name="Temp")
        admin_client.get(NEW)
        assert session_state(admin_client)["data"]["project_name"] == ""

    def test_reload_mid_way_resumes(self, admin_client, catalog):
        post(admin_client, "step", 1, goto=2, project_name="Keep me")
        page = admin_client.get(f"{NEW}?step=2")
        assert b"Keep me" in page.content and b'aria-current="step"' in page.content

    def test_product_type_filter_limits_features(self, admin_client, catalog):
        f = Feature.objects.get(pk=catalog["features"][0])
        f.product_type = "Telco"
        f.save()
        post(admin_client, "step", 1, goto=2, project_name="X", product_type_filter="Payment")
        page = admin_client.get(f"{NEW}?step=2").content.decode()
        assert f'name="feature_ids" value="{f.id}"' not in page and "Filtered by product type: Payment" in page


# ── Autosave, save draft, final save ─────────────────────


class TestPersistence:
    def test_autosave_creates_one_draft_then_updates(self, admin_client, catalog):
        assert post(admin_client, "autosave", 1, project_name="").status_code == 204
        assert Estimation.objects.count() == 0
        resp = post(admin_client, "autosave", 1, project_name="Auto")
        assert resp.status_code == 200 and "Draft autosaved" in resp.content.decode()
        assert Estimation.objects.count() == 1
        est = Estimation.objects.get()
        assert est.status == "DRAFT" and est.project_name == "Auto"
        assert session_state(admin_client)["meta"]["draft_id"] == est.id
        # nothing changed -> no write
        assert post(admin_client, "autosave", 1, project_name="Auto").status_code == 204
        resp = post(admin_client, "autosave", 2, feature_ids=[catalog["features"][0]])
        assert resp.status_code == 200
        assert Estimation.objects.count() == 1
        est.refresh_from_db()
        assert est.wizard_inputs["feature_ids"] == [catalog["features"][0]]

    def test_save_draft(self, admin_client, catalog):
        assert post(admin_client, "save-draft", 1, project_name="").status_code == 400
        resp = post(admin_client, "save-draft", 1, project_name="Manual")
        assert resp.status_code == 200 and "Draft saved" in resp.content.decode()
        assert "Draft saved (ID" in resp["HX-Trigger"]
        post(admin_client, "save-draft", 1, project_name="Manual 2")
        assert list(Estimation.objects.values_list("project_name", flat=True)) == ["Manual 2"]

    def test_final_save_reuses_draft_and_redirects(self, admin_client, catalog):
        fill_all(admin_client, catalog)
        post(admin_client, "autosave", 7)
        draft = Estimation.objects.get()
        resp = post(admin_client, "save", 7)
        assert resp.status_code == 204 and resp["HX-Redirect"] == f"/estimation/{draft.id}"
        assert Estimation.objects.count() == 1
        assert "wizard:new" not in admin_client.session
        draft.refresh_from_db()
        assert draft.team_allocations.count() == 2 and draft.risks.count() == 1
        assert draft.wizard_inputs["pr_details"][0]["pr_number"] == "PR-1"

    def test_final_save_without_draft_creates_one(self, admin_client, catalog):
        fill_all(admin_client, catalog)
        resp = post(admin_client, "save", 7)
        est = Estimation.objects.get()
        assert resp["HX-Redirect"] == f"/estimation/{est.id}" and est.creator.username == "admin"
        assert est.created_by == "admin"

    def test_final_save_sends_back_to_invalid_step(self, admin_client, catalog):
        post(admin_client, "step", 1, goto=7, project_name="Skipper")  # jump via the progress strip
        resp = post(admin_client, "save", 7)
        assert resp.status_code == 422 and resp["HX-Push-Url"].endswith("step=2")
        assert b"Select at least one feature" in resp.content
        assert Estimation.objects.count() == 0

    def test_task_testers_from_review(self, admin_client, catalog):
        fill_all(admin_client, catalog)
        preview = post(admin_client, "preview", 7).content.decode()
        assert 'name="tt_name"' in preview
        first = services.calculate_preview(st.calculate_input(session_state(admin_client)["data"]))["tasks"][0]
        post(admin_client, "save", 7, _tasks="1", tt_name=[first["name"]], tt_value=["3"])
        est = Estimation.objects.get()
        assert est.tasks.filter(task_name=first["name"]).first().assigned_testers == 3


# ── Review preview ───────────────────────────────────────


class TestPreview:
    def test_preview_matches_service_and_api(self, admin_client, api, catalog):
        fill_all(admin_client, catalog)
        resp = post(admin_client, "preview", 7)
        assert resp.status_code == 200
        data = session_state(admin_client)["data"]
        expected = services.calculate_preview(st.calculate_input(data))
        api_out = api.post("/api/estimations/calculate", json=st.calculate_input(data).model_dump(by_alias=True,
                                                                                                    mode="json"))
        assert api_out.json()["grand_total_hours"] == pytest.approx(expected["grand_total_hours"])
        body = resp.content.decode()
        assert f"{expected['grand_total_hours']:.1f}" in body
        assert f"{expected['total_tester_hours']:.1f}" in body
        assert expected["feasibility_status"] in body
        assert session_state(admin_client)["meta"]["calc_total"] == expected["grand_total_hours"]
        assert 'id="wz-rail-total" hx-swap-oob' in body
        # synthetic tasks: PR-1 has no tests -> test creation task (added on save)
        assert "Test Creation (PR without existing tests)" in body and "added on save" in body
        # completion date from the testing start date
        cd = services.estimated_completion_date("2026-11-09", expected["elapsed_days"])
        assert cd.isoformat() in body

    def test_saved_numbers_match_create_service(self, admin_client, catalog):
        fill_all(admin_client, catalog)
        data = session_state(admin_client)["data"]
        post(admin_client, "save", 7)
        est = Estimation.objects.get()
        ref = services.create_estimation(EstimationCreate.model_validate(data), est.creator)
        assert est.grand_total_hours == pytest.approx(ref.grand_total_hours)
        assert est.estimated_completion_date == ref.estimated_completion_date


# ── Resume / edit / revise ───────────────────────────────


def full_payload(c) -> dict:
    f, d, p = c["features"], c["duts"], c["profiles"]
    return {
        "project_name": "API Project", "project_type": "NEW",
        "feature_ids": f[:4], "new_feature_ids": [f[2]], "reference_project_ids": [c["hist"]],
        "dut_ids": d[:2], "profile_ids": p[:2], "dut_profile_matrix": [[d[0], p[0]], [d[1], p[1]]],
        "pr_fixes": {"simple": 1, "medium": 2, "complex": 0},
        "pr_details": [{"pr_number": "PR-5", "link": "http://pr/5", "description": "desc", "priority": "HIGH",
                        "complexity": "medium", "status": "Open", "test_available": False}],
        "team_size": 3, "has_leader": True, "start_date": "2026-10-01", "expected_delivery": "2026-12-31",
        "working_days": 40, "expected_releases": 2, "project_goals": "G", "target_customer": "T",
        "project_reference": None, "team_id": c["team"].id,
        "team_allocations": [{"team_member_id": c["members"][1], "role": "TESTER", "allocated_hours": 12.5}],
        "risk_item_ids": c["risks"], "document_type_ids": c["docs"], "document_counts": {str(c["docs"][0]): 3},
        "task_assigned_testers": {"Environment setup": 2}, "testing_start_date": "2026-10-05",
        "product_type_filter": "All", "applied_presets": [{"id": 9, "name": "P"}],
    }


NUMBERS = ["grand_total_hours", "total_tester_hours", "total_leader_hours", "pr_fix_hours", "pr_no_test_hours",
           "study_hours", "release_extra_hours", "documentation_hours", "buffer_hours", "elapsed_days",
           "dut_profile_combinations", "estimated_completion_date", "feasibility_status"]


class TestEdit:
    def test_resume_draft_round_trip(self, admin_client, api, catalog):
        created = api.post("/api/estimations", json=full_payload(catalog)).json()
        eid = created["id"]
        before = Estimation.objects.get(pk=eid)
        tasks_before = list(before.tasks.order_by("id").values_list("task_name", "calculated_hours",
                                                                     "assigned_testers"))
        page = admin_client.get(f"/estimation/{eid}/edit")
        assert page.status_code == 200 and page["HX-Push-Url"] == f"/estimation/{eid}/edit?step=1"
        assert b"Edit Estimation" in page.content and b"in place" in page.content
        data = session_state(admin_client, f"wizard:edit:{eid}")["data"]
        for key, val in full_payload(catalog).items():
            assert data[key] == val, key
        resp = post(admin_client, "save", 7, f"/estimation/{eid}/edit")
        assert resp.status_code == 204 and resp["HX-Redirect"] == f"/estimation/{eid}"
        after = Estimation.objects.get(pk=eid)
        for key in NUMBERS:
            assert getattr(after, key) == getattr(before, key), key
        assert after.version == 1 and after.status == "DRAFT"
        assert after.wizard_inputs == before.wizard_inputs
        assert list(after.tasks.order_by("id").values_list("task_name", "calculated_hours",
                                                            "assigned_testers")) == tasks_before
        assert list(after.team_allocations.values_list("team_member_id", "allocated_hours")) == [
            (catalog["members"][1], 12.5)]
        assert sorted(after.risks.values_list("risk_item_id", flat=True)) == catalog["risks"]
        assert Estimation.objects.count() == 1

    def test_draft_edit_autosaves_in_place(self, admin_client, api, catalog):
        eid = api.post("/api/estimations", json=full_payload(catalog)).json()["id"]
        admin_client.get(f"/estimation/{eid}/edit")
        base = f"/estimation/{eid}/edit"
        assert post(admin_client, "autosave", 1, base, project_name="API Project", project_type="NEW",
                    project_goals="G", target_customer="T", product_type_filter="All").status_code == 204
        resp = post(admin_client, "autosave", 1, base, project_name="Renamed", project_type="NEW")
        assert resp.status_code == 200
        est = Estimation.objects.get(pk=eid)
        assert est.project_name == "Renamed" and est.version == 1 and Estimation.objects.count() == 1

    def test_finalized_estimation_is_locked(self, admin_client, api, catalog):
        eid = api.post("/api/estimations", json=full_payload(catalog)).json()["id"]
        api.post(f"/api/estimations/{eid}/status", json={"status": "FINAL"})
        page = admin_client.get(f"/estimation/{eid}/edit")
        assert page.status_code == 200 and b"Only DRAFT or REVISED estimations can be edited." in page.content
        assert post(admin_client, "step", 1, f"/estimation/{eid}/edit", goto=2).status_code == 400

    def test_revision_snapshots_and_bumps_version(self, admin_client, api, catalog):
        eid = api.post("/api/estimations", json=full_payload(catalog)).json()["id"]
        api.post(f"/api/estimations/{eid}/status", json={"status": "REVISED"})
        base = f"/estimation/{eid}/edit"
        page = admin_client.get(f"{base}?step=7")
        assert b"Save Revision" in page.content and b"Revising" in page.content
        assert b"/autosave" not in page.content
        # no autosave and no draft saving while revising: the state stays in the session
        assert post(admin_client, "autosave", 1, base, project_name="Changed").status_code == 204
        assert post(admin_client, "save-draft", 1, base, project_name="Changed").status_code == 400
        post(admin_client, "step", 1, base, goto=2, project_name="Changed", project_type="NEW")
        assert Estimation.objects.get(pk=eid).project_name == "API Project"
        post(admin_client, "step", 6, base, goto=7, team_size="5", working_days="40", expected_releases="2")
        resp = post(admin_client, "save", 7, base)
        assert resp.status_code == 204 and resp["HX-Redirect"] == f"/estimation/{eid}"
        est = Estimation.objects.get(pk=eid)
        assert est.version == 2 and est.status == "DRAFT" and est.project_name == "Changed"
        assert est.wizard_inputs["team_size"] == 5
        assert EstimationVersionSnapshot.objects.filter(estimation=est, version=1).count() == 1
        assert f"wizard:edit:{eid}" not in admin_client.session

    def test_revision_cannot_add_project_features(self, admin_client, api, catalog):
        eid = api.post("/api/estimations", json=full_payload(catalog)).json()["id"]
        api.post(f"/api/estimations/{eid}/status", json={"status": "REVISED"})
        admin_client.get(f"/estimation/{eid}/edit")
        resp = post(admin_client, "feature/new", 2, f"/estimation/{eid}/edit", feature_ids=catalog["features"][:1])
        assert resp.status_code == 400 and "DRAFT" in resp.json()["detail"]


# ── Features, presets, Jira, inbox ───────────────────────


class TestDialogs:
    def test_custom_feature_requires_name_then_creates_draft(self, admin_client, catalog):
        resp = post(admin_client, "feature/new", 2)
        assert resp.status_code == 400 and "project name" in resp.json()["detail"]
        post(admin_client, "step", 1, goto=2, project_name="Custom")
        resp = post(admin_client, "feature/new", 2, feature_ids=[catalog["features"][0]])
        assert resp.status_code == 200 and b"Add custom feature for this project" in resp.content
        draft = Estimation.objects.get()
        assert session_state(admin_client)["meta"]["draft_id"] == draft.id
        bad = post(admin_client, "feature/create", 2, **{"pf-name": "", "pf-complexity_weight": "1",
                                                          "pf-base_effort_hours": "8"})
        assert bad.status_code == 422 and b"Feature name is required." in bad.content
        resp = post(admin_client, "feature/create", 2, feature_ids=[catalog["features"][0]], **{
            "pf-name": "Only Here", "pf-category": "Project-Specific", "pf-complexity_weight": "1.5",
            "pf-base_effort_hours": "8", "pf-product_type": "All"})
        assert resp.status_code == 200 and resp["HX-Retarget"] == "#wizard"
        assert "close-modal" in resp["HX-Trigger"]
        feat = Feature.objects.get(name="Only Here")
        assert feat.owner_estimation_id == draft.id and not feat.is_global
        assert session_state(admin_client)["data"]["feature_ids"] == [catalog["features"][0], feat.id]
        assert b"Only Here" in resp.content and b"project</span>" in resp.content
        # edit it
        assert post(admin_client, f"feature/{feat.id}/edit", 2).status_code == 200
        resp = post(admin_client, f"feature/{feat.id}/update", 2, feature_ids=[feat.id], **{
            "pf-name": "Renamed", "pf-complexity_weight": "2", "pf-base_effort_hours": "4",
            "pf-product_type": "All"})
        assert resp.status_code == 200
        feat.refresh_from_db()
        assert feat.name == "Renamed" and feat.complexity_weight == 2
        # a global feature cannot be edited from the wizard
        assert post(admin_client, f"feature/{catalog['features'][0]}/edit", 2).status_code == 404

    def test_promotion_from_review(self, web_login, estimator, make_user, catalog):
        client = web_login(estimator)
        post(client, "step", 1, goto=2, project_name="Promo")
        post(client, "feature/new", 2)
        post(client, "feature/create", 2, **{"pf-name": "PF", "pf-complexity_weight": "1",
                                             "pf-base_effort_hours": "2", "pf-product_type": "All"})
        feat = Feature.objects.get(name="PF")
        page = client.get(f"{NEW}?step=7").content.decode()
        assert "Request to add to global" in page and "Promote to global" not in page
        resp = post(client, f"feature/{feat.id}/request-promotion", 7)
        assert resp.status_code == 200 and b"requested" in resp.content
        assert Feature.objects.get(pk=feat.id).promotion_requested
        assert post(client, f"feature/{feat.id}/promote", 7).status_code == 403

    def test_promote_as_approver(self, web_login, make_user, catalog):
        client = web_login(make_user("APPROVER"))
        post(client, "step", 1, goto=2, project_name="Promo")
        post(client, "feature/new", 2)
        post(client, "feature/create", 2, **{"pf-name": "PG", "pf-complexity_weight": "1",
                                             "pf-base_effort_hours": "2", "pf-product_type": "All"})
        feat = Feature.objects.get(name="PG")
        assert post(client, f"feature/{feat.id}/promote", 7).status_code == 200
        assert Feature.objects.get(pk=feat.id).is_global

    def test_presets_apply_and_save(self, admin_client, admin_user, catalog):
        f = catalog["features"]
        preset = FeaturePreset.objects.create(name="Core", feature_ids=[f[3], f[4]], owner_user=admin_user)
        assert post(admin_client, "preset/apply", 2, preset_id="").status_code == 400
        resp = post(admin_client, "preset/apply", 2, feature_ids=[f[0]], preset_id=preset.id)
        assert resp.status_code == 200 and "Applied preset 'Core' (+2 feature(s))." in resp["HX-Trigger"]
        data = session_state(admin_client)["data"]
        assert data["feature_ids"] == [f[0], f[3], f[4]]
        assert data["applied_presets"] == [{"id": preset.id, "name": "Core"}]
        # save selection as preset
        assert post(admin_client, "preset/new", 2).status_code == 400  # nothing selected
        resp = post(admin_client, "preset/new", 2, feature_ids=[f[0], f[1]])
        assert resp.status_code == 200 and b"2 feature(s)" in resp.content
        assert post(admin_client, "preset/create", 2, feature_ids=[f[0], f[1]],
                    **{"ps-preset_name": ""}).status_code == 422
        resp = post(admin_client, "preset/create", 2, feature_ids=[f[0], f[1]],
                    **{"ps-preset_name": "Mine", "ps-preset_description": "d"})
        assert resp.status_code == 200 and "Preset saved." in resp["HX-Trigger"]
        saved = FeaturePreset.objects.get(name="Mine")
        assert saved.feature_ids == sorted([f[0], f[1]]) and saved.owner_user == admin_user

    def test_jira_import(self, admin_client, catalog):
        items = [
            {"key": "PR-1", "summary": "Crash", "priority": "High", "status": "Open", "description": "",
             "link": "https://jira.invalid/browse/PR-1"},
            {"key": "PR-2", "summary": "Typo", "priority": "Medium", "status": "Done", "description": "Body",
             "link": ""},
        ]
        with mock.patch("apps.integrations.service.jira_pr_items", return_value=items):
            resp = post(admin_client, "jira", 5, pr_simple="0", pr_medium="0", pr_complex="0",
                        pr_number=["PR-2"], pr_test=["1"])
        assert resp.status_code == 200 and b"Import PR Items from Registry" in resp.content
        assert b"added</span>" in resp.content  # PR-2 already in the estimation
        assert post(admin_client, "jira/import", 5).status_code == 400
        resp = post(admin_client, "jira/import", 5, pr_number=["PR-2"], pr_test=["1"],
                    jira_item=[json.dumps(i) for i in items])
        assert resp.status_code == 200 and "Imported 1 PR item(s)." in resp["HX-Trigger"]
        rows = session_state(admin_client)["data"]["pr_details"]
        assert [r["pr_number"] for r in rows] == ["PR-2", "PR-1"]
        assert rows[1] == {"pr_number": "PR-1", "link": "https://jira.invalid/browse/PR-1", "description": "Crash",
                           "priority": "High", "complexity": "complex", "status": "Open", "test_available": True}

    def test_jira_not_configured(self, admin_client, catalog):
        resp = post(admin_client, "jira", 5)
        assert resp.status_code == 400 and "JIRA" in resp.json()["detail"]

    def test_request_prefill(self, admin_client, catalog):
        req = Request.objects.create(request_number="REQ-0007", title="New SIM", requester_name="Dana",
                                     description="Please test", received_date=date(2026, 11, 1))
        page = admin_client.get(f"{NEW}?request_id={req.id}").content.decode()
        assert "Estimation request based on REQ-0007: New SIM" in page
        assert "Requested by Dana." in page and "Please test" in page and "REQ · REQ-0007" in page
        fill_all(admin_client, catalog)
        post(admin_client, "save", 7)
        est = Estimation.objects.get()
        assert est.request_id == req.id
        req.refresh_from_db()
        assert req.status == "ESTIMATED"
