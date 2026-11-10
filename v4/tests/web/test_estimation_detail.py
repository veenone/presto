"""Estimation detail page (/estimation/<id>): rendering, tabs and role-dependent controls."""

from __future__ import annotations

import pytest

from apps.catalog.models import TaskTemplate
from apps.estimations.models import Estimation, EstimationVersionSnapshot, HistoricalProject
from apps.estimations.pages.markup import render_description
from apps.estimations.versions import snapshot_estimation
from tests.reports.factories import make_document_type, make_estimation

pytestmark = pytest.mark.django_db
HX_REGION = {"HX-Request": "true", "HX-Target": "est-detail"}

PR_DETAILS = [
    {"pr_number": "PR-101", "link": "https://tracker.example/PR-101", "priority": "HIGH", "complexity": "medium",
     "status": "Open", "test_available": False,
     "description": "## Steps\n- open **menu**\n{color:#de350b}red text{color}\n<script>alert(1)</script>"},
    {"pr_number": "PR-102", "link": "javascript:alert(1)", "complexity": "simple", "test_available": True,
     "description": ""},
]


def rich_estimation(**fields) -> Estimation:
    tmpl = TaskTemplate.objects.order_by("id").first()
    doc_a = make_document_type("Test Plan", 6.0, linked=tmpl)
    doc_b = make_document_type("Test Report", 4.0, linked=tmpl)
    doc_c = make_document_type("Release Note", 2.0)
    est = make_estimation(
        with_request=True, with_team=True, with_risk=True,
        wizard_extra={
            "pr_details": PR_DETAILS,
            "pr_fixes": {"simple": 1, "medium": 1, "complex": 0},
            "document_type_ids": [doc_a.id, doc_b.id, doc_c.id],
            "document_counts": {str(doc_a.id): 2},
        },
        **fields,
    )
    Estimation.objects.filter(pk=est.pk).update(
        elapsed_hours=50, elapsed_days=7.1, elapsed_weeks=1.4, documentation_hours=12.0, pr_no_test_hours=8.0,
        project_goals="Ship <it>", target_customer="Bank A")
    est.refresh_from_db()
    return est


@pytest.fixture
def est(seeded):
    return rich_estimation()


@pytest.fixture
def as_role(web_login, make_user):
    def _as(role: str, **extra):
        return web_login(make_user(role, **extra))
    return _as


