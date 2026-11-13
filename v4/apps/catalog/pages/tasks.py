"""Task Templates page (v3 ``pages/tasks.py``), built on apps.web.crud.

Templates link to any number of features, each with an optional hours
override (``feature_hours``); the dialog shows an hours box next to every
checked feature, as v3 did below the multi-select.
"""

from __future__ import annotations

from django import forms
from django.http import Http404

from apps.catalog import services
from apps.catalog.models import Feature, TaskTemplate
from apps.core import services as core_services
from apps.web.crud import Column, Segment, Tile
from apps.web.forms import StyledForm, blank_to_none, product_type_choices

from .feature_presets import feature_options, parse_ids
from .filtering import FilteredCrudPage, SelectFilter

TASK_TYPES = ["SETUP", "EXECUTION", "ANALYSIS", "REPORTING", "STUDY"]


class TaskTemplateForm(StyledForm):
    name = forms.CharField(max_length=255)
    task_type = forms.ChoiceField(label="Task Type", initial="EXECUTION")
    base_effort_hours = forms.FloatField(min_value=0.1, initial=8, label="Base Effort Hours (default)",
                                         widget=forms.NumberInput(attrs={"step": "0.5"}))
    scales_with_dut = forms.BooleanField(required=False, label="Scales with DUT")
    scales_with_profile = forms.BooleanField(required=False, label="Scales with Profile")
    is_parallelizable = forms.BooleanField(required=False, label="Is Parallelizable")
    is_pr_fix = forms.BooleanField(required=False, label="PR Fix Template",
                                   help_text="If on, this template is excluded when no PR fixes are expected.")
    product_type = forms.ChoiceField(required=False, label="Product Type (optional)")
    description = forms.CharField(required=False, widget=forms.Textarea(attrs={"rows": 3}))

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        initial = self.initial or {}
        types = [(t, t.title()) for t in TASK_TYPES]
        if initial.get("task_type") and initial["task_type"] not in TASK_TYPES:
            types.append((initial["task_type"], initial["task_type"]))
        self.fields["task_type"].choices = types
        pts = product_type_choices()
        if initial.get("product_type") and initial["product_type"] not in dict(pts):
            pts.append((initial["product_type"], initial["product_type"]))
        self.fields["product_type"].choices = pts
        if self.is_bound:
            self.feature_ids = parse_ids(self.data.getlist("feature_ids"))
            self.raw_hours = {str(fid): (self.data.get(f"hours_{fid}") or "").strip() for fid in self.feature_ids}
        else:
            self.feature_ids = list(initial.get("feature_ids") or [])
            self.raw_hours = {k: v for k, v in (initial.get("feature_hours") or {}).items()}
        self.hours_errors: list[str] = []

    def clean_name(self):
        name = self.cleaned_data["name"].strip()
        if not name:
            raise forms.ValidationError("Name is required.")
        return name

    def clean(self):
        data = super().clean()
        hours: dict[str, float] = {}
        names = dict(Feature.objects.filter(pk__in=self.feature_ids).values_list("id", "name"))
        for fid in self.feature_ids:
            raw = self.raw_hours.get(str(fid))
            if raw in (None, ""):
                continue
            try:
                value = float(raw)
            except (TypeError, ValueError):
                value = -1.0
            if value <= 0:
                self.hours_errors.append(
                    f"Hours override for '{names.get(fid, fid)}' must be a positive number (or blank).")
            else:
                hours[str(fid)] = value
        if self.hours_errors:
            raise forms.ValidationError("Fix the per-feature hours overrides.")
        data["feature_ids"], data["feature_hours"] = self.feature_ids, hours
        return data

    @property
    def picker(self) -> dict:
        return {
            "label": "Features (optional) and per-feature hours override", "required": False, "hours": True,
            "help": "Tick the features this template applies to (none = global). "
                    "Leave an hours box blank to use the default base hours.",
            "options": feature_options(self.feature_ids, self.raw_hours), "errors": self.hours_errors,
        }


