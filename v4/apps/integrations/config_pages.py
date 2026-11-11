"""Integrations page (port of v3 ``pages/integrations.py``).

One tab per system (REDMINE, JIRA, EMAIL, OUTLINE, SNIPE_IT) with the v3
sections and fields. Tabs load as htmx partials and the selected tab is kept
in the URL (``/integrations?tab=JIRA``).

Any signed-in user may view the page. Saving, Test Connection, Sync, the
webhook secret and the Jira PR connection test need ADMIN, like
``PUT/POST /api/integrations/...``; everyone else gets a read-only view.

Saving writes the same shapes as ``PUT /api/integrations/{system}``: top-level
``base_url`` / ``username`` / ``api_key`` and the ``additional_config`` JSON
object built from the labelled fields with v3 key names and value types.
Keys the form does not manage are kept (v3 replaced the whole object).
The Redmine webhook watchers go to the ``webhook_watchers`` configuration key
as a JSON list of user ids, as v3 did through ``PUT /api/configuration``.

Secrets (``api_key``, ``pr_api_key``, ``webhook_secret``) are never written
into the page: it shows whether one is set, replaces it when a new value is
typed and clears it on request. A generated webhook secret is shown once, in
the response to the Generate action, so it can be copied into Redmine.
"""

from __future__ import annotations

import json
import re
import secrets as pysecrets
from dataclasses import dataclass, field
from typing import Any

from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.http import Http404, HttpResponse, JsonResponse
from django.shortcuts import render
from django.urls import path
from django.views.decorators.http import require_GET, require_POST

from apps.accounts.audit import log_action
from apps.accounts.models import Role, User
from apps.api.common import client_ip
from apps.core import config as core_config
from apps.core import services as core_services
from apps.core.errors import ServiceError
from apps.web.htmx import trigger

from . import service
from .models import IntegrationConfig

SYSTEMS: tuple[str, ...] = ("REDMINE", "JIRA", "EMAIL", "OUTLINE", "SNIPE_IT")
SYSTEM_ICONS = {
    "REDMINE": "bug_report", "JIRA": "view_kanban", "EMAIL": "email",
    "OUTLINE": "article", "SNIPE_IT": "inventory_2",
}
SYSTEM_LABELS = {
    "REDMINE": "Redmine", "JIRA": "Jira", "EMAIL": "Email", "OUTLINE": "Outline", "SNIPE_IT": "Snipe-IT",
}
TOP_LEVEL = ("base_url", "username", "api_key")
WEBHOOK_PATH = "/api/webhooks/redmine"
WRITE_ROLE = Role.ADMIN


# ── Field catalogue (v3 panels) ─────────────────────────────────────────────

@dataclass(frozen=True)
class Spec:
    key: str
    label: str
    kind: str = "text"   # text textarea int bool choice secret watchers categories
    placeholder: str = ""
    help: str = ""
    default: Any = ""
    min: int | None = None
    max: int | None = None
    choices: tuple[str, ...] = ()
    wide: bool = False   # spans the whole section row

    @property
    def top_level(self) -> bool:
        return self.key in TOP_LEVEL


@dataclass(frozen=True)
class SectionSpec:
    title: str
    fields: tuple[Spec, ...]
    help: str = ""
    extra: str = ""      # template hook: "webhook", "pr_actions", "category_fetch"


SSL = Spec("ssl_verify", "Verify SSL", "bool", default=True,
           help="Turn off only for self-signed / internal-CA certificates.")


def _url(placeholder: str, label: str = "Base URL") -> Spec:
    return Spec("base_url", label, placeholder=placeholder, wide=True)


def _key(label: str = "API Key", what: str = "API key") -> Spec:
    return Spec("api_key", label, "secret", help=f"Stored on the server; type a new {what} to replace it.",
                wide=True)


