"""Web URLs for this page group (see apps/web/urls/__init__.py): request inbox list and detail."""

from django.urls import path

from apps.inbox.pages import actions, detail
from apps.inbox.pages.list import InboxPage

_R = "requests/<int:request_id>"

urlpatterns: list = [
    *InboxPage.urls("requests"),
    path("requests/sync", actions.sync_modal_view, name="requests_sync"),
    path("requests/sync/<str:system>", actions.sync_view, name="requests_sync_run"),
    path(_R, detail.detail_view, name="request_detail"),
    path(f"{_R}/assign", actions.assign_view, name="request_assign"),
    path(f"{_R}/attachments", actions.upload_view, name="request_upload"),
    path(f"{_R}/attachments/<int:index>", actions.download_view, name="request_attachment"),
]
