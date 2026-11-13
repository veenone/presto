"""Catalog services: features, presets, task templates, DUTs, profiles, registries.

Ports the v3 ``/features``, ``/feature-presets``, ``/task-templates``,
``/task-presets``, ``/dut-types``, ``/profiles``, ``/risk-items``,
``/document-types`` and ``/historical-projects`` logic. Failures raise
``ServiceError`` with the v3 status code and message.
"""

from __future__ import annotations

import logging
from typing import Any

from django.db import IntegrityError, connection, transaction
from django.db.models import Q

from apps.accounts.models import User
from apps.accounts.roles import has_role
from apps.core.services import ServiceError
from apps.estimations.models import Estimation, HistoricalProject

from .models import (
    DocumentType,
    DutType,
    Feature,
    FeaturePreset,
    RiskItem,
    TaskPreset,
    TaskTemplate,
    TaskTemplateFeature,
    TestProfile,
)

logger = logging.getLogger(__name__)


def _product_filter(qs, product_type: str | None):
    """Rows for ``product_type`` plus type-agnostic rows (v3 filter)."""
    if product_type:
        qs = qs.filter(Q(product_type=product_type) | Q(product_type__isnull=True))
    return qs


def _apply(obj, updates: dict[str, Any]) -> None:
    for key, val in updates.items():
        setattr(obj, key, val)


# ── Feature presets ──────────────────────────────────────

def preset_out(p: FeaturePreset) -> dict:
    try:
        fids = [int(x) for x in (p.feature_ids or [])]
    except (TypeError, ValueError):
        fids = []
    owner = p.owner_user
    return {
        "id": p.id, "name": p.name, "description": p.description, "product_type": p.product_type,
        "feature_ids": fids, "owner_user_id": p.owner_user_id,
        "owner_name": (owner.display_name or owner.username) if owner else None,
    }


def list_feature_presets(product_type: str | None = None) -> list[dict]:
    out = [preset_out(p) for p in FeaturePreset.objects.select_related("owner_user").order_by("name")]
    if product_type:
        out = [p for p in out if not p["product_type"] or p["product_type"] == product_type]
    return out


def create_feature_preset(user: User, name: str | None, description: str | None,
                          product_type: str | None, feature_ids: list[int]) -> dict:
    name = (name or "").strip()
    if not name:
        raise ServiceError(400, "Preset name is required.")
    if not feature_ids:
        raise ServiceError(400, "A preset must contain at least one feature.")
    preset = FeaturePreset.objects.create(
        name=name,
        description=(description or "").strip() or None,
        product_type=product_type or None,
        feature_ids=sorted({int(f) for f in feature_ids}),
        owner_user=user,
    )
    return preset_out(preset)


def _get_preset_for_owner(preset_id: int, user: User, verb: str) -> FeaturePreset:
    preset = FeaturePreset.objects.select_related("owner_user").filter(pk=preset_id).first()
    if preset is None:
        raise ServiceError(404, "Preset not found")
    if user.role != "ADMIN" and preset.owner_user_id is not None and preset.owner_user_id != user.id:
        raise ServiceError(403, f"Only the preset's owner or an ADMIN can {verb} it.")
    return preset


def update_feature_preset(preset_id: int, user: User, updates: dict[str, Any]) -> dict:
    preset = _get_preset_for_owner(preset_id, user, "edit")
    if updates.get("name") is not None:
        name = updates["name"].strip()
        if not name:
            raise ServiceError(400, "Preset name cannot be empty.")
        preset.name = name
    if updates.get("description") is not None:
        preset.description = updates["description"].strip() or None
    if updates.get("product_type") is not None:
        preset.product_type = updates["product_type"] or None
    if updates.get("feature_ids") is not None:
        if not updates["feature_ids"]:
            raise ServiceError(400, "A preset must contain at least one feature.")
        preset.feature_ids = sorted({int(f) for f in updates["feature_ids"]})
    preset.save()
    return preset_out(preset)


def delete_feature_preset(preset_id: int, user: User) -> None:
    _get_preset_for_owner(preset_id, user, "delete").delete()


