"""Dialog forms of the wizard (project feature, save selection as preset)."""

from __future__ import annotations

from django import forms

from apps.web.forms import StyledForm, blank_to_none


class ProjectFeatureForm(StyledForm):
    """v3 "Add custom feature for this project" / "Edit project feature" dialog."""

    name = forms.CharField(label="Feature name", max_length=255,
                           error_messages={"required": "Feature name is required."})
    category = forms.CharField(label="Category", max_length=128, required=False, initial="Project-Specific")
    complexity_weight = forms.FloatField(label="Complexity weight", min_value=0.1, initial=1.0,
                                         widget=forms.NumberInput(attrs={"step": "0.1"}))
    base_effort_hours = forms.FloatField(label="Base effort (hours)", min_value=0, initial=8.0,
                                         widget=forms.NumberInput(attrs={"step": "0.5"}))
    product_type = forms.ChoiceField(label="Product Type", required=False)
    has_existing_tests = forms.BooleanField(label="Has existing tests", required=False)
    description = forms.CharField(label="Description (optional)", required=False,
                                  widget=forms.Textarea(attrs={"rows": 3}))

    def __init__(self, *args, product_types: list[str] | None = None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["product_type"].choices = [("All", "All")] + [(p, p) for p in product_types or []]

    def clean_name(self):
        name = (self.cleaned_data.get("name") or "").strip()
        if not name:
            raise forms.ValidationError("Feature name is required.")
        return name

    def service_fields(self) -> dict:
        d = self.cleaned_data
        pt = d.get("product_type")
        return {
            "name": d["name"],
            "category": blank_to_none((d.get("category") or "").strip()),
            "complexity_weight": float(d["complexity_weight"]),
            "base_effort_hours": float(d["base_effort_hours"]),
            "product_type": None if pt in (None, "", "All") else pt,
            "has_existing_tests": bool(d.get("has_existing_tests")),
            "description": blank_to_none((d.get("description") or "").strip()),
        }


class PresetForm(StyledForm):
    """v3 "Save selection as preset" dialog."""

    preset_name = forms.CharField(label="Preset name", max_length=255,
                                  error_messages={"required": "Preset name is required."})
    preset_description = forms.CharField(label="Description (optional)", required=False,
                                         widget=forms.Textarea(attrs={"rows": 2}))
