"""Catalog models: features, task templates, presets, DUTs, profiles, registries."""

from __future__ import annotations

from django.conf import settings
from django.db import models
from django.db.models import Q

from apps.core.models import created_at_field


class Feature(models.Model):
    """A testable capability. Global (catalog) or owned by one estimation."""

    name = models.CharField(max_length=255)
    category = models.CharField(max_length=128, null=True, blank=True)
    complexity_weight = models.FloatField(default=1.0, db_default=1.0)
    has_existing_tests = models.BooleanField(default=False, db_default=False)
    description = models.TextField(null=True, blank=True)
    product_type = models.CharField(max_length=128, null=True, blank=True)
    study_effort_hours = models.FloatField(null=True, blank=True)
    # Baseline effort for features not driven by a linked template (0 = templates only).
    base_effort_hours = models.FloatField(default=0.0, db_default=0.0)
    is_global = models.BooleanField(default=True, db_default=True)
    owner_estimation = models.ForeignKey(
        "estimations.Estimation", null=True, blank=True, on_delete=models.CASCADE,
        related_name="owned_features", db_index=True,  # v3 index: ix_features_owner_estimation_id
    )
    # Estimator asked an approver to promote this project feature to the catalog.
    promotion_requested = models.BooleanField(default=False, db_default=False)
    created_at = created_at_field(null=True, blank=True)

    class Meta:
        db_table = "features"
        ordering = ["category", "name"]
        constraints = [
            # Global features are unique by (name, category); project features are exempt.
            models.UniqueConstraint(
                fields=["name", "category"], condition=Q(is_global=True),
                name="uq_features_global_name_category",
            ),
        ]

    def __str__(self) -> str:
        return self.name


class FeaturePreset(models.Model):
    """Saved feature selection, bound to a product type (estimator self-service)."""

    name = models.CharField(max_length=255)
    description = models.TextField(null=True, blank=True)
    product_type = models.CharField(max_length=128, null=True, blank=True)
    feature_ids = models.JSONField(default=list, db_column="feature_ids_json")
    owner_user = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
        related_name="feature_presets", db_index=False,
    )
    created_at = created_at_field()

    class Meta:
        db_table = "feature_presets"
        ordering = ["name"]

    def __str__(self) -> str:
        return self.name


class TaskTemplate(models.Model):
    # Legacy one-to-many link kept from v3; the many-to-many link is TaskTemplateFeature.
    feature = models.ForeignKey(
        Feature, null=True, blank=True, on_delete=models.CASCADE,
        related_name="owned_templates", db_index=False,
    )
    name = models.CharField(max_length=255)
    task_type = models.CharField(max_length=32)
    base_effort_hours = models.FloatField()
    scales_with_dut = models.BooleanField(default=False)
    scales_with_profile = models.BooleanField(default=False)
    is_parallelizable = models.BooleanField(default=False)
    is_pr_fix = models.BooleanField(default=False, db_default=False)
    description = models.TextField(null=True, blank=True)
    product_type = models.CharField(max_length=128, null=True, blank=True)

    class Meta:
        db_table = "task_templates"
        ordering = ["name"]

    def __str__(self) -> str:
        return self.name


class TaskTemplateFeature(models.Model):
    """Template <-> feature link with an optional per-feature hours override."""

    pk = models.CompositePrimaryKey("task_template_id", "feature_id")
    task_template = models.ForeignKey(
        TaskTemplate, on_delete=models.CASCADE, related_name="feature_links", db_index=False,
    )
    feature = models.ForeignKey(
        Feature, on_delete=models.CASCADE, related_name="template_links", db_index=False,
    )
    base_hours_override = models.FloatField(null=True, blank=True)

    class Meta:
        db_table = "task_template_features"


class TaskPreset(models.Model):
    name = models.CharField(max_length=255, unique=True)
    product_type = models.CharField(max_length=128, null=True, blank=True)
    description = models.TextField(null=True, blank=True)
    task_template_ids = models.JSONField(default=list, db_column="task_template_ids_json")
    created_at = created_at_field()

    class Meta:
        db_table = "task_presets"
        ordering = ["name"]

    def __str__(self) -> str:
        return self.name


class DocumentType(models.Model):
    """Document deliverable; may overlap a task template (hours deducted)."""

    name = models.CharField(max_length=255, unique=True)
    description = models.TextField(null=True, blank=True)
    category = models.CharField(max_length=64, default="Report")
    base_effort_hours = models.FloatField(default=4.0)
    is_active = models.BooleanField(default=True)
    task_template = models.ForeignKey(
        TaskTemplate, null=True, blank=True, on_delete=models.SET_NULL,
        related_name="document_types", db_index=False,
    )
    created_at = created_at_field()

    class Meta:
        db_table = "document_types"
        ordering = ["name"]

    def __str__(self) -> str:
        return self.name


class DutType(models.Model):
    name = models.CharField(max_length=255, unique=True)
    category = models.CharField(max_length=128, null=True, blank=True)
    complexity_multiplier = models.FloatField(default=1.0)
    product_type = models.CharField(max_length=128, null=True, blank=True)

    class Meta:
        db_table = "dut_types"
        ordering = ["name"]

    def __str__(self) -> str:
        return self.name


class TestProfile(models.Model):
    name = models.CharField(max_length=255, unique=True)
    description = models.TextField(null=True, blank=True)
    effort_multiplier = models.FloatField(default=1.0)
    product_type = models.CharField(max_length=128, null=True, blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        db_table = "test_profiles"
        ordering = ["name"]

    def __str__(self) -> str:
        return self.name


class RiskItem(models.Model):
    name = models.CharField(max_length=255, unique=True)
    category = models.CharField(max_length=64, default="General")
    description = models.TextField(null=True, blank=True)
    likelihood = models.CharField(max_length=16, default="MEDIUM")
    impact = models.CharField(max_length=16, default="MEDIUM")
    mitigation = models.TextField(null=True, blank=True)
    is_active = models.BooleanField(default=True)
    created_at = created_at_field()

    class Meta:
        db_table = "risk_items"
        ordering = ["name"]

    def __str__(self) -> str:
        return self.name


class PublicHoliday(models.Model):
    date = models.DateField()
    name = models.CharField(max_length=255)
    country = models.CharField(max_length=64, default="", blank=True)
    is_recurring = models.BooleanField(default=False)

    class Meta:
        db_table = "public_holidays"
        ordering = ["date"]

    def __str__(self) -> str:
        return f"{self.date} {self.name}"