# ── Task templates ───────────────────────────────────────

def _linked_feature_ids(tmpl: TaskTemplate) -> list[int]:
    return [lnk.feature_id for lnk in tmpl.feature_links.all()]


def template_out(tmpl: TaskTemplate, with_hours: bool = True) -> dict:
    """v3 ``TaskTemplateOut``; nested under a feature, ``feature_hours`` stays empty (as v3)."""
    links = list(tmpl.feature_links.all())
    feature_ids = [lnk.feature_id for lnk in links]
    feature_id = tmpl.feature_id
    if feature_ids and feature_id is None:
        feature_id = feature_ids[0] if len(feature_ids) == 1 else None
    hours = (
        {str(lnk.feature_id): lnk.base_hours_override for lnk in links if lnk.base_hours_override is not None}
        if with_hours else {}
    )
    return {
        "id": tmpl.id, "feature_id": feature_id, "feature_ids": feature_ids, "feature_hours": hours,
        "name": tmpl.name, "task_type": tmpl.task_type, "base_effort_hours": tmpl.base_effort_hours,
        "scales_with_dut": tmpl.scales_with_dut, "scales_with_profile": tmpl.scales_with_profile,
        "is_parallelizable": tmpl.is_parallelizable, "is_pr_fix": tmpl.is_pr_fix,
        "description": tmpl.description, "product_type": tmpl.product_type,
    }


def list_task_templates(feature_id: int | None = None, product_type: str | None = None) -> list[dict]:
    qs = TaskTemplate.objects.order_by("id").prefetch_related("feature_links")
    if feature_id is not None:
        qs = qs.filter(feature_links__feature_id=feature_id).distinct()
    return [template_out(t) for t in _product_filter(qs, product_type)]


def _check_feature_ids(fids: list[int]) -> None:
    known = set(Feature.objects.filter(pk__in=fids).values_list("id", flat=True))
    unknown = sorted(set(fids) - known)
    if unknown:
        raise ServiceError(400, f"Unknown feature id(s): {', '.join(map(str, unknown))}")


def _set_feature_links(tmpl: TaskTemplate, fids: list[int], feature_hours: dict[str, float]) -> None:
    _check_feature_ids(fids)
    TaskTemplateFeature.objects.filter(task_template=tmpl).delete()
    TaskTemplateFeature.objects.bulk_create([
        TaskTemplateFeature(
            task_template=tmpl, feature_id=fid, base_hours_override=feature_hours.get(str(fid)),
        )
        for fid in dict.fromkeys(fids)
    ])


@transaction.atomic
def create_task_template(data: dict[str, Any]) -> dict:
    fids = data.pop("feature_ids", None) or []
    hours = data.pop("feature_hours", None) or {}
    legacy = data.pop("feature_id", None)
    if not fids and legacy:
        fids = [legacy]
    data["feature_id"] = fids[0] if len(fids) == 1 else None
    if data["feature_id"] is not None:
        _check_feature_ids(fids)
    tmpl = TaskTemplate.objects.create(**data)
    if fids:
        _set_feature_links(tmpl, fids, hours)
    return template_out(tmpl)


@transaction.atomic
def update_task_template(template_id: int, updates: dict[str, Any]) -> dict:
    tmpl = TaskTemplate.objects.filter(pk=template_id).first()
    if tmpl is None:
        raise ServiceError(404, "Task template not found")
    fids = updates.pop("feature_ids", None)
    hours = updates.pop("feature_hours", None)
    _apply(tmpl, updates)
    if fids is not None:
        _set_feature_links(tmpl, fids, hours or {})
        tmpl.feature_id = fids[0] if len(fids) == 1 else None
    tmpl.save()
    return template_out(tmpl)


def delete_task_template(template_id: int) -> None:
    tmpl = TaskTemplate.objects.filter(pk=template_id).first()
    if tmpl is None:
        raise ServiceError(404, "Task template not found")
    tmpl.delete()


# ── Features ─────────────────────────────────────────────

