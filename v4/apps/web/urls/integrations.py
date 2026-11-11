"""Web URLs for this page group (see apps/web/urls/__init__.py): integrations config, notifications page."""

from apps.integrations.config_pages import urlpatterns as _integrations
from apps.notifications.pages import urlpatterns as _notifications

urlpatterns: list = [*_integrations, *_notifications]
