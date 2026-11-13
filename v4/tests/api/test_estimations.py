"""Estimation lifecycle endpoints (ported from v3 test_api.py and test_phase6_api.py, plus extras)."""

from __future__ import annotations

import json
from datetime import date
from unittest import mock

import pytest

from apps.accounts.models import AuditLog
from apps.catalog.models import DocumentType, Feature, PublicHoliday, RiskItem, TaskTemplate
from apps.core import config
from apps.core.models import Configuration
from apps.estimations.models import Estimation, EstimationVersionSnapshot, HistoricalProject
from apps.inbox.models import Request

NOTIFY_STATUS = "apps.notifications.service.NotificationService.notify_estimation_status_changed"
NOTIFY_ASSIGN = "apps.notifications.service.NotificationService.notify_user_assigned"
MINIMAL = {"project_name": "X", "project_type": "NEW"}


def _feature_ids(n: int) -> list[int]:
    return list(Feature.objects.order_by("category", "name").values_list("id", flat=True)[:n])


def _create_estimation(api, **overrides) -> dict:
    """Minimal estimation (v3 test_phase6_api helper)."""
    payload = {
        "project_name": "Test Project",
        "project_type": "NEW",
        "feature_ids": [],
        "team_size": 2,
        "has_leader": True,
        "working_days": 20,
        "pr_fixes": {"simple": 2, "medium": 1, "complex": 0},
        **overrides,
    }
    resp = api.post("/api/estimations", json=payload)
    assert resp.status_code == 201, resp.content
    return resp.json()


def _to_status(api, est_id: int, *statuses: str) -> None:
    for s in statuses:
        resp = api.post(f"/api/estimations/{est_id}/status", json={"status": s, "approved_by": "Boss"})
        assert resp.status_code == 200, resp.content


@pytest.fixture
def estimator(make_user, seeded):
    return make_user("ESTIMATOR", username="est1", display_name="Est One")


@pytest.fixture
def est_api(api_as, estimator):
    return api_as(estimator)


# ── v3 TestEstimationEndpoints ───────────────────────────


class TestEstimationEndpoints:
    def test_create_estimation(self, api):
        resp = api.post("/api/estimations", json={
            "project_name": "API Test Project",
            "project_type": "EVOLUTION",
            "feature_ids": _feature_ids(3),
            "new_feature_ids": [],
            "dut_ids": [1, 2],
            "profile_ids": [1],
            "pr_fixes": {"simple": 1, "medium": 1, "complex": 0},
            "team_size": 2,
            "has_leader": True,
            "working_days": 20,
            "expected_delivery": "2026-04-01",
        })
        assert resp.status_code == 201
        data = resp.json()
        assert data["project_name"] == "API Test Project"
        assert data["feasibility_status"] in ("FEASIBLE", "AT_RISK", "NOT_FEASIBLE")
        assert data["grand_total_hours"] > 0
        assert len(data["tasks"]) > 0
        assert data["estimation_number"].startswith("EST")
        assert data["status"] == "DRAFT"
        assert data["assigned_to_name"] == "Administrator"

    def test_list_estimations(self, api):
        _create_estimation(api, project_name="A")
        _create_estimation(api, project_name="B")
        resp = api.get("/api/estimations")
        assert resp.status_code == 200
        assert [e["project_name"] for e in resp.json()] == ["B", "A"]

    def test_create_requires_estimator(self, api_as, make_user, seeded):
        viewer = api_as(make_user("VIEWER"))
        resp = viewer.post("/api/estimations", json={"project_name": "X", "project_type": "NEW"})
        assert resp.status_code == 403

    def test_list_requires_auth(self, anon_api):
        assert anon_api.get("/api/estimations").status_code == 401

    def test_create_links_request_and_related_names(self, api, seeded):
        req = Request.objects.create(request_number="REQ-9", title="T", requester_name="R",
                                     received_date=date(2026, 1, 1))
        from apps.teams.models import Team, TeamMember
        team = Team.objects.create(name="QA")
        member = TeamMember.objects.create(name="Alice", role="TESTER")
        risk = RiskItem.objects.create(name="Late DUTs", category="Supply", likelihood="HIGH", impact="HIGH")
        est = _create_estimation(
            api, request_id=req.id, team_id=team.id, risk_item_ids=[risk.id],
            team_allocations=[{"team_member_id": member.id, "role": "TESTER", "allocated_hours": 40}],
        )
        req.refresh_from_db()
        assert req.status == "ESTIMATED"
        assert est["request_number"] == "REQ-9"
        assert est["team_name"] == "QA"
        assert est["team_allocations"] == [{"team_member_id": member.id, "role": "TESTER",
                                            "allocated_hours": 40.0, "team_member_name": "Alice"}]
        r = est["risks"][0]
        assert (r["risk_item_name"], r["risk_item_category"], r["risk_item_likelihood"],
                r["risk_item_impact"]) == ("Late DUTs", "Supply", "HIGH", "HIGH")

    def test_get_not_found(self, api):
        resp = api.get("/api/estimations/9999")
        assert resp.status_code == 404
        assert resp.json() == {"detail": "Estimation not found"}

    def test_completion_date_skips_weekends_and_holidays(self, api):
        PublicHoliday.objects.create(date=date(2026, 12, 25), name="Xmas", is_recurring=True)
        est = _create_estimation(api, testing_start_date="2026-12-21")
        assert est["elapsed_days"] > 0
        assert est["estimated_completion_date"] is not None
        completion = date.fromisoformat(est["estimated_completion_date"])
        assert completion.weekday() < 5 and completion != date(2026, 12, 25)


