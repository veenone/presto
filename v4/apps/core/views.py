from django.conf import settings
from django.http import JsonResponse
from django.views.decorators.http import require_GET


@require_GET
def healthcheck(request):
    """Unauthenticated liveness probe (same path and shape as v3)."""
    return JsonResponse({"status": "ok", "version": settings.APP_VERSION})
