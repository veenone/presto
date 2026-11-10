"""DUT Registry page (v3 pages/duts.py): CRUD plus bulk edit, bulk delete and reinit.

Bulk selection lives in the browser (Alpine ``selected`` ids, see
``templates/registries/duts_page.html``); the bulk dialogs receive the ids as
repeated ``ids`` parameters and apply the same service calls as the per-row
API endpoints, one DUT at a time, reporting ``Updated X/N`` like v3.
"""

from __future__ import annotations

from django import forms
from django.core.exceptions import PermissionDenied
from django.http import Http404, HttpResponse, JsonResponse
from django.shortcuts import render
from django.urls import path as url_path
from django.views.decorators.http import require_http_methods

from apps.accounts.models import Role
from apps.catalog import services
from apps.catalog.models import DutType
from apps.core.errors import ServiceError
from apps.core.services import dut_categories
from apps.web.crud import Column, CrudPage, Segment, Tile
from apps.web.forms import StyledForm, blank_to_none, product_type_choices
from apps.web.htmx import trigger

CLEAR = "__clear__"


def _with_current(choices: list[tuple[str, str]], current) -> list[tuple[str, str]]:
    if current and current not in dict(choices):
        return [*choices, (current, current)]
    return choices


class DutForm(StyledForm):
    name = forms.CharField(max_length=255)
    category = forms.ChoiceField()
    complexity_multiplier = forms.FloatField(min_value=0.1, max_value=10.0, initial=1.0,
                                             label="Complexity Multiplier",
                                             widget=forms.NumberInput(attrs={"step": "0.1"}))
    product_type = forms.ChoiceField(required=False, label="Product Type (optional)")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        cats = dut_categories()
        initial = self.initial or {}
        self.fields["category"].choices = _with_current([(c, c) for c in cats], initial.get("category"))
        self.fields["category"].initial = cats[0] if cats else "Other"
        self.fields["product_type"].choices = _with_current(product_type_choices(), initial.get("product_type"))

    def clean_name(self):
        name = self.cleaned_data["name"].strip()
        if not name:
            raise forms.ValidationError("Name is required.")
        return name


class DutBulkEditForm(StyledForm):
    set_category = forms.BooleanField(required=False, label="Category")
    category = forms.ChoiceField(required=False, label="New Category")
    set_multiplier = forms.BooleanField(required=False, label="Multiplier")
    complexity_multiplier = forms.FloatField(required=False, min_value=0.1, max_value=10.0, initial=1.0,
                                             label="New Multiplier",
                                             widget=forms.NumberInput(attrs={"step": "0.1"}))
    set_product_type = forms.BooleanField(required=False, label="Product Type")
    product_type = forms.ChoiceField(required=False, label="New Product Type")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        cats = dut_categories()
        self.fields["category"].choices = [(c, c) for c in cats]
        self.fields["category"].initial = cats[0] if cats else "Other"
        types = [c for c in product_type_choices() if c[0]]
        self.fields["product_type"].choices = [(CLEAR, "(clear)"), *types]
        self.fields["product_type"].initial = types[0][0] if types else CLEAR

    def clean(self):
        data = super().clean()
        if not (data.get("set_category") or data.get("set_multiplier") or data.get("set_product_type")):
            raise forms.ValidationError("Check at least one field to update.")
        if data.get("set_category") and not data.get("category"):
            self.add_error("category", "Choose a category.")
        if data.get("set_multiplier") and data.get("complexity_multiplier") is None:
            self.add_error("complexity_multiplier", "Enter a multiplier.")
        return data

    def payload(self) -> dict:
        d = self.cleaned_data
        out: dict = {}
        if d.get("set_category"):
            out["category"] = d["category"]
        if d.get("set_multiplier"):
            out["complexity_multiplier"] = d["complexity_multiplier"]
        if d.get("set_product_type"):
            out["product_type"] = None if d["product_type"] in (CLEAR, "") else d["product_type"]
        return out


