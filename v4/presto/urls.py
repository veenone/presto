from django.urls import path

from apps.core import views as core_views

urlpatterns = [
    path("api/healthcheck", core_views.healthcheck, name="healthcheck"),
]