PANELS: dict[str, tuple[SectionSpec, ...]] = {
    "REDMINE": (
        SectionSpec("Connection Settings", (_url("https://redmine.example.com"), _key())),
        SectionSpec("Additional Settings", (
            Spec("project_id", "Project ID", placeholder="e.g., 1"),
            Spec("tracker_id", "Tracker ID", placeholder="e.g., 1"),
        )),
        SectionSpec("Auto-sync Settings", (
            Spec("poll_interval_minutes", "Polling Interval (minutes)", "int", default=0, min=0,
                 help="Set to 0 to disable auto-polling. Requires app restart to take effect."),
            Spec("webhook_secret", "Webhook Secret", "secret", wide=True,
                 help="Shared secret for webhook validation, sent by Redmine as ?token=."),
        ), extra="webhook"),
        SectionSpec("Webhook Notification Watchers", (
            Spec("webhook_watchers", "Watchers", "watchers", wide=True),
        ), help="Select users who should be notified when new requests are imported via webhook."),
        SectionSpec("Field Mappings", (
            Spec("effort_hours_field_id", "Effort Hours Field ID", placeholder="Custom field ID or 'estimated_hours'"),
            Spec("feasibility_field_id", "Feasibility Field ID", placeholder="Custom field ID"),
            Spec("estimation_number_field_id", "Estimation Number Field ID", placeholder="Custom field ID"),
        )),
        SectionSpec("Task Breakdown Export", (
            Spec("subtask_tracker_id", "Sub-task Tracker ID", placeholder="(uses parent issue tracker if empty)",
                 help="Redmine tracker ID for sub-tasks. Leave empty to use the same tracker as the parent issue.",
                 wide=True),
            SSL,
        ), help="Configure sub-task creation when exporting estimation task breakdown to Redmine."),
    ),
    "JIRA": (
        SectionSpec("Connection Settings", (
            _url("https://jira.example.com"), _key(), Spec("username", "Username", placeholder="Jira account username",
                                                           wide=True),
        )),
        SectionSpec("Additional Settings", (
            Spec("jql_filter", "JQL Filter", "textarea", wide=True,
                 placeholder='e.g., type = "Feature Request" AND status = "Open"'),
            Spec("project_key", "Project Key", placeholder="e.g., PROJ", wide=True),
        )),
        SectionSpec("Deployment Settings", (
            Spec("auth_mode", "Auth Mode", "choice", default="auto", choices=("auto", "basic", "pat")),
            Spec("issue_type", "Issue Type", placeholder="e.g., Story"),
            Spec("is_cloud", "Jira Cloud", "bool", default=False, help="Check if using Jira Cloud"),
            Spec("ssl_verify", "Verify SSL", "bool", default=True, help="Uncheck for self-signed certs"),
            Spec("bypass_proxy", "Bypass proxy", "bool", default=False,
                 help="Enable for internal Jira behind a corporate proxy (502 errors)."),
        )),
        SectionSpec("Field Mappings", (
            Spec("effort_hours_field", "Effort Hours Custom Field",
                 placeholder="customfield_10000 or 'originalEstimate'"),
            Spec("feasibility_field", "Feasibility Custom Field", placeholder="customfield_10001"),
            Spec("estimation_number_field", "Estimation Number Custom Field", placeholder="customfield_10002"),
        )),
        SectionSpec("X-Ray Integration", (
            Spec("xray_enabled", "Enable X-Ray Integration", "bool", default=False,
                 help="Enable test result sync with X-Ray"),
            Spec("xray_project_key", "X-Ray Project Key", placeholder="e.g., XRAY"),
        )),
        SectionSpec("Task Breakdown Export", (
            Spec("task_export_type", "Standalone Task Issue Type", placeholder="e.g. Todo, Task (auto-detect if empty)",
                 help="Issue type for standalone tasks (no parent). Auto-detected from project if empty."),
            Spec("subtask_type", "Sub-task Issue Type", default="Sub-task", placeholder="Sub-task",
                 help="Issue type when creating sub-tasks under a parent issue (default: Sub-task)."),
        ), help="Configure sub-task creation when exporting estimation task breakdown to Jira."),
        SectionSpec("Problem Reports (PR)", (
            Spec("pr_api_key", "PR API Key / Token", "secret", wide=True,
                 help="Optional separate API key for PR queries. Leave empty to use the connection API key."),
            Spec("pr_bypass_proxy", "PR bypass proxy", "bool", default=False,
                 help="Bypass the corporate proxy for PR queries (internal hosts / 502 errors)."),
            Spec("pr_jql_filter", "PR JQL Filter", "textarea",
                 placeholder='e.g., type = "Bug" AND status = "Open" AND project = PROJ', wide=True),
            Spec("pr_fields", "PR Additional Fields (comma-separated)",
                 placeholder="e.g., customfield_10100,priority,components", wide=True,
                 help="Optional Jira fields to fetch alongside summary, status, and priority. "
                      "These will be available as extra columns when importing PRs."),
        ), help="Configure a separate JQL query to import Problem Reports / Defects from Jira. "
                "These can be used as PR fix items in estimations.", extra="pr_actions"),
    ),
    "EMAIL": (
        SectionSpec("SMTP Settings", (
            Spec("smtp_host", "SMTP Host", placeholder="smtp.gmail.com"),
            Spec("smtp_port", "SMTP Port", "int", default=587, min=1, max=65535),
            Spec("smtp_use_tls", "Use TLS", "bool", default=True, help="Enable TLS encryption for SMTP"),
        )),
        SectionSpec("Authentication", (
            Spec("username", "SMTP Username", placeholder="your-email@example.com", wide=True),
            _key("SMTP Password", "password"),
        )),
        SectionSpec("Sender Settings", (
            Spec("sender_email", "Sender Email", placeholder="noreply@example.com"),
            Spec("sender_name", "Sender Name", placeholder="Estimation Tool"),
        )),
    ),
    "OUTLINE": (
        SectionSpec("Connection Settings", (_url("https://wiki.example.com", "Outline URL"), _key())),
        SectionSpec("Publishing Settings", (
            Spec("collection_id", "Collection ID", placeholder="UUID of target collection", wide=True),
            Spec("auto_publish", "Auto-publish on approval", "bool", default=False,
                 help="Automatically publish estimation to Outline when status changes to APPROVED"),
            SSL,
        )),
    ),
    "SNIPE_IT": (
        SectionSpec("Connection Settings", (_url("https://snipeit.example.com"), _key("API Key (Bearer Token)"))),
        SectionSpec("Asset Settings", (
            Spec("categories", "Categories (select from Snipe-IT)", "categories", wide=True,
                 help="Select categories to filter DUT assets. Click 'Fetch Categories' to load from Snipe-IT."),
            Spec("timeout", "Timeout (seconds)", "int", default=30, min=5, max=120),
            SSL,
        )),
    ),
}

