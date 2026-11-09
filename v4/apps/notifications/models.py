from __future__ import annotations

from django.conf import settings
from django.db import models

from apps.core.models import created_at_field


class WebhookNotification(models.Model):
    """In-app notification shown under the sidebar bell."""

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
        related_name="notifications", db_index=True,  # v3: ix_webhook_notifications_user_id
    )
    title = models.CharField(max_length=255)
    message = models.TextField(default="", blank=True)
    source = models.CharField(max_length=32, default="REDMINE")
    request = models.ForeignKey(
        "inbox.Request", null=True, blank=True, on_delete=models.SET_NULL,
        related_name="notifications", db_index=False,
    )
    is_read = models.BooleanField(default=False)
    created_at = created_at_field()

    class Meta:
        db_table = "webhook_notifications"
        ordering = ["-created_at", "-id"]
