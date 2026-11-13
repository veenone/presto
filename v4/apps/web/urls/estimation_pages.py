"""Web URLs for this page group (see apps/web/urls/__init__.py): estimations list, estimation detail."""

from django.urls import path

from apps.estimations.pages import actions, detail, downloads
from apps.estimations.pages.list import EstimationsPage

_E = "estimation/<int:estimation_id>"

urlpatterns: list = [
    *EstimationsPage.urls("estimations"),
    path(_E, detail.detail_view, name="estimation_detail"),
    path(f"{_E}/status", actions.status_view, name="estimation_status"),
    path(f"{_E}/assign", actions.assign_view, name="estimation_assign"),
    path(f"{_E}/archive", actions.archive_view, name="estimation_archive"),
    path(f"{_E}/calibrate", actions.calibrate_view, name="estimation_calibrate"),
    path(f"{_E}/export", actions.export_view, name="estimation_export"),
    path(f"{_E}/export-tasks", actions.export_tasks_view, name="estimation_export_tasks"),
    path(f"{_E}/send-report", actions.send_report_view, name="estimation_send_report"),
    path(f"{_E}/compare", actions.compare_modal_view, name="estimation_compare"),
    path(f"{_E}/compare/report", downloads.comparison_report, name="estimation_compare_report"),
    path(f"{_E}/versions/diff", actions.versions_diff_view, name="estimation_versions_diff"),
    path(f"{_E}/report/executive-summary", downloads.executive_summary, name="estimation_report_summary"),
    path(f"{_E}/report/<str:fmt>", downloads.report_download, name="estimation_report"),
]