# Top-level columns a panel does not edit are cleared on save, as v3 did.
CLEARED_TOP_LEVEL = {
    "REDMINE": ("username",), "OUTLINE": ("username",), "SNIPE_IT": ("username",), "EMAIL": ("base_url",),
}
# Saved value that Test / Sync need first (v3 required_check), as (location, key, message).
REQUIRED_FOR_ACTIONS = {
    "EMAIL": ("extra", "smtp_host", "SMTP Host is required before testing."),
    "OUTLINE": ("top", "base_url", "Outline URL is required before testing/syncing."),
}
DEFAULT_REQUIRED = ("top", "base_url", "Base URL is required before testing/syncing.")


def specs_for(system: str) -> list[Spec]:
    return [f for s in PANELS[system] for f in s.fields]


# ── Helpers ─────────────────────────────────────────────────────────────────

def can_edit(user) -> bool:
    return getattr(user, "role", None) == WRITE_ROLE


def _require_edit(request) -> None:
    if not can_edit(request.user):
        raise PermissionDenied


def _system_or_404(system: str) -> str:
    name = (system or "").upper()
    if name not in SYSTEMS:
        raise Http404(f"Integration {system} not found")
    return name


def _row(system: str) -> IntegrationConfig | None:
    return IntegrationConfig.objects.filter(system_name=system).first()


