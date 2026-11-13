"""Feature Presets admin page (v3 ``pages/feature_presets.py``), built on apps.web.crud.

v3 showed one card per preset; here it is the standard list page (same data:
name, description, product type, feature count, owner) with a details dialog
listing the member features. Writes go through the feature-preset services, so
the owner-or-ADMIN rule and messages match ``/api/feature-presets``.
"""

from __future__ import annotations

import copy

from django import forms
from django.core.exceptions import PermissionDenied
from django.http import Http404
from django.shortcuts import render
from django.urls import path as url_path
from django.views.decorators.http import require_GET

from apps.accounts.roles import has_role
from apps.catalog import services
from apps.catalog.models import Feature, FeaturePreset
from apps.core import services as core_services
from apps.web.crud import Column, CrudPage, Segment, Tile
from apps.web.forms import StyledForm


def feature_options(selected: list[int], extra_hours: dict | None = None) -> list[dict]:
    """Picker options: the global catalog plus any selected feature outside it."""
    feats = list(Feature.objects.filter(is_global=True).order_by("category", "name"))
    known = {f.id for f in feats}
    missing = [fid for fid in selected if fid not in known]
    feats += list(Feature.objects.filter(pk__in=missing).order_by("name"))
    chosen = set(selected)
    hours = extra_hours or {}
    return [
        {"id": f.id, "name": f.name, "meta": " · ".join(x for x in (f.category, f.product_type) if x),
         "search": f"{f.name} {f.category or ''} {f.product_type or ''}", "checked": f.id in chosen,
         "hours": hours.get(str(f.id))}
        for f in feats
    ]


def parse_ids(values) -> list[int]:
    out = []
    for v in values:
        try:
            out.append(int(v))
        except (TypeError, ValueError):
            continue
    return list(dict.fromkeys(out))


class PresetForm(StyledForm):
    name = forms.CharField(max_length=255, required=False, label="Name *")
    description = forms.CharField(required=False, widget=forms.Textarea(attrs={"rows": 3}))
    product_type = forms.ChoiceField(required=False, label="Product Type")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        choices = [("", "All")] + [(t, t) for t in core_services.product_types()]
        pt = (self.initial or {}).get("product_type")
        if pt and pt not in dict(choices):
            choices.append((pt, pt))
        self.fields["product_type"].choices = choices
        if self.is_bound:
            self.feature_ids = parse_ids(self.data.getlist("feature_ids"))
        else:
            self.feature_ids = list((self.initial or {}).get("feature_ids") or [])

    def clean_name(self):
        name = (self.cleaned_data.get("name") or "").strip()
        if not name:
            raise forms.ValidationError("Preset name is required.")
        return name

    def clean(self):
        data = super().clean()
        if not self.feature_ids:
            raise forms.ValidationError("Select at least one feature.")
        data["feature_ids"] = self.feature_ids
        return data

    @property
    def picker(self) -> dict:
        return {"label": "Features", "required": True, "options": feature_options(self.feature_ids),
                "errors": [], "hours": False}


