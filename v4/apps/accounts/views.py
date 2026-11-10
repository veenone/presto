"""Web sign-in (local, LDAP, OIDC), sign-out, password change and theme preference."""

from __future__ import annotations

import secrets

from django.contrib import auth, messages
from django.contrib.auth.decorators import login_not_required
from django.http import HttpResponse, HttpResponseBadRequest
from django.shortcuts import redirect, render
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST

from apps.api.common import client_ip

from . import services
from .audit import log_action
from .forms import LoginForm, PasswordChangeForm
from .models import ThemePreference

DJANGO_BACKEND = "django.contrib.auth.backends.ModelBackend"
OIDC_STATE_KEY = "oidc_state"
OIDC_NEXT_KEY = "oidc_next"


def _safe_next(request, default: str = "/") -> str:
    target = request.POST.get("next") or request.GET.get("next") or default
    if url_has_allowed_host_and_scheme(target, allowed_hosts={request.get_host()},
                                       require_https=request.is_secure()):
        return target
    return default


def _start_session(request, user) -> None:
    auth.login(request, user, backend=DJANGO_BACKEND)


@login_not_required
def login_view(request):
    if request.user.is_authenticated:
        return redirect(_safe_next(request))
    providers = services.get_auth_providers()
    form = LoginForm(request.POST or None, initial={"auth_method": "local"})
    error = ""
    if request.method == "POST" and form.is_valid():
        method = form.cleaned_data.get("auth_method") or "auto"
        if method == "local" and "ldap" not in providers:
            method = "auto"
        try:
            user = services.verify_credentials(
                form.cleaned_data["username"], form.cleaned_data["password"], method, client_ip(request),
            )
        except services.AuthError as exc:
            error = exc.detail
        else:
            _start_session(request, user)
            return redirect(_safe_next(request))
    status = 401 if error else 200
    return render(request, "accounts/login.html", {
        "form": form, "error": error, "providers": providers,
        "next": _safe_next(request, ""), "method": request.POST.get("auth_method", "local"),
    }, status=status)


@require_POST
def logout_view(request):
    if request.user.is_authenticated:
        log_action(request.user, "LOGOUT", ip_address=client_ip(request))
    auth.logout(request)
    return redirect("login")


@login_not_required
def oidc_start(request):
    from .oidc_provider import OIDCProvider

    provider = OIDCProvider()
    state = secrets.token_urlsafe(32)
    url = provider.get_authorization_url(state) if provider.is_configured else None
    if not url:
        messages.error(request, "Single sign-on is not configured.")
        return redirect("login")
    request.session[OIDC_STATE_KEY] = state
    request.session[OIDC_NEXT_KEY] = _safe_next(request)
    return redirect(url)


@login_not_required
def oidc_callback(request):
    from .oidc_provider import OIDCProvider

    state = request.GET.get("state", "")
    expected = request.session.pop(OIDC_STATE_KEY, None)
    next_url = request.session.pop(OIDC_NEXT_KEY, "/")
    if not expected or not secrets.compare_digest(state, expected):
        return HttpResponseBadRequest("Invalid single sign-on state. Start the sign-in again.")
    code = request.GET.get("code", "")
    user = OIDCProvider().handle_callback(code, state) if code else None
    if user is None or not user.is_active:
        log_action(None, "LOGIN_FAILED", "auth", details={"auth_method": "oidc"},
                   ip_address=client_ip(request))
        messages.error(request, "Single sign-on failed. Contact your administrator.")
        return redirect("login")
    _start_session(request, user)
    log_action(user, "LOGIN", details={"auth_method": "oidc"}, ip_address=client_ip(request))
    return redirect(next_url)


def change_password_view(request):
    form = PasswordChangeForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        ok = services.change_password(request.user.pk, form.cleaned_data["current_password"],
                                      form.cleaned_data["new_password"])
        if ok:
            request.user.refresh_from_db()
            auth.update_session_auth_hash(request, request.user)
            log_action(request.user, "CHANGE_PASSWORD", "user", request.user.pk,
                       ip_address=client_ip(request))
            messages.success(request, "Password changed.")
            return redirect("dashboard")
        form.add_error("current_password", "Current password is incorrect")
    return render(request, "accounts/change_password.html", {
        "form": form, "page_title": "Change Password",
        "local_account": bool(request.user.password),
    })


@require_POST
def set_theme(request):
    """Save the signed-in user's theme (decision D6); the browser keeps a copy too."""
    theme = request.POST.get("theme", "")
    if theme not in ThemePreference.values:
        return HttpResponseBadRequest("theme must be light or dark")
    request.user.theme_preference = theme
    request.user.save(update_fields=["theme_preference"])
    return HttpResponse(status=204)
