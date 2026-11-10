"""Document Types page (v3 pages/documents.py), built on apps.web.crud."""

from __future__ import annotations

from django import forms
from django.http import Http404

from apps.catalog import services
from apps.catalog.models import DocumentType, TaskTemplate
from apps.web.crud import Column, CrudPage, Segment, Tile
from apps.web.forms import StyledForm

from .risks import DeleteRoleMixin

CATEGORIES = ["Planning", "Report", "Compliance", "Other"]


class DocumentTypeForm(StyledForm):
    name = forms.CharField(max_length=255, widget=forms.TextInput(attrs={"placeholder": "e.g., Test Report"}))
    category = forms.ChoiceField(choices=[(c, c) for c in CATEGORIES], initial="Report")
    base_effort_hours = forms.FloatField(min_value=0.5, initial=4.0, label="Base Effort Hours",
                                         widget=forms.NumberInput(attrs={"step": "0.5"}))
    task_template_id = forms.TypedChoiceField(
        required=False, coerce=int, empty_value=None, label="Linked Task Template",
        help_text="Links this document type's effort to a specific task template.")
    description = forms.CharField(required=False, widget=forms.Textarea(attrs={"rows": 2, "placeholder": "Optional"}))
    is_active = forms.BooleanField(required=False, initial=True, label="Active")

    def __init__(self, *args, editing: bool = False, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["task_template_id"].choices = [("", "— None —")] + [
            (t.id, t.name) for t in TaskTemplate.objects.order_by("name")
        ]
        current = (self.initial or {}).get("category")
        if current and current not in CATEGORIES:  # keep categories set through the API
            self.fields["category"].choices = [*self.fields["category"].choices, (current, current)]
        if not editing:  # v3: new types are always active (create has no is_active)
            del self.fields["is_active"]

    def clean_name(self):
        name = self.cleaned_data["name"].strip()
        if not name:
            raise forms.ValidationError("Name is required.")
        return name


class DocumentTypesPage(DeleteRoleMixin, CrudPage):
    url_name = "document_types"
    title = "Document Types"
    noun = "Document Type"
    write_role = "ESTIMATOR"   # POST/PUT /api/document-types
    delete_role = "APPROVER"   # DELETE /api/document-types/{id}
    form_class = DocumentTypeForm
    template_name = "registries/documents_page.html"
    results_template = "registries/_results_rows.html"
    search_placeholder = "Search by name, category, linked task…"
    default_sort = "name"
    columns = [
        Column("name", "Name", sortable=True, kind="strong"),
        Column("category", "Category", sortable=True),
        Column("base_effort_hours", "Base Hours", sortable=True, kind="mono", align="right", fmt="{:g}"),
        Column("task_template_name", "Linked Task", sortable=True),
        Column("description", "Description", kind="truncate"),
        Column("is_active", "Active", sortable=True, kind="bool", align="center"),
    ]
    search_fields = ["name", "category", "task_template_name", "description"]

    def get_rows(self, request):
        return services.list_document_types(include_inactive=True)

    def get_segments(self, rows):
        return [Segment("all", "All")] + [
            Segment(c.lower(), c, lambda r, c=c: (r["category"] or "") == c) for c in CATEGORIES
        ]

    def get_tiles(self, rows):
        active = sum(1 for r in rows if r["is_active"])
        linked = sum(1 for r in rows if r["task_template_name"])
        avg = sum(float(r["base_effort_hours"] or 0) for r in rows) / len(rows) if rows else 0.0
        tiles = [
            Tile("Total Types", str(len(rows)), f"{active} active · {linked} linked to a task", "all"),
            Tile("Avg Hours", f"{avg:.1f}h", "average base effort"),
        ]
        counts = {c: sum(1 for r in rows if r["category"] == c) for c in CATEGORIES}
        for c in sorted(CATEGORIES, key=lambda c: -counts[c])[:2]:
            if counts[c]:
                tiles.append(Tile(c, str(counts[c]), "filter to category", c.lower()))
        return tiles

    def get_form(self, request, data=None, initial=None, obj_id=None):
        return DocumentTypeForm(data=data, initial=initial, editing=obj_id is not None)

    def _get(self, obj_id: int) -> DocumentType:
        obj = DocumentType.objects.filter(pk=obj_id).first()
        if obj is None:
            raise Http404
        return obj

    def get_initial(self, request, obj_id):
        d = self._get(obj_id)
        return {"name": d.name, "category": d.category, "base_effort_hours": d.base_effort_hours,
                "task_template_id": d.task_template_id or "", "description": d.description or "",
                "is_active": d.is_active}

    def object_label(self, request, obj_id):
        return f"'{self._get(obj_id).name}'"

    @staticmethod
    def _payload(data: dict) -> dict:
        return {
            "name": data["name"],
            "category": data["category"] or "Report",
            "base_effort_hours": data["base_effort_hours"],
            "task_template_id": data.get("task_template_id") or None,
            "description": (data.get("description") or "").strip() or None,
        }

    def create(self, request, data):
        return services.create_document_type(self._payload(data))

    def update(self, request, obj_id, data):
        self._get(obj_id)
        return services.update_document_type(obj_id, {**self._payload(data), "is_active": bool(data.get("is_active"))})

    def delete(self, request, obj_id):
        self._get(obj_id)
        services.delete_document_type(obj_id)
