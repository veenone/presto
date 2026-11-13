"""Sign-in gate for HTML pages.

Every non-API page needs a signed-in user unless its view is marked with
``django.contrib.auth.decorators.login_not_required``. The JSON API under
``/api/`` keeps its own bearer-token auth and is never redirected.
"""

from __future__ import annotations

from urllib.parse import quote

from django.conf import settings
from django.contrib.auth.middleware import LoginRequiredMiddleware
from django.http import HttpResponse

from .htmx import is_htmx

EXEMPT_PREFIXES = ("/api/", "/" + settings.STATIC_URL.lstrip("/"))


class PageLoginRequiredMiddleware(LoginRequiredMiddleware):
    def process_view(self, request, view_func, view_args, view_kwargs):
        if request.path.startswith(EXEMPT_PREFIXES):
            return None
        return super().process_view(request, view_func, view_args, view_kwargs)

    def handle_no_permission(self, request, view_func):
        if is_htmx(request):
            # A partial request after the session expired: send the whole page to login.
            current = request.headers.get("HX-Current-URL", "/")
            path = "/" + current.split("://", 1)[-1].split("/", 1)[-1] if "://" in current else current
            response = HttpResponse(status=401)
            response["HX-Redirect"] = f"{settings.LOGIN_URL}?next={quote(path)}"
            return response
        return super().handle_no_permission(request, view_func)
