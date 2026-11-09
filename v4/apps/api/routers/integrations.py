"""API routes: /integrations/*, /webhooks/redmine, /estimations/{id}/export, /estimations/{id}/export-tasks.

Literal paths (``/integrations/SNIPE_IT/assets``, ``/integrations/JIRA/pr-items``,
``/integrations/outline/search``...) never share a path with a parameterised
``/integrations/{system_name}/...`` route: each has a distinct last segment.
"""

from __future__ import annotations

import hmac
import logging

from django.db import transaction
from ninja import Router
from ninja.errors import HttpError, ValidationError
from pydantic import BaseModel
from pydantic import ValidationError as PydanticValidationError

from apps.api.auth import authenticated, role
from apps.api.common import get_or_404, parse_json_text
from apps.api.schemas.integrations import (
    ConnectionTestOut,
    ExportTasksInput,
    IntegrationConfigUpdate,
    SyncResultOut,
    integration_out,
)
from apps.core import config
from apps.estimations.models import Estimation
from apps.integrations import service
from apps.integrations.models import IntegrationConfig
from apps.notifications.models import WebhookNotification

logger = logging.getLogger(__name__)

router = Router(auth=authenticated, tags=["integrations"])

EST_NOT_FOUND = "Estimation not found"


def _status_value(status) -> str:
    return status.value if hasattr(status, "value") else str(status)


def _connection_test_out(result) -> dict:
    return ConnectionTestOut(
        success=result.success, message=result.message, details=result.details,
    ).model_dump()


def _get_config_or_404(system_name: str) -> IntegrationConfig:
    cfg = IntegrationConfig.objects.filter(system_name=system_name.upper()).first()
    if not cfg:
        raise HttpError(404, f"Integration {system_name} not found")
    return cfg


# ── Integration config ───────────────────────────────────


@router.get("/integrations")
def list_integrations(request) -> list[dict]:
    return [integration_out(c) for c in IntegrationConfig.objects.all()]


@router.get("/integrations/{system_name}")
def get_integration(request, system_name: str) -> dict:
    return integration_out(_get_config_or_404(system_name))


@router.put("/integrations/{system_name}", auth=role("ADMIN"))
def update_integration(request, system_name: str, data: IntegrationConfigUpdate) -> dict:
    system_name = system_name.upper()
    cfg = IntegrationConfig.objects.filter(system_name=system_name).first() or IntegrationConfig(
        system_name=system_name,
    )
    changes = data.model_dump(exclude_unset=True)
    if "additional_config_json" in changes:
        text = changes.pop("additional_config_json")
        parsed = parse_json_text(text, None) if text else {}
        if not isinstance(parsed, dict):
            raise HttpError(400, "additional_config_json must be a JSON object")
        cfg.additional_config = parsed
    for key, val in changes.items():
        if key == "enabled" and val is None:
            continue  # NOT NULL column
        setattr(cfg, key, val)
    cfg.save()
    return integration_out(cfg)


@router.post("/integrations/{system_name}/test", auth=role("ADMIN"))
def test_integration_endpoint(request, system_name: str) -> dict:
    return _connection_test_out(service.test_integration(system_name.upper()))


@router.post("/integrations/{system_name}/sync", auth=role("ADMIN"))
def trigger_sync(request, system_name: str) -> dict:
    name = system_name.upper()
    # Outline is export-only (push estimations to the wiki); the others import requests.
    if name == "OUTLINE":
        result = service.sync_export_all(name)
    else:
        result = service.sync_import(name)
    return SyncResultOut(
        system=result.system,
        direction=result.direction,
        status=_status_value(result.status),
        items_processed=result.items_processed,
        items_created=result.items_created,
        items_updated=result.items_updated,
        items_failed=result.items_failed,
        errors=result.errors,
    ).model_dump()


@router.get("/integrations/{system_name}/status")
def integration_health(request, system_name: str) -> dict:
    cfg = _get_config_or_404(system_name)
    return {
        "system_name": cfg.system_name,
        "enabled": cfg.enabled,
        "last_sync_at": str(cfg.last_sync_at) if cfg.last_sync_at else None,
        "configured": bool(cfg.base_url or cfg.api_key),
    }


