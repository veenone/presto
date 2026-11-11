"""Forms for the request inbox dialogs (same fields and rules as the v3 dialogs and API schemas)."""

from __future__ import annotations

from datetime import date

from django import forms

from apps.web.forms import StyledForm, blank_to_none, product_type_choices

from .common import EDIT_STATUSES, PRIORITIES, SOURCES

_DATE = forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d")


def _choices(values: list[str]) -> list[tuple[str, str]]:
    return [(v, v) for v in values]


class _ProductTypeMixin:
    def _setup_product_type(self) -> None:
        choices = product_type_choices()
        current = (self.initial or {}).get("product_type")
        if current and current not in dict(choices):
            choices.append((current, current))  # keep values removed from Settings
        self.fields["product_type"].choices = choices


class RequestCreateForm(_ProductTypeMixin, StyledForm):
    wide_fields = ("title", "description", "notes")
    is_edit = False
    request_number = forms.CharField(
        max_length=64, required=False, label="Request Number",
        help_text="Prefilled with the next number from the numbering settings; blank numbers automatically.")
    request_source = forms.ChoiceField(choices=_choices(SOURCES), initial="MANUAL", label="Source")
    title = forms.CharField(max_length=500)
    description = forms.CharField(required=False, widget=forms.Textarea(attrs={"rows": 4}))
    requester_name = forms.CharField(max_length=255, label="Requester Name")
    requester_email = forms.EmailField(max_length=254, required=False, label="Requester Email")
    business_unit = forms.CharField(max_length=255, required=False, label="Business Unit")
    priority = forms.ChoiceField(choices=_choices(PRIORITIES), initial="MEDIUM")
    external_id = forms.CharField(max_length=255, required=False, label="External ID (optional)")
    product_type = forms.ChoiceField(required=False, label="Product Type (optional)")
    requested_delivery_date = forms.DateField(required=False, widget=_DATE, label="Requested Delivery Date")
    received_date = forms.DateField(widget=_DATE, label="Received Date")
    notes = forms.CharField(required=False, widget=forms.Textarea(attrs={"rows": 3}))

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["received_date"].initial = date.today
        self._setup_product_type()

    def clean_title(self):
        title = self.cleaned_data["title"].strip()
        if not title:
            raise forms.ValidationError("Title is required.")
        return title

    def clean_requester_name(self):
        name = self.cleaned_data["requester_name"].strip()
        if not name:
            raise forms.ValidationError("Requester name is required.")
        return name

    @staticmethod
    def payload(d: dict) -> dict:
        return {
            "request_number": d.get("request_number", "").strip(),
            "request_source": d["request_source"],
            "external_id": blank_to_none(d.get("external_id", "").strip()),
            "title": d["title"],
            "description": blank_to_none(d.get("description", "").strip()),
            "requester_name": d["requester_name"],
            "requester_email": blank_to_none(d.get("requester_email") or ""),
            "business_unit": blank_to_none(d.get("business_unit", "").strip()),
            "priority": d["priority"],
            "product_type": blank_to_none(d.get("product_type")),
            "requested_delivery_date": d.get("requested_delivery_date"),
            "received_date": d["received_date"],
            "notes": blank_to_none(d.get("notes", "").strip()),
        }


class RequestEditForm(_ProductTypeMixin, StyledForm):
    wide_fields = ("title", "description", "notes")
    is_edit = True
    title = forms.CharField(max_length=500)
    description = forms.CharField(required=False, widget=forms.Textarea(attrs={"rows": 5}))
    priority = forms.ChoiceField(choices=_choices(PRIORITIES))
    status = forms.ChoiceField(choices=_choices(EDIT_STATUSES))
    requester_name = forms.CharField(max_length=255, label="Requester Name")
    requester_email = forms.EmailField(max_length=254, required=False, label="Requester Email")
    business_unit = forms.CharField(max_length=255, required=False, label="Business Unit")
    product_type = forms.ChoiceField(required=False, label="Product Type (optional)")
    requested_delivery_date = forms.DateField(required=False, widget=_DATE, label="Requested Delivery Date")
    notes = forms.CharField(required=False, widget=forms.Textarea(attrs={"rows": 3}))

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._setup_product_type()
        current = (self.initial or {}).get("status")
        if current and current not in EDIT_STATUSES:  # e.g. DELETED_UPSTREAM: keep it selectable
            self.fields["status"].choices = [*self.fields["status"].choices, (current, current)]

    def clean_title(self):
        title = self.cleaned_data["title"].strip()
        if not title:
            raise forms.ValidationError("Title is required.")
        return title

    def clean_requester_name(self):
        name = self.cleaned_data["requester_name"].strip()
        if not name:
            raise forms.ValidationError("Requester name is required.")
        return name

    @staticmethod
    def payload(d: dict) -> dict:
        return {
            "title": d["title"],
            "description": blank_to_none(d.get("description", "").strip()),
            "priority": d["priority"],
            "status": d["status"],
            "requester_name": d["requester_name"],
            "requester_email": blank_to_none(d.get("requester_email") or ""),
            "business_unit": blank_to_none(d.get("business_unit", "").strip()),
            "product_type": blank_to_none(d.get("product_type")),
            "requested_delivery_date": d.get("requested_delivery_date"),
            "notes": blank_to_none(d.get("notes", "").strip()),
        }


class AssignForm(StyledForm):
    assigned_to_id = forms.TypedChoiceField(coerce=int, label="Assign to")

    def __init__(self, *args, users=(), **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["assigned_to_id"].choices = [("", "Select a user…")] + [
            (u.id, f"{u.display_name or u.username} ({u.role})") for u in users
        ]
        self.fields["assigned_to_id"].error_messages["required"] = "Please select a user."


class UploadForm(forms.Form):
    file = forms.FileField()
