"""Web URLs for this page group (see apps/web/urls/__init__.py)."""

from apps.catalog.pages.feature_presets import FeaturePresetsPage
from apps.catalog.pages.features import FeaturesPage
from apps.catalog.pages.tasks import TasksPage

urlpatterns = [
    *FeaturesPage.urls("features"),
    *FeaturePresetsPage.urls("feature-presets"),
    *TasksPage.urls("tasks"),
]