def _feature_qs():
    return Feature.objects.prefetch_related("template_links__task_template__feature_links")


def feature_out(f: Feature) -> dict:
    templates = sorted((lnk.task_template for lnk in f.template_links.all()), key=lambda t: t.id)
    return {
        "id": f.id, "name": f.name, "category": f.category, "complexity_weight": f.complexity_weight,
        "has_existing_tests": f.has_existing_tests, "description": f.description,
        "product_type": f.product_type, "study_effort_hours": f.study_effort_hours,
        "base_effort_hours": f.base_effort_hours, "is_global": f.is_global,
        "owner_estimation_id": f.owner_estimation_id, "promotion_requested": f.promotion_requested,
        "created_at": f.created_at,
        "task_templates": [template_out(t, with_hours=False) for t in templates],
    }


def _reload(feature_id: int) -> dict:
    return feature_out(_feature_qs().get(pk=feature_id))


def list_features(product_type: str | None = None, estimation_id: int | None = None,
                  include_project: bool = True) -> list[dict]:
    qs = _feature_qs().order_by("id")
    if estimation_id is not None and include_project:
        qs = qs.filter(Q(is_global=True) | Q(owner_estimation_id=estimation_id))
    else:
        qs = qs.filter(is_global=True)
    return [feature_out(f) for f in _product_filter(qs, product_type)]


def list_project_features(promotion_requested: bool | None = None) -> list[dict]:
    """Project-specific (non-global) features, optionally only those flagged for promotion."""
    qs = _feature_qs().filter(is_global=False).order_by("id")
    if promotion_requested is not None:
        qs = qs.filter(promotion_requested=promotion_requested)
    return [feature_out(f) for f in qs]


def can_manage_feature(user: User, feature: Feature) -> bool:
    """Same rule as ``update_feature`` / ``delete_feature`` (for showing actions)."""
    if feature.is_global:
        return has_role(user.role, "APPROVER")
    return user.role == "ADMIN" or _is_project_author(user, feature)


def can_request_promotion(user: User, feature: Feature) -> bool:
    """Same rule as ``request_feature_promotion`` (for showing the action)."""
    if feature.is_global or not has_role(user.role, "ESTIMATOR"):
        return False
    return user.role == "ADMIN" or _is_project_author(user, feature)


def get_feature_or_error(feature_id: int) -> Feature:
    feature = Feature.objects.filter(pk=feature_id).first()
    if feature is None:
        raise ServiceError(404, "Feature not found")
    return feature


def get_feature(feature_id: int) -> dict:
    get_feature_or_error(feature_id)
    return _reload(feature_id)


def assert_no_global_feature_clash(name: str, category: str | None, exclude_id: int | None = None) -> None:
    """Raise 409 if a global feature already uses (name, category)."""
    qs = Feature.objects.filter(name=name, category=category, is_global=True)
    if exclude_id is not None:
        qs = qs.exclude(pk=exclude_id)
    if qs.exists():
        raise ServiceError(
            409,
            f"A global feature '{name}' already exists in category '{category or '(no category)'}'. "
            "Choose a different name or category.",
        )


def create_feature(data: dict[str, Any]) -> dict:
    """Create a global (shared-catalog) feature."""
    assert_no_global_feature_clash(data["name"], data.get("category"))
    try:
        with transaction.atomic():
            feature = Feature.objects.create(**data, is_global=True, owner_estimation=None)
    except IntegrityError as exc:
        raise ServiceError(409, f"Feature '{data['name']}' already exists in this category.") from exc
    return _reload(feature.id)


def _is_project_author(user: User, feature: Feature) -> bool:
    owner = (
        Estimation.objects.filter(pk=feature.owner_estimation_id).first()
        if feature.owner_estimation_id else None
    )
    return bool(owner) and (owner.creator_id is None or owner.creator_id == user.id)


