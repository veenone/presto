from __future__ import annotations

from django.db import models


class IntegrationConfig(models.Model):
    """Connection settings for REDMINE, JIRA, EMAIL, OUTLINE, SNIPE_IT."""

    system_name = models.CharField(max_length=32, unique=True)
    base_url = models.CharField(max_length=500, null=True, blank=True)
    api_key = models.CharField(max_length=500, null=True, blank=True)
    username = models.CharField(max_length=255, null=True, blank=True)
    additional_config = models.JSONField(default=dict, db_column="additional_config_json")
    enabled = models.BooleanField(default=False)
    last_sync_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "integration_config"
        ordering = ["system_name"]

    def __str__(self) -> str:
        return self.system_name
