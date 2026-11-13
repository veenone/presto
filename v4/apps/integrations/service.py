"""Integration service: orchestrates adapters and manages sync operations (port of v3)."""

from __future__ import annotations

from datetime import date

from django.db import transaction
from django.utils import timezone

from apps.core.numbering import generate_number
from apps.estimations.models import Estimation
from apps.inbox.models import Request

from .adapters import (
    BaseAdapter,
    ConnectionTestResult,
    EmailAdapter,
    ExternalRequest,
    JiraAdapter,
    OutlineAdapter,
    RedmineAdapter,
    SnipeItAdapter,
    SyncResult,
    SyncStatus,
)
from .models import IntegrationConfig

ADAPTER_MAP: dict[str, type[BaseAdapter]] = {
    "REDMINE": RedmineAdapter,
    "JIRA": JiraAdapter,
    "EMAIL": EmailAdapter,
    "OUTLINE": OutlineAdapter,
    "SNIPE_IT": SnipeItAdapter,
}


def _not_configured(system_name: str, direction: str) -> SyncResult:
    return SyncResult(
        system=system_name,
        direction=direction,
        status=SyncStatus.FAILED,
        errors=[f"{system_name} is not configured or not enabled."],
    )


def adapter_config(config_row: IntegrationConfig) -> dict:
    """The config dict the v3 adapters expect."""
    additional = config_row.additional_config if isinstance(config_row.additional_config, dict) else {}
    return {
        "base_url": config_row.base_url or "",
        "api_key": config_row.api_key or "",
        "username": config_row.username or "",
        "additional_config": additional,
    }


def get_adapter(system_name: str) -> BaseAdapter | None:
    """Create an adapter instance for the given system using stored config."""
    config_row = IntegrationConfig.objects.filter(system_name=system_name).first()
    if not config_row or not config_row.enabled:
        return None
    adapter_cls = ADAPTER_MAP.get(system_name)
    if not adapter_cls:
        return None
    return adapter_cls(adapter_config(config_row))


def test_integration(system_name: str) -> ConnectionTestResult:
    """Test connection for a given integration system."""
    adapter = get_adapter(system_name)
    if not adapter:
        return ConnectionTestResult(False, f"{system_name} is not configured or not enabled.")
    return adapter.test_connection()


test_integration.__test__ = False  # not a pytest test


def generate_request_number() -> str:
    """Next REQ-YYYY-NNN number."""
    return generate_number("request_number_prefix", Request, "REQ")


def _parse_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except (ValueError, TypeError):
        return None


def _persist_imported_items(system_name: str, items: list[ExternalRequest]) -> tuple[int, int]:
    """Persist ExternalRequest items as Request rows. Returns (created, updated)."""
    created = 0
    updated = 0
    with transaction.atomic():
        for ext in items:
            existing = Request.objects.filter(
                external_id=ext.external_id, request_source=system_name,
            ).first()
            if existing:
                existing.title = ext.title
                existing.description = ext.description or existing.description
                existing.requester_name = ext.requester_name or existing.requester_name
                existing.priority = ext.priority
                delivery = _parse_date(ext.requested_delivery_date)
                if delivery:
                    existing.requested_delivery_date = delivery
                existing.save()
                updated += 1
            else:
                Request.objects.create(
                    request_number=generate_request_number(),
                    request_source=system_name,
                    external_id=ext.external_id,
                    title=ext.title,
                    description=ext.description or "",
                    requester_name=ext.requester_name or "Unknown",
                    priority=ext.priority,
                    status="NEW",
                    requested_delivery_date=_parse_date(ext.requested_delivery_date),
                    received_date=date.today(),
                )
                created += 1
    return created, updated


def _touch_last_sync(system_name: str) -> None:
    IntegrationConfig.objects.filter(system_name=system_name).update(last_sync_at=timezone.now())


def sync_import(system_name: str) -> SyncResult:
    """Import requests from an external system."""
    adapter = get_adapter(system_name)
    if not adapter:
        return _not_configured(system_name, "IMPORT")

    result = adapter.import_requests()

    if result.imported_items:
        created, updated = _persist_imported_items(system_name, result.imported_items)
        result.items_created = created
        result.items_updated = updated

    # Previously synced requests missing from the import were deleted/closed upstream.
    if result.status in (SyncStatus.SUCCESS, SyncStatus.PARTIAL):
        imported_ext_ids = {ext.external_id for ext in (result.imported_items or [])}
        stale = (
            Request.objects.filter(request_source=system_name, external_id__isnull=False)
            .exclude(status="DELETED_UPSTREAM")
            .exclude(external_id="")
            .exclude(external_id__in=imported_ext_ids)
        )
        stale_count = stale.update(status="DELETED_UPSTREAM")
        if stale_count:
            result.errors.append(
                f"{stale_count} previously synced request(s) no longer found "
                f"in {system_name} — marked as DELETED_UPSTREAM."
            )

    _touch_last_sync(system_name)
    return result


