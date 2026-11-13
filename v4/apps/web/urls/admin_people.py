"""Web URLs for this page group (see apps/web/urls/__init__.py): users, RBAC, audit log."""

from django.urls import path

from apps.accounts.pages import audit, rbac
from apps.accounts.pages.users import UsersPage

urlpatterns: list = [
    *UsersPage.urls("users"),
    path("rbac", rbac.rbac_view, name="rbac"),
    path("rbac/reset", rbac.rbac_reset_view, name="rbac_reset"),
    path("audit", audit.audit_view, name="audit"),
    path("audit/<int:entry_id>", audit.audit_detail_view, name="audit_detail"),
]
