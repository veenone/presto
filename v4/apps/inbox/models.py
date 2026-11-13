"""Request inbox (v3 ``requests`` table)."""

from __future__ import annotations

from django.conf import settings
from django.db import models

from apps.core.models import created_at_field, updated_at_field


class Request(models.Model):
    request_number = models.CharField(max_length=64, unique=True)
    request_source = models.CharField(max_length=32, default="MANUAL")
    external_id = models.CharField(max_length=255, null=True, blank=True)
    title = models.CharField(max_length=500)
    description = models.TextField(null=True, blank=True)
    requester_name = models.CharField(max_length=255)
    requester_email = models.CharField(max_length=254, null=True, blank=True)
    business_unit = models.CharField(max_length=255, null=True, blank=True)
    priority = models.CharField(max_length=16, default="MEDIUM")
    status = models.CharField(max_length=32, default="NEW")
    requested_delivery_date = models.DateField(null=True, blank=True)
    received_date = models.DateField()
    attachments = models.JSONField(default=list, db_column="attachments_json")
    assigned_to = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
        related_name="assigned_requests", db_index=False,
    )
    notes = models.TextField(null=True, blank=True)
    product_type = models.CharField(max_length=128, null=True, blank=True)
    created_at = created_at_field()
    updated_at = updated_at_field()

    class Meta:
        db_table = "requests"
        ordering = ["-received_date", "-id"]

    def __str__(self) -> str:
        return f"{self.request_number} {self.title}"
