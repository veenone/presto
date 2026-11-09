"""API routes: /notifications/*."""

from __future__ import annotations

from ninja import Router
from ninja.errors import HttpError

from apps.api.auth import authenticated, role
from apps.api.schemas.notifications import EmailTestOut, OkOut, UnreadCountOut, WebhookNotificationOut
from apps.notifications.models import WebhookNotification

router = Router(auth=authenticated, tags=["notifications"])


@router.get("/notifications", response=list[WebhookNotificationOut])
def list_notifications(request):
    """The current user's notifications: unread first, newest first, at most 50."""
    return list(
        WebhookNotification.objects.filter(user=request.auth).order_by("is_read", "-created_at", "-id")[:50]
    )


@router.get("/notifications/unread-count", response=UnreadCountOut)
def unread_notification_count(request):
    return {"unread_count": WebhookNotification.objects.filter(user=request.auth, is_read=False).count()}


@router.put("/notifications/{notification_id}/read", response=OkOut)
def mark_notification_read(request, notification_id: int):
    """Mark one of the current user's notifications as read."""
    updated = WebhookNotification.objects.filter(pk=notification_id, user=request.auth).update(is_read=True)
    if not updated:
        raise HttpError(404, "Notification not found")
    return {"ok": True}


@router.post("/notifications/test-email", response=EmailTestOut, auth=role("ADMIN"))
def test_email_connection(request):
    """Open an SMTP connection with the smtp_* settings and log in."""
    from apps.notifications.service import NotificationService

    ok, message = NotificationService().test_connection()
    return {"success": ok, "message": message}


@router.post("/notifications/mark-all-read", response=OkOut)
def mark_all_notifications_read(request):
    WebhookNotification.objects.filter(user=request.auth, is_read=False).update(is_read=True)
    return {"ok": True}
