"""User Management page (v3 ``pages/users.py``): ADMIN-only list, add/edit/delete,
role filter, bulk role assignment, active toggle and the LDAP actions.

Every write goes through :mod:`apps.accounts.services` and writes the same
audit entries as ``apps/api/routers/accounts.py``.
"""

from __future__ import annotations

from django import forms
from django.http import Http404, HttpResponse
from django.shortcuts import render
from django.urls import path as url_path
from django.views.decorators.http import require_http_methods, require_POST

from apps.accounts import services
from apps.accounts.audit import log_action
from apps.accounts.ldap_provider import LDAPProvider
from apps.accounts.models import Role, User
from apps.api.common import client_ip
from apps.core.errors import ServiceError
from apps.teams.models import TeamMember
from apps.web.crud import Column, CrudPage, Segment, Tile
from apps.web.forms import StyledForm, blank_to_none
from apps.web.htmx import trigger

from .common import ROLE_CHOICES, detail, require_admin

PROVIDER_CHOICES = [("local", "local"), ("ldap", "ldap"), ("oidc", "oidc")]
SELF_ROLE_HELP = "You cannot change your own role — this prevents locking yourself out of admin access."
SELF_ACTIVE_HELP = "You cannot deactivate your own account."


def team_member_choices() -> list[tuple[str, str]]:
    return [("", "(None)")] + [(str(m.pk), m.name) for m in TeamMember.objects.order_by("name")]


def _team_member_id(value) -> int | None:
    value = blank_to_none(value)
    return int(value) if value else None


