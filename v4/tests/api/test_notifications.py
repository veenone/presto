"""In-app notifications (/notifications/*) and the NotificationService."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from apps.core import config
from apps.notifications.models import WebhookNotification
from apps.notifications.service import NotificationService


def _notif(user, **extra) -> WebhookNotification:
    return WebhookNotification.objects.create(user=user, title=extra.pop("title", "N"), **extra)


class TestNotificationEndpoints:
    def test_list_own_unread_first(self, api, admin_user, make_user):
        read = _notif(admin_user, title="read", is_read=True)
        unread = _notif(admin_user, title="unread")
        _notif(make_user("VIEWER"), title="other user")
        resp = api.get("/api/notifications")
        assert resp.status_code == 200
        data = resp.json()
        assert [n["id"] for n in data] == [unread.id, read.id]
        assert data[0]["user_id"] == admin_user.id
        assert data[0]["source"] == "REDMINE"

    def test_list_limited_to_50(self, api, admin_user):
        WebhookNotification.objects.bulk_create(
            [WebhookNotification(user=admin_user, title=str(i)) for i in range(55)]
        )
        assert len(api.get("/api/notifications").json()) == 50

    def test_list_requires_auth(self, anon_api):
        assert anon_api.get("/api/notifications").status_code == 401

    def test_unread_count(self, api, admin_user):
        _notif(admin_user)
        _notif(admin_user)
        _notif(admin_user, is_read=True)
        assert api.get("/api/notifications/unread-count").json() == {"unread_count": 2}

    def test_mark_read(self, api, admin_user):
        n = _notif(admin_user)
        resp = api.put(f"/api/notifications/{n.id}/read")
        assert resp.status_code == 200
        assert resp.json() == {"ok": True}
        n.refresh_from_db()
        assert n.is_read is True

    def test_mark_read_other_users_notification_is_404(self, api, make_user):
        n = _notif(make_user("VIEWER"))
        resp = api.put(f"/api/notifications/{n.id}/read")
        assert resp.status_code == 404
        assert resp.json()["detail"] == "Notification not found"

    def test_mark_read_missing(self, api):
        assert api.put("/api/notifications/9999/read").status_code == 404

    def test_mark_all_read(self, api, admin_user, make_user):
        _notif(admin_user)
        other = _notif(make_user("VIEWER"))
        assert api.post("/api/notifications/mark-all-read").json() == {"ok": True}
        assert not WebhookNotification.objects.filter(user=admin_user, is_read=False).exists()
        other.refresh_from_db()
        assert other.is_read is False

    def test_test_email_not_configured(self, api):
        config.set_value("smtp_host", "")
        resp = api.post("/api/notifications/test-email")
        assert resp.status_code == 200
        assert resp.json()["success"] is False
        assert "not configured" in resp.json()["message"]

    def test_test_email_success(self, api):
        config.set_value("smtp_host", "smtp.example.com")
        config.set_value("smtp_port", "2525")
        config.set_value("smtp_user", "")
        with patch("apps.notifications.service.smtplib.SMTP") as smtp:
            resp = api.post("/api/notifications/test-email")
        assert resp.json() == {
            "success": True, "message": "Connected to smtp.example.com:2525 (no auth credentials set).",
        }
        smtp.assert_called_once_with("smtp.example.com", 2525, timeout=10)

    def test_test_email_requires_admin(self, api_as, make_user):
        assert api_as(make_user("APPROVER")).post("/api/notifications/test-email").status_code == 403


class TestNotificationService:
    def test_not_configured_sends_nothing(self, seeded):
        config.set_value("smtp_host", "")
        assert NotificationService().is_configured is False
        assert NotificationService().notify_request_imported("R", "T", "REDMINE", "PO") is False

    def test_status_changed_on_final_emails_creator_and_admins(self, seeded, make_user):
        config.set_value("smtp_host", "smtp.example.com")
        config.set_value("smtp_port", "25")
        config.set_value("smtp_tls", "false")
        seeded.email = "admin@example.com"
        seeded.save()
        creator = make_user("ESTIMATOR", email="creator@example.com")
        server = MagicMock()
        with patch("apps.notifications.service.smtplib.SMTP", return_value=server):
            ok = NotificationService().notify_estimation_status_changed(
                "EST-1", "P", "DRAFT", "FINAL", "Admin", 10.0, creator_user_id=creator.id,
            )
        assert ok is True
        _, recipients, _ = server.sendmail.call_args.args
        assert sorted(recipients) == ["admin@example.com", "creator@example.com"]

    def test_user_assigned_and_promotion(self, seeded, make_user):
        config.set_value("smtp_host", "smtp.example.com")
        config.set_value("smtp_port", "25")
        user = make_user("APPROVER", email="appr@example.com", display_name="Appr")
        server = MagicMock()
        with patch("apps.notifications.service.smtplib.SMTP", return_value=server):
            svc = NotificationService()
            assert svc.notify_user_assigned("EST-1", "P", user.id, "Admin") is True
            assert svc.notify_feature_promotion_requested("F", None, "Est") is True
            assert svc.notify_deadline_approaching("EST-1", "P", "2026-12-01", 2, assigned_user_id=user.id)
        assert server.sendmail.call_count == 3

    def test_send_failure_returns_false(self, seeded, make_user):
        config.set_value("smtp_host", "smtp.example.com")
        config.set_value("smtp_port", "25")
        user = make_user("VIEWER", email="v@example.com")
        with patch("apps.notifications.service.smtplib.SMTP", side_effect=OSError("refused")):
            assert NotificationService().notify_user_assigned("E", "P", user.id, "A") is False
