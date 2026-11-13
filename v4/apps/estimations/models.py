"""Estimations, their breakdowns, version snapshots and historical projects."""

from __future__ import annotations

from django.conf import settings
from django.db import models

from apps.core.models import created_at_field


class Estimation(models.Model):
    request = models.ForeignKey(
        "inbox.Request", null=True, blank=True, on_delete=models.SET_NULL,
        related_name="estimations", db_index=False,
    )
    estimation_number = models.CharField(max_length=64, unique=True, null=True, blank=True)
    project_name = models.CharField(max_length=255)
    project_type = models.CharField(max_length=32)
    reference_project_ids = models.JSONField(default=list)
    dut_count = models.IntegerField(default=0)
    profile_count = models.IntegerField(default=0)
    dut_profile_combinations = models.IntegerField(default=0)
    pr_fix_count = models.IntegerField(default=0)
    start_date = models.DateField(null=True, blank=True)
    expected_delivery = models.DateField(null=True, blank=True)
    total_tester_hours = models.FloatField(default=0)
    total_leader_hours = models.FloatField(default=0)
    pr_fix_hours = models.FloatField(default=0)
    study_hours = models.FloatField(default=0)
    buffer_hours = models.FloatField(default=0)
    grand_total_hours = models.FloatField(default=0)
    grand_total_days = models.FloatField(default=0)
    elapsed_hours = models.FloatField(default=0, db_default=0)
    elapsed_days = models.FloatField(default=0, db_default=0)
    elapsed_weeks = models.FloatField(default=0, db_default=0)
    estimated_completion_date = models.DateField(null=True, blank=True)
    feasibility_status = models.CharField(max_length=16, default="FEASIBLE")
    status = models.CharField(max_length=16, default="DRAFT")
    created_at = created_at_field()
    # v3 keeps both a display name and a user FK for creator and approver.
    created_by = models.CharField(max_length=255, null=True, blank=True)
    creator = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
        related_name="created_estimations", db_column="created_by_id", db_index=False,
    )
    approved_by = models.CharField(max_length=255, null=True, blank=True)
    approver = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
        related_name="approved_estimations", db_column="approved_by_id", db_index=False,
    )
    approved_at = models.DateTimeField(null=True, blank=True)
    assigned_to = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
        related_name="assigned_estimations", db_index=False,
    )
    version = models.IntegerField(default=1)
    wizard_inputs = models.JSONField(default=dict, db_column="wizard_inputs_json")
    expected_releases = models.IntegerField(default=1)
    release_extra_hours = models.FloatField(default=0)
    documentation_hours = models.FloatField(default=0)
    pr_no_test_hours = models.FloatField(default=0)
    project_goals = models.TextField(null=True, blank=True)
    target_customer = models.TextField(null=True, blank=True)
    project_reference = models.CharField(max_length=255, null=True, blank=True)
    team = models.ForeignKey(
        "teams.Team", null=True, blank=True, on_delete=models.SET_NULL,
        related_name="estimations", db_index=False,
    )

    class Meta:
        db_table = "estimations"
        ordering = ["-created_at", "-id"]

    def __str__(self) -> str:
        return f"{self.estimation_number or self.pk} {self.project_name}"


class EstimationTask(models.Model):
    estimation = models.ForeignKey(
        Estimation, on_delete=models.CASCADE, related_name="tasks", db_index=False,
    )
    task_template = models.ForeignKey(
        "catalog.TaskTemplate", null=True, blank=True, on_delete=models.SET_NULL,
        related_name="estimation_tasks", db_index=False,
    )
    task_name = models.CharField(max_length=255)
    task_type = models.CharField(max_length=32)
    base_hours = models.FloatField(default=0)
    calculated_hours = models.FloatField(default=0)
    assigned_testers = models.IntegerField(default=1)
    has_leader_support = models.BooleanField(default=False)
    leader_hours = models.FloatField(default=0)
    is_new_feature_study = models.BooleanField(default=False)
    formula = models.CharField(max_length=500, null=True, blank=True)
    notes = models.TextField(null=True, blank=True)
    # Plain columns in v3 (no FK): the feature may be deleted later.
    feature_id = models.IntegerField(null=True, blank=True)
    feature_name = models.CharField(max_length=255, null=True, blank=True)

    class Meta:
        db_table = "estimation_tasks"
        ordering = ["id"]


class EstimationTeamAllocation(models.Model):
    estimation = models.ForeignKey(
        Estimation, on_delete=models.CASCADE, related_name="team_allocations", db_index=False,
    )
    team_member = models.ForeignKey(
        "teams.TeamMember", on_delete=models.CASCADE, related_name="allocations", db_index=False,
    )
    role = models.CharField(max_length=64, null=True, blank=True)
    allocated_hours = models.FloatField(default=0)

    class Meta:
        db_table = "estimation_team_allocations"


class EstimationRisk(models.Model):
    estimation = models.ForeignKey(
        Estimation, on_delete=models.CASCADE, related_name="risks", db_index=False,
    )
    risk_item = models.ForeignKey(
        "catalog.RiskItem", on_delete=models.CASCADE, related_name="estimation_risks", db_index=False,
    )
    notes = models.TextField(null=True, blank=True)

    class Meta:
        db_table = "estimation_risks"


class EstimationVersionSnapshot(models.Model):
    """Immutable snapshot of an estimation at a given version (for diffs)."""

    estimation = models.ForeignKey(
        Estimation, on_delete=models.CASCADE, # v3 index: ix_estimation_version_snapshots_estimation_id
        related_name="version_snapshots", db_index=True,
    )
    version = models.IntegerField()
    snapshot = models.JSONField(default=dict, db_column="snapshot_json")
    created_at = created_at_field()

    class Meta:
        db_table = "estimation_version_snapshots"
        ordering = ["estimation", "version"]


class HistoricalProject(models.Model):
    """Completed project used for calibration (actual vs estimated hours)."""

    project_name = models.CharField(max_length=255)
    project_type = models.CharField(max_length=32)
    estimated_hours = models.FloatField(null=True, blank=True)
    actual_hours = models.FloatField(null=True, blank=True)
    dut_count = models.IntegerField(null=True, blank=True)
    profile_count = models.IntegerField(null=True, blank=True)
    pr_count = models.IntegerField(null=True, blank=True)
    features = models.JSONField(default=list, db_column="features_json")
    completion_date = models.DateField(null=True, blank=True)
    notes = models.TextField(null=True, blank=True)
    estimation = models.ForeignKey(
        Estimation, null=True, blank=True, on_delete=models.SET_NULL,
        related_name="historical_records", db_index=False,
    )

    class Meta:
        db_table = "historical_projects"
        ordering = ["-completion_date", "-id"]

    def __str__(self) -> str:
        return self.project_name