class FeaturePresetsPage(CrudPage):
    url_name = "feature_presets"
    title = "Feature Presets"
    noun = "Preset"
    write_role = "ESTIMATOR"  # POST/PUT/DELETE /api/feature-presets
    form_class = PresetForm
    default_sort = "name"
    template_name = "catalog/presets/page.html"
    results_template = "catalog/presets/_results.html"
    form_template = "catalog/presets/_form_modal.html"
    search_placeholder = "Search by name, description, owner…"
    columns = [
        Column("name", "Name", sortable=True, kind="strong"),
        Column("description", "Description", kind="truncate"),
        Column("product_label", "Product Type", sortable=True),
        Column("feature_count", "Features", sortable=True, kind="mono", align="right"),
        Column("owner_name", "Owner", sortable=True),
    ]
    search_fields = ["name", "description", "product_type", "owner_name"]

    def get_form(self, request, data=None, initial=None, obj_id=None):
        return PresetForm(data=data, initial=initial)

    @staticmethod
    def _can_manage(user, owner_user_id) -> bool:
        """``_get_preset_for_owner`` rule: owner, ownerless preset, or ADMIN."""
        return has_role(user.role, "ESTIMATOR") and (
            user.role == "ADMIN" or owner_user_id is None or owner_user_id == user.id)

    def get_rows(self, request):
        rows = []
        for p in services.list_feature_presets():
            manage = self._can_manage(request.user, p["owner_user_id"])
            rows.append({
                "id": p["id"], "name": p["name"], "description": p["description"] or "",
                "product_type": p["product_type"] or "", "product_label": p["product_type"] or "Any",
                "feature_ids": p["feature_ids"], "feature_count": len(p["feature_ids"]),
                "owner_name": p["owner_name"] or "", "mine": p["owner_user_id"] == request.user.id,
                "can_manage": manage,
            })
        return rows

    def get_segments(self, rows):
        segs = [Segment("all", "All"), Segment("mine", "Mine", lambda r: r["mine"]),
                Segment("any", "Any product type", lambda r: not r["product_type"])]
        types = list(core_services.product_types())
        types += sorted({r["product_type"] for r in rows if r["product_type"] and r["product_type"] not in types})
        segs += [Segment(f"pt:{t}", t, lambda r, t=t: r["product_type"] == t) for t in types]
        return segs

    def get_tiles(self, rows):
        covered = len({fid for r in rows for fid in r["feature_ids"]})
        mine = sum(1 for r in rows if r["mine"])
        any_pt = sum(1 for r in rows if not r["product_type"])
        return [
            Tile("Total Presets", str(len(rows)), f"{covered} distinct features covered", "all"),
            Tile("Mine", str(mine), "presets you own", "mine"),
            Tile("Any Product Type", str(any_pt), f"{len(rows) - any_pt} bound to a product type", "any"),
        ]

    def context(self, request, **extra):
        ctx = super().context(request, **extra)
        # The details button is for everyone who sees the page; edit/delete follow can_manage.
        view = copy.copy(self)
        view.can_add = self.user_can_write(request)
        ctx["crud"], ctx["can_write"] = view, True
        return ctx

    def _get(self, request, obj_id: int, *, manage: bool = True) -> FeaturePreset:
        preset = FeaturePreset.objects.filter(pk=obj_id).first()
        if preset is None:
            raise Http404
        if manage and not self._can_manage(request.user, preset.owner_user_id):
            raise PermissionDenied
        return preset

    def get_initial(self, request, obj_id):
        p = self._get(request, obj_id)
        return {"name": p.name, "description": p.description or "", "product_type": p.product_type or "",
                "feature_ids": services.preset_out(p)["feature_ids"]}

    def object_label(self, request, obj_id):
        return f"'{self._get(request, obj_id).name}'"

    def create(self, request, data):
        return services.create_feature_preset(request.user, data["name"], data.get("description"),
                                              data.get("product_type") or None, data["feature_ids"])

    def update(self, request, obj_id, data):
        # "" (not None) clears description / product type: the service skips None values.
        return services.update_feature_preset(obj_id, request.user, {
            "name": data["name"], "description": data.get("description") or "",
            "product_type": data.get("product_type") or "", "feature_ids": data["feature_ids"],
        })

    def delete(self, request, obj_id):
        services.delete_feature_preset(obj_id, request.user)

    def detail_view(self, request, obj_id: int):
        self.check_view(request)
        p = services.preset_out(self._get(request, obj_id, manage=False))
        names = dict(Feature.objects.filter(pk__in=p["feature_ids"]).values_list("id", "name"))
        return render(request, "catalog/presets/_detail_modal.html", {
            "preset": p, "members": [names.get(fid, f"Feature {fid}") for fid in p["feature_ids"]],
        })

    @classmethod
    def urls(cls, prefix: str) -> list:
        patterns = super().urls(prefix)
        page = next(p.callback.__self__ for p in patterns if p.name == cls.url_name)
        return [*patterns, url_path(f"{prefix.strip('/')}/<int:obj_id>", require_GET(page.detail_view),
                                    name="feature_presets_detail")]