# ── Redmine webhook ──────────────────────────────────────


def _notify_watchers(items_created: int) -> None:
    from apps.accounts.models import User

    watcher_ids = config.get_json("webhook_watchers", [])
    if not isinstance(watcher_ids, list):
        return
    ids = [int(uid) for uid in watcher_ids]
    existing = set(User.objects.filter(pk__in=ids).values_list("pk", flat=True))
    with transaction.atomic():
        for uid in ids:
            if uid not in existing:
                continue  # unknown user: skip instead of failing the batch
            WebhookNotification.objects.create(
                user_id=uid,
                title=f"{items_created} new request(s) imported",
                message=f"Redmine webhook imported {items_created} new request(s).",
                source="REDMINE",
            )


@router.post("/webhooks/redmine", auth=None)
def redmine_webhook(request) -> dict:
    """Incoming Redmine webhook (no JWT; ``?token=`` shared secret when configured)."""
    cfg = IntegrationConfig.objects.filter(system_name="REDMINE").first()
    if not cfg or not cfg.enabled:
        raise HttpError(400, "Redmine integration is not enabled")

    additional = cfg.additional_config if isinstance(cfg.additional_config, dict) else {}
    webhook_secret = str(additional.get("webhook_secret") or "")
    if webhook_secret:
        token = request.GET.get("token", "")
        if not hmac.compare_digest(token.encode(), webhook_secret.encode()):
            raise HttpError(403, "Invalid webhook token")

    try:
        result = service.sync_import("REDMINE")
    except Exception as exc:
        raise HttpError(500, f"Webhook processing failed: {exc}") from exc

    if result.items_created > 0:
        try:
            _notify_watchers(result.items_created)
        except Exception:  # never break the webhook response for notifications
            logger.warning("Webhook watcher notification failed", exc_info=True)

    return {"status": "ok", "items_created": result.items_created, "items_updated": result.items_updated}


# ── Estimation export ────────────────────────────────────


@router.post("/estimations/{estimation_id}/export", auth=role("ESTIMATOR"))
def export_estimation_to_external(request, estimation_id: int) -> dict:
    """Export estimation results to the linked external system (Redmine/Jira)."""
    estimation = get_or_404(Estimation, estimation_id, EST_NOT_FOUND)
    if not estimation.request_id:
        raise HttpError(400, "Estimation is not linked to a request")
    req = estimation.request
    if not req or not req.external_id:
        raise HttpError(400, "Linked request has no external ID")
    if req.request_source == "MANUAL":
        raise HttpError(400, "Request source is MANUAL — no external system to export to")

    estimation_data = {
        "external_id": req.external_id,
        "grand_total_hours": estimation.grand_total_hours,
        "feasibility_status": estimation.feasibility_status,
        "estimation_number": estimation.estimation_number or f"EST-{estimation.id}",
        "project_name": estimation.project_name,
        "status": estimation.status,
    }
    result = service.sync_export(req.request_source, estimation_data)
    return {
        "status": _status_value(result.status),
        "system": result.system,
        "items_updated": result.items_updated,
        "errors": result.errors,
    }


def _optional_body(request, schema: type[BaseModel]) -> BaseModel | None:
    """Parse an optional JSON body (ninja rejects an empty body for ``Schema | None``)."""
    if request.content_type != "application/json" or not request.body.strip():
        return None
    try:
        return schema.model_validate_json(request.body)
    except PydanticValidationError as exc:
        errors = exc.errors(include_url=False, include_input=False, include_context=False)
        raise ValidationError(errors) from exc


_EXPORT_TASKS_OPENAPI = {
    "requestBody": {
        "required": False,
        "content": {"application/json": {"schema": ExportTasksInput.model_json_schema()}},
    },
}


