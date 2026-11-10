"""Settings and Backup & Restore pages (port of v3 ``pages/settings.py`` and ``pages/backup.py``).

Settings
    Every configuration key, grouped into collapsible sections as v3
    ``SECTION_KEYS`` does, each edited with an input that matches its type.
    Any signed-in user may view the page; saving and the connection / maintenance
    actions need ADMIN, like ``PUT /api/configuration/{key}``. Secret values are
    never written into the HTML: the page shows whether one is set and only
    overwrites it when a new value is typed.

Backup & Restore (ADMIN)
    Download the v3-format JSON backup; restore from a file in two steps (the
    file is checked and summarised first, nothing changes until confirmed).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.http import Http404, HttpResponse, JsonResponse
from django.shortcuts import redirect, render
from django.urls import path, reverse
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from apps.accounts.audit import log_action
from apps.accounts.models import Role
from apps.api.common import client_ip

from . import backup as backup_service
from . import services
from .errors import ServiceError
from .models import Configuration

# ── Key catalogue ────────────────────────────────────────────────────────────

APP_ROLES = ("ADMIN", "APPROVER", "ESTIMATOR", "VIEWER")

# Section order and fixed members, as in v3 SECTION_KEYS. The prefix sections
# collect ``smtp_*``, ``ldap_*`` and ``oidc_*``; everything else goes to Other.
SECTION_KEYS: dict[str, list[str]] = {
    "Estimation Parameters": [
        "leader_effort_ratio", "new_feature_study_hours", "working_hours_per_day",
        "buffer_percentage", "pr_fix_base_hours", "estimation_number_prefix",
        "pr_priority_list", "pr_hours_simple", "pr_hours_medium", "pr_hours_complex",
        "pr_no_test_hours", "pr_no_test_task_template_id", "release_effort_factor",
        "release_effort_task_types",
    ],
    "Workflow Automation": ["outline_auto_export_states", "auto_create_historical_project"],
    "Branding": ["logo_url", "logo_height"],
    "Appearance": [
        "table_header_bg_light", "table_header_bg_dark", "sidebar_bg_light", "sidebar_bg_dark",
        "content_bg_light", "content_bg_dark", "button_color_light", "button_color_dark",
        "calendar_today_bg_light", "calendar_today_bg_dark",
        "calendar_weekend_bg_light", "calendar_weekend_bg_dark",
    ],
    "Data Management": ["dut_categories", "feature_categories", "project_types"],
    "Field Labels": ["label_project_start_date", "label_testing_start_date", "label_deadline"],
    "Email / SMTP": [],
    "LDAP": [],
    "OIDC": [],
    "Other": [],
}
PREFIX_SECTIONS = {"smtp_": "Email / SMTP", "ldap_": "LDAP", "oidc_": "OIDC"}
SECTION_ICONS = {
    "Estimation Parameters": "calculate", "Workflow Automation": "bolt", "Branding": "image",
    "Appearance": "palette", "Data Management": "category", "Field Labels": "label",
    "Email / SMTP": "mail", "LDAP": "domain", "OIDC": "key", "Other": "settings",
}
# Role-mapping keys get their own matrix sections after the others (v3 _MAPPING_META).
ROLE_MAPPINGS = {
    "ldap_group_mapping_json": (
        "LDAP Group Mapping", "domain",
        "Map application roles to Active Directory / LDAP group DNs. "
        "Example: CN=EstimationAdmins,OU=Groups,DC=example,DC=com",
    ),
    "oidc_role_mapping_json": (
        "OIDC Role Mapping", "vpn_key",
        "Map application roles to OIDC claim values. "
        "Enter the value from the configured role claim (e.g. estimation-admin).",
    ),
}


@dataclass(frozen=True)
class Spec:
    kind: str = "text"  # text float int bool choice list jsonlist json color secret roles template readonly
    min: float | None = None
    max: float | None = None
    required: bool = False
    choices: tuple[str, ...] = ()
    clearable: bool = True  # secrets: offer "clear"
    placeholder: str = ""


def _float(lo: float | None = 0, hi: float | None = None) -> Spec:
    return Spec("float", min=lo, max=hi, required=True)


SPECS: dict[str, Spec] = {
    # Estimation parameters
    "leader_effort_ratio": _float(0, 10),
    "new_feature_study_hours": _float(0),
    "working_hours_per_day": _float(0.5, 24),
    "buffer_percentage": _float(0, 100),
    "pr_fix_base_hours": _float(0),
    "pr_hours_simple": _float(0),
    "pr_hours_medium": _float(0),
    "pr_hours_complex": _float(0),
    "pr_no_test_hours": _float(0),
    "release_effort_factor": _float(0, 10),
    "pr_no_test_task_template_id": Spec("template"),
    "estimation_number_prefix": Spec(required=True),
    "request_number_prefix": Spec(required=True),
    "pr_priority_list": Spec("list", required=True),
    "release_effort_task_types": Spec("list"),
    "pr_scales_with_profile": Spec("bool"),
    # Workflow
    "outline_auto_export_states": Spec("list", placeholder="FINAL,APPROVED"),
    "auto_create_historical_project": Spec("choice", choices=("manual", "on_approve", "on_complete")),
    # Branding
    "logo_url": Spec("logo"),
    "logo_height": Spec("int", min=10, max=400, required=True),
    # Data management
    "dut_categories": Spec("list", required=True),
    "feature_categories": Spec("list", required=True),
    "project_types": Spec("list", required=True),
    "product_types": Spec("jsonlist", required=True),
    "team_skills": Spec("jsonlist", required=True),
    "webhook_watchers": Spec("json"),
    # Field labels (wizard date labels)
    "label_project_start_date": Spec(required=True),
    "label_testing_start_date": Spec(required=True),
    "label_deadline": Spec(required=True),
    # SMTP
    "smtp_port": Spec("int", min=1, max=65535, required=True),
    "smtp_tls": Spec("bool"),
    "smtp_password": Spec("secret"),
    # LDAP
    "ldap_bind_password": Spec("secret"),
    "ldap_user_filter": Spec(placeholder="(sAMAccountName={username})"),
    "ldap_group_mapping_json": Spec("roles"),
    # OIDC
    "oidc_client_secret": Spec("secret"),
    "oidc_role_mapping_json": Spec("roles"),
    # Other
    "jwt_secret": Spec("secret", clearable=False),
    "schema_version": Spec("readonly"),
}
COLOR_RE = re.compile(r"^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})$")
SECRET_KEYS = frozenset(k for k, s in SPECS.items() if s.kind == "secret")


def spec_for(key: str) -> Spec:
    if key in SPECS:
        return SPECS[key]
    if "_bg_" in key or "_color_" in key:  # v3 colour-picker rule
        return Spec("color", required=True)
    if key.endswith("_json"):
        return Spec("json")
    if any(word in key for word in ("password", "secret")):
        return Spec("secret")
    return Spec()


def classify(key: str) -> str:
    for section, keys in SECTION_KEYS.items():
        if key in keys:
            return section
    for prefix, section in PREFIX_SECTIONS.items():
        if key.startswith(prefix):
            return section
    return "Other"


# ── Value handling ──────────────────────────────────────────────────────────

def _split(text: str) -> list[str]:
    return [p.strip() for p in str(text or "").split(",") if p.strip()]


def _json_items(raw: str) -> list[str]:
    try:
        value = json.loads(raw or "[]")
    except (TypeError, json.JSONDecodeError):
        return _split(raw)
    return [str(v) for v in value] if isinstance(value, list) else []


def _mapping(raw: str) -> dict[str, str]:
    try:
        value = json.loads(raw or "{}")
    except (TypeError, json.JSONDecodeError):
        return {}
    return {str(k): str(v) for k, v in value.items()} if isinstance(value, dict) else {}


def display_value(spec: Spec, raw: str) -> str:
    """Stored text as shown in the input."""
    if spec.kind == "jsonlist":
        return ", ".join(_json_items(raw))
    if spec.kind == "list":
        return ", ".join(_split(raw))
    return raw or ""


def canonical(spec: Spec, raw: str) -> Any:
    """Comparable form of a stored value (so formatting-only edits are not saves)."""
    raw = raw or ""
    if spec.kind == "list":
        return _split(raw)
    if spec.kind == "jsonlist":
        return _json_items(raw)
    if spec.kind == "roles":
        return _mapping(raw)
    if spec.kind == "json":
        try:
            return json.loads(raw) if raw.strip() else None
        except json.JSONDecodeError:
            return raw
    if spec.kind == "bool":
        return raw.strip().lower() in {"1", "true", "yes", "on"}
    if spec.kind == "color":
        return raw.strip().lower()
    return raw.strip()


class Invalid(Exception):
    pass


def parse_value(key: str, spec: Spec, post) -> str | None:
    """New stored text for ``key`` from the POST data; None when the form did not send it.

    Raises :class:`Invalid` with a message for the field.
    """
    if spec.kind == "readonly":
        return None
    if spec.kind == "bool":
        values = post.getlist(key)
        return None if not values else ("true" if "true" in values else "false")
    if spec.kind == "roles":
        names = [f"{key}__{r}" for r in APP_ROLES]
        if not any(n in post for n in names):
            return None
        mapping = {r: post.get(f"{key}__{r}", "").strip() for r in APP_ROLES}
        return json.dumps({r: v for r, v in mapping.items() if v}, separators=(",", ":"))
    if spec.kind == "secret":
        if spec.clearable and post.get(f"{key}__clear"):
            return ""
        value = post.get(key, "")
        return value if value.strip() else None  # blank = keep the stored secret
    if key not in post:
        return None
    text = post.get(key, "").strip()
    if not text:
        if spec.required:
            raise Invalid("A value is required.")
        return ""
    kind = spec.kind
    if kind in ("float", "int"):
        try:
            number = float(text) if kind == "float" else int(text)
        except ValueError:
            raise Invalid("Enter a whole number." if kind == "int" else "Enter a number.") from None
        if spec.min is not None and number < spec.min:
            raise Invalid(f"Must be at least {_num(spec.min)}.")
        if spec.max is not None and number > spec.max:
            raise Invalid(f"Must be at most {_num(spec.max)}.")
        return text
    if kind == "color":
        if not COLOR_RE.match(text):
            raise Invalid("Enter a hex colour such as #1976D2.")
        return text
    if kind == "choice":
        if text not in spec.choices:
            raise Invalid(f"Choose one of: {', '.join(spec.choices)}.")
        return text
    if kind == "template":
        from apps.catalog.models import TaskTemplate

        if not text.isdigit() or not TaskTemplate.objects.filter(pk=int(text)).exists():
            raise Invalid("Choose an existing task template.")
        return text
    if kind == "list":
        items = _split(text)
        if spec.required and not items:
            raise Invalid("Enter at least one value.")
        return ",".join(items)
    if kind == "jsonlist":
        items = _split(text)
        if spec.required and not items:
            raise Invalid("Enter at least one value.")
        return json.dumps(items)
    if kind == "json":
        try:
            value = json.loads(text)
        except json.JSONDecodeError as exc:
            raise Invalid(f"Invalid JSON: {exc.msg} (line {exc.lineno}, column {exc.colno}).") from None
        if key == "webhook_watchers" and not (
            isinstance(value, list) and all(isinstance(v, int) and not isinstance(v, bool) for v in value)
        ):
            raise Invalid("Enter a JSON array of user IDs, e.g. [1, 4].")
        return text
    if key == "ldap_user_filter" and "{username}" not in text:
        raise Invalid("The filter must contain {username}.")
    return text


def _num(value: float) -> str:
    return str(int(value)) if float(value).is_integer() else str(value)


# ── View model ──────────────────────────────────────────────────────────────

@dataclass
class Field:
    key: str
    spec: Spec
    description: str
    value: str = ""
    error: str = ""
    is_set: bool = False          # secrets
    roles: list[tuple[str, str]] = field(default_factory=list)
    choices: list[tuple[str, str]] = field(default_factory=list)

    @property
    def kind(self) -> str:
        return self.spec.kind

    @property
    def checked(self) -> bool:
        return self.value.strip().lower() in {"1", "true", "yes", "on"}

    @property
    def picker_value(self) -> str:
        v = self.value.strip()
        if re.match(r"^#[0-9a-fA-F]{6}$", v):
            return v
        if re.match(r"^#[0-9a-fA-F]{3}$", v):
            return "#" + "".join(c * 2 for c in v[1:])
        return "#000000"


@dataclass
class Section:
    name: str
    icon: str
    fields: list[Field]
    help: str = ""
    has_error: bool = False


def _template_choices(current: str) -> list[tuple[str, str]]:
    from apps.catalog.models import TaskTemplate

    choices = [("", "— none —")] + [
        (str(t.pk), f"#{t.pk} · {t.name}") for t in TaskTemplate.objects.order_by("name")
    ]
    if current and current not in dict(choices):
        choices.append((current, f"#{current} (missing)"))
    return choices


def build_sections(post=None, errors: dict[str, str] | None = None) -> tuple[list[Section], int]:
    """Sections for the page; with ``post`` the submitted values are shown (validation re-render)."""
    errors = errors or {}
    rows = list(Configuration.objects.order_by("key"))
    grouped: dict[str, list[Field]] = {name: [] for name in SECTION_KEYS}
    order = {k: i for keys in SECTION_KEYS.values() for i, k in enumerate(keys)}
    mapping_fields: dict[str, Field] = {}
    for row in rows:
        spec = spec_for(row.key)
        raw = row.value or ""
        f = Field(row.key, spec, row.description or "", error=errors.get(row.key, ""))
        if spec.kind == "secret":
            f.is_set = bool(raw)
        elif spec.kind == "roles":
            current = _mapping(raw)
            if post is not None and any(f"{row.key}__{r}" in post for r in APP_ROLES):
                current = {r: post.get(f"{row.key}__{r}", "") for r in APP_ROLES}
            f.roles = [(r, current.get(r, "")) for r in APP_ROLES]
            mapping_fields[row.key] = f
            continue
        elif spec.kind == "bool" and post is not None and post.getlist(row.key):
            f.value = "true" if "true" in post.getlist(row.key) else "false"
        elif post is not None and row.key in post and spec.kind != "readonly":
            f.value = post.get(row.key, "")
        else:
            f.value = display_value(spec, raw)
        if spec.kind == "template":
            f.choices = _template_choices(f.value)
        grouped[classify(row.key)].append(f)

    sections = []
    for name, fields in grouped.items():
        if not fields:
            continue
        fields.sort(key=lambda f: (order.get(f.key, 999), f.key))
        sections.append(Section(name, SECTION_ICONS.get(name, "settings"), fields,
                                has_error=any(f.error for f in fields)))
    for key, (title, icon, help_text) in ROLE_MAPPINGS.items():
        if key in mapping_fields:
            f = mapping_fields[key]
            sections.append(Section(title, icon, [f], help=help_text, has_error=bool(f.error)))
    return sections, len(rows)


# ── Settings views ──────────────────────────────────────────────────────────

def _is_admin(user) -> bool:
    return getattr(user, "role", None) == Role.ADMIN


def _require_admin(request) -> None:
    if not _is_admin(request.user):
        raise PermissionDenied


def _is_htmx(request) -> bool:
    return request.headers.get("HX-Request") == "true"


def _toast(response: HttpResponse, message: str, level: str = "success", **events) -> HttpResponse:
    response["HX-Trigger"] = json.dumps({"toast": {"message": message, "level": level}, **events})
    return response


def _settings_context(request, sections, total, **extra) -> dict:
    return {"page_title": "Settings", "sections": sections, "total_keys": total,
            "can_edit": _is_admin(request.user), **extra}


@require_GET
def settings_page(request):
    sections, total = build_sections()
    return render(request, "admin_system/settings.html", _settings_context(request, sections, total))


@require_POST
def settings_save(request):
    """Validate every submitted key, then save the changed ones together (or none)."""
    _require_admin(request)
    post = request.POST
    errors: dict[str, str] = {}
    changes: dict[str, str] = {}
    for row in Configuration.objects.order_by("key"):
        spec = spec_for(row.key)
        try:
            new = parse_value(row.key, spec, post)
        except Invalid as exc:
            errors[row.key] = str(exc)
            continue
        if new is None:
            continue
        if spec.kind == "secret":
            if new != (row.value or ""):
                changes[row.key] = new
        elif canonical(spec, new) != canonical(spec, row.value or ""):
            changes[row.key] = new

    if errors:
        sections, total = build_sections(post, errors)
        ctx = _settings_context(request, sections, total, error_count=len(errors))
        template = "admin_system/partials/_settings_form.html" if _is_htmx(request) else "admin_system/settings.html"
        resp = render(request, template, ctx, status=422)
        if _is_htmx(request):
            noun = "field needs" if len(errors) == 1 else "fields need"
            _toast(resp, f"Not saved: {len(errors)} {noun} attention.", "error")
        return resp

    if not changes:
        if _is_htmx(request):
            return _toast(HttpResponse(status=204), "No changes detected.", "info")
        messages.info(request, "No changes detected.")
        return redirect("settings")

    with transaction.atomic():
        for key, value in changes.items():
            services.update_configuration(key, value)
    log_action(request.user, "UPDATE", "configuration", None,
               {"keys": sorted(changes)}, client_ip(request))
    # Reload (as v3 did) so theme colours, logo and labels apply everywhere.
    messages.success(request, f"Saved {len(changes)} configuration key(s).")
    if _is_htmx(request):
        resp = HttpResponse(status=204)
        resp["HX-Redirect"] = reverse("settings")
        return resp
    return redirect("settings")


def _logo_field() -> Field:
    row = Configuration.objects.filter(key="logo_url").first()
    return Field("logo_url", SPECS["logo_url"], row.description if row and row.description else
                 "URL or path to custom logo image", value=row.value if row else "")


@require_POST
def settings_logo(request):
    """Upload the branding logo (same storage as POST /api/configuration/logo/upload)."""
    _require_admin(request)
    upload = request.FILES.get("file")
    ctx = {"f": None, "can_edit": True}
    if upload is None:
        error, status = "Choose an image file to upload.", 422
    else:
        try:
            services.save_logo(upload.name, upload.read())
        except ServiceError as exc:
            error, status = exc.detail, 422
        else:
            error, status = "", 200
    ctx["f"] = _logo_field()
    ctx["f"].error = error
    resp = render(request, "admin_system/partials/_logo_field.html", ctx, status=status)
    if status == 200:
        _toast(resp, "Logo uploaded and saved.")
    else:
        _toast(resp, f"Logo upload failed: {error}", "error")
    return resp


@require_POST
def settings_test_smtp(request):
    _require_admin(request)
    from apps.notifications.service import NotificationService

    ok, message = NotificationService().test_connection()
    return _toast(HttpResponse(status=204), f"SMTP {'OK' if ok else 'failed'}: {message}",
                  "success" if ok else "warning")


@require_POST
def settings_test_ldap(request):
    _require_admin(request)
    from apps.accounts.ldap_provider import LDAPProvider

    ok, message = LDAPProvider().test_connection()
    return _toast(HttpResponse(status=204), f"LDAP {'OK' if ok else 'failed'}: {message}",
                  "success" if ok else "warning")


@require_POST
def settings_ldap_sync(request):
    _require_admin(request)
    from apps.accounts.ldap_provider import LDAPProvider

    provider = LDAPProvider()
    if not provider.is_configured:
        return JsonResponse({"detail": "LDAP is not configured"}, status=400)
    result = provider.sync_users()
    log_action(request.user, "LDAP_SYNC", details=result, ip_address=client_ip(request))
    errs = result.get("errors") or []
    msg = f"LDAP sync: {result.get('created', 0)} created, {result.get('skipped', 0)} skipped (already exist)"
    if errs:
        msg += f" — {len(errs)} error(s): " + "; ".join(str(e) for e in errs[:3])
    return _toast(HttpResponse(status=204), msg, "warning" if errs else "success")


# Maintenance actions behind a confirmation dialog (v3 "Reinitialize ..." buttons).
MAINTENANCE = {
    "ldap-reset": {
        "title": "Reinitialize LDAP users?",
        "text": "Deletes ALL LDAP-synced users (auth provider = ldap). Local accounts and your own "
                "account are kept. Re-run 'Sync LDAP Users' afterwards to repopulate them correctly.",
        "button": "Delete & reset", "token": False,
    },
    "reinit-requests": {
        "title": "Reinitialize request inbox?",
        "text": "Deletes ALL requests and resets the request-number counter to 1. Estimations are kept "
                "but lose their link to the deleted request. This cannot be undone.",
        "button": "Delete & reinitialize", "token": True,
    },
    "reinit-estimations": {
        "title": "Reinitialize estimations?",
        "text": "Deletes ALL estimations (including their tasks, team allocations, risks and "
                "project-scoped features) and resets the estimation-number counter to 1. "
                "This cannot be undone.",
        "button": "Delete & reinitialize", "token": True,
    },
}
REINIT_TOKEN = "REINITIALIZE"


def _run_maintenance(request, action: str, token: str) -> str:
    """Perform ``action``; returns the toast text. Raises ServiceError on a bad token."""
    ip = client_ip(request)
    if action == "ldap-reset":
        from apps.accounts.services import reset_ldap_synced_users

        deleted = reset_ldap_synced_users(keep_user=request.user)
        log_action(request.user, "LDAP_RESET_USERS", details={"deleted": deleted}, ip_address=ip)
        return f"Removed {deleted} LDAP-synced user(s). Run 'Sync LDAP Users' to repopulate."
    if action == "reinit-requests":
        from apps.inbox import services as inbox_services

        if token.strip().upper() != inbox_services.REQUEST_REINIT_TOKEN:
            raise ServiceError(400, f"Type {inbox_services.REQUEST_REINIT_TOKEN} exactly to confirm.")
        deleted = inbox_services.reinit_inbox()
        log_action(request.user, "REQUEST_INBOX_REINIT", resource_type="request",
                   details={"deleted": deleted}, ip_address=ip)
        return f"Request inbox reinitialized: {deleted} request(s) removed, ID counter reset to 1."
    from apps.estimations import services as est_services

    if token.strip().upper() != est_services.REINIT_TOKEN:
        raise ServiceError(400, f"Type {est_services.REINIT_TOKEN} exactly to confirm.")
    result = est_services.reinit_estimations(token, request.user, ip)
    return f"Estimations reinitialized: {result['deleted']} estimation(s) removed, ID counter reset to 1."


@require_http_methods(["GET", "POST"])
def settings_maintenance(request, action: str):
    _require_admin(request)
    if action not in MAINTENANCE:
        raise Http404
    ctx = {"m": MAINTENANCE[action], "action_url": request.path, "token_word": REINIT_TOKEN}
    if request.method == "POST":
        try:
            message = _run_maintenance(request, action, request.POST.get("confirm", ""))
        except ServiceError as exc:
            ctx["error"] = exc.detail
            return render(request, "admin_system/partials/_confirm_modal.html", ctx, status=422)
        return _toast(HttpResponse(status=204), message, **{"close-modal": True})
    return render(request, "admin_system/partials/_confirm_modal.html", ctx)


# ── Backup & Restore (ADMIN) ────────────────────────────────────────────────

@require_GET
def backup_page(request):
    _require_admin(request)
    return render(request, "admin_system/backup.html", {
        "page_title": "Backup & Restore",
        "tables": [name for name, _ in backup_service.BACKUP_TABLES],
    })


@require_GET
def backup_download(request):
    """The v3-format JSON backup as a file download (same as GET /api/admin/backup)."""
    _require_admin(request)
    document, counts = backup_service.build_backup()
    log_action(request.user, "BACKUP", resource_type="system", details={"tables": counts},
               ip_address=client_ip(request))
    stamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    resp = HttpResponse(json.dumps(document, indent=2, default=str), content_type="application/json")
    resp["Content-Disposition"] = f'attachment; filename="presto_backup_{stamp}.json"'
    return resp


def _restore_error(request, message: str) -> HttpResponse:
    resp = render(request, "admin_system/partials/_restore_result.html", {"error": message}, status=422)
    resp["HX-Retarget"] = "#restore-result"
    resp["HX-Reswap"] = "innerHTML"
    return _toast(resp, message, "error", **{"close-modal": True})


def summarise_backup(document: Any) -> dict:
    """What a backup file holds, for the confirmation step. Raises ValueError if malformed."""
    if not isinstance(document, dict) or not isinstance(document.get("tables"), dict):
        raise ValueError("Invalid backup format: missing 'tables' key")
    known = dict(backup_service.BACKUP_TABLES)
    tables = document["tables"]
    rows = [(name, len(tables[name] or [])) for name in known if name in tables]
    if not rows:
        raise ValueError("The file contains none of the tables a backup restores.")
    for name, _ in rows:
        if not isinstance(tables[name] or [], list):
            raise ValueError(f"Invalid backup format: '{name}' is not a list of rows")
    return {
        "version": str(document.get("version") or "unknown"),
        "timestamp": str(document.get("timestamp") or ""),
        "rows": rows,
        "skipped": [name for name in known if name not in tables],
        "ignored": sorted(k for k in tables if k not in known),
    }


@require_POST
def backup_restore(request):
    """Step 1 (no ``confirm``): check the file and show a confirmation dialog.
    Step 2 (``confirm=yes``): replace the tables and show the result summary."""
    _require_admin(request)
    upload = request.FILES.get("file")
    if upload is None:
        return _restore_error(request, "Choose a backup file first.")
    filename = upload.name or "backup.json"
    if not filename.lower().endswith(".json"):
        return _restore_error(request, "Please upload a JSON file.")
    try:
        document = json.loads(upload.read())
    except Exception as exc:  # noqa: BLE001  (any decode error is the user's file)
        return _restore_error(request, f"Invalid JSON file: {exc}")
    try:
        summary = summarise_backup(document)
    except ValueError as exc:
        return _restore_error(request, str(exc))

    if request.POST.get("confirm") != "yes":
        return render(request, "admin_system/partials/_restore_confirm.html",
                      {"filename": filename, "summary": summary})

    try:
        tables, rows = backup_service.restore_backup(document)
    except ValueError as exc:
        return _restore_error(request, str(exc))
    except backup_service.RestoreError as exc:
        return _restore_error(request, f"Restore failed: {exc}")
    log_action(request.user, "RESTORE", resource_type="system",
               details={"tables_restored": tables, "rows_restored": rows}, ip_address=client_ip(request))
    resp = render(request, "admin_system/partials/_restore_result.html", {
        "filename": filename, "restored": [(t, rows.get(t, 0)) for t in tables],
    })
    resp["HX-Retarget"] = "#restore-result"
    resp["HX-Reswap"] = "innerHTML"
    return _toast(resp, "Restore completed successfully!", **{"close-modal": True})


# ── URLs ────────────────────────────────────────────────────────────────────

urlpatterns = [
    path("settings", settings_page, name="settings"),
    path("settings/save", settings_save, name="settings_save"),
    path("settings/logo", settings_logo, name="settings_logo"),
    path("settings/test-smtp", settings_test_smtp, name="settings_test_smtp"),
    path("settings/test-ldap", settings_test_ldap, name="settings_test_ldap"),
    path("settings/ldap-sync", settings_ldap_sync, name="settings_ldap_sync"),
    path("settings/maintenance/<slug:action>", settings_maintenance, name="settings_maintenance"),
    path("admin/backup", backup_page, name="backup"),
    path("admin/backup/download", backup_download, name="backup_download"),
    path("admin/backup/restore", backup_restore, name="backup_restore"),
]
