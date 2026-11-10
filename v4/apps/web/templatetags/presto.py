from django import template
from django.utils.html import format_html

register = template.Library()


@register.simple_tag
def icon(name: str, extra_class: str = "") -> str:
    """Material Symbols icon (ligature name, e.g. ``dashboard``)."""
    return format_html('<span class="icon {}" aria-hidden="true">{}</span>', extra_class, name)


@register.filter
def has_perm(perms, key: str) -> bool:
    """``{% if ui_perms|has_perm:"manage_duts" %}``"""
    return key in (perms or ())


@register.filter
def initials(name: str) -> str:
    parts = (name or "").split()
    if not parts:
        return "?"
    if len(parts) == 1:
        return parts[0][:2].upper()
    return (parts[0][0] + parts[-1][0]).upper()