def sync_export(system_name: str, estimation_data: dict) -> SyncResult:
    """Export estimation results to an external system."""
    adapter = get_adapter(system_name)
    if not adapter:
        return _not_configured(system_name, "EXPORT")
    return adapter.export_estimation(estimation_data)


def sync_export_task_breakdown(system_name: str, estimation_data: dict) -> SyncResult:
    """Export estimation task breakdown as sub-tasks to an external system."""
    adapter = get_adapter(system_name)
    if not adapter:
        return _not_configured(system_name, "EXPORT")
    if not hasattr(adapter, "create_task_breakdown"):
        return SyncResult(
            system=system_name,
            direction="EXPORT",
            status=SyncStatus.SKIPPED,
            errors=[f"{system_name} does not support task breakdown export."],
        )
    return adapter.create_task_breakdown(estimation_data)


def estimation_to_export_dict(est: Estimation) -> dict:
    """Convert an Estimation to the dict expected by adapters (v3 ``_estimation_to_export_dict``)."""
    tasks_data = [
        {
            "task_name": t.task_name,
            "task_type": t.task_type,
            "calculated_hours": t.calculated_hours,
            "leader_hours": t.leader_hours,
            "assigned_testers": t.assigned_testers,
            "has_leader_support": t.has_leader_support,
            "is_new_feature_study": t.is_new_feature_study,
            "notes": t.notes,
            "base_hours": t.base_hours,
        }
        for t in est.tasks.all()
    ]
    wizard = est.wizard_inputs if isinstance(est.wizard_inputs, dict) else {}
    pr_fixes = wizard.get("pr_fixes", {}) or {}
    if not isinstance(pr_fixes, dict):
        pr_fixes = {}
    assigned = est.assigned_to

    return {
        "estimation_number": est.estimation_number or f"EST-{est.id}",
        "project_name": est.project_name,
        "project_type": est.project_type,
        "status": est.status,
        "feasibility_status": est.feasibility_status,
        "total_tester_hours": est.total_tester_hours,
        "total_leader_hours": est.total_leader_hours,
        "pr_fix_hours": est.pr_fix_hours,
        "study_hours": est.study_hours,
        "release_extra_hours": est.release_extra_hours or 0,
        "documentation_hours": est.documentation_hours or 0,
        "buffer_hours": est.buffer_hours,
        "grand_total_hours": est.grand_total_hours,
        "grand_total_days": est.grand_total_days,
        "dut_count": est.dut_count,
        "profile_count": est.profile_count,
        "dut_profile_combinations": est.dut_profile_combinations,
        "pr_fix_count": est.pr_fix_count,
        "created_at": str(est.created_at) if est.created_at else "",
        "assigned_to_name": (assigned.display_name or assigned.username) if assigned else None,
        "version": est.version or 1,
        "project_goals": est.project_goals,
        "target_customer": est.target_customer,
        "tasks": tasks_data,
        "pr_simple": pr_fixes.get("simple", 0),
        "pr_medium": pr_fixes.get("medium", 0),
        "pr_complex": pr_fixes.get("complex", 0),
        "pr_details": wizard.get("pr_details", []),
        "pr_no_test_hours": est.pr_no_test_hours or 0,
        "start_date": str(est.start_date) if est.start_date else "",
        "expected_delivery": str(est.expected_delivery) if est.expected_delivery else "",
    }


def sync_export_all(system_name: str) -> SyncResult:
    """Export every estimation to an external system; returns an aggregate SyncResult."""
    adapter = get_adapter(system_name)
    if not adapter:
        return _not_configured(system_name, "EXPORT")

    estimations = list(
        Estimation.objects.select_related("assigned_to").prefetch_related("tasks")
    )
    if not estimations:
        return SyncResult(
            system=system_name,
            direction="EXPORT",
            status=SyncStatus.SUCCESS,
            items_processed=0,
            errors=["No estimations found to export."],
        )

    created = updated = failed = 0
    errors: list[str] = []
    for est in estimations:
        result = adapter.export_estimation(estimation_to_export_dict(est))
        if result.status == SyncStatus.SUCCESS:
            created += result.items_created
            updated += result.items_updated
        else:
            failed += 1
            errors.extend(result.errors)

    _touch_last_sync(system_name)

    if failed == 0:
        status = SyncStatus.SUCCESS
    else:
        status = SyncStatus.PARTIAL if created + updated > 0 else SyncStatus.FAILED
    return SyncResult(
        system=system_name,
        direction="EXPORT",
        status=status,
        items_processed=len(estimations),
        items_created=created,
        items_updated=updated,
        items_failed=failed,
        errors=errors,
    )