def _extra(row: IntegrationConfig | None) -> dict:
    if row is None:
        return {}
    value = row.additional_config
    if isinstance(value, str):
        try:
            value = json.loads(value or "{}")
        except json.JSONDecodeError:
            return {}
    return dict(value) if isinstance(value, dict) else {}


def _as_bool(value: Any, default: bool) -> bool:
    if value is None or value == "":
        return default
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


def _toast(response: HttpResponse, message: str, level: str = "success", **events) -> HttpResponse:
    return trigger(response, toast={"message": message, "level": level}, **events)


_POOL_ERROR = re.compile(r"HTTPS?ConnectionPool\(host='([^']+)', port=(\d+)\)")
_REASONS = (
    (re.compile(r"\[Errno -?\d+\] ([^'\")]+)"), None),
    (re.compile(r"(Read timed out|Connect timeout|timed out)", re.I), "timed out"),
    (re.compile(r"(CERTIFICATE_VERIFY_FAILED|SSLError)", re.I), "TLS certificate verification failed"),
    (re.compile(r"(Name or service not known|nodename nor servname|getaddrinfo failed)", re.I), "host name not found"),
)


def clean_error(message: str) -> str:
    """Shorten raw ``requests``/urllib3 connection errors to "Cannot reach host:port (reason)"."""
    text = str(message or "")
    pool = _POOL_ERROR.search(text)
    if not pool:
        return text
    reason = "connection failed"
    for pattern, label in _REASONS:
        found = pattern.search(text)
        if found:
            reason = label or found.group(1).strip()
            break
    prefix = text[:pool.start()].rstrip(" :")
    cleaned = f"Cannot reach {pool.group(1)}:{pool.group(2)} ({reason})"
    return f"{prefix}: {cleaned}" if prefix else cleaned


def _prefixed(prefix: str, message: str) -> str:
    """``prefix: message`` without repeating a prefix the adapter already wrote."""
    return message if message.lower().startswith(prefix.lower()) else f"{prefix}: {message}"


def _detail(message: str, status: int) -> JsonResponse:
    return JsonResponse({"detail": message}, status=status)


def watcher_ids() -> list[int]:
    raw = core_config.get_json("webhook_watchers", [])
    if not isinstance(raw, list):
        return []
    ids = []
    for v in raw:
        try:
            ids.append(int(v))
        except (TypeError, ValueError):
            continue
    return ids


def _user_label(u: User) -> str:
    return u.display_name or u.username


def webhook_base_url(request) -> str:
    return request.build_absolute_uri(WEBHOOK_PATH)


# ── View model ──────────────────────────────────────────────────────────────

@dataclass
class FieldView:
    spec: Spec
    value: str = ""
    checked: bool = False
    is_set: bool = False
    error: str = ""
    options: list[dict] = field(default_factory=list)   # watchers / categories

    @property
    def key(self) -> str:
        return self.spec.key

    @property
    def kind(self) -> str:
        return self.spec.kind

    @property
    def selected_labels(self) -> list[str]:
        return [o["label"] for o in self.options if o["selected"]]


@dataclass
class SectionView:
    title: str
    help: str
    extra: str
    fields: list[FieldView]


def _category_options(selected: list[str], fetched: list[str] | None = None) -> list[dict]:
    names = list(dict.fromkeys([*selected, *sorted(fetched or [])]))
    return [{"value": n, "label": n, "selected": n in selected} for n in names]