# ── Preview and recalculation ────────────────────────────


class TestCalculation:
    def test_preview_does_not_persist(self, api):
        resp = api.post("/api/estimations/calculate", json={
            "project_type": "NEW", "feature_ids": _feature_ids(2), "dut_ids": [1, 2],
            "pr_fixes": {"simple": 1}, "team_size": 1,
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["grand_total_hours"] > 0
        assert data["tasks"] and {"name", "formula", "feature_name"} <= set(data["tasks"][0])
        assert Estimation.objects.count() == 0

    def test_preview_appends_risk_registry_items(self, api):
        ri = RiskItem.objects.create(name="Vendor slip", category="Schedule", likelihood="HIGH", impact="")
        resp = api.post("/api/estimations/calculate", json={"project_type": "NEW", "risk_item_ids": [ri.id]})
        assert "[Schedule] Vendor slip (Likelihood: HIGH, Impact: ?)" in resp.json()["risk_messages"]

    def test_preview_validation_error(self, api):
        assert api.post("/api/estimations/calculate", json={}).status_code == 422

    def test_recalculate(self, api):
        est = _create_estimation(api)
        resp = api.post(f"/api/estimations/{est['id']}/calculate")
        assert resp.status_code == 200
        data = resp.json()
        assert data["grand_total_hours"] > 0
        assert data["documentation_hours"] == 0

    def test_recalculate_not_found(self, api):
        assert api.post("/api/estimations/9999/calculate").status_code == 404


# ── Detail extras: document deliverables and synthetic tasks ──


class TestEstimationDetail:
    def test_document_overlap_deduction(self, api):
        report_tmpl = TaskTemplate.objects.get(name="Test report writing")  # 8h, global template
        a = DocumentType.objects.create(name="Plan A", category="Planning", base_effort_hours=6,
                                        task_template=report_tmpl)
        b = DocumentType.objects.create(name="Matrix B", category="Report", base_effort_hours=4,
                                        task_template=report_tmpl)
        c = DocumentType.objects.create(name="Notes C", category="Report", base_effort_hours=3)
        est = _create_estimation(api, document_type_ids=[a.id, b.id, c.id], document_counts={str(a.id): 2})
        # Group (12 + 4) minus the 8h template once = 8h, plus 3h unlinked.
        assert est["documentation_hours"] == pytest.approx(11.0)

        detail = api.get(f"/api/estimations/{est['id']}").json()
        dd = {d["name"]: d for d in detail["document_deliverables"]}
        assert dd["Plan A"]["total_hours"] == 12 and dd["Plan A"]["effective_hours"] == 6.0
        assert dd["Matrix B"]["effective_hours"] == 2.0
        assert "Deducted 8.0h across group" in dd["Plan A"]["overlap_note"]
        assert dd["Notes C"]["effective_hours"] == 3 and dd["Notes C"]["overlap_note"] is None
        doc_tasks = [t for t in detail["tasks"] if t["task_type"] == "DOCUMENTATION"]
        assert {t["task_name"]: t["calculated_hours"] for t in doc_tasks} == {
            "Test report writing (additional documentation)": 8.0,
            "Documentation effort": 3.0,
        }
        assert all(t["id"] == 0 and t["assigned_testers"] == 0 for t in doc_tasks)

    def test_pr_no_test_synthetic_task(self, api):
        est = _create_estimation(api, pr_details=[
            {"pr_number": "PR-7", "test_available": False},
            {"pr_number": "PR-8", "test_available": True},
            {"pr_number": "PR-9", "test_available": False},
        ])
        assert est["pr_no_test_hours"] == 16.0
        detail = api.get(f"/api/estimations/{est['id']}").json()
        synth = [t for t in detail["tasks"] if t["task_type"] == "PR_TEST_CREATION"]
        assert len(synth) == 1
        assert synth[0]["calculated_hours"] == 16.0
        assert synth[0]["notes"] == "2 PR(s) without tests: PR-7, PR-9 (8.0h each)"
        # Synthetic rows are not stored.
        assert not Estimation.objects.get(pk=est["id"]).tasks.filter(task_type="PR_TEST_CREATION").exists()


# ── v3 TestEstimationUpdate ──────────────────────────────


class TestEstimationUpdate:
    def test_update_project_name(self, api):
        est = _create_estimation(api)
        resp = api.put(f"/api/estimations/{est['id']}", json={"project_name": "Updated Project"})
        assert resp.status_code == 200
        assert resp.json()["project_name"] == "Updated Project"

    def test_update_expected_delivery(self, api):
        est = _create_estimation(api)
        resp = api.put(f"/api/estimations/{est['id']}", json={"expected_delivery": "2026-06-01"})
        assert resp.status_code == 200
        assert resp.json()["expected_delivery"] == "2026-06-01"

    def test_update_not_found(self, api):
        assert api.put("/api/estimations/9999", json={"project_name": "X"}).status_code == 404

    def test_partial_update(self, api):
        est = _create_estimation(api)
        resp = api.put(f"/api/estimations/{est['id']}", json={"project_name": "Only Name Changed"})
        data = resp.json()
        assert data["project_name"] == "Only Name Changed"
        assert data["project_type"] == est["project_type"]

    def test_update_requires_estimator(self, api, api_as, make_user):
        est = _create_estimation(api)
        resp = api_as(make_user("VIEWER")).put(f"/api/estimations/{est['id']}", json={"project_name": "X"})
        assert resp.status_code == 403


# ── v3 TestEstimationDelete ──────────────────────────────


class TestEstimationDelete:
    def test_delete_estimation(self, api):
        est = _create_estimation(api)
        assert api.delete(f"/api/estimations/{est['id']}").status_code == 204
        assert api.get(f"/api/estimations/{est['id']}").status_code == 404

    def test_delete_not_found(self, api):
        assert api.delete("/api/estimations/9999").status_code == 404

    def test_delete_requires_admin(self, api, est_api):
        est = _create_estimation(api)
        assert est_api.delete(f"/api/estimations/{est['id']}").status_code == 403


# ── v3 TestEstimationStatusWorkflow ──────────────────────


class TestEstimationStatusWorkflow:
    def test_draft_to_final(self, api):
        est = _create_estimation(api)
        assert est["status"] == "DRAFT"
        resp = api.post(f"/api/estimations/{est['id']}/status", json={"status": "FINAL"})
        assert resp.status_code == 200
        assert resp.json()["status"] == "FINAL"

    def test_final_to_approved(self, api):
        est = _create_estimation(api)
        _to_status(api, est["id"], "FINAL")
        resp = api.post(f"/api/estimations/{est['id']}/status",
                        json={"status": "APPROVED", "approved_by": "Manager Smith"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "APPROVED"
        assert data["approved_by"] == "Manager Smith"
        assert data["approved_at"] is not None

    def test_approved_to_revised(self, api):
        est = _create_estimation(api)
        _to_status(api, est["id"], "FINAL", "APPROVED")
        resp = api.post(f"/api/estimations/{est['id']}/status", json={"status": "REVISED"})
        data = resp.json()
        assert data["status"] == "REVISED"
        assert data["approved_by"] is None
        assert data["approved_at"] is None

    def test_revised_to_draft(self, api):
        est = _create_estimation(api)
        _to_status(api, est["id"], "REVISED")
        resp = api.post(f"/api/estimations/{est['id']}/status", json={"status": "DRAFT"})
        assert resp.json()["status"] == "DRAFT"

    def test_invalid_transition(self, api):
        est = _create_estimation(api)
        resp = api.post(f"/api/estimations/{est['id']}/status", json={"status": "APPROVED"})
        assert resp.status_code == 400
        assert resp.json()["detail"] == (
            "Invalid status transition: DRAFT -> APPROVED. Allowed: ['FINAL', 'REVISED']"
        )

    @pytest.mark.parametrize("path,target", [
        ([], "DRAFT"), (["FINAL"], "DRAFT"), (["FINAL", "APPROVED"], "FINAL"), (["REVISED"], "FINAL"),
    ])
    def test_more_invalid_transitions(self, api, path, target):
        est = _create_estimation(api)
        _to_status(api, est["id"], *path)
        resp = api.post(f"/api/estimations/{est['id']}/status", json={"status": target})
        assert resp.status_code == 400

    def test_status_not_found(self, api):
        assert api.post("/api/estimations/9999/status", json={"status": "FINAL"}).status_code == 404

    def test_status_requires_estimator(self, api, api_as, make_user):
        est = _create_estimation(api)
        viewer = api_as(make_user("VIEWER"))
        resp = viewer.post(f"/api/estimations/{est['id']}/status", json={"status": "FINAL"})
        assert resp.status_code == 403

    def test_notification_sent_and_failure_ignored(self, api):
        est = _create_estimation(api)
        with mock.patch(NOTIFY_STATUS, side_effect=RuntimeError("smtp down")) as notify:
            resp = api.post(f"/api/estimations/{est['id']}/status", json={"status": "FINAL"})
        assert resp.status_code == 200
        args, kwargs = notify.call_args
        assert args == (est["estimation_number"], "Test Project", "DRAFT", "FINAL", "Administrator",
                        est["grand_total_hours"])
        assert kwargs["assigned_user_id"] == est["assigned_to_id"]

    def test_export_to_originating_system(self, api):
        req = Request.objects.create(request_number="REQ-1", title="T", requester_name="R",
                                     received_date=date(2026, 1, 1), request_source="REDMINE",
                                     external_id="42")
        est = _create_estimation(api, request_id=req.id)
        with mock.patch("apps.integrations.service.sync_export") as export:
            _to_status(api, est["id"], "FINAL")
        system, payload = export.call_args.args
        assert system == "REDMINE"
        assert payload["external_id"] == "42" and payload["status"] == "FINAL"

    def test_no_export_for_manual_request(self, api):
        req = Request.objects.create(request_number="REQ-2", title="T", requester_name="R",
                                     received_date=date(2026, 1, 1), external_id="7")
        est = _create_estimation(api, request_id=req.id)
        with mock.patch("apps.integrations.service.sync_export") as export:
            _to_status(api, est["id"], "FINAL")
        export.assert_not_called()

    def test_outline_auto_export(self, api):
        config.set_value("outline_auto_export_states", "final, approved")
        est = _create_estimation(api)
        adapter = mock.Mock()
        with mock.patch("apps.integrations.service.get_adapter", return_value=adapter) as get_adapter:
            _to_status(api, est["id"], "REVISED")
            adapter.export_estimation.assert_not_called()
            _to_status(api, est["id"], "DRAFT", "FINAL")
        get_adapter.assert_called_with("OUTLINE")
        assert adapter.export_estimation.call_count == 1
        assert adapter.export_estimation.call_args.args[0]["project_name"] == "Test Project"

    def test_outline_failure_does_not_block(self, api):
        config.set_value("outline_auto_export_states", "FINAL")
        est = _create_estimation(api)
        with mock.patch("apps.integrations.service.get_adapter", side_effect=RuntimeError("boom")):
            resp = api.post(f"/api/estimations/{est['id']}/status", json={"status": "FINAL"})
        assert resp.status_code == 200

    def test_auto_create_historical_project_on_approve(self, api):
        config.set_value("auto_create_historical_project", "on_approve")
        est = _create_estimation(api)
        _to_status(api, est["id"], "FINAL")
        assert not HistoricalProject.objects.exists()
        _to_status(api, est["id"], "APPROVED", "REVISED", "DRAFT", "FINAL", "APPROVED")
        hp = HistoricalProject.objects.get()
        assert hp.estimation_id == est["id"] and hp.estimated_hours == est["grand_total_hours"]

    def test_manual_historical_rule_creates_nothing(self, api):
        est = _create_estimation(api)
        _to_status(api, est["id"], "FINAL", "APPROVED")
        assert not HistoricalProject.objects.exists()


# ── Draft editing ────────────────────────────────────────


class TestDraftEdit:
    def test_edit_draft_recalculates_in_place(self, api):
        est = _create_estimation(api)
        resp = api.put(f"/api/estimations/{est['id']}/draft", json={
            "project_name": "Edited", "project_type": "NEW", "feature_ids": _feature_ids(2),
            "dut_ids": [1, 2, 3], "team_size": 3,
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["project_name"] == "Edited"
        assert data["version"] == 1 and data["status"] == "DRAFT"
        assert data["dut_count"] == 3
        assert json.loads(data["wizard_inputs_json"])["team_size"] == 3
        assert not EstimationVersionSnapshot.objects.exists()
        assert AuditLog.objects.filter(action="EDIT_DRAFT_ESTIMATION", resource_id=est["id"]).exists()

    def test_edit_requires_draft(self, api):
        est = _create_estimation(api)
        _to_status(api, est["id"], "FINAL")
        resp = api.put(f"/api/estimations/{est['id']}/draft", json=MINIMAL)
        assert resp.status_code == 400
        assert resp.json()["detail"].startswith(
            "Only DRAFT estimations can be edited in place (current: FINAL)")

    def test_edit_by_other_estimator_forbidden(self, api, est_api):
        est = _create_estimation(api)
        resp = est_api.put(f"/api/estimations/{est['id']}/draft", json=MINIMAL)
        assert resp.status_code == 403
        assert resp.json()["detail"] == "Only the estimation's author or an ADMIN can edit this draft"

    def test_author_can_edit(self, est_api):
        est = _create_estimation(est_api)
        resp = est_api.put(f"/api/estimations/{est['id']}/draft", json=MINIMAL)
        assert resp.status_code == 200

    def test_edit_not_found(self, api):
        resp = api.put("/api/estimations/9999/draft", json={"project_name": "X", "project_type": "NEW"})
        assert resp.status_code == 404


# ── v3 TestEstimationVersioning + diff ───────────────────


class TestEstimationVersioning:
    def _create(self, api) -> dict:
        resp = api.post("/api/estimations", json={
            "project_name": "Versioning Test",
            "project_type": "EVOLUTION",
            "feature_ids": _feature_ids(2),
            "new_feature_ids": [],
            "dut_ids": [1, 2],
            "profile_ids": [1],
            "dut_profile_matrix": [[1, 1], [2, 1]],
            "pr_fixes": {"simple": 1, "medium": 0, "complex": 0},
            "team_size": 2,
            "has_leader": True,
            "working_days": 20,
        })
        assert resp.status_code == 201
        return resp.json()

    def _revise_payload(self) -> dict:
        return {
            "project_name": "Versioning Test (Revised)",
            "project_type": "EVOLUTION",
            "feature_ids": _feature_ids(3),
            "new_feature_ids": [],
            "dut_ids": [1, 2],
            "profile_ids": [1],
            "dut_profile_matrix": [[1, 1], [2, 1]],
            "pr_fixes": {"simple": 2, "medium": 1, "complex": 0},
            "team_size": 3,
            "has_leader": True,
            "working_days": 25,
        }

    def test_create_stores_wizard_inputs_and_version(self, api):
        est = self._create(api)
        assert est["version"] == 1
        inputs = json.loads(est["wizard_inputs_json"])
        assert inputs["team_size"] == 2
        assert inputs["has_leader"] is True
        assert len(inputs["feature_ids"]) == 2
        assert inputs["pr_fixes"]["simple"] == 1

    def test_revise_bumps_version(self, api):
        est = self._create(api)
        resp = api.post(f"/api/estimations/{est['id']}/status", json={"status": "REVISED"})
        assert resp.json()["status"] == "REVISED"
        revise = api.put(f"/api/estimations/{est['id']}/revise", json=self._revise_payload())
        assert revise.status_code == 200
        revised = revise.json()
        assert revised["version"] == 2
        assert revised["status"] == "DRAFT"
        assert revised["project_name"] == "Versioning Test (Revised)"
        assert revised["grand_total_hours"] > 0
        inputs = json.loads(revised["wizard_inputs_json"])
        assert inputs["team_size"] == 3
        assert inputs["working_days"] == 25
        assert len(inputs["feature_ids"]) == 3
        assert AuditLog.objects.filter(action="REVISE_ESTIMATION", details={"new_version": 2}).exists()

    def test_revise_requires_revised_status(self, api):
        est = self._create(api)
        resp = api.put(f"/api/estimations/{est['id']}/revise", json={
            "project_name": "Should Fail", "project_type": "EVOLUTION", "feature_ids": [1],
            "dut_ids": [1], "profile_ids": [1], "team_size": 1, "working_days": 10,
        })
        assert resp.status_code == 400
        assert "REVISED" in resp.json()["detail"]

    def test_outline_auto_export_config(self, api):
        assert Configuration.objects.filter(key="outline_auto_export_states").exists()

    def test_revise_creates_snapshot_and_diff(self, api):
        est = self._create(api)
        _to_status(api, est["id"], "FINAL", "APPROVED", "REVISED")
        api.put(f"/api/estimations/{est['id']}/revise", json=self._revise_payload())

        snap = EstimationVersionSnapshot.objects.get(estimation_id=est["id"])
        assert snap.version == 1
        assert snap.snapshot["project_name"] == "Versioning Test"
        assert snap.snapshot["status"] == "REVISED"
        assert snap.snapshot["wizard_inputs"]["team_size"] == 2

        versions = api.get(f"/api/estimations/{est['id']}/versions").json()
        assert [v["version"] for v in versions] == [1, 2]
        assert versions[0]["id"] == snap.id and "is_current" not in versions[0]
        assert versions[1]["id"] is None and versions[1]["is_current"] is True

        diff = api.get(f"/api/estimations/{est['id']}/versions/1/diff/2").json()
        assert diff["estimation_id"] == est["id"]
        fields = {c["field"]: c for c in diff["changes"]}
        assert fields["Project Name"] == {"field": "Project Name", "version_a": "Versioning Test",
                                          "version_b": "Versioning Test (Revised)"}
        assert "Grand Total Hours" in fields
        assert set(diff["input_changes"]) == {"feature_ids"}
        assert len(diff["input_changes"]["feature_ids"]["added"]) == 1
        assert {"added_tasks", "removed_tasks", "modified_tasks"} <= set(diff)

    def test_versions_without_snapshots(self, api):
        est = self._create(api)
        versions = api.get(f"/api/estimations/{est['id']}/versions").json()
        assert len(versions) == 1 and versions[0]["is_current"] is True

    def test_diff_unknown_version(self, api):
        est = self._create(api)
        resp = api.get(f"/api/estimations/{est['id']}/versions/1/diff/5")
        assert resp.status_code == 404
        assert resp.json()["detail"] == "Version 5 snapshot not found"

    def test_versions_not_found(self, api):
        assert api.get("/api/estimations/9999/versions").status_code == 404
        assert api.get("/api/estimations/9999/versions/1/diff/2").status_code == 404


# ── v3 TestEstimationReinit ──────────────────────────────


class TestEstimationReinit:
    def _make(self, api):
        return _create_estimation(api, project_name="Reinit Test", feature_ids=_feature_ids(3),
                                  dut_ids=[1, 2], profile_ids=[1], expected_delivery="2026-04-01")

    def test_reinit_rejects_wrong_token(self, api):
        self._make(api)
        resp = api.post("/api/estimations/reinit", json={"confirm": "nope"})
        assert resp.status_code == 400
        assert resp.json()["detail"] == "Confirmation token mismatch. Type 'REINITIALIZE' to proceed."
        assert len(api.get("/api/estimations").json()) >= 1

    def test_reinit_rejects_empty_token(self, api):
        assert api.post("/api/estimations/reinit", json={"confirm": ""}).status_code == 400

    def test_reinit_deletes_all_with_correct_token(self, api):
        self._make(api)
        self._make(api)
        resp = api.post("/api/estimations/reinit", json={"confirm": "  reinitialize "})
        assert resp.status_code == 200
        assert resp.json() == {"deleted": 2,
                               "message": "All estimations cleared (2 removed), ID counter reset to 1."}
        assert api.get("/api/estimations").json() == []
        assert AuditLog.objects.filter(action="ESTIMATION_REINIT").exists()

    def test_reinit_requires_auth(self, anon_api):
        assert anon_api.post("/api/estimations/reinit", json={"confirm": "REINITIALIZE"}).status_code == 401

    def test_reinit_requires_admin(self, est_api):
        assert est_api.post("/api/estimations/reinit", json={"confirm": "REINITIALIZE"}).status_code == 403


# ── v3 TestCalibration ───────────────────────────────────


class TestCalibration:
    def test_calibrate_with_reference(self, api):
        hist = HistoricalProject.objects.create(project_name="Past Project", project_type="EVOLUTION",
                                                estimated_hours=100, actual_hours=120,
                                                dut_count=2, profile_count=2)
        est = _create_estimation(api, project_name="Calibrated Project", project_type="EVOLUTION",
                                 reference_project_ids=[hist.id],
                                 pr_fixes={"simple": 5, "medium": 2, "complex": 1})
        resp = api.post(f"/api/estimations/{est['id']}/calibrate")
        assert resp.status_code == 200
        data = resp.json()
        assert data["accuracy_ratio"] == 1.2
        assert data["adjusted_grand_total"] > 0
        assert data["reference_projects"] == [{"project_name": "Past Project", "estimated_hours": 100.0,
                                               "actual_hours": 120.0, "accuracy_ratio": 1.2}]

    def test_calibrate_no_references(self, api):
        est = _create_estimation(api)
        resp = api.post(f"/api/estimations/{est['id']}/calibrate")
        assert resp.status_code == 400
        assert resp.json()["detail"] == "No reference projects linked to this estimation."

    def test_calibrate_reference_without_actuals(self, api):
        hist = HistoricalProject.objects.create(project_name="P", project_type="NEW", estimated_hours=50)
        est = _create_estimation(api, reference_project_ids=[hist.id])
        resp = api.post(f"/api/estimations/{est['id']}/calibrate")
        assert resp.json()["detail"] == "Reference projects lack estimated/actual hours data."

    def test_calibrate_missing_reference(self, api):
        est = _create_estimation(api, reference_project_ids=[999])
        resp = api.post(f"/api/estimations/{est['id']}/calibrate")
        assert resp.json()["detail"] == "No reference projects found."

    def test_calibrate_not_found(self, api):
        assert api.post("/api/estimations/9999/calibrate").status_code == 404


# ── Assignment ───────────────────────────────────────────


class TestAssign:
    def test_assign_notifies(self, api, estimator):
        est = _create_estimation(api)
        with mock.patch(NOTIFY_ASSIGN) as notify:
            resp = api.post(f"/api/estimations/{est['id']}/assign", params={"assigned_to_id": estimator.id})
        assert resp.status_code == 200
        assert resp.json() == {"status": "ok", "assigned_to_id": estimator.id}
        notify.assert_called_once_with(
            est["estimation_number"], "Test Project", estimator.id, "Administrator")
        assert api.get(f"/api/estimations/{est['id']}").json()["assigned_to_name"] == "Est One"
        assert AuditLog.objects.filter(action="ASSIGN", resource_id=est["id"]).exists()

    def test_assign_unknown_user(self, api):
        est = _create_estimation(api)
        resp = api.post(f"/api/estimations/{est['id']}/assign", params={"assigned_to_id": 999})
        assert resp.status_code == 404
        assert resp.json()["detail"] == "Target user not found"

    def test_assign_requires_approver(self, api, est_api, estimator):
        est = _create_estimation(api)
        resp = est_api.post(f"/api/estimations/{est['id']}/assign", params={"assigned_to_id": estimator.id})
        assert resp.status_code == 403

    def test_assign_estimation_not_found(self, api, estimator):
        resp = api.post("/api/estimations/9999/assign", params={"assigned_to_id": estimator.id})
        assert resp.status_code == 404


# ── Archive to history ───────────────────────────────────


class TestArchive:
    def test_archive_approved(self, api):
        fids = _feature_ids(2)
        est = _create_estimation(api, feature_ids=fids)
        _to_status(api, est["id"], "FINAL", "APPROVED")
        resp = api.post(f"/api/estimations/{est['id']}/archive")
        assert resp.status_code == 201
        data = resp.json()
        assert data["estimation_id"] == est["id"]
        assert data["estimated_hours"] == est["grand_total_hours"]
        assert json.loads(data["features_json"]) == fids
        assert data["notes"] == f"Archived from estimation {est['estimation_number']}"

    def test_archive_requires_approved(self, api):
        est = _create_estimation(api)
        resp = api.post(f"/api/estimations/{est['id']}/archive")
        assert resp.status_code == 400
        assert resp.json()["detail"] == "Only APPROVED estimations can be archived (current: DRAFT)"

    def test_archive_twice(self, api):
        est = _create_estimation(api)
        _to_status(api, est["id"], "FINAL", "APPROVED")
        api.post(f"/api/estimations/{est['id']}/archive")
        resp = api.post(f"/api/estimations/{est['id']}/archive")
        assert resp.status_code == 400
        assert resp.json()["detail"] == "This estimation has already been archived"

    def test_archive_requires_approver(self, api, est_api):
        est = _create_estimation(api)
        assert est_api.post(f"/api/estimations/{est['id']}/archive").status_code == 403


# ── Project-scoped features ──────────────────────────────


class TestProjectFeatures:
    def test_create_project_feature(self, api):
        est = _create_estimation(api)
        resp = api.post(f"/api/estimations/{est['id']}/features",
                        json={"name": "Custom eSIM", "category": "Custom", "base_effort_hours": 12})
        assert resp.status_code == 201
        data = resp.json()
        assert data["is_global"] is False
        assert data["owner_estimation_id"] == est["id"]
        assert data["task_templates"] == []
        assert Feature.objects.get(pk=data["id"]).owner_estimation_id == est["id"]

    def test_project_feature_counts_in_estimation(self, api):
        est = _create_estimation(api)
        fid = api.post(f"/api/estimations/{est['id']}/features",
                       json={"name": "Custom eSIM", "base_effort_hours": 12}).json()["id"]
        resp = api.put(f"/api/estimations/{est['id']}/draft",
                       json={"project_name": "P", "project_type": "NEW", "feature_ids": [fid]})
        task = next(t for t in resp.json()["tasks"] if t["feature_id"] == fid)
        assert task["task_name"] == "Custom eSIM" and task["calculated_hours"] == 12.0

    def test_same_name_global_feature_allowed(self, api):
        existing = Feature.objects.filter(is_global=True).first()
        est = _create_estimation(api)
        resp = api.post(f"/api/estimations/{est['id']}/features",
                        json={"name": existing.name, "category": existing.category})
        assert resp.status_code == 201

    def test_duplicate_in_project_conflicts(self, api):
        est = _create_estimation(api)
        api.post(f"/api/estimations/{est['id']}/features", json={"name": "Dup"})
        resp = api.post(f"/api/estimations/{est['id']}/features", json={"name": "Dup"})
        assert resp.status_code == 409
        assert resp.json()["detail"] == (
            "This project already has a feature 'Dup' in category '(no category)'.")

    def test_requires_draft(self, api):
        est = _create_estimation(api)
        _to_status(api, est["id"], "FINAL")
        resp = api.post(f"/api/estimations/{est['id']}/features", json={"name": "X"})
        assert resp.status_code == 400
        assert resp.json()["detail"] == (
            "Project features can only be added to a DRAFT estimation (current: FINAL)")

    def test_other_estimator_forbidden(self, api, est_api):
        est = _create_estimation(api)
        resp = est_api.post(f"/api/estimations/{est['id']}/features", json={"name": "X"})
        assert resp.status_code == 403

    def test_viewer_forbidden_and_not_found(self, api, api_as, make_user):
        assert api.post("/api/estimations/9999/features", json={"name": "X"}).status_code == 404
        est = _create_estimation(api)
        viewer = api_as(make_user("VIEWER"))
        assert viewer.post(f"/api/estimations/{est['id']}/features", json={"name": "X"}).status_code == 403

    def test_deleting_estimation_removes_project_features(self, api):
        est = _create_estimation(api)
        api.post(f"/api/estimations/{est['id']}/features", json={"name": "Gone"})
        api.delete(f"/api/estimations/{est['id']}")
        assert not Feature.objects.filter(name="Gone").exists()
