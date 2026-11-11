"""Request detail page: access, fields, markup, attachments, edit dialog, assignment and linked estimations."""

from __future__ import annotations

import json
from datetime import date
from unittest import mock

import pytest

from apps.accounts.models import AuditLog
from apps.inbox import services
from apps.inbox.models import Request
from tests.reports.factories import make_estimation

pytestmark = pytest.mark.django_db
HX = {"HX-Request": "true"}
HX_MODAL = {"HX-Request": "true", "HX-Target": "modal"}
HX_REGION = {"HX-Request": "true", "HX-Target": "req-detail"}


def events(resp) -> dict:
    return json.loads(resp.get("HX-Trigger") or "{}")


@pytest.fixture(autouse=True)
def _attachments_dir(settings, tmp_path):
    settings.ATTACHMENTS_DIR = tmp_path / "attachments"


@pytest.fixture
def as_role(web_login, make_user, seeded):
    def _as(role: str, **extra):
        return web_login(make_user(role, **extra))
    return _as


@pytest.fixture
def req(seeded):
    return Request.objects.create(
        request_number="REQ_26/0001", title="SIM Toolkit v2.5 validation", requester_name="Product Office",
        priority="HIGH", status="NEW", received_date=date(2026, 10, 20), business_unit="Payments",
        description="h2. Scope\n*bold* {color:red}urgent{color}\n<script>alert(1)</script>",
        notes="Call before Friday",
    )


# ── Page ─────────────────────────────────────────────────


class TestDetailPage:
    def test_renders_fields_and_safe_markup(self, admin_client, req):
        resp = admin_client.get(f"/requests/{req.id}")
        assert resp.status_code == 200
        body = resp.content.decode()
        assert "Request #REQ_26/0001" in body and "Product Office" in body and "Payments" in body
        assert "2026-10-20" in body and "Call before Friday" in body
        assert 'class="badge inb-badge inb-pri-HIGH"' in body and 'class="badge inb-badge inb-status-NEW"' in body
        assert '<span style="color:red">urgent</span>' in body
        assert "<strong>bold</strong>" in body or "<em>bold</em>" in body
        assert "<script>alert(1)</script>" not in body and "&lt;script&gt;" in body
        assert "No attachments." in body and "No estimations linked to this request yet." in body

    def test_unknown_id_404(self, admin_client, seeded):
        assert admin_client.get("/requests/99999").status_code == 404

    def test_viewer_refused(self, as_role, req):
        assert as_role("VIEWER").get(f"/requests/{req.id}").status_code == 403

    def test_region_partial_for_refresh(self, admin_client, req):
        resp = admin_client.get(f"/requests/{req.id}", headers=HX_REGION)
        body = resp.content.decode()
        assert 'id="req-detail"' in body and "<html" not in body
        assert 'hx-trigger="crud-refresh from:body"' in body

    def test_admin_sees_all_actions(self, admin_client, req):
        body = admin_client.get(f"/requests/{req.id}").content.decode()
        assert 'data-testid="edit-request"' in body
        assert f'href="/estimation/new?request_id={req.id}"' in body
        assert f'hx-post="/requests/{req.id}/attachments"' in body
        assert f'hx-post="/requests/{req.id}/assign"' in body

    def test_estimator_hides_assign(self, as_role, req):
        body = as_role("ESTIMATOR").get(f"/requests/{req.id}").content.decode()
        assert 'data-testid="edit-request"' in body and f'hx-post="/requests/{req.id}/attachments"' in body
        assert f'hx-post="/requests/{req.id}/assign"' not in body
        assert "Not assigned yet." in body

    def test_read_only_viewer_with_matrix_permission(self, as_role, req):
        from apps.core import config
        config.set_value("rbac_matrix", json.dumps({"VIEWER": ["view_requests"]}))
        body = as_role("VIEWER").get(f"/requests/{req.id}").content.decode()
        assert 'data-testid="edit-request"' not in body and "/estimation/new?request_id=" not in body
        assert "/attachments\"" not in body and "/assign\"" not in body

    def test_linked_estimations(self, admin_client, seeded):
        est = make_estimation(with_request=True, project_name="SIM Toolkit v2.5")
        body = admin_client.get(f"/requests/{est.request_id}").content.decode()
        assert f'href="/estimation/{est.id}"' in body and "SIM Toolkit v2.5" in body
        assert "EST-2026-0001" in body
        assert "est-status-DRAFT" in body


# ── Edit dialog ──────────────────────────────────────────


