"""Schemas for features, presets, task templates, DUTs, profiles and registries (v3 ``api/schemas.py``)."""

from __future__ import annotations

from datetime import date, datetime

from ninja import Schema
from pydantic import AliasChoices, BaseModel, Field

from apps.api.common import JsonText

# ── Features ──────────────────────────────────────────────


class FeatureBase(BaseModel):
    name: str
    category: str | None = None
    complexity_weight: float = 1.0
    has_existing_tests: bool = False
    description: str | None = None
    product_type: str | None = None
    study_effort_hours: float | None = None
    base_effort_hours: float = 0.0


class FeatureCreate(FeatureBase):
    pass


class FeatureUpdate(BaseModel):
    name: str | None = None
    category: str | None = None
    complexity_weight: float | None = None
    has_existing_tests: bool | None = None
    description: str | None = None
    product_type: str | None = None
    study_effort_hours: float | None = None
    base_effort_hours: float | None = None


class FeaturePresetCreate(BaseModel):
    name: str
    description: str | None = None
    product_type: str | None = None
    feature_ids: list[int] = []


class FeaturePresetUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    product_type: str | None = None
    feature_ids: list[int] | None = None


class FeaturePresetOut(Schema):
    id: int
    name: str
    description: str | None = None
    product_type: str | None = None
    feature_ids: list[int] = []
    owner_user_id: int | None = None
    owner_name: str | None = None


class TaskTemplateOut(Schema):
    id: int
    feature_id: int | None = None
    feature_ids: list[int] = []
    feature_hours: dict[str, float] = {}  # {feature_id_str: base_hours_override}
    name: str
    task_type: str
    base_effort_hours: float
    scales_with_dut: bool
    scales_with_profile: bool
    is_parallelizable: bool
    is_pr_fix: bool = False
    description: str | None = None
    product_type: str | None = None


class FeatureOut(Schema):
    id: int
    name: str
    category: str | None = None
    complexity_weight: float = 1.0
    has_existing_tests: bool = False
    description: str | None = None
    product_type: str | None = None
    study_effort_hours: float | None = None
    base_effort_hours: float = 0.0
    is_global: bool = True
    owner_estimation_id: int | None = None
    promotion_requested: bool = False
    created_at: datetime | None = None
    task_templates: list[TaskTemplateOut] = []


# ── Task templates ────────────────────────────────────────

class TaskTemplateCreate(BaseModel):
    feature_id: int | None = None  # legacy single-feature link
    feature_ids: list[int] = []
    feature_hours: dict[str, float] = {}
    name: str
    task_type: str
    base_effort_hours: float
    scales_with_dut: bool = False
    scales_with_profile: bool = False
    is_parallelizable: bool = False
    is_pr_fix: bool = False
    description: str | None = None
    product_type: str | None = None


class TaskTemplateUpdate(BaseModel):
    name: str | None = None
    task_type: str | None = None
    base_effort_hours: float | None = None
    scales_with_dut: bool | None = None
    scales_with_profile: bool | None = None
    is_parallelizable: bool | None = None
    is_pr_fix: bool | None = None
    description: str | None = None
    product_type: str | None = None
    feature_ids: list[int] | None = None
    feature_hours: dict[str, float] | None = None


# ── DUT types ─────────────────────────────────────────────

class DutTypeBase(BaseModel):
    name: str
    category: str | None = None
    complexity_multiplier: float = 1.0
    product_type: str | None = None


class DutTypeCreate(DutTypeBase):
    pass


class DutTypeUpdate(BaseModel):
    name: str | None = None
    category: str | None = None
    complexity_multiplier: float | None = None
    product_type: str | None = None


class DutTypeOut(Schema):
    id: int
    name: str
    category: str | None = None
    complexity_multiplier: float = 1.0
    product_type: str | None = None


class DutReinitOut(Schema):
    status: str
    message: str


# ── Test profiles ─────────────────────────────────────────

class TestProfileBase(BaseModel):
    name: str
    description: str | None = None
    effort_multiplier: float = 1.0
    product_type: str | None = None
    is_active: bool = True


class TestProfileCreate(TestProfileBase):
    pass


class TestProfileUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    effort_multiplier: float | None = None
    product_type: str | None = None
    is_active: bool | None = None


class TestProfileOut(Schema):
    id: int
    name: str
    description: str | None = None
    effort_multiplier: float = 1.0
    product_type: str | None = None
    is_active: bool = True


# ── Historical projects ──────────────────────────────────

class HistoricalProjectCreate(BaseModel):
    project_name: str
    project_type: str
    estimated_hours: float | None = None
    actual_hours: float | None = None
    dut_count: int | None = None
    profile_count: int | None = None
    pr_count: int | None = None
    features_json: str = "[]"
    completion_date: date | None = None
    notes: str | None = None
    estimation_id: int | None = None


class HistoricalProjectOut(Schema):
    id: int
    project_name: str
    project_type: str
    estimated_hours: float | None = None
    actual_hours: float | None = None
    dut_count: int | None = None
    profile_count: int | None = None
    pr_count: int | None = None
    features_json: JsonText = Field("[]", validation_alias=AliasChoices("features_json", "features"))
    completion_date: date | None = None
    notes: str | None = None
    estimation_id: int | None = None


# ── Task presets ─────────────────────────────────────────

class TaskPresetCreate(BaseModel):
    name: str
    product_type: str | None = None
    description: str | None = None
    task_template_ids: list[int] = []


class TaskPresetUpdate(BaseModel):
    name: str | None = None
    product_type: str | None = None
    description: str | None = None
    task_template_ids: list[int] | None = None


class TaskPresetOut(Schema):
    id: int
    name: str
    product_type: str | None = None
    description: str | None = None
    task_template_ids: list[int] = []
    created_at: datetime | None = None


# ── Document types ──────────────────────────────────────

class DocumentTypeCreate(BaseModel):
    name: str
    description: str | None = None
    category: str = "Report"
    base_effort_hours: float = 4.0
    task_template_id: int | None = None


class DocumentTypeUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    category: str | None = None
    base_effort_hours: float | None = None
    is_active: bool | None = None
    task_template_id: int | None = None


class DocumentTypeOut(Schema):
    id: int
    name: str
    description: str | None = None
    category: str
    base_effort_hours: float
    is_active: bool
    task_template_id: int | None = None
    task_template_name: str | None = None
    created_at: datetime | None = None


# ── Risk items ──────────────────────────────────────────

class RiskItemCreate(BaseModel):
    name: str
    category: str = "General"
    description: str | None = None
    likelihood: str = "MEDIUM"
    impact: str = "MEDIUM"
    mitigation: str | None = None


class RiskItemUpdate(BaseModel):
    name: str | None = None
    category: str | None = None
    description: str | None = None
    likelihood: str | None = None
    impact: str | None = None
    mitigation: str | None = None
    is_active: bool | None = None


class RiskItemOut(Schema):
    id: int
    name: str
    category: str
    description: str | None = None
    likelihood: str
    impact: str
    mitigation: str | None = None
    is_active: bool = True
    created_at: datetime | None = None
