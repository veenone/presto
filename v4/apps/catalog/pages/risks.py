"""Risk Registry page (v3 pages/risks.py), built on apps.web.crud.

Also holds two small helpers shared by this page group:
:class:`ToneBadge` (a coloured badge cell that still sorts and searches as text)
and :class:`DeleteRoleMixin` (delete needs a higher role than add/edit).
"""

from __future__ import annotations

from django import forms
from django.core.exceptions import PermissionDenied
from django.http import Http404
from django.utils.html import format_html

from apps.accounts.roles import has_role
from apps.catalog import services
from apps.catalog.models import RiskItem
from apps.web.crud import Column, CrudPage, Segment, Tile
from apps.web.forms import StyledForm

LEVELS = ["LOW", "MEDIUM", "HIGH"]
LEVEL_TONES = {"HIGH": "negative", "CRITICAL": "negative", "MEDIUM": "warning", "LOW": "positive"}


class ToneBadge(str):
    """A string rendered as a coloured badge by the ``cell`` tag (via ``__html__``).

    It stays a plain string for search and sorting.
    """

    tone: str = ""

    def __new__(cls, value, tone: str = ""):
        obj = super().__new__(cls, value)
        obj.tone = tone
        return obj

    def __str__(self) -> str:  # keep the subclass through str() in the cell tag
        return self

    def __html__(self) -> str:
        return format_html('<span class="badge reg-badge tone-{}">{}</span>', self.tone or "none",
                           str.__str__(self))


def level_badge(value: str | None) -> ToneBadge | None:
    if not value:
        return None
    return ToneBadge(value, LEVEL_TONES.get(str(value).upper(), ""))


class DeleteRoleMixin:
    """Add/edit need ``write_role``; delete needs ``delete_role`` (API parity)."""

    delete_role = "APPROVER"

    def user_can_delete(self, request) -> bool:
        return has_role(request.user.role, self.delete_role)

    def context(self, request, **extra):
        ctx = super().context(request, **extra)
        ctx["can_delete_rows"] = self.can_delete and self.user_can_delete(request)
        return ctx

    def delete_view(self, request, obj_id):
        self.check_view(request)
        if not self.user_can_delete(request):
            raise PermissionDenied
        return super().delete_view(request, obj_id)



class RiskForm(StyledForm):
    name = forms.CharField(max_length=255)
    category = forms.CharField(max_length=64, required=False)
    likelihood = forms.ChoiceField(choices=[(v, v) for v in LEVELS], initial="MEDIUM")
    impact = forms.ChoiceField(choices=[(v, v) for v in LEVELS], initial="MEDIUM")
    description = forms.CharField(required=False, widget=forms.Textarea(attrs={"rows": 3}))
    mitigation = forms.CharField(required=False, widget=forms.Textarea(attrs={"rows": 3}))

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for key in ("likelihood", "impact"):  # keep values set outside the UI
            current = (self.initial or {}).get(key)
            if current and current not in LEVELS:
                self.fields[key].choices = [*self.fields[key].choices, (current, current)]

    def clean_name(self):
        name = self.cleaned_data["name"].strip()
        if not name:
            raise forms.ValidationError("Name is required.")
        return name


class RisksPage(DeleteRoleMixin, CrudPage):
    url_name = "risks"
    title = "Risk Registry"
    noun = "Risk"
    write_role = "ESTIMATOR"   # POST/PUT /api/risk-items
    delete_role = "APPROVER"   # DELETE /api/risk-items/{id}
    form_class = RiskForm
    results_template = "registries/_results_rows.html"
    search_placeholder = "Search by name, category, likelihood, impact…"
    columns = [
        Column("id", "ID", sortable=True, kind="mono"),
        Column("name", "Name", sortable=True, kind="strong"),
        Column("category", "Category", sortable=True),
        Column("likelihood", "Likelihood", sortable=True),
        Column("impact", "Impact", sortable=True),
    ]
    search_fields = ["name", "category", "likelihood", "impact", "description", "id"]

    FIELDS = ["name", "category", "likelihood", "impact", "description", "mitigation"]

    def get_rows(self, request):
        return [
            {"id": r.id, "name": r.name, "category": r.category, "description": r.description,
             "likelihood": level_badge(r.likelihood), "impact": level_badge(r.impact)}
            for r in services.list_risk_items()
        ]

    def get_segments(self, rows):
        return [Segment("all", "All")] + [
            Segment(v.lower(), v.title(), lambda r, v=v: (r["likelihood"] or "") == v) for v in LEVELS
        ]

    def get_tiles(self, rows):
        def n(v):
            return sum(1 for r in rows if (r["likelihood"] or "") == v)
        high_impact = sum(1 for r in rows if (r["impact"] or "") == "HIGH")
        cats = len({r["category"] or "" for r in rows} - {""})
        return [
            Tile("Total Risks", str(len(rows)),
                 f"{cats} categor{'ies' if cats != 1 else 'y'} · {high_impact} high impact", "all"),
            Tile("High Likelihood", str(n("HIGH")), "filter to high", "high"),
            Tile("Medium", str(n("MEDIUM")), "filter to medium", "medium"),
            Tile("Low", str(n("LOW")), "filter to low", "low"),
        ]

    def _get(self, obj_id: int) -> RiskItem:
        obj = RiskItem.objects.filter(pk=obj_id).first()
        if obj is None:
            raise Http404
        return obj

    def get_initial(self, request, obj_id):
        r = self._get(obj_id)
        return {f: getattr(r, f) or "" for f in self.FIELDS}

    def object_label(self, request, obj_id):
        return f"'{self._get(obj_id).name}' (ID {obj_id})"

    @staticmethod
    def _payload(data: dict) -> dict:
        return {
            "name": data["name"],
            "category": (data.get("category") or "").strip(),
            "likelihood": data["likelihood"],
            "impact": data["impact"],
            "description": (data.get("description") or "").strip(),
            "mitigation": (data.get("mitigation") or "").strip(),
        }

    def create(self, request, data):
        return services.create_risk_item(self._payload(data))

    def update(self, request, obj_id, data):
        self._get(obj_id)
        return services.update_risk_item(obj_id, self._payload(data))

    def delete(self, request, obj_id):
        self._get(obj_id)
        services.delete_risk_item(obj_id)