def build_panel(system: str, post=None, errors: dict[str, str] | None = None) -> dict:
    """Sections, enabled flag and status for one system; ``post`` re-shows submitted values."""
    errors = errors or {}
    row = _row(system)
    extra = _extra(row)
    sections = []
    for sec in PANELS[system]:
        views = []
        for spec in sec.fields:
            fv = FieldView(spec, error=errors.get(spec.key, ""))
            stored = getattr(row, spec.key, None) if spec.top_level and row else extra.get(spec.key)
            if spec.kind == "secret":
                fv.is_set = bool(stored)
            elif spec.kind == "bool":
                if post is not None:
                    fv.checked = "true" in post.getlist(spec.key)
                else:
                    fv.checked = _as_bool(stored, bool(spec.default))
            elif spec.kind == "watchers":
                chosen = set(watcher_ids())
                if post is not None:
                    chosen = {int(v) for v in post.getlist(spec.key) if str(v).isdigit()}
                users = sorted(User.objects.filter(is_active=True), key=lambda u: _user_label(u).lower())
                fv.options = [{"value": str(u.pk), "label": _user_label(u), "sub": u.username,
                               "selected": u.pk in chosen} for u in users]
            elif spec.kind == "categories":
                saved = [c.strip() for c in str(stored or "").split(",") if c.strip()]
                if post is not None:
                    saved = [c for c in post.getlist(spec.key) if c.strip()]
                fv.options = _category_options(saved)
            elif post is not None and spec.key in post:
                fv.value = post.get(spec.key, "")
            elif stored is None or stored == "":
                fv.value = str(spec.default)
            else:
                fv.value = str(stored)
            views.append(fv)
        sections.append(SectionView(sec.title, sec.help, sec.extra, views))

    enabled = bool(row.enabled) if row else False
    if post is not None:
        enabled = "true" in post.getlist("enabled")
    return {
        "system": system,
        "label": SYSTEM_LABELS[system],
        "icon": SYSTEM_ICONS[system],
        "sections": sections,
        "enabled": enabled,
        "row": row,
        "last_sync_at": row.last_sync_at if row else None,
        "webhook_secret_set": bool(extra.get("webhook_secret")),
    }


def strip_cells() -> list[dict]:
    rows = {r.system_name: r for r in IntegrationConfig.objects.filter(system_name__in=SYSTEMS)}
    return [{
        "system": s, "icon": SYSTEM_ICONS[s],
        "enabled": bool(rows[s].enabled) if s in rows else False,
        "last_sync_at": rows[s].last_sync_at if s in rows else None,
        "configured": bool(s in rows and (rows[s].base_url or rows[s].api_key)),
    } for s in SYSTEMS]


def _context(request, system: str, **extra) -> dict:
    cells = strip_cells()
    ctx = {
        "page_title": "Integrations",
        "systems": [{"name": s, "icon": SYSTEM_ICONS[s]} for s in SYSTEMS],
        "active": system,
        "cells": cells,
        "enabled_count": sum(1 for c in cells if c["enabled"]),
        "total_systems": len(SYSTEMS),
        "can_edit": can_edit(request.user),
        "webhook_base": webhook_base_url(request),
        "p": build_panel(system, extra.pop("post", None), extra.pop("errors", None)),
    }
    ctx.update(extra)
    return ctx


# ── Parsing a save ──────────────────────────────────────────────────────────

class Invalid(Exception):
    pass


def _parse_int(spec: Spec, text: str) -> int:
    text = (text or "").strip()
    if not text:
        return int(spec.default or 0)
    try:
        number = int(text)
    except ValueError:
        try:
            as_float = float(text)
        except ValueError:
            raise Invalid("Enter a whole number.") from None
        if not as_float.is_integer():
            raise Invalid("Enter a whole number.") from None
        number = int(as_float)
    if spec.min is not None and number < spec.min:
        raise Invalid(f"Must be at least {spec.min}.")
    if spec.max is not None and number > spec.max:
        raise Invalid(f"Must be at most {spec.max}.")
    return number


