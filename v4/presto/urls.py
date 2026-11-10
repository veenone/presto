from importlib import import_module

from django.conf import settings
from django.urls import path, re_path
from django.views.static import serve

from apps.accounts import views as account_views
from apps.api.api import api
from apps.notifications import views as notification_views
from apps.web import views as web_views
from apps.web.navigation import ITEMS_BY_NAME
from apps.web.urls import MODULES as WEB_URL_MODULES

handler400 = "apps.web.views.error_400"
handler403 = "apps.web.views.error_403"
handler404 = "apps.web.views.error_404"
handler500 = "apps.web.views.error_500"

urlpatterns = [
    path("api/", api.urls),
    # Uploaded branding files (logo), served at the v3 URL.
    re_path(r"^api/static/uploads/(?P<path>.*)$", serve, {"document_root": settings.UPLOADS_DIR}),
    # Account
    path("login", account_views.login_view, name="login"),
    path("logout", account_views.logout_view, name="logout"),
    path("auth/oidc/start", account_views.oidc_start, name="oidc_start"),
    path("auth/oidc/callback", account_views.oidc_callback, name="oidc_callback"),
    path("account/password", account_views.change_password_view, name="change_password"),
    path("me/theme", account_views.set_theme, name="set_theme"),
    # Notification bell (htmx partials)
    path("notifications/badge", notification_views.badge, name="notifications_badge"),
    path("notifications/panel", notification_views.panel, name="notifications_panel"),
    path("notifications/<int:notification_id>/read", notification_views.mark_read, name="notification_read"),
    path("notifications/read-all", notification_views.mark_all_read, name="notifications_read_all"),
    # Pages
    path("", web_views.dashboard, name="dashboard"),
]

for _module in WEB_URL_MODULES:
    urlpatterns += import_module(_module).urlpatterns
_registered = {p.name for p in urlpatterns if getattr(p, "name", None)}

# Pages whose HTML arrives in later phases keep their v3 URLs and access rules now.
_DETAIL_ROUTES = {
    "requests": ["requests/<int:request_id>"],
    "estimations": ["estimation/<int:estimation_id>", "estimation/<int:estimation_id>/edit"],
}
for _name, _item in ITEMS_BY_NAME.items():
    if _name in _registered:
        continue
    urlpatterns.append(path(_item.path.lstrip("/"), web_views.placeholder, {"url_name": _name}, name=_name))
    for _i, _route in enumerate(_DETAIL_ROUTES.get(_name, [])):
        urlpatterns.append(
            path(_route, web_views.placeholder, {"url_name": _name}, name=f"{_name}_detail_{_i}"))
