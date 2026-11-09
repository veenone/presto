from django.conf import settings
from django.urls import path, re_path
from django.views.static import serve

from apps.api.api import api

urlpatterns = [
    path("api/", api.urls),
    # Uploaded branding files (logo), served at the v3 URL.
    re_path(r"^api/static/uploads/(?P<path>.*)$", serve, {"document_root": settings.UPLOADS_DIR}),
]
