"""Web URLs for this page group (see apps/web/urls/__init__.py)."""

from apps.catalog.pages.history import HistoryPage
from apps.catalog.pages.holidays import HolidaysPage
from apps.teams.pages import TeamPage

urlpatterns = [
    *HistoryPage.urls("history"),
    *TeamPage.urls("team"),
    *HolidaysPage.urls("public-holidays"),
]