def get_integration_status() -> list[dict]:
    """Status of all configured integrations."""
    return [
        {
            "system_name": cfg.system_name,
            "enabled": cfg.enabled,
            "base_url": cfg.base_url or "",
            "last_sync_at": str(cfg.last_sync_at) if cfg.last_sync_at else None,
            "has_api_key": bool(cfg.api_key),
        }
        for cfg in IntegrationConfig.objects.all()
    ]


# ── Read helpers for the web pages (same rules and messages as the API routes) ──

def snipeit_saved_categories() -> list[str]:
    """Category names saved on the Snipe-IT integration (``categories``, comma separated)."""
    cfg = IntegrationConfig.objects.filter(system_name="SNIPE_IT").first()
    extra = cfg.additional_config if cfg and isinstance(cfg.additional_config, dict) else {}
    return [c.strip() for c in str(extra.get("categories") or "").split(",") if c.strip()]


def snipeit_assets(categories: list[str] | None = None) -> list[dict]:
    """Hardware assets from Snipe-IT (``GET /integrations/SNIPE_IT/assets``)."""
    from apps.core.errors import ServiceError

    adapter = get_adapter("SNIPE_IT")
    if not adapter:
        raise ServiceError(400, "Snipe-IT integration is not configured or not enabled.")
    if not isinstance(adapter, SnipeItAdapter):
        raise ServiceError(500, "Invalid Snipe-IT adapter")
    try:
        return adapter.get_hardware_by_category(list(categories or []))
    except Exception as exc:
        raise ServiceError(502, f"Failed to load assets: {exc}") from exc


def jira_pr_configured() -> bool:
    cfg = IntegrationConfig.objects.filter(system_name="JIRA").first()
    return bool(cfg and cfg.enabled)


def _jira_error_detail(resp) -> str:
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


def jira_pr_items(jql: str = "") -> list[dict]:
    """Problem reports from Jira via the PR JQL filter (``GET /integrations/JIRA/pr-items``)."""
    from apps.core.errors import ServiceError

    cfg = IntegrationConfig.objects.filter(system_name="JIRA").first()
    if not cfg or not cfg.enabled:
        raise ServiceError(400, "JIRA integration not configured or not enabled.")
    adapter = get_adapter("JIRA")
    if not isinstance(adapter, JiraAdapter):
        raise ServiceError(400, "JIRA integration not available.")
    extra = cfg.additional_config if isinstance(cfg.additional_config, dict) else {}
    jql = jql or extra.get("pr_jql_filter", "")
    if not jql:
        raise ServiceError(400, "No PR JQL filter provided.")
    try:
        resp = adapter._pr_request(
            "GET", adapter._url("search"),
            params={"jql": jql, "maxResults": 200,
                    "fields": "summary,priority,status,issuetype,created,description"},
        )
    except Exception as exc:
        raise ServiceError(500, f"Failed to fetch PR items: {exc}") from exc
    if resp.status_code >= 400:
        detail = _jira_error_detail(resp)
        raise ServiceError(400, f"Jira returned HTTP {resp.status_code}: {detail or 'no error body'}")
    try:
        issues = resp.json().get("issues", [])
    except Exception as exc:
        raise ServiceError(500, f"Failed to fetch PR items: {exc}") from exc
    base = (adapter.base_url or "").rstrip("/")
    result = []
    for issue in issues:
        fields = issue.get("fields", {})
        key = issue.get("key", "")
        raw_desc = fields.get("description")
        result.append({
            "key": key,
            "summary": fields.get("summary", ""),
            "priority": (fields.get("priority") or {}).get("name", "Medium"),
            "status": (fields.get("status") or {}).get("name", ""),
            "issue_type": (fields.get("issuetype") or {}).get("name", ""),
            "created": fields.get("created", ""),
            "description": "" if isinstance(raw_desc, dict) else (raw_desc or ""),
            "link": f"{base}/browse/{key}" if base and key else "",
        })
    return result