def update_feature(feature_id: int, user: User, updates: dict[str, Any]) -> dict:
    feature = get_feature_or_error(feature_id)
    # Global catalog edits need APPROVER+; a project feature may be edited by its
    # owning estimation's author or an ADMIN.
    if feature.is_global:
        if not has_role(user.role, "APPROVER"):
            raise ServiceError(403, "Editing a global feature requires APPROVER role")
    elif user.role != "ADMIN" and not _is_project_author(user, feature):
        raise ServiceError(403, "Only the project's author or an ADMIN can edit this project feature")
    new_name = updates.get("name", feature.name)
    new_category = updates.get("category", feature.category)
    if (new_name, new_category) != (feature.name, feature.category):
        clash = Feature.objects.filter(name=new_name, category=new_category).exclude(pk=feature_id)
        # Globals clash only with globals; project features only within their estimation.
        if feature.is_global:
            clash = clash.filter(is_global=True)
        else:
            clash = clash.filter(owner_estimation_id=feature.owner_estimation_id)
        if clash.exists():
            raise ServiceError(
                409,
                f"Feature '{new_name}' already exists in category '{new_category or '(no category)'}'. "
                "Choose a different name or category.",
            )
    _apply(feature, updates)
    try:
        with transaction.atomic():
            feature.save()
    except IntegrityError as exc:
        raise ServiceError(409, f"Feature '{new_name}' already exists in this category.") from exc
    return _reload(feature.id)


def delete_feature(feature_id: int, user: User) -> None:
    feature = get_feature_or_error(feature_id)
    if feature.is_global:
        if not has_role(user.role, "APPROVER"):
            raise ServiceError(403, "Deleting a global feature requires APPROVER role")
    elif user.role != "ADMIN" and not _is_project_author(user, feature):
        raise ServiceError(403, "Only the project's author or an ADMIN can delete this project feature")
    feature.delete()


def promote_feature(feature_id: int) -> dict:
    """Promote a project-specific feature into the shared global catalog."""
    feature = get_feature_or_error(feature_id)
    if feature.is_global:
        raise ServiceError(400, "Feature is already global")
    assert_no_global_feature_clash(feature.name, feature.category, exclude_id=feature.id)
    feature.is_global = True
    feature.owner_estimation = None
    feature.promotion_requested = False
    feature.save()
    return _reload(feature.id)


def request_feature_promotion(feature_id: int, user: User) -> dict:
    """Flag a project feature for promotion and notify approvers (best effort)."""
    feature = get_feature_or_error(feature_id)
    if feature.is_global:
        raise ServiceError(400, "Feature is already global")
    if user.role != "ADMIN" and not _is_project_author(user, feature):
        raise ServiceError(403, "Only the project's author or an ADMIN can request promotion")
    feature.promotion_requested = True
    feature.save(update_fields=["promotion_requested"])

    try:
        from apps.notifications.service import NotificationService

        NotificationService().notify_feature_promotion_requested(
            feature_name=feature.name,
            category=feature.category,
            requested_by=user.display_name,
        )
    except Exception:  # notifications must never break the request
        logger.debug("Feature promotion notification failed", exc_info=True)
    return _reload(feature.id)


# ── DUT types ────────────────────────────────────────────

def list_dut_types(product_type: str | None = None):
    return list(_product_filter(DutType.objects.order_by("id"), product_type))


def create_dut_type(data: dict[str, Any]) -> DutType:
    if DutType.objects.filter(name=data["name"]).exists():
        raise ServiceError(409, f"DUT type with name '{data['name']}' already exists")
    return DutType.objects.create(**data)


def update_dut_type(dut_id: int, updates: dict[str, Any]) -> DutType:
    dut = DutType.objects.filter(pk=dut_id).first()
    if dut is None:
        raise ServiceError(404, "DUT type not found")
    new_name = updates.get("name")
    clash = DutType.objects.filter(name=new_name).exclude(pk=dut_id)
    if new_name and new_name != dut.name and clash.exists():
        raise ServiceError(409, f"DUT type with name '{new_name}' already exists")
    _apply(dut, updates)
    try:
        with transaction.atomic():
            dut.save()
    except IntegrityError as exc:
        raise ServiceError(409, "Failed to update — possible duplicate name") from exc
    return dut


