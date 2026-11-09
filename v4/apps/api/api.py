"""The ``/api`` NinjaAPI instance.

All routers are mounted without a prefix; each declares full paths so the
URL layout matches v3 exactly. Django resolves URLs by path before method, so
two routers must never declare the same path (even with different methods).
"""

from django.conf import settings
from django.http import JsonResponse
from ninja import NinjaAPI
from ninja.errors import AuthenticationError

from .routers import (
    accounts,
    admin,
    catalog,
    configuration,
    dashboard,
    estimations,
    holidays,
    imports,
    inbox,
    integrations,
    notifications,
    reports,
    system,
    teams,
)

api = NinjaAPI(
    title="Test Effort Estimation Tool",
    version=settings.APP_VERSION,
    description="API for managing test effort estimations",
    urls_namespace="api",
    docs_url="/docs",
)


@api.exception_handler(AuthenticationError)
def _unauthenticated(request, exc):
    resp = JsonResponse({"detail": "Missing or invalid Authorization header"}, status=401)
    resp["WWW-Authenticate"] = "Bearer"
    return resp


for module in (
    system, accounts, catalog, teams, configuration, holidays, estimations, reports,
    inbox, integrations, notifications, dashboard, admin, imports,
):
    api.add_router("", module.router)
