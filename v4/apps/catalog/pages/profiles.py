"""Catalog web pages (list + add/edit/delete), built on apps.web.crud."""

from __future__ import annotations

from django import forms
from django.http import Http404

from apps.catalog import services
from apps.catalog.models import TestProfile
from apps.web.crud import Column, CrudPage, Segment, Tile
from apps.web.forms import StyledForm, blank_to_none, product_type_choices

# ── Test profiles ────────────────────────────────────────────────────────────

class ProfileForm(StyledForm):
    name = forms.CharField(max_length=255)
    description = forms.CharField(required=False, widget=forms.Textarea(attrs={"rows": 3}))
    effort_multiplier = forms.FloatField(min_value=0.1, max_value=10.0, initial=1.0,
                                         widget=forms.NumberInput(attrs={"step": "0.1"}))
    product_type = forms.ChoiceField(required=False, label="Product Type (optional)")
    is_active = forms.BooleanField(required=False, initial=True, label="Active")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        choices = product_type_choices()
        current = (self.initial or {}).get("product_type")
        if current and current not in dict(choices):
            choices.append((current, current))  # keep values removed from Settings
        self.fields["product_type"].choices = choices

    def clean_name(self):
        name = self.cleaned_data["name"].strip()
        if not name:
            raise forms.ValidationError("Name is required.")
        return name


class ProfilesPage(CrudPage):
    url_name = "profiles"
    title = "Test Profiles"
    noun = "Profile"
    write_role = "APPROVER"  # same as POST/PUT/DELETE /api/profiles
    form_class = ProfileForm
    search_placeholder = "Search by name, description, product type…"
    columns = [
        Column("id", "ID", sortable=True, kind="mono"),
        Column("name", "Name", sortable=True, kind="strong"),
        Column("description", "Description", kind="truncate"),
        Column("effort_multiplier", "Multiplier", sortable=True, kind="mono", fmt="{:g}"),
        Column("product_type", "Product Type", sortable=True),
        Column("is_active", "Active", sortable=True, kind="bool", align="center"),
    ]
    search_fields = ["name", "description", "product_type", "effort_multiplier", "id"]

    FIELDS = ["id", "name", "description", "effort_multiplier", "product_type", "is_active"]

    def get_rows(self, request):
        return [{f: getattr(p, f) for f in self.FIELDS} for p in services.list_profiles()]

    def get_segments(self, rows):
        return [
            Segment("all", "All"),
            Segment("active", "Active", lambda r: r["is_active"] is not False),
            Segment("inactive", "Inactive", lambda r: r["is_active"] is False),
        ]

    def get_tiles(self, rows):
        active = sum(1 for r in rows if r["is_active"] is not False)
        with_pt = sum(1 for r in rows if r["product_type"])
        avg = sum(float(r["effort_multiplier"] or 0) for r in rows) / len(rows) if rows else 0.0
        tiles = [
            Tile("Total Profiles", str(len(rows)), f"{with_pt} have product type set", "all"),
            Tile("Active", str(active), f"{len(rows) - active} inactive", "active"),
        ]
        if len(rows) - active:
            tiles.append(Tile("Inactive", str(len(rows) - active), "filter to inactive", "inactive"))
        tiles.append(Tile("Avg Multiplier", f"{avg:.2f}×", "across all profiles"))
        return tiles

    def _get(self, obj_id: int) -> TestProfile:
        obj = TestProfile.objects.filter(pk=obj_id).first()
        if obj is None:
            raise Http404
        return obj

    def get_initial(self, request, obj_id):
        p = self._get(obj_id)
        return {f: getattr(p, f) for f in self.FIELDS if f != "id"}

    def object_label(self, request, obj_id):
        return f"'{self._get(obj_id).name}' (ID {obj_id})"

    @staticmethod
    def _payload(data: dict) -> dict:
        return {
            "name": data["name"],
            "description": data.get("description") or "",
            "effort_multiplier": data["effort_multiplier"],
            "product_type": blank_to_none(data.get("product_type")),
            "is_active": bool(data.get("is_active")),
        }

    def create(self, request, data):
        return services.create_profile(self._payload(data))

    def update(self, request, obj_id, data):
        self._get(obj_id)
        return services.update_profile(obj_id, self._payload(data))

    def delete(self, request, obj_id):
        self._get(obj_id)
        services.delete_profile(obj_id)
