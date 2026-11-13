from __future__ import annotations

from django.db import models

from apps.core.models import created_at_field


class Team(models.Model):
    name = models.CharField(max_length=255, unique=True)
    description = models.TextField(null=True, blank=True)
    created_at = created_at_field()

    class Meta:
        db_table = "teams"
        ordering = ["name"]

    def __str__(self) -> str:
        return self.name


class TeamMember(models.Model):
    name = models.CharField(max_length=255)
    role = models.CharField(max_length=64)
    available_hours_per_day = models.FloatField(default=7.0)
    skills = models.JSONField(default=list, db_column="skills_json")
    team = models.ForeignKey(
        Team, null=True, blank=True, on_delete=models.SET_NULL,
        related_name="members", db_index=False,
    )

    class Meta:
        db_table = "team_members"
        ordering = ["name"]

    def __str__(self) -> str:
        return self.name