def delete_dut_type(dut_id: int) -> None:
    dut = DutType.objects.filter(pk=dut_id).first()
    if dut is None:
        raise ServiceError(404, "DUT type not found")
    dut.delete()


def _reset_sequence(table: str) -> None:
    """Reset an auto-increment counter (SQLite / MySQL / PostgreSQL); best effort."""
    vendor = connection.vendor
    statements = {
        "sqlite": [f"DELETE FROM sqlite_sequence WHERE name='{table}'"],
        "mysql": [f"ALTER TABLE {table} AUTO_INCREMENT = 1"],
        "postgresql": [f"SELECT setval(pg_get_serial_sequence('{table}', 'id'), 1, false)"],
    }.get(vendor, [])
    for sql in statements:
        try:
            with transaction.atomic(), connection.cursor() as cursor:
                cursor.execute(sql)
        except Exception:
            logger.debug("Could not reset sequence for %s", table, exc_info=True)


def reinit_dut_types() -> dict:
    """Delete all DUT types and reset the ID counter."""
    DutType.objects.all().delete()
    _reset_sequence(DutType._meta.db_table)
    return {"status": "ok", "message": "DUT registry cleared, ID counter reset to 1"}


# ── Test profiles ────────────────────────────────────────

def list_profiles(product_type: str | None = None):
    return list(_product_filter(TestProfile.objects.order_by("id"), product_type))


def create_profile(data: dict[str, Any]) -> TestProfile:
    if TestProfile.objects.filter(name=data["name"]).exists():
        raise ServiceError(409, f"Profile with name '{data['name']}' already exists")
    return TestProfile.objects.create(**data)


def update_profile(profile_id: int, updates: dict[str, Any]) -> TestProfile:
    profile = TestProfile.objects.filter(pk=profile_id).first()
    if profile is None:
        raise ServiceError(404, "Profile not found")
    new_name = updates.get("name")
    if new_name and TestProfile.objects.filter(name=new_name).exclude(pk=profile_id).exists():
        raise ServiceError(409, f"Profile with name '{new_name}' already exists")
    _apply(profile, updates)
    profile.save()
    return profile


def delete_profile(profile_id: int) -> None:
    profile = TestProfile.objects.filter(pk=profile_id).first()
    if profile is None:
        raise ServiceError(404, "Profile not found")
    profile.delete()


# ── Historical projects ──────────────────────────────────

def list_historical_projects():
    return list(HistoricalProject.objects.order_by("id"))


def _check_estimation(data: dict[str, Any]) -> None:
    eid = data.get("estimation_id")
    if eid is not None and not Estimation.objects.filter(pk=eid).exists():
        raise ServiceError(404, "Estimation not found")


def create_historical_project(data: dict[str, Any]) -> HistoricalProject:
    _check_estimation(data)
    return HistoricalProject.objects.create(**data)


def update_historical_project(project_id: int, updates: dict[str, Any]) -> HistoricalProject:
    proj = HistoricalProject.objects.filter(pk=project_id).first()
    if proj is None:
        raise ServiceError(404, "Historical project not found")
    _check_estimation(updates)
    _apply(proj, updates)
    proj.save()
    return proj


def delete_historical_project(project_id: int) -> None:
    proj = HistoricalProject.objects.filter(pk=project_id).first()
    if proj is None:
        raise ServiceError(404, "Historical project not found")
    proj.delete()


# ── Task presets ─────────────────────────────────────────

def task_preset_out(p: TaskPreset) -> dict:
    return {
        "id": p.id, "name": p.name, "product_type": p.product_type, "description": p.description,
        "task_template_ids": list(p.task_template_ids or []), "created_at": p.created_at,
    }


def list_task_presets(product_type: str | None = None) -> list[dict]:
    qs = TaskPreset.objects.order_by("id")
    if product_type:
        qs = qs.filter(product_type=product_type)
    return [task_preset_out(p) for p in qs]


