"""Feature Catalog page (v3 ``pages/features.py``), built on apps.web.crud.

Extras over the plain list page:

* Product Type and Scope filters next to the search box. Scope "Catalog" is the
  v3 view (global features); "Project features" and "Promotion requested" list
  estimation-owned features so approvers can promote them to the catalog.
* Promote (APPROVER) and Request promotion (project author / ADMIN) row actions,
  calling the same services as ``POST /api/features/{id}/promote`` and
  ``/request-promotion``.
* Manage Categories dialog (ADMIN, like ``PUT /api/configuration/{key}``).
"""

from __future__ import annotations

import copy

from django import forms
from django.core.exceptions import PermissionDenied
from django.http import Http404, HttpResponse
from django.shortcuts import render
from django.urls import path as url_path
from django.views.decorators.http import require_http_methods

from apps.accounts.roles import has_role
from apps.catalog import services
from apps.catalog.models import Feature
from apps.core import services as core_services
from apps.core.errors import ServiceError
from apps.estimations.models import Estimation
from apps.web.crud import Column, Segment, Tile
from apps.web.forms import StyledForm, blank_to_none, product_type_choices
from apps.web.htmx import trigger

from .filtering import FilteredCrudPage, SelectFilter

SCOPES = [("catalog", "Catalog"), ("project", "Project features"), ("requested", "Promotion requested")]