class _UserFields(StyledForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["team_member_id"].choices = team_member_choices()

    def clean_display_name(self):
        name = self.cleaned_data["display_name"].strip()
        if not name:
            raise forms.ValidationError("Display Name is required.")
        return name


class UserCreateForm(_UserFields):
    username = forms.CharField(max_length=150, label="Username")
    display_name = forms.CharField(max_length=255, label="Display Name")
    email = forms.EmailField(required=False, max_length=254, label="Email")
    password = forms.CharField(required=False, label="Password", widget=forms.PasswordInput(render_value=False),
                               help_text="Required for local accounts.")
    role = forms.ChoiceField(choices=ROLE_CHOICES, initial=Role.VIEWER, label="Role")
    auth_provider = forms.ChoiceField(choices=PROVIDER_CHOICES, initial="local", label="Auth Provider")
    team_member_id = forms.ChoiceField(required=False, label="Team Member (optional)")

    def clean(self):
        data = super().clean()
        if data.get("auth_provider", "local") == "local" and not data.get("password"):
            self.add_error("password", "Password is required for new users.")
        return data


class UserEditForm(_UserFields):
    display_name = forms.CharField(max_length=255, label="Display Name")
    email = forms.EmailField(required=False, max_length=254, label="Email")
    role = forms.ChoiceField(choices=ROLE_CHOICES, label="Role")
    is_active = forms.BooleanField(required=False, label="Active")
    team_member_id = forms.ChoiceField(required=False, label="Team Member (optional)")

    def __init__(self, *args, is_self: bool = False, **kwargs):
        super().__init__(*args, **kwargs)
        if is_self:
            # Self-lockout guard (v3): a disabled field keeps its initial value.
            self.fields["role"].disabled = True
            self.fields["role"].help_text = SELF_ROLE_HELP
            self.fields["is_active"].disabled = True
            self.fields["is_active"].help_text = SELF_ACTIVE_HELP


class UsersPage(CrudPage):
    url_name = "users"
    title = "User Management"
    noun = "User"
    write_role = Role.ADMIN  # same as /api/users
    columns = [
        Column("username", "Username", sortable=True, kind="mono"),
        Column("display_name", "Display Name", sortable=True),
        Column("email", "Email", sortable=True, kind="mono"),
        Column("role", "Role", sortable=True, kind="badge"),
        Column("is_active", "Active", sortable=True, kind="bool", align="center"),
        Column("auth_provider", "Provider", sortable=True, kind="mono"),
        Column("team_member", "Team Member", sortable=True),
    ]
    search_fields = ["username", "display_name", "email", "role", "auth_provider", "id"]
    default_sort = "username"
    search_placeholder = "Search by username, display name, email, role…"
    template_name = "admin_people/users_page.html"
    results_template = "admin_people/_users_results.html"

    # -- list ----------------------------------------------------------------
    def get_rows(self, request):
        me = request.user.pk
        return [
            {
                "id": u.pk, "username": u.username, "display_name": u.display_name, "email": u.email,
                "role": u.role, "is_active": u.is_active, "auth_provider": u.auth_provider or "local",
                "team_member": u.team_member.name if u.team_member else None,
                "team_member_id": u.team_member_id, "is_self": u.pk == me,
            }
            for u in User.objects.select_related("team_member").order_by("username")
        ]

    def get_segments(self, rows):
        return [Segment("all", "All")] + [
            Segment(role, role.title(), lambda r, role=role: r["role"] == role) for role, _ in ROLE_CHOICES
        ]

    def get_tiles(self, rows):
        active = sum(1 for r in rows if r["is_active"])
        by_provider = {p: sum(1 for r in rows if r["auth_provider"] == p) for p, _ in PROVIDER_CHOICES}
        tiles = [
            Tile("Total Users", str(len(rows)), f"{active} active · {len(rows) - active} inactive", "all"),
            Tile("Admins", str(sum(1 for r in rows if r["role"] == Role.ADMIN)), "filter to admins", Role.ADMIN),
            Tile("Approvers", str(sum(1 for r in rows if r["role"] == Role.APPROVER)), "filter to approvers",
                 Role.APPROVER),
        ]
        federated = by_provider["ldap"] + by_provider["oidc"]
        if federated:
            sub = f"{by_provider['ldap']} LDAP · {by_provider['oidc']} OIDC · {by_provider['local']} local"
            tiles.append(Tile("Federated", str(federated), sub))
        return tiles

    def extra_context(self, request):
        return {"ldap_configured": LDAPProvider().is_configured}

    # -- forms -----------------------------------------------------------------
    def _get(self, obj_id: int) -> User:
        user = services.get_user_by_id(obj_id)
        if user is None:
            raise Http404("User not found")
        return user

    def get_form(self, request, data=None, initial=None, obj_id=None):
        if obj_id is None:
            return UserCreateForm(data=data, initial=initial)
        form = UserEditForm(data=data, initial=initial, is_self=obj_id == request.user.pk)
        form.username = self._get(obj_id).username
        return form

    def get_initial(self, request, obj_id):
        u = self._get(obj_id)
        return {"display_name": u.display_name, "email": u.email or "", "role": u.role,
                "is_active": u.is_active, "team_member_id": str(u.team_member_id or "")}

    def _form_response(self, request, form, action, heading, status=200):
        if getattr(form, "username", None):
            heading = f"Edit User: {form.username}"
        return super()._form_response(request, form, action, heading, status)

    def object_label(self, request, obj_id):
        return f"user '{self._get(obj_id).username}'"

    # -- writes (same checks, messages and audit entries as the API) ------------
    def create(self, request, data):
        email = blank_to_none(data.get("email"))
        services.check_user_unique(username=data["username"], email=email)
        user = services.create_user(
            username=data["username"], display_name=data["display_name"],
            password=data.get("password") or None, email=email, role=data["role"],
            auth_provider=data["auth_provider"], team_member_id=_team_member_id(data.get("team_member_id")),
        )
        log_action(request.user, "CREATE", "user", user.pk, ip_address=client_ip(request))
        return user

    def update(self, request, obj_id, data):
        self._get(obj_id)
        email = blank_to_none(data.get("email"))
        services.check_user_unique(email=email, exclude_id=obj_id)
        changes = {"display_name": data["display_name"], "email": email,
                   "team_member_id": _team_member_id(data.get("team_member_id"))}
        if obj_id != request.user.pk:
            changes["role"] = data["role"]
            changes["is_active"] = bool(data.get("is_active"))
        user = services.update_user(obj_id, **changes)
        log_action(request.user, "UPDATE", "user", obj_id, ip_address=client_ip(request))
        return user

    def delete(self, request, obj_id):
        if obj_id == request.user.pk:
            raise ServiceError(400, "Cannot delete your own account")
        if not services.delete_user(obj_id):
            raise ServiceError(404, "User not found")
        log_action(request.user, "DELETE", "user", obj_id, ip_address=client_ip(request))

    # -- extra actions ------------------------------------------------------------
    def _done(self, message: str, level: str = "success", close: bool = False) -> HttpResponse:
        events = {"crud-refresh": True, "toast": {"message": message, "level": level}}
        if close:
            events["close-modal"] = True
        return trigger(HttpResponse(status=204), **events)

    def _bulk_ids(self, values) -> list[int]:
        ids = []
        for v in values:
            try:
                ids.append(int(v))
            except (TypeError, ValueError):
                continue
        return ids

    def bulk_role_view(self, request):
        """Bulk Set Role dialog (v3): skips the signed-in admin, needs a confirmation tick."""
        require_admin(request)
        source = request.POST if request.method == "POST" else request.GET
        selected = list(User.objects.filter(pk__in=self._bulk_ids(source.getlist("ids"))).order_by("username"))
        if not selected:
            return detail("Select at least one user first.")
        ctx = {
            "selected": selected, "shown": selected[:10], "more": max(0, len(selected) - 10),
            "includes_self": any(u.pk == request.user.pk for u in selected),
            "roles": [r for r, _ in ROLE_CHOICES], "role": source.get("role") or Role.VIEWER,
            "action": request.path, "errors": [],
        }
        if request.method == "POST":
            role = request.POST.get("role")
            if role not in ctx["roles"]:
                ctx["errors"].append("Choose a valid role.")
            if not request.POST.get("confirm"):
                ctx["errors"].append(f"Tick “I understand this changes {len(selected)} users” to apply.")
            if ctx["errors"]:
                return render(request, "admin_people/_bulk_role_modal.html", ctx, status=422)
            success = 0
            for user in selected:
                if user.pk == request.user.pk:
                    continue
                services.update_user(user.pk, role=role)
                log_action(request.user, "UPDATE", "user", user.pk, ip_address=client_ip(request))
                success += 1
            return self._done(f"Role updated for {success}/{len(selected)} users.", close=True)
        return render(request, "admin_people/_bulk_role_modal.html", ctx)

    def toggle_active_view(self, request, obj_id: int):
        require_admin(request)
        user = self._get(obj_id)
        if user.pk == request.user.pk:
            return detail(SELF_ACTIVE_HELP)
        services.update_user(user.pk, is_active=not user.is_active)
        log_action(request.user, "UPDATE", "user", user.pk, ip_address=client_ip(request))
        state = "deactivated" if user.is_active else "activated"
        return self._done(f"User '{user.username}' {state}.")

    def ldap_test_view(self, request):
        require_admin(request)
        ok, message = LDAPProvider().test_connection()
        resp = HttpResponse(status=204)
        return trigger(resp, toast={"message": f"LDAP {'OK' if ok else 'failed'}: {message}",
                                    "level": "success" if ok else "error"})

    def ldap_sync_view(self, request):
        require_admin(request)
        provider = LDAPProvider()
        if not provider.is_configured:
            return detail("LDAP is not configured")
        result = provider.sync_users()
        log_action(request.user, "LDAP_SYNC", details=result, ip_address=client_ip(request))
        msg = f"LDAP sync: {result.get('created', 0)} created, {result.get('skipped', 0)} skipped (already exist)"
        errors = result.get("errors") or []
        if errors:
            msg += f" — {len(errors)} error(s): " + "; ".join(str(e) for e in errors[:3])
            return self._done(msg, "warning")
        return self._done(msg)

    def ldap_reset_view(self, request):
        require_admin(request)
        ctx = {"action": request.path, "error": ""}
        if request.method == "POST":
            if not request.POST.get("confirm"):
                ctx["error"] = "Tick the confirmation box to continue."
                return render(request, "admin_people/_ldap_reset_modal.html", ctx, status=422)
            deleted = services.reset_ldap_synced_users(keep_user=request.user)
            log_action(request.user, "LDAP_RESET_USERS", details={"deleted": deleted},
                       ip_address=client_ip(request))
            return self._done(f"Removed {deleted} LDAP-synced user(s). Run 'Sync LDAP Users' to repopulate.",
                              close=True)
        return render(request, "admin_people/_ldap_reset_modal.html", ctx)

    # -- access: everything here is ADMIN only -------------------------------------
    def check_view(self, request):
        require_admin(request)

    @classmethod
    def urls(cls, prefix: str) -> list:
        page = cls()
        prefix = prefix.strip("/")
        both = require_http_methods(["GET", "POST"])
        return [
            url_path(prefix, require_http_methods(["GET"])(page.list_view), name=cls.url_name),
            url_path(f"{prefix}/new", both(page.new_view), name=f"{cls.url_name}_new"),
            url_path(f"{prefix}/<int:obj_id>/edit", both(page.edit_view), name=f"{cls.url_name}_edit"),
            url_path(f"{prefix}/<int:obj_id>/delete", both(page.delete_view), name=f"{cls.url_name}_delete"),
            url_path(f"{prefix}/<int:obj_id>/toggle-active", require_POST(page.toggle_active_view),
                     name="users_toggle_active"),
            url_path(f"{prefix}/bulk-role", both(page.bulk_role_view), name="users_bulk_role"),
            url_path(f"{prefix}/ldap/test", require_POST(page.ldap_test_view), name="users_ldap_test"),
            url_path(f"{prefix}/ldap/sync", require_POST(page.ldap_sync_view), name="users_ldap_sync"),
            url_path(f"{prefix}/ldap/reset", both(page.ldap_reset_view), name="users_ldap_reset"),
        ]
