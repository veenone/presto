"""API routes: /features/*, /feature-presets/*, /task-templates/*, /task-presets/*, /dut-types/*,
/profiles/*, /risk-items/*, /document-types/*, /historical-projects/*."""

from collections.abc import Callable
from typing import Any

from ninja import Router
from ninja.errors import HttpError

from apps.api.auth import authenticated, role
from apps.api.common import parse_json_text
from apps.api.schemas.catalog import (
    DocumentTypeCreate,
    DocumentTypeOut,
    DocumentTypeUpdate,
    DutReinitOut,
    DutTypeCreate,
    DutTypeOut,
    DutTypeUpdate,
    FeatureCreate,
    FeatureOut,
    FeaturePresetCreate,
    FeaturePresetOut,
    FeaturePresetUpdate,
    FeatureUpdate,
    HistoricalProjectCreate,
    HistoricalProjectOut,
    RiskItemCreate,
    RiskItemOut,
    RiskItemUpdate,
    TaskPresetCreate,
    TaskPresetOut,
    TaskPresetUpdate,
    TaskTemplateCreate,
    TaskTemplateOut,
    TaskTemplateUpdate,
    TestProfileCreate,
    TestProfileOut,
    TestProfileUpdate,
)
from apps.catalog import services
from apps.core.services import ServiceError

router = Router(auth=authenticated, tags=["catalog"])


def call(fn: Callable[..., Any], *args, **kwargs) -> Any:
    """Run a service function, mapping ``ServiceError`` to ``HttpError``."""
    try:
        return fn(*args, **kwargs)
    except ServiceError as exc:
        raise HttpError(exc.status_code, exc.detail) from exc


# ── Feature presets ──────────────────────────────────────

@router.get("/feature-presets", response=list[FeaturePresetOut])
def list_feature_presets(request, product_type: str | None = None):
    """List shared feature presets; type-bound presets only show for their product type."""
    return services.list_feature_presets(product_type)


@router.post("/feature-presets", response={201: FeaturePresetOut}, auth=role("ESTIMATOR"))
def create_feature_preset(request, data: FeaturePresetCreate):
    return 201, call(
        services.create_feature_preset, request.auth, data.name, data.description,
        data.product_type, data.feature_ids,
    )


@router.put("/feature-presets/{preset_id}", response=FeaturePresetOut, auth=role("ESTIMATOR"))
def update_feature_preset(request, preset_id: int, data: FeaturePresetUpdate):
    return call(services.update_feature_preset, preset_id, request.auth, data.model_dump())


@router.delete("/feature-presets/{preset_id}", response={204: None}, auth=role("ESTIMATOR"))
def delete_feature_preset(request, preset_id: int):
    call(services.delete_feature_preset, preset_id, request.auth)
    return 204, None


# ── Features ─────────────────────────────────────────────

@router.get("/features", response=list[FeatureOut])
def list_features(request, product_type: str | None = None, estimation_id: int | None = None,
                  include_project: bool = True):
    """Global catalog; with ``estimation_id`` also that estimation's project features."""
    return services.list_features(product_type, estimation_id, include_project)


@router.post("/features", response={201: FeatureOut}, auth=role("APPROVER"))
def create_feature(request, data: FeatureCreate):
    """Create a global (shared-catalog) feature."""
    return 201, call(services.create_feature, data.model_dump())


@router.get("/features/{feature_id}", response=FeatureOut)
def get_feature(request, feature_id: int):
    return call(services.get_feature, feature_id)


@router.put("/features/{feature_id}", response=FeatureOut, auth=role("ESTIMATOR"))
def update_feature(request, feature_id: int, data: FeatureUpdate):
    return call(services.update_feature, feature_id, request.auth, data.model_dump(exclude_unset=True))


@router.delete("/features/{feature_id}", response={204: None}, auth=role("ESTIMATOR"))
def delete_feature(request, feature_id: int):
    call(services.delete_feature, feature_id, request.auth)
    return 204, None


@router.post("/features/{feature_id}/promote", response=FeatureOut, auth=role("APPROVER"))
def promote_feature_to_global(request, feature_id: int):
    """Promote a project-specific feature into the shared global catalog."""
    return call(services.promote_feature, feature_id)


@router.post("/features/{feature_id}/request-promotion", response=FeatureOut, auth=role("ESTIMATOR"))
def request_feature_promotion(request, feature_id: int):
    """Flag a project feature so an approver can promote it later."""
    return call(services.request_feature_promotion, feature_id, request.auth)


# ── Task templates ───────────────────────────────────────

@router.get("/task-templates", response=list[TaskTemplateOut])
def list_task_templates(request, feature_id: int | None = None, product_type: str | None = None):
    return services.list_task_templates(feature_id, product_type)


@router.post("/task-templates", response={201: TaskTemplateOut}, auth=role("APPROVER"))
def create_task_template(request, data: TaskTemplateCreate):
    return 201, call(services.create_task_template, data.model_dump())


@router.put("/task-templates/{template_id}", response=TaskTemplateOut, auth=role("APPROVER"))
def update_task_template(request, template_id: int, data: TaskTemplateUpdate):
    return call(services.update_task_template, template_id, data.model_dump(exclude_unset=True))


@router.delete("/task-templates/{template_id}", response={204: None}, auth=role("APPROVER"))
def delete_task_template(request, template_id: int):
    call(services.delete_task_template, template_id)
    return 204, None


# ── DUT types ────────────────────────────────────────────

@router.get("/dut-types", response=list[DutTypeOut])
def list_dut_types(request, product_type: str | None = None):
    return services.list_dut_types(product_type)


