"""Web URLs for this page group (see apps/web/urls/__init__.py)."""

from apps.catalog.pages.documents import DocumentTypesPage
from apps.catalog.pages.duts import DutsPage
from apps.catalog.pages.risks import RisksPage
from apps.integrations.pages import AssetsPage, PrRegistryPage

urlpatterns = [
    *DutsPage.urls("duts"),
    *AssetsPage.urls("assets"),
    *DocumentTypesPage.urls("document-types"),
    *RisksPage.urls("risks"),
    *PrRegistryPage.urls("pr-registry"),
]
