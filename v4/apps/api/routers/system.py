"""Unauthenticated system endpoints (same paths and shapes as v3)."""

from django.conf import settings
from ninja import Router

router = Router(tags=["system"])


@router.get("/healthcheck", auth=None)
def healthcheck(request):
    """Liveness probe."""
    return {"status": "ok", "version": settings.APP_VERSION}


@router.get("/host-config", auth=None)
def host_config(request):
    """Runtime configuration for clients."""
    return {
        "api_version": settings.APP_VERSION,
        "auth_providers": ["local", "ldap", "oidc"],
        "title": "Test Effort Estimation Tool",
    }
