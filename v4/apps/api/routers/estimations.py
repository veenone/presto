"""API routes: /estimations/* except report, send-report and export paths."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from ninja import Router, Status
from ninja.errors import HttpError

from apps.api.auth import authenticated, role
from apps.api.common import client_ip
from apps.api.schemas.catalog import FeatureCreate, FeatureOut, HistoricalProjectOut
from apps.api.schemas.estimations import (
    CalculateInput,
    CalculationResultOut,
    CalibrationResultOut,
    EstimationCreate,
    EstimationOut,
    EstimationReinit,
    EstimationRevise,
    EstimationStatusUpdate,
    EstimationUpdate,
    serialize_estimation,
)
from apps.estimations import services, versions
from apps.estimations.models import Estimation

router = Router(auth=authenticated, tags=["estimations"])

def _call[T](fn: Callable[..., T], *args: Any, **kwargs: Any) -> T:
    """Run a service function, mapping ``ServiceError`` to the v3 HTTP error."""
    try:
        return fn(*args, **kwargs)
    except services.ServiceError as exc:
        raise HttpError(exc.status_code, exc.detail) from exc


def _get(estimation_id: int) -> Estimation:
    return _call(services.get_estimation, estimation_id)


# ── Collection ───────────────────────────────────────────


@router.post("/estimations/calculate", response=CalculationResultOut)
def calculate_estimation_preview(request, data: CalculateInput):
    """Run calculation from wizard inputs without persisting to DB."""
    return services.calculate_preview(data)


@router.get("/estimations", response=list[EstimationOut])
def list_estimations(request):
    qs = Estimation.objects.select_related("assigned_to", "team", "request").order_by("-created_at", "-id")
    return [serialize_estimation(e) for e in qs]


@router.post("/estimations", response={201: EstimationOut}, auth=role("ESTIMATOR"))
def create_estimation(request, data: EstimationCreate):
    """Create a new estimation: resolve inputs, run calculation, save result."""
    estimation = services.create_estimation(data, request.auth)
    return Status(201, serialize_estimation(estimation))


@router.post("/estimations/reinit", auth=role("ADMIN"))
def reinit_estimations(request, data: EstimationReinit) -> dict:
    """Delete every estimation and reset the ID counter (confirmation token required)."""
    return _call(services.reinit_estimations, data.confirm, request.auth, client_ip(request))


# ── Single estimation ────────────────────────────────────


@router.get("/estimations/{estimation_id}", response=EstimationOut)
def get_estimation(request, estimation_id: int):
    return services.estimation_detail(_get(estimation_id))


@router.put("/estimations/{estimation_id}", response=EstimationOut, auth=role("ESTIMATOR"))
def update_estimation(request, estimation_id: int, data: EstimationUpdate):
    """Update estimation metadata (project name, type, delivery date, notes)."""
    estimation = services.update_metadata(_get(estimation_id), data.model_dump(exclude_unset=True))
    return serialize_estimation(estimation)


@router.delete("/estimations/{estimation_id}", response={204: None}, auth=role("ADMIN"))
def delete_estimation(request, estimation_id: int):
    _get(estimation_id).delete()
    return Status(204, None)


@router.post("/estimations/{estimation_id}/calculate", response=CalculationResultOut)
def recalculate_estimation(request, estimation_id: int):
    """Recalculate an existing estimation and return the results."""
    return services.recalculate(_get(estimation_id))


@router.post("/estimations/{estimation_id}/status", response=EstimationOut, auth=role("ESTIMATOR"))
def update_estimation_status(request, estimation_id: int, data: EstimationStatusUpdate):
    """Update estimation status following the workflow: DRAFT -> FINAL -> APPROVED."""
    estimation = _call(services.change_status, _get(estimation_id), data.status, data.approved_by,
                       request.auth)
    return serialize_estimation(estimation)


@router.put("/estimations/{estimation_id}/draft", response=EstimationOut, auth=role("ESTIMATOR"))
def update_draft_estimation(request, estimation_id: int, data: EstimationRevise):
    """Resume editing a DRAFT estimation: recalculate in place with new inputs."""
    estimation = _call(services.edit_draft, _get(estimation_id), data, request.auth, client_ip(request))
    return serialize_estimation(estimation)


@router.put("/estimations/{estimation_id}/revise", response=EstimationOut, auth=role("ESTIMATOR"))
def revise_estimation(request, estimation_id: int, data: EstimationRevise):
    """Revise an estimation: re-run calculation with new inputs, bump version, reset to DRAFT."""
    estimation = _call(services.revise, _get(estimation_id), data, request.auth, client_ip(request))
    return serialize_estimation(estimation)


@router.post("/estimations/{estimation_id}/calibrate", response=CalibrationResultOut)
def calibrate_estimation(request, estimation_id: int):
    """Apply historical calibration to an estimation."""
    return _call(services.calibrate_estimation, _get(estimation_id))


@router.post("/estimations/{estimation_id}/assign", auth=role("APPROVER"))
def assign_estimation(request, estimation_id: int, assigned_to_id: int):
    return _call(services.assign, _get(estimation_id), assigned_to_id, request.auth, client_ip(request))


@router.post(
    "/estimations/{estimation_id}/archive", response={201: HistoricalProjectOut}, auth=role("APPROVER"),
)
def archive_estimation_to_history(request, estimation_id: int):
    """Convert an APPROVED estimation into a historical project record."""
    return Status(201, _call(services.archive_to_history, _get(estimation_id)))


@router.post("/estimations/{estimation_id}/features", response={201: FeatureOut}, auth=role("ESTIMATOR"))
def create_project_feature(request, estimation_id: int, data: FeatureCreate):
    """Create a feature scoped to one DRAFT estimation (author or ADMIN only)."""
    estimation = _get(estimation_id)
    return Status(201, _call(services.create_project_feature, estimation, data.model_dump(), request.auth))


# ── Version history ──────────────────────────────────────


@router.get("/estimations/{estimation_id}/versions")
def get_estimation_versions(request, estimation_id: int) -> list[dict]:
    """Get all version snapshots for an estimation."""
    return versions.list_versions(_get(estimation_id))


@router.get("/estimations/{estimation_id}/versions/{version_a}/diff/{version_b}")
def diff_estimation_versions(request, estimation_id: int, version_a: int, version_b: int) -> dict:
    """Compare two versions of an estimation and return the differences."""
    estimation = _get(estimation_id)
    try:
        return versions.diff_versions(estimation, version_a, version_b)
    except versions.VersionNotFound as exc:
        raise HttpError(404, str(exc)) from exc