@router.post(
    "/estimations/{estimation_id}/export-tasks", auth=role("ESTIMATOR"), openapi_extra=_EXPORT_TASKS_OPENAPI,
)
def export_task_breakdown(request, estimation_id: int) -> dict:
    """Export the task breakdown as sub-tasks to Jira or Redmine.

    Body (optional): ``{"target_system": ..., "issue_key": ...}``. Without
    ``target_system`` the system comes from the linked request, else the first
    enabled integration supporting task breakdown (JIRA, then REDMINE).
    """
    estimation = get_or_404(Estimation, estimation_id, EST_NOT_FOUND)
    body = _optional_body(request, ExportTasksInput)
    target_system = body.target_system if body else None
    external_id = body.issue_key if body else None  # an explicit issue key wins

    system_name = None
    if target_system:
        system_name = target_system.upper()
    elif estimation.request_id:
        req = estimation.request
        if req and req.request_source != "MANUAL":
            system_name = req.request_source
            if not external_id and req.external_id:
                external_id = req.external_id

    if not system_name:
        for name in ("JIRA", "REDMINE"):
            adapter = service.get_adapter(name)
            if adapter and hasattr(adapter, "create_task_breakdown"):
                system_name = name
                break

    if not system_name:
        raise HttpError(400, "No target system specified and no enabled integration found (Jira/Redmine)")

    estimation_data = service.estimation_to_export_dict(estimation)
    if external_id:
        estimation_data["external_id"] = external_id
    result = service.sync_export_task_breakdown(system_name, estimation_data)
    return {
        "status": _status_value(result.status),
        "system": result.system,
        "items_created": result.items_created,
        "items_processed": result.items_processed,
        "errors": result.errors,
    }


# ── Outline wiki ─────────────────────────────────────────


@router.post("/integrations/outline/publish/{estimation_id}", auth=role("ESTIMATOR"))
def publish_to_outline(request, estimation_id: int) -> dict:
    from apps.catalog.models import DutType, TestProfile
    from apps.estimations import calc
    from apps.reports.builders import build_risk_messages

    estimation = get_or_404(Estimation, estimation_id, EST_NOT_FOUND)
    adapter = service.get_adapter("OUTLINE")
    if not adapter:
        raise HttpError(400, "Outline integration is not configured")

    est_data = service.estimation_to_export_dict(estimation)
    wizard = estimation.wizard_inputs if isinstance(estimation.wizard_inputs, dict) else {}

    est_data["pr_no_test_hours"] = estimation.pr_no_test_hours or 0
    est_data["working_weeks"] = round((estimation.grand_total_days or 0) / 5.0, 1)
    est_data["working_hours_per_day"] = config.get_float("working_hours_per_day", 7.0)
    est_data["start_date"] = str(estimation.start_date) if estimation.start_date else ""
    est_data["testing_start_date"] = wizard.get("testing_start_date", "")
    est_data["expected_delivery"] = str(estimation.expected_delivery) if estimation.expected_delivery else ""

    dut_ids = wizard.get("dut_ids", [])
    profile_ids = wizard.get("profile_ids", [])
    if dut_ids:
        est_data["dut_names"] = list(DutType.objects.filter(id__in=dut_ids).values_list("name", flat=True))
    if profile_ids:
        est_data["profile_names"] = list(
            TestProfile.objects.filter(id__in=profile_ids).values_list("name", flat=True)
        )

    est_data["team_members"] = [
        {
            "name": a.team_member.name if a.team_member else "Unknown",
            "role": a.role or "",
            "allocated_hours": a.allocated_hours,
        }
        for a in estimation.team_allocations.select_related("team_member").order_by("id")
    ]

    doc_deliverables = calc.compute_doc_deliverables(estimation)
    est_data["document_deliverables"] = doc_deliverables
    est_data["risk_messages"] = build_risk_messages(estimation)

    tasks = est_data.get("tasks", [])
    tasks.extend(calc.build_doc_synthetic_tasks(doc_deliverables))
    tasks.extend(calc.build_pr_no_test_synthetic_tasks(
        wizard.get("pr_details", []), config.get_float("pr_no_test_hours", 8.0),
    ))
    est_data["tasks"] = tasks

    result = adapter.export_estimation(est_data)
    return {
        "status": _status_value(result.status),
        "items_created": result.items_created,
        "items_updated": result.items_updated,
        "errors": result.errors,
    }


@router.get("/integrations/outline/search")
def search_outline(request, query: str, limit: int = 10):
    adapter = service.get_adapter("OUTLINE")
    if not adapter:
        raise HttpError(400, "Outline integration is not configured")
    return adapter.search_documents(query, limit)