@router.post("/dut-types", response={201: DutTypeOut}, auth=role("APPROVER"))
def create_dut_type(request, data: DutTypeCreate):
    return 201, call(services.create_dut_type, data.model_dump())


@router.post("/dut-types/reinit", response=DutReinitOut, auth=role("ADMIN"))
def reinit_dut_types(request):
    """Delete all DUT types and reset the auto-increment ID counter."""
    return services.reinit_dut_types()


@router.put("/dut-types/{dut_id}", response=DutTypeOut, auth=role("APPROVER"))
def update_dut_type(request, dut_id: int, data: DutTypeUpdate):
    return call(services.update_dut_type, dut_id, data.model_dump(exclude_unset=True))


@router.delete("/dut-types/{dut_id}", response={204: None}, auth=role("APPROVER"))
def delete_dut_type(request, dut_id: int):
    call(services.delete_dut_type, dut_id)
    return 204, None


# ── Test profiles ────────────────────────────────────────

@router.get("/profiles", response=list[TestProfileOut])
def list_profiles(request, product_type: str | None = None):
    return services.list_profiles(product_type)


@router.post("/profiles", response={201: TestProfileOut}, auth=role("APPROVER"))
def create_profile(request, data: TestProfileCreate):
    return 201, call(services.create_profile, data.model_dump())


@router.put("/profiles/{profile_id}", response=TestProfileOut, auth=role("APPROVER"))
def update_profile(request, profile_id: int, data: TestProfileUpdate):
    return call(services.update_profile, profile_id, data.model_dump(exclude_unset=True))


@router.delete("/profiles/{profile_id}", response={204: None}, auth=role("APPROVER"))
def delete_profile(request, profile_id: int):
    call(services.delete_profile, profile_id)
    return 204, None


# ── Historical projects ──────────────────────────────────

def _historical_fields(data: HistoricalProjectCreate, exclude_unset: bool = False) -> dict:
    fields = data.model_dump(exclude_unset=exclude_unset)
    if "features_json" in fields:
        fields["features"] = parse_json_text(fields.pop("features_json"), [])
    return fields


@router.get("/historical-projects", response=list[HistoricalProjectOut])
def list_historical_projects(request):
    return services.list_historical_projects()


@router.post("/historical-projects", response={201: HistoricalProjectOut}, auth=role("APPROVER"))
def create_historical_project(request, data: HistoricalProjectCreate):
    return 201, call(services.create_historical_project, _historical_fields(data))


@router.put("/historical-projects/{project_id}", response=HistoricalProjectOut, auth=role("APPROVER"))
def update_historical_project(request, project_id: int, data: HistoricalProjectCreate):
    return call(services.update_historical_project, project_id, _historical_fields(data, exclude_unset=True))


@router.delete("/historical-projects/{project_id}", response={204: None}, auth=role("APPROVER"))
def delete_historical_project(request, project_id: int):
    call(services.delete_historical_project, project_id)
    return 204, None


# ── Task presets ─────────────────────────────────────────

@router.get("/task-presets", response=list[TaskPresetOut])
def list_task_presets(request, product_type: str | None = None):
    return services.list_task_presets(product_type)


@router.post("/task-presets", response={201: TaskPresetOut}, auth=role("ESTIMATOR"))
def create_task_preset(request, data: TaskPresetCreate):
    return 201, call(services.create_task_preset, data.model_dump())


@router.put("/task-presets/{preset_id}", response=TaskPresetOut, auth=role("ESTIMATOR"))
def update_task_preset(request, preset_id: int, data: TaskPresetUpdate):
    return call(services.update_task_preset, preset_id, data.model_dump())


@router.delete("/task-presets/{preset_id}", response={204: None}, auth=role("ESTIMATOR"))
def delete_task_preset(request, preset_id: int):
    call(services.delete_task_preset, preset_id)
    return 204, None


# ── Risk items ───────────────────────────────────────────

@router.get("/risk-items", response=list[RiskItemOut])
def list_risk_items(request):
    return services.list_risk_items()


@router.post("/risk-items", response={201: RiskItemOut}, auth=role("ESTIMATOR"))
def create_risk_item(request, data: RiskItemCreate):
    return 201, call(services.create_risk_item, data.model_dump())


@router.put("/risk-items/{item_id}", response=RiskItemOut, auth=role("ESTIMATOR"))
def update_risk_item(request, item_id: int, data: RiskItemUpdate):
    return call(services.update_risk_item, item_id, data.model_dump(exclude_unset=True))


@router.delete("/risk-items/{item_id}", response={204: None}, auth=role("APPROVER"))
def delete_risk_item(request, item_id: int):
    call(services.delete_risk_item, item_id)
    return 204, None


# ── Document types ───────────────────────────────────────

@router.get("/document-types", response=list[DocumentTypeOut])
def list_document_types(request):
    return services.list_document_types()


@router.get("/document-types/all", response=list[DocumentTypeOut])
def list_all_document_types(request):
    return services.list_document_types(include_inactive=True)


@router.post("/document-types", response={201: DocumentTypeOut}, auth=role("ESTIMATOR"))
def create_document_type(request, data: DocumentTypeCreate):
    return 201, call(services.create_document_type, data.model_dump())


@router.put("/document-types/{item_id}", response=DocumentTypeOut, auth=role("ESTIMATOR"))
def update_document_type(request, item_id: int, data: DocumentTypeUpdate):
    return call(services.update_document_type, item_id, data.model_dump(exclude_unset=True))


@router.delete("/document-types/{item_id}", response={204: None}, auth=role("APPROVER"))
def delete_document_type(request, item_id: int):
    call(services.delete_document_type, item_id)
    return 204, None