class DutsPage(CrudPage):
    url_name = "duts"
    title = "DUT Registry"
    noun = "DUT Type"
    write_role = "APPROVER"   # POST/PUT/DELETE /api/dut-types
    reinit_role = "ADMIN"     # POST /api/dut-types/reinit
    form_class = DutForm
    template_name = "registries/duts_page.html"
    results_template = "registries/_duts_results.html"
    search_placeholder = "Search by name, category, product type…"
    columns = [
        Column("id", "ID", sortable=True, kind="mono"),
        Column("name", "Name", sortable=True, kind="strong"),
        Column("category", "Category", sortable=True),
        Column("complexity_multiplier", "Multiplier", sortable=True, kind="mono", fmt="{:g}"),
        Column("product_type", "Product Type", sortable=True),
    ]
    search_fields = ["name", "category", "product_type", "complexity_multiplier", "id"]
    FIELDS = ["id", "name", "category", "complexity_multiplier", "product_type"]

    # -- list ---------------------------------------------------------------
    def get_rows(self, request):
        return [{f: getattr(d, f) for f in self.FIELDS} for d in services.list_dut_types()]

    def get_segments(self, rows):
        return [Segment("all", "All")] + [
            Segment(c, c, lambda r, c=c: (r["category"] or "") == c) for c in dut_categories()
        ]

    def get_tiles(self, rows):
        with_pt = sum(1 for r in rows if r["product_type"])
        tiles = [Tile("Total DUTs", str(len(rows)), f"{with_pt} have product type set", "all")]
        counts = {c: sum(1 for r in rows if (r["category"] or "") == c) for c in dut_categories()}
        for c in sorted(counts, key=lambda c: -counts[c])[:3]:
            if counts[c]:
                tiles.append(Tile(c, str(counts[c]), "filter to category", c))
        return tiles

    def user_can_reinit(self, request) -> bool:
        return request.user.role == Role.ADMIN

    def extra_context(self, request):
        return {"can_reinit": self.user_can_reinit(request)}

    # -- single-row CRUD ------------------------------------------------------
    def _get(self, obj_id: int) -> DutType:
        obj = DutType.objects.filter(pk=obj_id).first()
        if obj is None:
            raise Http404
        return obj

    def get_initial(self, request, obj_id):
        d = self._get(obj_id)
        return {f: getattr(d, f) for f in self.FIELDS if f != "id"}

    def object_label(self, request, obj_id):
        return f"'{self._get(obj_id).name}' (ID {obj_id})"

    @staticmethod
    def _payload(data: dict) -> dict:
        return {
            "name": data["name"],
            "category": data["category"],
            "complexity_multiplier": data["complexity_multiplier"],
            "product_type": blank_to_none(data.get("product_type")),
        }

    def create(self, request, data):
        return services.create_dut_type(self._payload(data))

    def update(self, request, obj_id, data):
        self._get(obj_id)
        return services.update_dut_type(obj_id, self._payload(data))

    def delete(self, request, obj_id):
        self._get(obj_id)
        services.delete_dut_type(obj_id)

    # -- bulk -----------------------------------------------------------------
    @staticmethod
    def _selected(request) -> list[DutType]:
        source = request.POST if request.method == "POST" else request.GET
        ids = []
        for raw in source.getlist("ids"):
            try:
                ids.append(int(raw))
            except (TypeError, ValueError):
                continue
        return list(DutType.objects.filter(pk__in=ids).order_by("id"))

    @staticmethod
    def _no_selection() -> HttpResponse:
        return JsonResponse({"detail": "Select at least one DUT type first."}, status=400)

    @staticmethod
    def _done(message: str, failed: int) -> HttpResponse:
        resp = HttpResponse(status=204)
        return trigger(resp, **{"crud-refresh": True, "close-modal": True},
                       toast={"message": message, "level": "warning" if failed else "success"})

    def bulk_edit_view(self, request):
        self.check_write(request)
        duts = self._selected(request)
        if not duts:
            return self._no_selection()
        if request.method == "POST":
            form = DutBulkEditForm(data=request.POST)
            if form.is_valid():
                payload, ok = form.payload(), 0
                for dut in duts:
                    try:
                        services.update_dut_type(dut.id, dict(payload))
                        ok += 1
                    except ServiceError:
                        pass
                failed = len(duts) - ok
                msg = f"Updated {ok}/{len(duts)} DUT types." + (f" ({failed} failed)" if failed else "")
                return self._done(msg, failed)
            status = 422
        else:
            form, status = DutBulkEditForm(), 200
        return render(request, "registries/_duts_bulk_edit.html",
                      self.context(request, form=form, duts=duts, action=request.path), status=status)

    def bulk_delete_view(self, request):
        self.check_write(request)
        duts = self._selected(request)
        if not duts:
            return self._no_selection()
        if request.method == "POST":
            ok = 0
            for dut in duts:
                try:
                    services.delete_dut_type(dut.id)
                    ok += 1
                except ServiceError:
                    pass
            failed = len(duts) - ok
            msg = f"Deleted {ok}/{len(duts)} DUT types." + (f" ({failed} failed)" if failed else "")
            return self._done(msg, failed)
        return render(request, "registries/_duts_bulk_delete.html",
                      self.context(request, duts=duts, action=request.path))

    def reinit_view(self, request):
        self.check_view(request)
        if not self.user_can_reinit(request):
            raise PermissionDenied
        if request.method == "POST":
            services.reinit_dut_types()
            return self._done("DUT registry reinitialized.", 0)
        return render(request, "registries/_duts_reinit.html", self.context(request, action=request.path))

    @classmethod
    def urls(cls, prefix: str) -> list:
        base = super().urls(prefix)
        page = base[0].callback.__self__
        prefix = prefix.strip("/")
        methods = require_http_methods(["GET", "POST"])
        return [
            url_path(f"{prefix}/bulk-edit", methods(page.bulk_edit_view), name="duts_bulk_edit"),
            url_path(f"{prefix}/bulk-delete", methods(page.bulk_delete_view), name="duts_bulk_delete"),
            url_path(f"{prefix}/reinit", methods(page.reinit_view), name="duts_reinit"),
            *base,
        ]
