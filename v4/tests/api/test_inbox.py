"""Request inbox endpoints (/requests/*).

Ported from v3 test_api.py (TestRequestEndpoints, TestRequestInboxReinit) and
test_phase6_api.py (TestRequestDetail, TestAttachmentUpload), plus permission
and error cases.
"""

from __future__ import annotations

import json
from io import BytesIO
from itertools import count
from unittest.mock import MagicMock, patch

import pytest

from apps.accounts.models import AuditLog
from apps.estimations.models import Estimation
from apps.inbox.models import Request

_counter = count(1)


def _create_request(api, **extra) -> dict:
    payload = {
        "request_number": f"REQ_26/{next(_counter):04d}",
        "title": "Test Request",
        "requester_name": "John Doe",
        "requester_email": "john@example.com",
        "business_unit": "Testing",
        "priority": "HIGH",
        "received_date": "2026-02-27",
        **extra,
    }
    resp = api.post("/api/requests", json=payload)
    assert resp.status_code == 201, resp.content
    return resp.json()


@pytest.fixture(autouse=True)
def _attachments_dir(settings, tmp_path):
    settings.ATTACHMENTS_DIR = tmp_path / "attachments"


class TestRequestEndpoints:
    def test_create_request(self, api):
        resp = api.post("/api/requests", json={
            "request_number": "REQ_26/0001",
            "title": "Test Request",
            "requester_name": "John Doe",
            "received_date": "2026-02-26",
        })
        assert resp.status_code == 201
        data = resp.json()
        assert data["status"] == "NEW"
        assert data["request_number"] == "REQ_26/0001"
        assert data["attachments_json"] == "[]"
        assert data["request_source"] == "MANUAL"

    def test_list_requests(self, api):
        api.post("/api/requests", json={
            "request_number": "REQ_26/0002",
            "title": "Test",
            "requester_name": "Jane",
            "received_date": "2026-02-26",
        })
        resp = api.get("/api/requests")
        assert resp.status_code == 200
        assert len(resp.json()) >= 1

    def test_list_filter_by_status(self, api):
        a = _create_request(api)
        _create_request(api)
        api.put(f"/api/requests/{a['id']}", json={"status": "COMPLETED"})
        data = api.get("/api/requests", params={"status": "COMPLETED"}).json()
        assert [r["id"] for r in data] == [a["id"]]

    def test_create_requires_estimator(self, api_as, make_user):
        viewer = api_as(make_user("VIEWER"))
        resp = viewer.post("/api/requests", json={
            "request_number": "R1", "title": "T", "requester_name": "X", "received_date": "2026-01-01",
        })
        assert resp.status_code == 403

    def test_create_validation_error(self, api):
        resp = api.post("/api/requests", json={"title": "missing fields"})
        assert resp.status_code == 422

    def test_list_requires_auth(self, anon_api):
        assert anon_api.get("/api/requests").status_code == 401

    def test_get_request(self, api):
        req = _create_request(api)
        resp = api.get(f"/api/requests/{req['id']}")
        assert resp.status_code == 200
        assert resp.json()["title"] == "Test Request"

    def test_get_request_not_found(self, api):
        resp = api.get("/api/requests/9999")
        assert resp.status_code == 404
        assert resp.json() == {"detail": "Request not found"}

    def test_update_request(self, api):
        req = _create_request(api)
        resp = api.put(f"/api/requests/{req['id']}", json={"title": "New", "priority": "LOW"})
        assert resp.status_code == 200
        assert resp.json()["title"] == "New"
        assert resp.json()["priority"] == "LOW"
        assert resp.json()["requester_name"] == "John Doe"

    def test_update_not_found(self, api):
        assert api.put("/api/requests/9999", json={"title": "x"}).status_code == 404

    def test_update_requires_estimator(self, api, api_as, make_user):
        req = _create_request(api)
        resp = api_as(make_user("VIEWER")).put(f"/api/requests/{req['id']}", json={"title": "x"})
        assert resp.status_code == 403


class TestRequestInboxReinit:
    def test_reinit_rejects_wrong_token(self, api):
        _create_request(api)
        resp = api.post("/api/requests/reinit", json={"confirm": "nope"})
        assert resp.status_code == 400
        assert resp.json()["detail"] == "Confirmation token mismatch. Type 'REINITIALIZE' to proceed."
        assert len(api.get("/api/requests").json()) == 1

    def test_reinit_rejects_empty_token(self, api):
        assert api.post("/api/requests/reinit", json={"confirm": ""}).status_code == 400

    def test_reinit_deletes_all_with_correct_token(self, api):
        _create_request(api)
        _create_request(api)
        resp = api.post("/api/requests/reinit", json={"confirm": "REINITIALIZE"})
        assert resp.status_code == 200, resp.content
        assert resp.json()["deleted"] == 2
        assert resp.json()["message"] == "Request inbox cleared (2 removed), ID counter reset to 1."
        assert api.get("/api/requests").json() == []
        assert AuditLog.objects.filter(action="REQUEST_INBOX_REINIT").exists()

    def test_reinit_token_is_case_and_space_insensitive(self, api):
        _create_request(api)
        resp = api.post("/api/requests/reinit", json={"confirm": "  reinitialize  "})
        assert resp.status_code == 200
        assert resp.json()["deleted"] == 1

    def test_reinit_requires_auth(self, anon_api):
        resp = anon_api.post("/api/requests/reinit", json={"confirm": "REINITIALIZE"})
        assert resp.status_code == 401

    def test_reinit_requires_admin(self, api_as, make_user):
        resp = api_as(make_user("APPROVER")).post("/api/requests/reinit", json={"confirm": "REINITIALIZE"})
        assert resp.status_code == 403

    def test_reinit_keeps_estimations_and_resets_ids(self, api):
        req = _create_request(api)
        est = Estimation.objects.create(project_name="P", project_type="NEW", request_id=req["id"])
        api.post("/api/requests/reinit", json={"confirm": "REINITIALIZE"})
        est.refresh_from_db()
        assert est.request_id is None
        assert _create_request(api)["id"] == 1


