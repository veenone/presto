"""Web URLs for this page group (see apps/web/urls/__init__.py): dashboard."""

from django.urls import path

from apps.web import views

urlpatterns: list = [
    path("", views.dashboard, name="dashboard"),
    path("dashboard/summary", views.dashboard_summary, name="dashboard_summary"),
]