class TestEdit:
    def test_form_prefilled(self, admin_client, req):
        resp = admin_client.get(f"/requests/{req.id}/edit", headers=HX)
        assert resp.status_code == 200
        body = resp.content.decode()
        assert "Edit Request" in body and 'value="SIM Toolkit v2.5 validation"' in body
        assert '<option value="IN_ESTIMATION">' in body

    def test_save_changes_fields_and_status(self, as_role, req):
        resp = as_role("ESTIMATOR").post(f"/requests/{req.id}/edit", {
            "title": "New title", "description": "", "priority": "LOW", "status": "IN_ESTIMATION",
            "requester_name": "PO", "requester_email": "po@example.test", "business_unit": "",
            "product_type": "", "requested_delivery_date": "2026-12-01", "notes": "",
        }, headers=HX)
        assert resp.status_code == 204, resp.content
        ev = events(resp)
        assert ev["crud-refresh"] and ev["close-modal"] and ev["toast"]["message"] == "Request updated."
        req.refresh_from_db()
        assert (req.title, req.priority, req.status, req.requester_email) == (
            "New title", "LOW", "IN_ESTIMATION", "po@example.test")
        assert req.description is None and req.business_unit is None
        assert req.requested_delivery_date == date(2026, 12, 1)

    def test_validation_error(self, admin_client, req):
        resp = admin_client.post(f"/requests/{req.id}/edit", {
            "title": "", "priority": "LOW", "status": "BOGUS", "requester_name": "PO",
        }, headers=HX)
        assert resp.status_code == 422
        body = resp.content.decode()
        assert "This field is required." in body and "Select a valid choice." in body
        req.refresh_from_db()
        assert req.title == "SIM Toolkit v2.5 validation"

    def test_deleted_upstream_status_kept_selectable(self, admin_client, req):
        Request.objects.filter(pk=req.pk).update(status="DELETED_UPSTREAM")
        body = admin_client.get(f"/requests/{req.id}/edit", headers=HX).content.decode()
        assert '<option value="DELETED_UPSTREAM" selected>' in body

    def test_refused(self, as_role, req):
        from apps.core import config
        config.set_value("rbac_matrix", json.dumps({"VIEWER": ["view_requests"]}))
        assert as_role("VIEWER").post(f"/requests/{req.id}/edit", {"title": "x"}).status_code == 403

    def test_404(self, admin_client, req):
        assert admin_client.get("/requests/99999/edit").status_code == 404


# ── Assignment ───────────────────────────────────────────


class TestAssign:
    def test_inline_assign(self, as_role, make_user, req):
        target = make_user("ESTIMATOR", username="bob", display_name="Bob B")
        resp = as_role("APPROVER").post(f"/requests/{req.id}/assign", {"assigned_to_id": target.id}, headers=HX)
        assert resp.status_code == 204
        ev = events(resp)
        assert ev["toast"]["message"] == "Request assigned to Bob B." and ev["crud-refresh"]
        assert "close-modal" not in ev
        req.refresh_from_db()
        assert req.assigned_to_id == target.id
        assert AuditLog.objects.filter(action="ASSIGN", resource_type="request", resource_id=req.id).exists()

    def test_modal_assign_from_list(self, admin_client, make_user, req):
        target = make_user("ESTIMATOR", username="amy")
        resp = admin_client.get(f"/requests/{req.id}/assign", headers=HX)
        assert resp.status_code == 200 and "Assign REQ_26/0001" in resp.content.decode()
        resp = admin_client.post(f"/requests/{req.id}/assign", {"assigned_to_id": target.id}, headers=HX_MODAL)
        assert resp.status_code == 204 and events(resp)["close-modal"]

    def test_errors(self, admin_client, req):
        resp = admin_client.post(f"/requests/{req.id}/assign", {"assigned_to_id": ""}, headers=HX)
        assert resp.status_code == 400 and resp.json()["detail"] == "Please select a user."
        resp = admin_client.post(f"/requests/{req.id}/assign", {"assigned_to_id": "9999"}, headers=HX_MODAL)
        assert resp.status_code == 422 and "Select a valid choice." in resp.content.decode()

    def test_redmine_assignee_push(self, admin_client, make_user, req):
        Request.objects.filter(pk=req.pk).update(request_source="REDMINE", external_id="55")
        target = make_user("ESTIMATOR", username="carl")
        adapter = mock.Mock()
        with mock.patch("apps.integrations.service.get_adapter", return_value=adapter):
            assert admin_client.post(f"/requests/{req.id}/assign", {"assigned_to_id": target.id}).status_code == 204
        adapter.update_assignee.assert_called_once_with("55", "carl")

    def test_estimator_refused(self, as_role, req):
        assert as_role("ESTIMATOR").post(f"/requests/{req.id}/assign", {"assigned_to_id": 1}).status_code == 403

    def test_404(self, admin_client, req):
        assert admin_client.post("/requests/99999/assign", {"assigned_to_id": 1}).status_code == 404


# ── Attachments ──────────────────────────────────────────


def _upload(client, req, name="spec.txt", content=b"hello", ctype="text/plain"):
    from django.core.files.uploadedfile import SimpleUploadedFile
    return client.post(f"/requests/{req.id}/attachments",
                       {"file": SimpleUploadedFile(name, content, ctype)}, headers=HX)