# ── Snipe-IT ─────────────────────────────────────────────


def _snipeit_adapter():
    from apps.integrations.adapters import SnipeItAdapter

    adapter = service.get_adapter("SNIPE_IT")
    if not adapter:
        raise HttpError(400, "Snipe-IT integration is not configured or not enabled.")
    if not isinstance(adapter, SnipeItAdapter):
        raise HttpError(500, "Invalid Snipe-IT adapter")
    return adapter


@router.get("/integrations/SNIPE_IT/assets")
def get_snipeit_assets(request, categories: str = ""):
    """Hardware assets from Snipe-IT, optionally filtered by comma-separated categories."""
    adapter = _snipeit_adapter()
    cat_list = [c.strip() for c in categories.split(",") if c.strip()] if categories else []
    return adapter.get_hardware_by_category(cat_list)


@router.get("/integrations/SNIPE_IT/categories")
def get_snipeit_categories(request):
    return _snipeit_adapter().get_categories()


# ── Jira problem reports ─────────────────────────────────


def _jira_pr_adapter() -> tuple[IntegrationConfig, object]:
    from apps.integrations.adapters import JiraAdapter

    cfg = IntegrationConfig.objects.filter(system_name="JIRA").first()
    if not cfg or not cfg.enabled:
        raise HttpError(400, "JIRA integration not configured or not enabled.")
    adapter = service.get_adapter("JIRA")
    if not isinstance(adapter, JiraAdapter):
        raise HttpError(400, "JIRA integration not available.")
    return cfg, adapter


def _jira_error_detail(resp) -> str:
    """Jira puts the real reason (bad JQL, field, permission) in the body."""
    detail = (resp.text or "").strip()[:500]
    try:
        payload = resp.json()
        msgs = payload.get("errorMessages") or []
        field_errs = [f"{k}: {v}" for k, v in (payload.get("errors") or {}).items()]
        combined = "; ".join(msgs + field_errs)
        if combined:
            detail = combined
    except Exception:
        pass
    return detail


@router.get("/integrations/JIRA/pr-items")
def get_jira_pr_items(request, jql: str = ""):
    """PR / defect issues from Jira using a dedicated JQL query."""
    cfg, adapter = _jira_pr_adapter()
    extra = cfg.additional_config if isinstance(cfg.additional_config, dict) else {}
    if not jql:
        jql = extra.get("pr_jql_filter", "")
    if not jql:
        raise HttpError(400, "No PR JQL filter provided.")

    try:
        # Uses the PR token (falling back to the main key) and PR proxy setting.
        resp = adapter._pr_request(
            "GET",
            adapter._url("search"),
            params={
                "jql": jql,
                "maxResults": 200,
                "fields": "summary,priority,status,issuetype,created,description",
            },
        )
        if resp.status_code >= 400:
            detail = _jira_error_detail(resp)
            raise HttpError(400, f"Jira returned HTTP {resp.status_code}: {detail or 'no error body'}")
        issues = resp.json().get("issues", [])
        base = (adapter.base_url or "").rstrip("/")
        result = []
        for issue in issues:
            fields = issue.get("fields", {})
            key = issue.get("key", "")
            raw_desc = fields.get("description")
            # Jira DC description is a string; Cloud sends ADF (skipped).
            description = "" if isinstance(raw_desc, dict) else (raw_desc or "")
            result.append({
                "key": key,
                "summary": fields.get("summary", ""),
                "priority": (fields.get("priority") or {}).get("name", "Medium"),
                "status": (fields.get("status") or {}).get("name", ""),
                "issue_type": (fields.get("issuetype") or {}).get("name", ""),
                "created": fields.get("created", ""),
                "description": description,
                "link": f"{base}/browse/{key}" if base and key else "",
            })
        return result
    except HttpError:
        raise
    except Exception as exc:
        raise HttpError(500, f"Failed to fetch PR items: {exc}") from exc


@router.post("/integrations/JIRA/pr-test-connection", auth=role("ADMIN"))
def test_jira_pr_connection(request) -> dict:
    """Test the Jira PR-items connection (dedicated PR token/proxy)."""
    _, adapter = _jira_pr_adapter()
    return _connection_test_out(adapter.test_pr_connection())