class FeatureForm(StyledForm):
    name = forms.CharField(max_length=255)
    category = forms.ChoiceField()
    complexity_weight = forms.FloatField(min_value=0.1, max_value=10.0, initial=1.0, label="Complexity Weight",
                                         widget=forms.NumberInput(attrs={"step": "0.5"}))
    has_existing_tests = forms.BooleanField(required=False, label="Has Existing Tests")
    product_type = forms.ChoiceField(required=False, label="Product Type (optional)")
    study_effort_hours = forms.FloatField(
        required=False, min_value=0, max_value=200, label="Study Effort Hours (blank = use global default)",
        widget=forms.NumberInput(attrs={"step": "1"}),
        help_text="Hours of study effort when this feature is new. "
                  "Leave blank to use the global 'new_feature_study_hours' setting.",
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        initial = self.initial or {}
        cats = [(c, c) for c in core_services.feature_categories()]
        current = initial.get("category")
        if current and current not in dict(cats):
            cats.append((current, current))  # keep a category removed from the list
        self.fields["category"].choices = cats
        if cats and not current:
            self.fields["category"].initial = cats[0][0]
        pts = product_type_choices()
        pt = initial.get("product_type")
        if pt and pt not in dict(pts):
            pts.append((pt, pt))
        self.fields["product_type"].choices = pts

    def clean_name(self):
        name = self.cleaned_data["name"].strip()
        if not name:
            raise forms.ValidationError("Name is required.")
        return name


class FeaturesPage(FilteredCrudPage):
    url_name = "features"
    title = "Feature Catalog"
    noun = "Feature"
    write_role = "ESTIMATOR"  # PUT/DELETE /api/features/{id}; create needs APPROVER (see new_view)
    create_role = "APPROVER"  # POST /api/features
    form_class = FeatureForm
    results_template = "catalog/features/_results.html"
    template_name = "catalog/features/page.html"
    search_placeholder = "Search by name, category, product type…"
    columns = [
        Column("id", "ID", sortable=True, kind="mono"),
        Column("name", "Name", sortable=True, kind="strong"),
        Column("category", "Category", sortable=True, kind="badge"),
        Column("product_type", "Product Type", sortable=True),
        Column("complexity_weight", "Complexity", sortable=True, kind="mono", fmt="{:g}"),
        Column("study_effort_hours", "Study Effort (h)", sortable=True, kind="mono", fmt="{:g}h"),
        Column("has_existing_tests", "Has Tests", sortable=True, kind="bool", align="center"),
    ]
    project_columns = [
        Column("estimation", "Estimation", kind="truncate"),
        Column("promotion", "Promotion", kind="badge"),
    ]
    search_fields = ["name", "category", "product_type", "id", "estimation"]
    select_filters = [SelectFilter("product_type", "Product Type"), SelectFilter("scope", "Scope", "catalog")]

    # -- filters --------------------------------------------------------------
    def filter_choices(self, request, name):
        if name == "scope":
            return SCOPES
        return [("all", "All")] + [(t, t) for t in core_services.product_types()]

    def filter_match(self, row, name, value):
        if name == "scope":
            return True  # applied in get_rows (it changes the row set, totals and tiles)
        return super().filter_match(row, name, value)

    @staticmethod
    def scope(request) -> str:
        value = request.GET.get("scope", "catalog")
        return value if value in dict(SCOPES) else "catalog"

    # -- rows -----------------------------------------------------------------
    def get_rows(self, request):
        scope = self.scope(request)
        if scope == "catalog":
            feats = services.list_features()
        else:
            feats = services.list_project_features(True if scope == "requested" else None)
        user = request.user
        approver = has_role(user.role, "APPROVER")
        est_ids = {f["owner_estimation_id"] for f in feats if f["owner_estimation_id"]}
        ests = {e.id: e for e in Estimation.objects.filter(pk__in=est_ids)}
        rows = []
        for f in feats:
            est = ests.get(f["owner_estimation_id"])
            is_global = f["is_global"]
            if is_global:
                manage = approver
                request_ok = False
            else:
                author = user.role == "ADMIN" or (est is not None and est.creator_id in (None, user.id))
                manage = author
                request_ok = author and has_role(user.role, "ESTIMATOR") and not f["promotion_requested"]
            row = {
                "id": f["id"], "name": f["name"], "category": f["category"],
                "product_type": f["product_type"], "complexity_weight": f["complexity_weight"],
                "study_effort_hours": f["study_effort_hours"] if f["study_effort_hours"] is not None else "Default",
                "has_existing_tests": bool(f["has_existing_tests"]),
                "is_global": is_global,
                "estimation": _estimation_label(est) if est else "",
                "promotion": "Requested" if f["promotion_requested"] else "",
                "can_edit": manage, "can_delete": manage,
                "can_promote": not is_global and approver,
                "can_request": request_ok,
            }
            row["has_actions"] = any(row[k] for k in ("can_edit", "can_delete", "can_promote", "can_request"))
            rows.append(row)
        return rows

    def _categories(self, rows) -> list[str]:
        cats = list(core_services.feature_categories())
        for r in rows:
            if r["category"] and r["category"] not in cats:
                cats.append(r["category"])
        return cats

    def get_segments(self, rows):
        segs = [Segment("all", "All")]
        for c in self._categories(rows):
            segs.append(Segment(f"cat:{c}", c, lambda r, c=c: (r["category"] or "") == c))
        return segs

    def get_tiles(self, rows):
        with_tests = sum(1 for r in rows if r["has_existing_tests"])
        study_set = sum(1 for r in rows if r["study_effort_hours"] != "Default")
        tiles = [Tile("Total Features", str(len(rows)),
                      f"{with_tests} with tests · {study_set} have study override", "all")]
        counts = {c: sum(1 for r in rows if (r["category"] or "") == c) for c in self._categories(rows)}
        for c in sorted(counts, key=lambda c: -counts[c])[:2]:
            if counts[c]:
                tiles.append(Tile(c, str(counts[c]), "filter to category", f"cat:{c}"))
        requested = Feature.objects.filter(is_global=False, promotion_requested=True).count()
        if requested:
            tiles.append(Tile("Promotion Requests", str(requested), "set Scope to review"))
        return tiles

    # -- per-request view of the page (columns, Add button) ---------------------
    def context(self, request, **extra):
        ctx = super().context(request, **extra)
        view = copy.copy(self)
        view.can_add = has_role(request.user.role, self.create_role)
        if self.scope(request) != "catalog":
            view.columns = [*self.columns, *self.project_columns]
        ctx["crud"] = view
        results = ctx.get("results")
        if results is not None:
            ctx["can_write"] = any(r["has_actions"] for r in results.rows)
        ctx["can_manage_categories"] = request.user.role == "ADMIN"
        return ctx

    # -- access ---------------------------------------------------------------
    def _get(self, obj_id: int) -> Feature:
        feature = Feature.objects.filter(pk=obj_id).first()
        if feature is None:
            raise Http404
        return feature

    def _check_manage(self, request, feature: Feature) -> None:
        if not services.can_manage_feature(request.user, feature):
            raise PermissionDenied

    def new_view(self, request):
        self.check_view(request)
        if not has_role(request.user.role, self.create_role):
            raise PermissionDenied
        return super().new_view(request)

    # -- forms ----------------------------------------------------------------
    def get_initial(self, request, obj_id):
        f = self._get(obj_id)
        self._check_manage(request, f)
        return {
            "name": f.name, "category": f.category, "complexity_weight": f.complexity_weight,
            "has_existing_tests": f.has_existing_tests, "product_type": f.product_type,
            "study_effort_hours": f.study_effort_hours,
        }

    def object_label(self, request, obj_id):
        f = self._get(obj_id)
        self._check_manage(request, f)
        return f"'{f.name}' (ID {obj_id})"

    @staticmethod
    def _payload(data: dict) -> dict:
        return {
            "name": data["name"],
            "category": data["category"],
            "complexity_weight": data["complexity_weight"],
            "has_existing_tests": bool(data.get("has_existing_tests")),
            "product_type": blank_to_none(data.get("product_type")),
            "study_effort_hours": data.get("study_effort_hours"),
        }

    def create(self, request, data):
        return services.create_feature(self._payload(data))

    def update(self, request, obj_id, data):
        return services.update_feature(obj_id, request.user, self._payload(data))

    def delete(self, request, obj_id):
        services.delete_feature(obj_id, request.user)

    # -- promotion --------------------------------------------------------------
    def _confirm(self, request, feature, *, kind: str, error: str = "", status: int = 200):
        if kind == "promote":
            heading, verb, icon = "Promote Feature", "Promote", "upgrade"
            text = (f"Add '{feature.name}' to the global feature catalog? It stops being specific "
                    "to its estimation and becomes available to every estimation.")
        else:
            heading, verb, icon = "Request Promotion", "Request", "outgoing_mail"
            text = (f"Ask an approver to add '{feature.name}' to the global feature catalog? "
                    "Approvers are notified.")
        return render(request, "catalog/_confirm_modal.html", {
            "heading": heading, "text": text, "verb": verb, "icon": icon, "error": error,
            "action": request.path,
        }, status=status)

    def promote_view(self, request, obj_id: int):
        self.check_view(request)
        if not has_role(request.user.role, "APPROVER"):  # POST /api/features/{id}/promote
            raise PermissionDenied
        feature = self._get(obj_id)
        if request.method == "POST":
            try:
                out = services.promote_feature(obj_id)
            except ServiceError as exc:
                return self._confirm(request, feature, kind="promote", error=exc.detail, status=422)
            return self._saved_msg(f"'{out['name']}' is now a global feature.")
        return self._confirm(request, feature, kind="promote")

    def request_promotion_view(self, request, obj_id: int):
        self.check_view(request)
        if not has_role(request.user.role, "ESTIMATOR"):  # POST /api/features/{id}/request-promotion
            raise PermissionDenied
        feature = self._get(obj_id)
        if request.method == "POST":
            try:
                out = services.request_feature_promotion(obj_id, request.user)
            except ServiceError as exc:
                return self._confirm(request, feature, kind="request", error=exc.detail, status=422)
            return self._saved_msg(f"Requested adding '{out['name']}' to the global catalog.")
        return self._confirm(request, feature, kind="request")

    def _saved_msg(self, message: str) -> HttpResponse:
        resp = HttpResponse(status=204)
        return trigger(resp, **{"crud-refresh": True, "close-modal": True},
                       toast={"message": message, "level": "success"})

    # -- categories (ADMIN) -----------------------------------------------------
    def categories_view(self, request):
        self.check_view(request)
        if request.user.role != "ADMIN":  # PUT /api/configuration/{key} is ADMIN-only
            raise PermissionDenied
        cats = list(core_services.feature_categories())
        error, value, status, message = "", "", 200, ""
        if request.method == "POST":
            op = request.POST.get("op")
            name = (request.POST.get("name") or "").strip()
            if op == "remove" and name in cats:
                cats.remove(name)
                message = f"Removed '{name}'."
            elif op == "add":
                value = name
                if not name:
                    error = "Enter a category name."
                elif "," in name:
                    error = "Category names cannot contain commas."
                elif name in cats:
                    error = f"'{name}' already exists."
                else:
                    cats.append(name)
                    message, value = f"Added '{name}'.", ""
            else:
                error = "Unknown category."
            if error:
                status = 422
            else:
                core_services.update_configuration("feature_categories", ",".join(cats))
        resp = render(request, "catalog/features/_categories_modal.html", {
            "categories": cats, "error": error, "value": value, "action": request.path,
        }, status=status)
        if message:
            trigger(resp, **{"crud-refresh": True}, toast={"message": message, "level": "success"})
        return resp

    @classmethod
    def urls(cls, prefix: str) -> list:
        patterns = super().urls(prefix)
        page = next(p.callback.__self__ for p in patterns if p.name == cls.url_name)
        both = require_http_methods(["GET", "POST"])
        return [
            *patterns,
            url_path(f"{prefix}/categories", both(page.categories_view), name="features_categories"),
            url_path(f"{prefix}/<int:obj_id>/promote", both(page.promote_view), name="features_promote"),
            url_path(f"{prefix}/<int:obj_id>/request-promotion", both(page.request_promotion_view),
                     name="features_request_promotion"),
        ]


def _estimation_label(est: Estimation) -> str:
    num = est.estimation_number or f"#{est.id}"
    return f"{num} · {est.project_name}"

