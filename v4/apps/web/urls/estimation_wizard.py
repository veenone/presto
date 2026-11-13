"""Web URLs for this page group (see apps/web/urls/__init__.py).

The estimation wizard: ``/estimation/new`` (optionally ``?request_id=``) and
``/estimation/<id>/edit`` (draft in place or revision). Every wizard action
exists under both prefixes.
"""

from django.urls import path

from apps.estimations.wizard import views as w

_ACTIONS = [
    ("step", w.step_view, "step"),
    ("autosave", w.autosave_view, "autosave"),
    ("save-draft", w.save_draft_view, "save_draft"),
    ("preview", w.preview_view, "preview"),
    ("save", w.save_view, "save"),
    ("feature/new", w.feature_new_view, "feature_new"),
    ("feature/create", w.feature_create_view, "feature_create"),
    ("feature/<int:feature_id>/edit", w.feature_edit_view, "feature_edit"),
    ("feature/<int:feature_id>/update", w.feature_update_view, "feature_update"),
    ("feature/<int:feature_id>/request-promotion", w.feature_request_promotion_view, "feature_request"),
    ("feature/<int:feature_id>/promote", w.feature_promote_view, "feature_promote"),
    ("preset/apply", w.preset_apply_view, "preset_apply"),
    ("preset/new", w.preset_new_view, "preset_new"),
    ("preset/create", w.preset_create_view, "preset_create"),
    ("jira", w.jira_view, "jira"),
    ("jira/import", w.jira_import_view, "jira_import"),
]

urlpatterns: list = [
    path("estimation/new", w.page, name="estimation_new"),
    path("estimation/<int:estimation_id>/edit", w.page, name="estimation_edit"),
]
for _suffix, _view, _name in _ACTIONS:
    urlpatterns += [
        path(f"estimation/new/{_suffix}", _view, name=f"wizard_new_{_name}"),
        path(f"estimation/<int:estimation_id>/edit/{_suffix}", _view, name=f"wizard_edit_{_name}"),
    ]
