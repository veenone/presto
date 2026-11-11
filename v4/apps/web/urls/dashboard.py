"""Web URLs for this page group (see apps/web/urls/__init__.py)."""

from django.urls import path

from apps.web import views

urlpatterns: list = [path("", views.dashboard, name="dashboard")]