def parse_save(system: str, post, row: IntegrationConfig | None) -> tuple[dict, dict, list[int] | None, dict]:
    """Return ``(top_level, additional, watchers, errors)`` for a submitted panel.

    ``top_level`` maps column names to new values (``api_key`` only when it
    changes); ``additional`` is the full new ``additional_config`` object.
    """
    errors: dict[str, str] = {}
    top: dict[str, Any] = {name: None for name in CLEARED_TOP_LEVEL.get(system, ())}
    extra = _extra(row)
    watchers: list[int] | None = None
    for spec in specs_for(system):
        key = spec.key
        try:
            if spec.kind == "secret":
                new = (post.get(key) or "").strip()
                if post.get(f"{key}__clear"):
                    if spec.top_level:
                        top[key] = None
                    elif key == "webhook_secret":
                        extra[key] = ""        # v3 always writes the key
                    else:
                        extra.pop(key, None)   # v3 omits an empty pr_api_key
                elif new:
                    if spec.top_level:
                        top[key] = new
                    else:
                        extra[key] = new
                continue
            if spec.kind == "bool":
                value: Any = "true" in post.getlist(key)
            elif spec.kind == "int":
                value = _parse_int(spec, post.get(key, ""))
            elif spec.kind == "choice":
                value = (post.get(key) or spec.default).strip()
                if value not in spec.choices:
                    raise Invalid(f"Choose one of: {', '.join(spec.choices)}.")
            elif spec.kind == "categories":
                value = ", ".join(c.strip() for c in post.getlist(key) if c.strip())
            elif spec.kind == "watchers":
                ids = []
                for raw in post.getlist(key):
                    if not str(raw).isdigit():
                        raise Invalid("Choose users from the list.")
                    ids.append(int(raw))
                known = set(User.objects.filter(pk__in=ids).values_list("pk", flat=True))
                if set(ids) - known:
                    raise Invalid("Choose users from the list.")
                watchers = list(dict.fromkeys(ids))
                continue
            else:
                value = (post.get(key) or "").strip()
                if spec.default and not value and spec.kind == "text":
                    value = spec.default   # v3: subtask_type falls back to "Sub-task"
        except Invalid as exc:
            errors[key] = str(exc)
            continue
        if spec.top_level:
            top[key] = value or None
        else:
            extra[key] = value
    top["enabled"] = "true" in post.getlist("enabled")
    return top, extra, watchers, errors


# ── Views ───────────────────────────────────────────────────────────────────

@require_GET
def integrations_page(request):
    system = _system_or_404(request.GET.get("tab") or SYSTEMS[0])
    return render(request, "integrations_config/page.html", _context(request, system))


@require_GET
def integrations_tab(request, system: str):
    system = _system_or_404(system)
    return render(request, "integrations_config/partials/_region.html", _context(request, system))


@require_POST
def integrations_save(request, system: str):
    _require_edit(request)
    system = _system_or_404(system)
    row = _row(system)
    top, extra, watchers, errors = parse_save(system, request.POST, row)
    if errors:
        ctx = _context(request, system, post=request.POST, errors=errors, error_count=len(errors))
        resp = render(request, "integrations_config/partials/_panel.html", ctx, status=422)
        noun = "field needs" if len(errors) == 1 else "fields need"
        return _toast(resp, f"Not saved: {len(errors)} {noun} attention.", "error")

    with transaction.atomic():
        cfg = row or IntegrationConfig(system_name=system)
        for key, value in top.items():
            setattr(cfg, key, value)
        cfg.additional_config = extra
        cfg.save()
        if watchers is not None:
            core_services.update_configuration("webhook_watchers", json.dumps(watchers))
    log_action(request.user, "UPDATE", "integration", cfg.pk,
               {"system": system, "fields": sorted(top), "additional_keys": sorted(extra)}, client_ip(request))
    ctx = _context(request, system, oob=True)
    resp = render(request, "integrations_config/partials/_panel.html", ctx)
    return _toast(resp, "Credentials saved securely.")


def _missing_required(system: str) -> str:
    row = _row(system)
    if row is None or not row.enabled:
        return ""  # the service answers "not configured or not enabled"
    where, key, message = REQUIRED_FOR_ACTIONS.get(system, DEFAULT_REQUIRED)
    value = getattr(row, key, None) if where == "top" else _extra(row).get(key)
    return "" if str(value or "").strip() else message


