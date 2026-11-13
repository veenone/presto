"""Sidebar notification bell: unread badge, panel and mark-as-read actions (htmx)."""

from __future__ import annotations

from django.shortcuts import render
from django.views.decorators.http import require_GET, require_POST

from apps.web.htmx import trigger

from .models import WebhookNotification

PANEL_LIMIT = 50


def _mine(request):
    return WebhookNotification.objects.filter(user=request.user)


@require_GET
def badge(request):
    unread = _mine(request).filter(is_read=False).count()
    return render(request, "partials/_bell_badge.html", {"unread": unread})


def _render_panel(request):
    return render(request, "partials/_notifications_panel.html", {
        "notifications": _mine(request).select_related("request")[:PANEL_LIMIT],
    })


@require_GET
def panel(request):
    return _render_panel(request)


@require_POST
def mark_read(request, notification_id: int):
    _mine(request).filter(pk=notification_id).update(is_read=True)
    return trigger(_render_panel(request), **{"notifications-changed": True})


@require_POST
def mark_all_read(request):
    _mine(request).filter(is_read=False).update(is_read=True)
    response = trigger(_render_panel(request), **{"notifications-changed": True})
    return trigger(response, toast={"message": "All notifications marked as read", "level": "success"})