def create_task_preset(data: dict[str, Any]) -> dict:
    if TaskPreset.objects.filter(name=data["name"]).exists():
        raise ServiceError(400, f"Preset '{data['name']}' already exists")
    return task_preset_out(TaskPreset.objects.create(**data))


def update_task_preset(preset_id: int, updates: dict[str, Any]) -> dict:
    preset = TaskPreset.objects.filter(pk=preset_id).first()
    if preset is None:
        raise ServiceError(404, "Task preset not found")
    updates = {k: v for k, v in updates.items() if v is not None}
    new_name = updates.get("name")
    if new_name and TaskPreset.objects.filter(name=new_name).exclude(pk=preset_id).exists():
        raise ServiceError(400, f"Preset '{new_name}' already exists")
    _apply(preset, updates)
    preset.save()
    return task_preset_out(preset)


def delete_task_preset(preset_id: int) -> None:
    preset = TaskPreset.objects.filter(pk=preset_id).first()
    if preset is None:
        raise ServiceError(404, "Task preset not found")
    preset.delete()


# ── Risk items ───────────────────────────────────────────

def list_risk_items():
    return list(RiskItem.objects.filter(is_active=True).order_by("id"))


def create_risk_item(data: dict[str, Any]) -> RiskItem:
    if RiskItem.objects.filter(name=data["name"]).exists():
        raise ServiceError(400, f"Risk item '{data['name']}' already exists")
    return RiskItem.objects.create(**data)


def update_risk_item(item_id: int, updates: dict[str, Any]) -> RiskItem:
    item = RiskItem.objects.filter(pk=item_id).first()
    if item is None:
        raise ServiceError(404, "Risk item not found")
    new_name = updates.get("name")
    if new_name and RiskItem.objects.filter(name=new_name).exclude(pk=item_id).exists():
        raise ServiceError(400, f"Risk item '{new_name}' already exists")
    _apply(item, updates)
    item.save()
    return item


def delete_risk_item(item_id: int) -> None:
    item = RiskItem.objects.filter(pk=item_id).first()
    if item is None:
        raise ServiceError(404, "Risk item not found")
    item.delete()


# ── Document types ───────────────────────────────────────

def document_type_out(item: DocumentType) -> dict:
    tmpl = item.task_template
    return {
        "id": item.id, "name": item.name, "description": item.description, "category": item.category,
        "base_effort_hours": item.base_effort_hours, "is_active": item.is_active,
        "task_template_id": item.task_template_id, "task_template_name": tmpl.name if tmpl else None,
        "created_at": item.created_at,
    }


def list_document_types(include_inactive: bool = False) -> list[dict]:
    qs = DocumentType.objects.select_related("task_template").order_by("name")
    if not include_inactive:
        qs = qs.filter(is_active=True)
    return [document_type_out(i) for i in qs]


def _check_template(data: dict[str, Any]) -> None:
    tid = data.get("task_template_id")
    if tid is not None and not TaskTemplate.objects.filter(pk=tid).exists():
        raise ServiceError(400, "Task template not found")


def create_document_type(data: dict[str, Any]) -> dict:
    if DocumentType.objects.filter(name=data["name"]).exists():
        raise ServiceError(400, f"Document type '{data['name']}' already exists")
    _check_template(data)
    return document_type_out(DocumentType.objects.create(**data))


def update_document_type(item_id: int, updates: dict[str, Any]) -> dict:
    item = DocumentType.objects.filter(pk=item_id).first()
    if item is None:
        raise ServiceError(404, "Document type not found")
    new_name = updates.get("name")
    if new_name and DocumentType.objects.filter(name=new_name).exclude(pk=item_id).exists():
        raise ServiceError(400, f"Document type '{new_name}' already exists")
    _check_template(updates)
    _apply(item, updates)
    item.save()
    return document_type_out(DocumentType.objects.select_related("task_template").get(pk=item.pk))


def delete_document_type(item_id: int) -> None:
    item = DocumentType.objects.filter(pk=item_id).first()
    if item is None:
        raise ServiceError(404, "Document type not found")
    item.delete()