class TestPage:
    def test_404_for_unknown_id(self, admin_client, seeded):
        resp = admin_client.get("/estimation/99999")
        assert resp.status_code == 404 and b"Not Found" in resp.content

    def test_every_role_can_view(self, as_role, est):
        for role in ("VIEWER", "ESTIMATOR", "APPROVER", "ADMIN"):
            assert as_role(role).get(f"/estimation/{est.id}").status_code == 200, role

    def test_header_card(self, admin_client, est):
        body = admin_client.get(f"/estimation/{est.id}").content.decode()
        assert "EST-2026-0001" in body and "Report Test" in body
        assert 'data-testid="grand-total">90<' in body
        assert "hours · 12.5 days · 2.5 weeks" in body
        assert "FEASIBLE · DRAFT" in body and "ed-status-FEASIBLE ed-status-DRAFT" in body
        assert "est-stripe-FEASIBLE" in body
        assert "1 DUT · 1 profile · 1 combinations" in body

    def test_tabs_and_counts(self, admin_client, est):
        body = admin_client.get(f"/estimation/{est.id}").content.decode()
        for label in ("Overview", "Tasks (6)", "Documents (3)", "PR Fixes (1)", "Team (1)", "Risks (1)",
                      "Versions (1)", "Workflow"):
            assert label in body, label

    def test_overview(self, admin_client, est):
        ctx = admin_client.get(f"/estimation/{est.id}").context
        assert [b["label"] for b in ctx["bars"]] == [
            "Tester Hours", "PR Fix Hours", "PR Test Creation", "Documentation", "Buffer"]
        body = admin_client.get(f"/estimation/{est.id}").content.decode()
        assert "Proposed Duration · Wall Clock" in body and "Elapsed Weeks" in body
        assert "Ship &lt;it&gt;" in body and "Target Customer" in body
        assert "Supply: Late hardware" in body  # risk chip

    def test_tasks_include_synthetic_rows(self, admin_client, est):
        resp = admin_client.get(f"/estimation/{est.id}")
        names = [t["task_name"] for t in resp.context["tasks"]]
        assert "Test Creation (PR without existing tests)" in names
        assert any("(additional documentation)" in n for n in names)
        assert "Documentation effort" in names
        body = resp.content.decode()
        assert "1 PR(s) without tests: PR-101" in body and "est-synthetic" in body

    def test_documents_with_overlap_note(self, admin_client, est):
        body = admin_client.get(f"/estimation/{est.id}").content.decode()
        assert "Deducted" in body and "already in task" in body
        assert "Release Note" in body and "est-overlap" in body

    def test_pr_details(self, admin_client, est):
        body = admin_client.get(f"/estimation/{est.id}").content.decode()
        assert "<details>" in body and "PR-101" in body
        assert '<span style="color:#de350b">red text</span>' in body
        assert "<strong>menu</strong>" in body
        assert "<script>alert(1)</script>" not in body and "&lt;script&gt;" in body
        assert 'href="javascript:' not in body  # unsafe links are shown as text only
        assert 'class="badge est-no">No<' in body and 'class="badge est-yes">Yes<' in body
        assert "Test Creation" in body  # PR test-creation tile

    def test_team_and_risks(self, admin_client, est):
        body = admin_client.get(f"/estimation/{est.id}").content.decode()
        assert "Alice" in body and "1 member · 40.0h" in body
        assert "Late hardware" in body and "est-level-HIGH" in body
        assert "Automatic Risk Assessment" in body

    def test_region_partial(self, admin_client, est):
        resp = admin_client.get(f"/estimation/{est.id}", headers=HX_REGION)
        body = resp.content.decode()
        assert resp.status_code == 200 and body.lstrip().startswith('<div id="est-detail"')
        assert "<html" not in body

    def test_empty_sections(self, admin_client, seeded):
        bare = Estimation.objects.create(project_name="Bare", project_type="NEW")
        body = admin_client.get(f"/estimation/{bare.id}").content.decode()
        for text in ("No task breakdown available", "No document deliverables", "No PR fixes",
                     "No team allocations", "No risks selected from the registry"):
            assert text in body, text
        assert "EST-" not in body.split("<h1")[0].split("Estimation</span>")[-1][:40]


