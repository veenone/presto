"""Shell pages: dashboard placeholder, not-yet-ported pages and error pages."""

from __future__ import annotations

from django.contrib.auth.decorators import login_not_required
from django.core.exceptions import PermissionDenied
from django.http import HttpResponseServerError
from django.shortcuts import render
from django.template import loader

from .navigation import ITEMS_BY_NAME, allowed

ERROR_PAGES: dict[int, dict[str, str]] = {
    400: {"icon": "report_problem", "title": "Bad Request", "tone": "warning",
          "message": "The server could not process the request. This usually means invalid or missing "
                     "data was sent. Please check your input and try again."},
    403: {"icon": "block", "title": "Access Denied", "tone": "negative",
          "message": "You do not have the required permissions to perform this action. Your current role "
                     "may not be authorized. Contact your administrator if you believe this is an error."},
    404: {"icon": "search_off", "title": "Not Found", "tone": "info",
          "message": "The resource you're looking for doesn't exist or has been moved."},
    422: {"icon": "edit_off", "title": "Validation Error", "tone": "warning",
          "message": "The submitted data did not pass validation. Please review your input and try again."},
    500: {"icon": "error_outline", "title": "Server Error", "tone": "negative",
          "message": "Something went wrong on the server. Please try again later or contact support."},
}


def render_error(request, status: int, detail: str = ""):
    page = ERROR_PAGES.get(status, ERROR_PAGES[500])
    return render(request, "errors/error.html", {"status": status, "page": page, "detail": detail},
                  status=status)


def dashboard(request):
    return render(request, "web/dashboard.html", {"page_title": "Dashboard"})


def placeholder(request, url_name: str, **kwargs):
    """A page that keeps its v3 URL and access rule while its HTML is being rebuilt."""
    item = ITEMS_BY_NAME[url_name]
    if not allowed(request.user, item.requires):
        raise PermissionDenied
    return render(request, "web/placeholder.html", {"item": item, "page_title": item.label})


@login_not_required
def error_400(request, exception=None):
    return render_error(request, 400)


@login_not_required
def error_403(request, exception=None):
    return render_error(request, 403)


@login_not_required
def error_404(request, exception=None):
    return render_error(request, 404)


@login_not_required
def error_500(request):
    # No request context: the shell's context processor needs the database,
    # which may be the reason for the error.
    return HttpResponseServerError(loader.render_to_string("errors/500.html"))


@login_not_required
def csrf_failure(request, reason=""):
    return render_error(request, 403, "Your form expired or was sent from another page. "
                                      "Reload the page and try again.")
