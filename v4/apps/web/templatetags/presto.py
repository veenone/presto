from django import template
from django.utils.html import format_html
from django.utils.safestring import mark_safe

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


_TRUNCATE = 60


@register.simple_tag
def cell(row: dict, col) -> str:
    """Render one table cell for a :class:`apps.web.crud.Column`."""
    value = row.get(col.key)
    if col.kind == "bool":
        name, tone = ("check_circle", "positive") if value is not False else ("cancel", "negative")
        label = "Yes" if value is not False else "No"
        return format_html('<span class="icon tone-{}" title="{}" aria-label="{}">{}</span>', tone, label, label, name)
    if value is None or value == "":
        return mark_safe('<span class="muted">—</span>')
    if col.fmt:
        try:
            value = col.fmt.format(value)
        except (ValueError, TypeError):
            pass
    text = str(value)
    if col.kind == "truncate" and len(text) > _TRUNCATE:
        return format_html('<span title="{}">{}…</span>', text, text[:_TRUNCATE])
    if col.kind == "badge":
        return format_html('<span class="badge">{}</span>', text)
    if col.kind == "strong":
        return format_html("<strong>{}</strong>", text)
    return format_html("{}", text)


@register.filter(name="get")
def get_item(mapping, key):
    """``{{ links.segments|get:seg.key }}``"""
    try:
        return mapping.get(key, "")
    except AttributeError:
        return ""