def _result(request, system: str, result: dict, toast: str, level: str, oob: bool = False) -> HttpResponse:
    ctx = {"system": system, "r": result}
    if oob:  # last sync time changed: refresh the status strip and the panel's sync label
        cells = strip_cells()
        row = _row(system)
        ctx.update(cells=cells, enabled_count=sum(1 for c in cells if c["enabled"]),
                   total_systems=len(SYSTEMS), oob=True,
                   p={"system": system, "last_sync_at": row.last_sync_at if row else None})
    resp = render(request, "integrations_config/partials/_result.html", ctx)
    return _toast(resp, toast, level)


@require_POST
def integrations_test(request, system: str):
    _require_edit(request)
    system = _system_or_404(system)
    missing = _missing_required(system)
    if missing:
        return _result(request, system, {"kind": "test", "ok": False, "message": missing},
                       f"{missing} Save it first.", "warning")
    try:
        res = service.test_integration(system)
        ok, message, details = bool(res.success), clean_error(res.message or ""), dict(res.details or {})
    except Exception as exc:  # noqa: BLE001  (adapter errors become a clean message)
        ok, message, details = False, clean_error(f"Test error: {exc}"), {}
    result = {"kind": "test", "ok": ok, "message": message,
              "details": [(k, v) for k, v in details.items() if not isinstance(v, (dict, list))]}
    toast = _prefixed("Connection OK", message) if ok else _prefixed("Connection failed", message)
    return _result(request, system, result, toast, "success" if ok else "warning")


@require_POST
def integrations_sync(request, system: str):
    _require_edit(request)
    system = _system_or_404(system)
    missing = _missing_required(system)
    if missing:
        return _result(request, system, {"kind": "sync", "ok": False, "message": missing},
                       f"{missing} Save it first.", "warning")
    try:
        res = service.sync_export_all(system) if system == "OUTLINE" else service.sync_import(system)
    except Exception as exc:  # noqa: BLE001
        msg = clean_error(f"Sync error: {exc}")
        return _result(request, system, {"kind": "sync", "ok": False, "message": msg}, msg, "error")
    status = res.status.value if hasattr(res.status, "value") else str(res.status)
    result = {
        "kind": "sync", "ok": status == "SUCCESS", "status": status,
        "processed": res.items_processed, "created": res.items_created,
        "updated": res.items_updated, "failed": res.items_failed, "errors": [clean_error(e) for e in res.errors or []],
    }
    summary = (f"Sync {status} — {res.items_processed} processed, {res.items_created} created, "
               f"{res.items_updated} updated, {res.items_failed} failed.")
    if result["errors"]:
        summary += f" Errors: {'; '.join(result['errors'][:3])}"
    result["message"] = summary
    level = "success" if status == "SUCCESS" else ("warning" if res.items_failed or status == "FAILED" else "info")
    return _result(request, system, result, summary, level, oob=True)


@require_POST
def integrations_webhook_secret(request):
    """Generate and save a new Redmine webhook secret; the response shows it once."""
    _require_edit(request)
    secret = pysecrets.token_urlsafe(32)
    with transaction.atomic():
        cfg = _row("REDMINE") or IntegrationConfig(system_name="REDMINE")
        extra = _extra(cfg if cfg.pk else None)
        extra["webhook_secret"] = secret
        cfg.additional_config = extra
        cfg.save()
    log_action(request.user, "UPDATE", "integration", cfg.pk,
               {"system": "REDMINE", "fields": ["webhook_secret"]}, client_ip(request))
    p = build_panel("REDMINE")
    ctx = {"p": p, "can_edit": True, "webhook_base": webhook_base_url(request), "revealed": secret,
           "f": next(f for s in p["sections"] for f in s.fields if f.key == "webhook_secret")}
    resp = render(request, "integrations_config/partials/_webhook.html", ctx)
    return _toast(resp, "New webhook secret saved. Copy the URL into Redmine now: it is shown only once.")


