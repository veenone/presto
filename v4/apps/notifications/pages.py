"""Notifications page (/notifications): the signed-in user's full notification list.

v3 only had the bell dialog (at most 50 rows); this page lists every
notification with paging, an unread filter and the same mark-read actions.
Only the user's own notifications are ever shown or changed.
"""

from __future__ import annotations

from django.core.paginator import Paginator
from django.http import Http404
from django.shortcuts import render
from django.urls import path
from django.views.decorators.http import require_GET, require_POST

from apps.web.htmx import trigger

from . import service

PAGE_SIZE = 20
FILTERS = ("all", "unread")


def _state(params) -> tuple[str, int]:
    filt = params.get("filter", "all")
    if filt not in FILTERS:
        filt = "all"
    try:
        page = max(1, int(params.get("page", 1)))
    except (TypeError, ValueError):
        page = 1
    return filt, page


def _context(request, filt: str, page: int, oob: bool = False) -> dict:
    user = request.user
    total = service.user_notifications(user).count()
    unread = service.user_notifications(user, unread_only=True).count()
    qs = service.user_notifications(user, unread_only=filt == "unread").select_related("request")
    paginator = Paginator(qs, PAGE_SIZE)
    page_obj = paginator.get_page(page)  # clamps to the last page (e.g. after marking the last unread)
    return {
        "page_title": "Notifications",
        "filter": filt,
        "page_obj": page_obj,
        "notifications": list(page_obj.object_list),
        "total": total,
        "unread": unread,
        "segments": [("all", "All", total), ("unread", "Unread", unread)],
        "oob": oob,
    }


@require_GET
def notifications_page(request):
    filt, page = _state(request.GET)
    return render(request, "notifications/page.html", _context(request, filt, page))


@require_GET
def notifications_results(request):
    filt, page = _state(request.GET)
    return render(request, "notifications/partials/_results.html", _context(request, filt, page, oob=True))


def _after_change(request, message: str = "") -> object:
    filt, page = _state(request.POST)
    resp = render(request, "notifications/partials/_results.html", _context(request, filt, page, oob=True))
    resp = trigger(resp, **{"notifications-changed": True})
    if message:
        resp = trigger(resp, toast={"message": message, "level": "success"})
    return resp


@require_POST
def notification_mark_read(request, notification_id: int):
    if not service.mark_notification_read(request.user, notification_id):
        raise Http404("Notification not found")
    return _after_change(request)


@require_POST
def notifications_mark_all_read(request):
    count = service.mark_all_notifications_read(request.user)
    return _after_change(request, "All notifications marked as read" if count else "No unread notifications")


urlpatterns = [
    path("notifications", notifications_page, name="notifications_page"),
    path("notifications/results", notifications_results, name="notifications_page_results"),
    path("notifications/<int:notification_id>/mark-read", notification_mark_read, name="notifications_page_read"),
    path("notifications/mark-all-read", notifications_mark_all_read, name="notifications_page_read_all"),
]
