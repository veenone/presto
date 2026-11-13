"""Form helpers: consistent widget styling and config-driven choices."""

from __future__ import annotations

from django import forms

from apps.core import config


class StyledForm(forms.Form):
    """Adds the design-system classes to every widget."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            widget = field.widget
            if isinstance(widget, forms.CheckboxInput):
                widget.attrs.setdefault("class", "switch-input")
            else:
                widget.attrs.setdefault("class", "input")


def product_type_choices(blank_label: str = "—") -> list[tuple[str, str]]:
    """Product types from Settings (``product_types`` JSON list), with an empty choice."""
    types = config.get_list("product_types", ["Payment", "Telco"])
    return [("", blank_label)] + [(t, t) for t in types]


def blank_to_none(value):
    return None if isinstance(value, str) and not value.strip() else value