class TasksPage(FilteredCrudPage):
    url_name = "tasks"
    title = "Task Templates"
    noun = "Task Template"
    write_role = "APPROVER"  # POST/PUT/DELETE /api/task-templates
    form_class = TaskTemplateForm
    default_sort = "id"
    template_name = "catalog/tasks/page.html"
    form_template = "catalog/tasks/_form_modal.html"
    search_placeholder = "Search by name, feature, description…"
    columns = [
        Column("name", "Name", sortable=True, kind="strong"),
        Column("task_type", "Type", sortable=True, kind="badge"),
        Column("feature", "Feature", kind="truncate"),
        Column("base_effort_hours", "Base Hours", sortable=True, kind="mono", align="right", fmt="{:g}"),
        Column("scales_with_dut", "Scales DUT", kind="bool", align="center"),
        Column("scales_with_profile", "Scales Profile", kind="bool", align="center"),
        Column("is_pr_fix", "PR Fix", kind="bool", align="center"),
        Column("product_type", "Product Type", sortable=True),
    ]
    search_fields = ["name", "task_type", "feature", "description", "product_type"]
    select_filters = [SelectFilter("product_type", "Product Type")]

    def filter_choices(self, request, name):
        return [("all", "All")] + [(t, t) for t in core_services.product_types()]

    def get_form(self, request, data=None, initial=None, obj_id=None):
        return TaskTemplateForm(data=data, initial=initial)

    def get_rows(self, request):
        templates = services.list_task_templates()
        names = dict(Feature.objects.values_list("id", "name"))
        rows = []
        for t in templates:
            fids = t["feature_ids"] or ([t["feature_id"]] if t["feature_id"] else [])
            feats = [names.get(fid, "?") for fid in fids]
            rows.append({
                "id": t["id"], "name": t["name"], "task_type": t["task_type"],
                "feature": ", ".join(feats) if feats else "Global", "feature_ids": fids,
                "base_effort_hours": t["base_effort_hours"],
                "scales_with_dut": bool(t["scales_with_dut"]), "scales_with_profile": bool(t["scales_with_profile"]),
                "is_pr_fix": bool(t["is_pr_fix"]), "description": t["description"] or "",
                "product_type": t["product_type"] or "",
            })
        return rows

    def get_segments(self, rows):
        types = list(TASK_TYPES) + sorted({r["task_type"] for r in rows} - set(TASK_TYPES))
        return [Segment("all", "All")] + [
            Segment(t.lower(), t.title(), lambda r, t=t: r["task_type"] == t) for t in types
        ]

    def get_tiles(self, rows):
        pr = sum(1 for r in rows if r["is_pr_fix"])
        tiles = [Tile("Total Templates", str(len(rows)), f"{pr} PR-fix templates", "all")]
        counts = {t: sum(1 for r in rows if r["task_type"] == t) for t in TASK_TYPES}
        for t in sorted(TASK_TYPES, key=lambda t: -counts[t])[:3]:
            if counts[t]:
                tiles.append(Tile(t.title(), str(counts[t]), "filter to type", t.lower()))
        return tiles

    def _get(self, obj_id: int) -> TaskTemplate:
        tmpl = TaskTemplate.objects.filter(pk=obj_id).first()
        if tmpl is None:
            raise Http404
        return tmpl

    def get_initial(self, request, obj_id):
        self._get(obj_id)
        t = next(x for x in services.list_task_templates() if x["id"] == obj_id)
        fids = t["feature_ids"] or ([t["feature_id"]] if t["feature_id"] else [])
        return {
            "name": t["name"], "task_type": t["task_type"], "base_effort_hours": t["base_effort_hours"],
            "scales_with_dut": t["scales_with_dut"], "scales_with_profile": t["scales_with_profile"],
            "is_parallelizable": t["is_parallelizable"], "is_pr_fix": t["is_pr_fix"],
            "product_type": t["product_type"] or "", "description": t["description"] or "",
            "feature_ids": fids, "feature_hours": {k: f"{v:g}" for k, v in t["feature_hours"].items()},
        }

    def object_label(self, request, obj_id):
        return f"'{self._get(obj_id).name}' (ID {obj_id})"

    @staticmethod
    def _payload(data: dict) -> dict:
        return {
            "name": data["name"], "task_type": data["task_type"],
            "feature_ids": data["feature_ids"], "feature_hours": data["feature_hours"],
            "base_effort_hours": data["base_effort_hours"],
            "scales_with_dut": bool(data.get("scales_with_dut")),
            "scales_with_profile": bool(data.get("scales_with_profile")),
            "is_parallelizable": bool(data.get("is_parallelizable")),
            "is_pr_fix": bool(data.get("is_pr_fix")),
            "description": data.get("description") or None,
            "product_type": blank_to_none(data.get("product_type")),
        }

    def create(self, request, data):
        return services.create_task_template(self._payload(data))

    def update(self, request, obj_id, data):
        self._get(obj_id)
        return services.update_task_template(obj_id, self._payload(data))

    def delete(self, request, obj_id):
        self._get(obj_id)
        services.delete_task_template(obj_id)