class TestRequestDetail:
    def test_request_detail_with_estimations(self, api):
        req = _create_request(api)
        Estimation.objects.create(
            project_name="Linked Project", project_type="EVOLUTION", request_id=req["id"],
            wizard_inputs={"step": 3},
        )
        resp = api.get(f"/api/requests/{req['id']}/detail")
        assert resp.status_code == 200
        data = resp.json()
        assert data["title"] == "Test Request"
        assert len(data["estimations"]) == 1
        est = data["estimations"][0]
        assert est["project_name"] == "Linked Project"
        assert json.loads(est["wizard_inputs_json"]) == {"step": 3}

    def test_request_detail_no_estimations(self, api):
        req = _create_request(api)
        resp = api.get(f"/api/requests/{req['id']}/detail")
        assert resp.status_code == 200
        assert len(resp.json()["estimations"]) == 0

    def test_request_detail_not_found(self, api):
        assert api.get("/api/requests/9999/detail").status_code == 404


class TestAttachmentUpload:
    def test_upload_file(self, api, settings):
        req = _create_request(api)
        file_content = b"Hello, this is a test file."
        resp = api.post(
            f"/api/requests/{req['id']}/attachments",
            files={"file": ("test.txt", BytesIO(file_content), "text/plain")},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data == {"status": "ok", "filename": "test.txt", "size": len(file_content)}

        attachments = json.loads(api.get(f"/api/requests/{req['id']}").json()["attachments_json"])
        assert len(attachments) == 1
        assert attachments[0]["filename"] == "test.txt"
        assert attachments[0]["mime_type"] == "text/plain"
        stored = settings.ATTACHMENTS_DIR / req["request_number"] / "test.txt"
        assert stored.read_bytes() == file_content

    def test_upload_to_nonexistent_request(self, api):
        resp = api.post(
            "/api/requests/9999/attachments",
            files={"file": ("test.txt", BytesIO(b"data"), "text/plain")},
        )
        assert resp.status_code == 404

    def test_upload_requires_estimator(self, api, api_as, make_user):
        req = _create_request(api)
        resp = api_as(make_user("VIEWER")).post(
            f"/api/requests/{req['id']}/attachments", files={"file": ("a.txt", b"x", "text/plain")},
        )
        assert resp.status_code == 403

    def test_upload_filename_cannot_escape_directory(self, api, settings):
        req = _create_request(api, request_number="REQ-SAFE")
        resp = api.post(
            f"/api/requests/{req['id']}/attachments",
            files={"file": ("../../evil.txt", b"x", "text/plain")},
        )
        assert resp.status_code == 200
        assert (settings.ATTACHMENTS_DIR / "REQ-SAFE" / "evil.txt").exists()


class TestAssignRequest:
    def test_assign(self, api, make_user):
        req = _create_request(api)
        user = make_user("ESTIMATOR", display_name="Est One")
        resp = api.post(f"/api/requests/{req['id']}/assign", params={"assigned_to_id": user.id})
        assert resp.status_code == 200
        assert resp.json() == {"status": "ok", "assigned_to_id": user.id}
        data = api.get(f"/api/requests/{req['id']}").json()
        assert data["assigned_to_id"] == user.id
        assert data["assigned_to_name"] == "Est One"
        assert AuditLog.objects.filter(action="ASSIGN", resource_type="request").exists()

    def test_assign_request_not_found(self, api, admin_user):
        resp = api.post("/api/requests/9999/assign", params={"assigned_to_id": admin_user.id})
        assert resp.status_code == 404
        assert resp.json()["detail"] == "Request not found"

    def test_assign_user_not_found(self, api):
        req = _create_request(api)
        resp = api.post(f"/api/requests/{req['id']}/assign", params={"assigned_to_id": 9999})
        assert resp.status_code == 404
        assert resp.json()["detail"] == "Target user not found"

    def test_assign_requires_approver(self, api, api_as, make_user, admin_user):
        req = _create_request(api)
        resp = api_as(make_user("ESTIMATOR")).post(
            f"/api/requests/{req['id']}/assign", params={"assigned_to_id": admin_user.id},
        )
        assert resp.status_code == 403

    def test_assign_syncs_redmine_assignee(self, api, make_user):
        req = Request.objects.create(
            request_number="REQ-R", title="T", requester_name="X", received_date="2026-01-01",
            request_source="REDMINE", external_id="42",
        )
        user = make_user("ESTIMATOR", username="jdoe")
        adapter = MagicMock()
        with patch("apps.integrations.service.get_adapter", return_value=adapter):
            resp = api.post(f"/api/requests/{req.id}/assign", params={"assigned_to_id": user.id})
        assert resp.status_code == 200
        adapter.update_assignee.assert_called_once_with("42", "jdoe")

    def test_assign_ignores_redmine_failure(self, api, make_user):
        req = Request.objects.create(
            request_number="REQ-R2", title="T", requester_name="X", received_date="2026-01-01",
            request_source="REDMINE", external_id="43",
        )
        user = make_user("ESTIMATOR")
        adapter = MagicMock()
        adapter.update_assignee.side_effect = RuntimeError("down")
        with patch("apps.integrations.service.get_adapter", return_value=adapter):
            resp = api.post(f"/api/requests/{req.id}/assign", params={"assigned_to_id": user.id})
        assert resp.status_code == 200