class TestAttachments:
    def test_upload_then_list_and_download(self, as_role, settings, req):
        client = as_role("ESTIMATOR")
        resp = _upload(client, req)
        assert resp.status_code == 204
        assert events(resp)["toast"]["message"] == "Uploaded spec.txt (5 bytes)." and events(resp)["crud-refresh"]
        assert (settings.ATTACHMENTS_DIR / "REQ_26" / "0001" / "spec.txt").read_bytes() == b"hello"

        body = client.get(f"/requests/{req.id}").content.decode()
        assert "spec.txt" in body and f'href="/requests/{req.id}/attachments/0"' in body and "text/plain" in body

        dl = client.get(f"/requests/{req.id}/attachments/0")
        assert dl.status_code == 200
        assert b"".join(dl.streaming_content) == b"hello"
        assert 'attachment; filename="spec.txt"' in dl["Content-Disposition"]
        assert dl["Content-Type"] == "text/plain"

    def test_upload_strips_path_components(self, admin_client, settings, req):
        assert _upload(admin_client, req, name="../../evil.txt").status_code == 204
        req.refresh_from_db()
        assert req.attachments[0]["filename"] == "evil.txt"
        assert (settings.ATTACHMENTS_DIR / "REQ_26" / "0001" / "evil.txt").exists()

    def test_upload_without_file(self, admin_client, req):
        resp = admin_client.post(f"/requests/{req.id}/attachments", {}, headers=HX)
        assert resp.status_code == 400 and resp.json()["detail"] == "Choose a file to upload."

    def test_upload_refused(self, as_role, req):
        from apps.core import config
        config.set_value("rbac_matrix", json.dumps({"VIEWER": ["view_requests"]}))
        assert _upload(as_role("VIEWER"), req).status_code == 403

    def test_upload_404(self, admin_client, req):
        assert admin_client.post("/requests/99999/attachments").status_code == 404

    def test_traversal_in_stored_path_rejected(self, admin_client, settings, tmp_path, req):
        secret = tmp_path / "secret.txt"
        secret.write_text("top secret")
        Request.objects.filter(pk=req.pk).update(attachments=[
            {"filename": "../../secret.txt", "filepath": str(secret)},
            {"filename": "x", "filepath": str(settings.ATTACHMENTS_DIR / ".." / "secret.txt")},
        ])
        assert admin_client.get(f"/requests/{req.id}/attachments/0").status_code == 404
        assert admin_client.get(f"/requests/{req.id}/attachments/1").status_code == 404

    def test_download_unknown_index_or_missing_file(self, admin_client, req):
        assert admin_client.get(f"/requests/{req.id}/attachments/0").status_code == 404
        Request.objects.filter(pk=req.pk).update(attachments=[{"filename": "gone.txt", "filepath": "/nope/gone.txt"}])
        assert admin_client.get(f"/requests/{req.id}/attachments/0").status_code == 404
        assert admin_client.get(f"/requests/{req.id}/attachments/5").status_code == 404
        assert admin_client.get("/requests/99999/attachments/0").status_code == 404

    def test_download_falls_back_to_current_root(self, admin_client, settings, req):
        """A stored absolute path from an older location still resolves under the current root by name."""
        services.save_attachment(req, "a.txt", b"abc", "text/plain")
        req.refresh_from_db()
        att = dict(req.attachments[0], filepath="/old/location/a.txt")
        Request.objects.filter(pk=req.pk).update(attachments=[att])
        resp = admin_client.get(f"/requests/{req.id}/attachments/0")
        assert resp.status_code == 200 and b"".join(resp.streaming_content) == b"abc"

    def test_download_refused_for_viewer(self, as_role, req):
        services.save_attachment(req, "a.txt", b"abc", "text/plain")
        assert as_role("VIEWER").get(f"/requests/{req.id}/attachments/0").status_code == 403

    def test_external_attachment_links_out(self, admin_client, req):
        Request.objects.filter(pk=req.pk).update(attachments=[
            {"filename": "log.zip", "external_url": "https://tracker.example.test/a/1", "source": "REDMINE"},
            {"filename": "bad", "external_url": "javascript:alert(1)"},
        ])
        body = admin_client.get(f"/requests/{req.id}").content.decode()
        assert 'href="https://tracker.example.test/a/1"' in body and "javascript:alert" not in body


class TestNumbering:
    def test_next_request_number_skips_taken(self, seeded):
        year = date.today().year
        assert services.next_request_number() == f"REQ-{year}-001"
        Request.objects.create(request_number=f"REQ-{year}-001", title="t", requester_name="r",
                               received_date=date.today())
        Request.objects.create(request_number=f"REQ-{year}-002", title="t", requester_name="r",
                               received_date=date.today())
        Request.objects.filter(request_number=f"REQ-{year}-001").update(request_number="OTHER")
        assert services.next_request_number() == f"REQ-{year}-003"