@require_POST
def jira_pr_test(request):
    """Test the Jira PR connection (``POST /api/integrations/JIRA/pr-test-connection``)."""
    _require_edit(request)
    from .adapters import JiraAdapter

    row = _row("JIRA")
    if not row or not row.enabled:
        msg = "JIRA integration not configured or not enabled."
        return _result(request, "JIRA", {"kind": "pr", "ok": False, "message": msg}, f"PR test failed: {msg}",
                       "warning")
    adapter = service.get_adapter("JIRA")
    if not isinstance(adapter, JiraAdapter):
        return _detail("JIRA integration not available.", 400)
    try:
        res = adapter.test_pr_connection()
        ok, message = bool(res.success), clean_error(res.message or "")
    except Exception as exc:  # noqa: BLE001
        ok, message = False, clean_error(f"PR test failed: {exc}")
    toast = _prefixed(f"PR connection {'OK' if ok else 'failed'}", message)
    return _result(request, "JIRA", {"kind": "pr", "ok": ok, "message": message}, toast,
                   "success" if ok else "warning")


@require_GET
def jira_pr_fetch(request):
    """Count the PR items the (unsaved) PR JQL filter returns (``GET /api/integrations/JIRA/pr-items``)."""
    jql = (request.GET.get("pr_jql_filter") or "").strip()
    if not jql:
        return _detail("PR JQL Filter is empty.", 400)
    try:
        items = service.jira_pr_items(jql)
    except ServiceError as exc:
        return _detail(clean_error(f"PR fetch failed: {exc.detail}"), exc.status_code)
    count = len(items)
    message = f"Fetched {count} PR item(s)"
    toast = f"Found {count} PR item(s)." if count else "No items found matching the filter."
    return _result(request, "JIRA", {"kind": "pr", "ok": True, "message": message}, toast,
                   "success" if count else "info")


@require_GET
def snipeit_categories(request):
    """Reload the category picker with the names from Snipe-IT, keeping the current selection."""
    from .adapters import SnipeItAdapter

    selected = [c for c in request.GET.getlist("categories") if c.strip()]
    adapter = service.get_adapter("SNIPE_IT")
    if not adapter:
        return _detail("Snipe-IT integration is not configured or not enabled.", 400)
    if not isinstance(adapter, SnipeItAdapter):
        return _detail("Invalid Snipe-IT adapter", 500)
    try:
        cats = adapter.get_categories()
    except Exception as exc:  # noqa: BLE001
        return _detail(clean_error(f"Failed to fetch categories: {exc}"), 502)
    names = sorted({str(c.get("name")) for c in cats or [] if isinstance(c, dict) and c.get("name")})
    spec = next(s for s in specs_for("SNIPE_IT") if s.key == "categories")
    fv = FieldView(spec, options=_category_options(selected, names))
    resp = render(request, "integrations_config/partials/_categories.html",
                  {"f": fv, "can_edit": can_edit(request.user)})
    if names:
        return _toast(resp, f"Loaded {len(names)} categories from Snipe-IT.")
    return _toast(resp, "No items found matching the filter.", "info")


urlpatterns = [
    path("integrations", integrations_page, name="integrations"),
    path("integrations/tab/<str:system>", integrations_tab, name="integrations_tab"),
    path("integrations/<str:system>/save", integrations_save, name="integrations_save"),
    path("integrations/<str:system>/test", integrations_test, name="integrations_test"),
    path("integrations/<str:system>/sync", integrations_sync, name="integrations_sync"),
    path("integrations/redmine/webhook-secret", integrations_webhook_secret, name="integrations_webhook_secret"),
    path("integrations/jira/pr-test", jira_pr_test, name="integrations_jira_pr_test"),
    path("integrations/jira/pr-fetch", jira_pr_fetch, name="integrations_jira_pr_fetch"),
    path("integrations/snipe-it/categories", snipeit_categories, name="integrations_snipeit_categories"),
]