class TestRoleControls:
    def test_viewer_sees_no_write_actions(self, as_role, est):
        body = as_role("VIEWER").get(f"/estimation/{est.id}").content.decode()
        for marker in ("/status\"", "Export Tasks", "Export to External", "Send by email", "Reassign to",
                       "Resume editing"):
            assert marker not in body, marker
        assert "Changing the status needs the Estimator role" in body
        # read-only actions stay available
        for marker in ("/report/xlsx", "/report/docx", "/report/pdf", "/report/executive-summary",
                       "Compare with another estimation", "Calibrate"):
            assert marker in body, marker

    def test_estimator_controls(self, as_role, est):
        body = as_role("ESTIMATOR").get(f"/estimation/{est.id}").content.decode()
        assert "Move to FINAL" in body and "Move to REVISED" in body
        assert "Export Tasks" in body and "Export to External" in body and "Send by email" in body
        assert "Reassign to" not in body  # APPROVER+
        assert "Resume editing" in body  # creator unknown -> anyone with the role may edit the draft

    def test_resume_editing_only_for_author_or_admin(self, as_role, make_user, est):
        author = make_user("ESTIMATOR", username="author")
        Estimation.objects.filter(pk=est.pk).update(creator=author)
        assert "Resume editing" not in as_role("ESTIMATOR").get(f"/estimation/{est.id}").content.decode()
        assert "Resume editing" in as_role("ADMIN").get(f"/estimation/{est.id}").content.decode()

    def test_edit_link_for_revised(self, as_role, est):
        Estimation.objects.filter(pk=est.pk).update(status="REVISED")
        body = as_role("ESTIMATOR").get(f"/estimation/{est.id}").content.decode()
        assert f'href="/estimation/{est.id}/edit"' in body and "Move to DRAFT" in body
        for status in ("FINAL", "APPROVED"):
            Estimation.objects.filter(pk=est.pk).update(status=status)
            body = as_role("ADMIN").get(f"/estimation/{est.id}").content.decode()
            assert f'href="/estimation/{est.id}/edit"' not in body

    def test_export_external_only_with_request(self, admin_client, seeded):
        plain = make_estimation(estimation_number="EST-X")
        assert "Export to External" not in admin_client.get(f"/estimation/{plain.id}").content.decode()

    def test_assign_for_approver(self, as_role, est):
        body = as_role("APPROVER").get(f"/estimation/{est.id}").content.decode()
        assert "Reassign to" in body and "No assignee set" in body

    def test_archive_card(self, as_role, est):
        Estimation.objects.filter(pk=est.pk).update(status="APPROVED")
        assert "Archive to History" not in as_role("ESTIMATOR").get(f"/estimation/{est.id}").content.decode()
        assert "Archive to History" in as_role("APPROVER").get(f"/estimation/{est.id}").content.decode()
        HistoricalProject.objects.create(project_name="Report Test", project_type="NEW", estimation=est)
        body = as_role("APPROVER").get(f"/estimation/{est.id}").content.decode()
        assert "Archive to History" not in body and "Archived to" in body

    def test_no_transitions_message(self, admin_client, est):
        Estimation.objects.filter(pk=est.pk).update(status="ARCHIVED")
        assert "No further transitions from ARCHIVED" in admin_client.get(f"/estimation/{est.id}").content.decode()


class TestVersions:
    def test_versions_tab_and_compare_button(self, admin_client, est):
        EstimationVersionSnapshot.objects.create(estimation=est, version=1, snapshot=snapshot_estimation(est))
        Estimation.objects.filter(pk=est.pk).update(version=2, grand_total_hours=120)
        body = admin_client.get(f"/estimation/{est.id}").content.decode()
        assert "Compare (2 versions)" in body and "Versions (2)" in body and "v2" in body
        assert f'hx-get="/estimation/{est.id}/versions/diff"' in body

    def test_single_version_hint(self, admin_client, est):
        body = admin_client.get(f"/estimation/{est.id}").content.decode()
        assert "Only one version so far" in body and "Compare (" not in body


class TestMarkup:
    def test_escapes_and_renders(self):
        html = render_description('# Title\n1. one\n2. two\n\n`<b>` **b** _i_ [x](https://a.b/c) '
                                  '{color:red;x:y}bad{color} {color:#0f0}ok{color} 2{^}nd{^}')
        assert "<h4>Title</h4>" in html and "<ol><li>one</li><li>two</li></ol>" in html
        assert "<code>&lt;b&gt;</code>" in html and "<strong>b</strong>" in html and "<em>i</em>" in html
        assert '<a href="https://a.b/c" target="_blank" rel="noopener noreferrer">x</a>' in html
        assert "red;x:y" not in html and "bad" in html
        assert '<span style="color:#0f0">ok</span>' in html and "<sup>nd</sup>" in html

    def test_attribute_injection_is_escaped(self):
        html = render_description('[x](https://a.b/"onmouseover="alert(1)) https://c.d/"><img src=x>')
        assert "<img" not in html and 'onmouseover="' not in html

    def test_empty(self):
        assert render_description(None) == "" and render_description("  ") == ""
