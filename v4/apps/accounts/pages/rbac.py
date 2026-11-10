"""RBAC Management page (v3 ``pages/rbac.py``): the 18 x 4 UI permission matrix.

Saved as JSON in the ``rbac_matrix`` configuration key, exactly as v3's
``PUT /configuration/rbac_matrix`` did (that endpoint writes no audit entry).
:func:`apps.accounts.permissions.get_matrix` reads the key on every request,
so a saved matrix drives navigation and page access at once.
"""

from __future__ import annotations

import json

from django.contrib import messages
from django.http import HttpResponse
from django.shortcuts import redirect, render
from django.views.decorators.http import require_http_methods

from apps.accounts.models import Role
from apps.accounts.permissions import (
    DEFAULT_MATRIX,
    MATRIX_CONFIG_KEY,
    PERMISSION_KEYS,
    PERMISSIONS,
    ROLES,
    get_matrix,
)
from apps.core import services as core_services
from apps.web.htmx import is_htmx, toast, trigger

from .common import require_admin

SAVED = "RBAC matrix saved successfully."
RESET = "RBAC matrix reset to defaults."


def save_matrix(matrix: dict[str, list[str]]) -> dict[str, list[str]]:
    """Store a matrix (unknown keys dropped, ADMIN always everything) in v3's format."""
    clean = {role: [k for k in PERMISSION_KEYS if k in set(matrix.get(role, []))] for role in ROLES}
    clean[Role.ADMIN] = list(PERMISSION_KEYS)
    core_services.update_configuration(MATRIX_CONFIG_KEY, json.dumps(clean, separators=(",", ":")))
    return clean


def _context(request) -> dict:
    matrix = get_matrix()
    granted = {role: set(perms) for role, perms in matrix.items()}
    rows = [
        {"key": key, "label": label, "description": desc,
         "cells": [{"role": role, "checked": key in granted[role], "locked": role == Role.ADMIN} for role in ROLES]}
        for key, label, desc in PERMISSIONS
    ]
    return {
        "page_title": "RBAC Management", "roles": ROLES, "rows": rows,
        "counts": [{"role": role, "count": len(granted[role])} for role in ROLES],
        "total": len(PERMISSIONS),
    }


def _body(request, message: str = "", status: int = 200) -> HttpResponse:
    resp = render(request, "admin_people/_rbac_body.html", _context(request), status=status)
    return toast(resp, message) if message else resp


@require_http_methods(["GET", "POST"])
def rbac_view(request):
    require_admin(request)
    if request.method == "POST":
        save_matrix({role: request.POST.getlist(f"perm_{role}") for role in ROLES})
        if is_htmx(request):
            return _body(request, SAVED)
        messages.success(request, SAVED)
        return redirect("rbac")
    if is_htmx(request) and request.headers.get("HX-Target") == "rbac-body":
        return _body(request)
    return render(request, "admin_people/rbac.html", _context(request))


@require_http_methods(["GET", "POST"])
def rbac_reset_view(request):
    """Confirmation dialog (with acknowledgement tick), then save the built-in defaults."""
    require_admin(request)
    ctx = {"action": request.path, "error": ""}
    if request.method == "POST":
        if not request.POST.get("confirm"):
            ctx["error"] = "Tick the confirmation box to continue."
            return render(request, "admin_people/_rbac_reset_modal.html", ctx, status=422)
        save_matrix(DEFAULT_MATRIX)
        if not is_htmx(request):
            messages.success(request, RESET)
            return redirect("rbac")
        return trigger(HttpResponse(status=204), **{"rbac-refresh": True, "close-modal": True},
                       toast={"message": RESET, "level": "success"})
    return render(request, "admin_people/_rbac_reset_modal.html", ctx)
