"""Historical Projects page (v3 ``pages/history.py``), built on apps.web.crud.

Writes go through ``apps.catalog.services`` and need APPROVER, like
``POST/PUT/DELETE /api/historical-projects``.
"""

from __future__ import annotations

import json
from datetime import date

from django import forms
from django.http import Http404

from apps.catalog import services
from apps.estimations.models import HistoricalProject
from apps.web.crud import Column, CrudPage, Segment, Tile
from apps.web.forms import StyledForm

PROJECT_TYPES = ["NEW", "EVOLUTION", "SUPPORT"]
OVER_RATIO = 1.3  # v3 highlights projects whose actual/estimated ratio exceeds this


def accuracy_ratio(estimated: float | None, actual: float | None) -> float | None:
    """actual / estimated, or None when there is no estimate (v3 shows N/A)."""
    if not estimated:
        return None
    return (actual or 0) / estimated


def format_age(value: date | None, today: date | None = None) -> str:
    """Compact relative age as in v3 (``today`` / ``3d`` / ``2w`` / ``5mo``)."""
    if not value:
        return ""
    days = ((today or date.today()) - value).days
    if days < 1:
        return "today"
    if days < 7:
        return f"{days}d"
    if days < 30:
        return f"{days // 7}w"
    return f"{days // 30}mo"


class HistoricalProjectForm(StyledForm):
    project_name = forms.CharField(max_length=255, label="Project Name")
    project_type = forms.ChoiceField(label="Project Type", initial="EVOLUTION")
    estimated_hours = forms.FloatField(required=False, min_value=0, label="Estimated Hours")
    actual_hours = forms.FloatField(required=False, min_value=0, label="Actual Hours")
    dut_count = forms.IntegerField(required=False, min_value=0, label="DUT Count")
    profile_count = forms.IntegerField(required=False, min_value=0, label="Profile Count")
    pr_count = forms.IntegerField(required=False, min_value=0, label="PR Count")
    completion_date = forms.DateField(required=False, label="Completion Date",
                                      widget=forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"))
    features_json = forms.CharField(required=False, initial="[]", label="Features JSON",
                                    widget=forms.Textarea(attrs={"rows": 3, "spellcheck": "false"}))
    notes = forms.CharField(required=False, widget=forms.Textarea(attrs={"rows": 3}))

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        choices = [(t, t) for t in PROJECT_TYPES]
        current = (self.initial or {}).get("project_type")
        if current and current not in PROJECT_TYPES:
            choices.append((current, current))  # keep values written by the API
        self.fields["project_type"].choices = choices
        for name in ("estimated_hours", "actual_hours"):
            self.fields[name].widget.attrs["step"] = "any"

    def clean_project_name(self):
        name = self.cleaned_data["project_name"].strip()
        if not name:
            raise forms.ValidationError("Project Name is required.")
        return name

    def clean_features_json(self):
        raw = (self.cleaned_data.get("features_json") or "").strip()
        if not raw:
            return []
        try:
            return json.loads(raw)
        except json.JSONDecodeError as exc:
            raise forms.ValidationError("Features JSON is not valid JSON.") from exc


class HistoryPage(CrudPage):
    url_name = "history"
    title = "Historical Projects"
    noun = "Project"
    empty_text = "No historical projects yet"
    write_role = "APPROVER"  # same as POST/PUT/DELETE /api/historical-projects
    form_class = HistoricalProjectForm
    template_name = "planning/history/page.html"
    results_template = "planning/_results.html"
    form_template = "planning/history/_form_modal.html"
    default_sort = "id"
    search_placeholder = "Search by project name, type, notes…"
    columns = [
        Column("project_name", "Project Name", sortable=True, kind="strong"),
        Column("project_type", "Type", sortable=True, kind="mono"),
        Column("estimated_hours", "Estimated h", sortable=True, kind="mono", align="right", fmt="{:g}"),
        Column("actual_hours", "Actual h", sortable=True, kind="mono", align="right", fmt="{:g}"),
        Column("accuracy_ratio", "Accuracy Ratio", sortable=True, kind="ratio", align="right"),
        Column("completion_date", "Completion Date", sortable=True, kind="age"),
    ]
    search_fields = ["project_name", "project_type", "notes", "id"]

    FIELDS = ["project_name", "project_type", "estimated_hours", "actual_hours", "dut_count",
              "profile_count", "pr_count", "completion_date", "notes"]

    def get_rows(self, request):
        today = date.today()
        rows = []
        for p in services.list_historical_projects():
            row = {"id": p.id, **{f: getattr(p, f) for f in self.FIELDS}}
            row["accuracy_ratio"] = accuracy_ratio(p.estimated_hours, p.actual_hours)
            row["age"] = format_age(p.completion_date, today)
            rows.append(row)
        return rows

    def get_segments(self, rows):
        return [Segment("all", "All")] + [
            Segment(t, t.title(), lambda r, t=t: r["project_type"] == t) for t in PROJECT_TYPES
        ]

    def get_tiles(self, rows):
        ratios = [r["accuracy_ratio"] for r in rows if r["accuracy_ratio"] is not None]
        avg = sum(ratios) / len(ratios) if ratios else 0.0
        over = sum(1 for x in ratios if x > OVER_RATIO)
        actual = sum(r["actual_hours"] or 0 for r in rows)
        tiles = [
            Tile("Total Projects", str(len(rows)), f"{actual:,.0f}h actual", "all"),
            Tile("Avg Accuracy", f"{avg:.2f}×" if avg else "—", f"{over} over {OVER_RATIO}× ratio"),
        ]
        for t in PROJECT_TYPES:
            n = sum(1 for r in rows if r["project_type"] == t)
            if n:
                tiles.append(Tile(t.title(), str(n), "filter to type", t))
        return tiles

    def _get(self, obj_id: int) -> HistoricalProject:
        obj = HistoricalProject.objects.filter(pk=obj_id).first()
        if obj is None:
            raise Http404
        return obj

    def get_initial(self, request, obj_id):
        p = self._get(obj_id)
        initial = {f: getattr(p, f) for f in self.FIELDS}
        initial["features_json"] = json.dumps(p.features if p.features is not None else [])
        return initial

    def object_label(self, request, obj_id):
        return f"'{self._get(obj_id).project_name}' (ID {obj_id})"

    @staticmethod
    def _payload(data: dict) -> dict:
        return {
            "project_name": data["project_name"],
            "project_type": data["project_type"],
            "estimated_hours": data.get("estimated_hours"),
            "actual_hours": data.get("actual_hours"),
            "dut_count": data.get("dut_count"),
            "profile_count": data.get("profile_count"),
            "pr_count": data.get("pr_count"),
            "completion_date": data.get("completion_date"),
            "features": data.get("features_json", []),
            "notes": (data.get("notes") or "").strip() or None,
        }

    def create(self, request, data):
        return services.create_historical_project(self._payload(data))

    def update(self, request, obj_id, data):
        self._get(obj_id)
        return services.update_historical_project(obj_id, self._payload(data))

    def delete(self, request, obj_id):
        self._get(obj_id)
        services.delete_historical_project(obj_id)
