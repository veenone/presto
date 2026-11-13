"""Integration service (port of v3 TestIntegrationService) and the sync_redmine command."""

from __future__ import annotations

from datetime import date
from io import StringIO
from unittest.mock import MagicMock, patch

import pytest
from django.core.management import CommandError, call_command

from apps.estimations.models import Estimation, EstimationTask
from apps.inbox.models import Request
from apps.integrations import service
from apps.integrations.adapters import (
    ExternalRequest,
    JiraAdapter,
    RedmineAdapter,
    SyncResult,
    SyncStatus,
)
from apps.integrations.management.commands import sync_redmine
from apps.integrations.models import IntegrationConfig

pytestmark = pytest.mark.django_db


def _config(system="REDMINE", **extra):
    defaults = {"base_url": "https://redmine.example.com", "api_key": "k", "enabled": True,
                "additional_config": {"project_id": "p"}}
    defaults.update(extra)
    return IntegrationConfig.objects.create(system_name=system, **defaults)


def _import_result(items, status=SyncStatus.SUCCESS):
    return SyncResult(system="REDMINE", direction="IMPORT", status=status,
                      items_processed=len(items), imported_items=items)


class TestIntegrationService:
    def test_get_adapter_returns_none_when_disabled(self):
        _config(enabled=False)
        assert service.get_adapter("REDMINE") is None

    def test_get_adapter_returns_none_for_unknown(self):
        _config("UNKNOWN_SYSTEM")
        assert service.get_adapter("UNKNOWN_SYSTEM") is None

    def test_get_adapter_builds_configured_adapter(self):
        _config()
        adapter = service.get_adapter("REDMINE")
        assert isinstance(adapter, RedmineAdapter)
        assert adapter.config["additional_config"] == {"project_id": "p"}

    def test_test_integration_not_configured(self):
        result = service.test_integration("REDMINE")
        assert result.success is False
        assert "not configured" in result.message

    def test_get_integration_status(self):
        _config()
        statuses = service.get_integration_status()
        assert len(statuses) == 1
        assert statuses[0]["system_name"] == "REDMINE"
        assert statuses[0]["has_api_key"] is True
        assert statuses[0]["last_sync_at"] is None

    def test_sync_import_creates_updates_and_marks_stale(self):
        _config()
        Request.objects.create(request_number="REQ-OLD", title="old", requester_name="x",
                               received_date=date.today(), request_source="REDMINE", external_id="1")
        Request.objects.create(request_number="REQ-GONE", title="gone", requester_name="x",
                               received_date=date.today(), request_source="REDMINE", external_id="2")
        items = [
            ExternalRequest(external_id="1", title="updated", requester_name="", priority="LOW",
                            requested_delivery_date="2026-12-01"),
            ExternalRequest(external_id="3", title="new", requester_name="PO", priority="HIGH"),
        ]
        with patch.object(RedmineAdapter, "import_requests", return_value=_import_result(items)):
            result = service.sync_import("REDMINE")
        assert (result.items_created, result.items_updated) == (1, 1)
        old = Request.objects.get(external_id="1")
        assert old.title == "updated" and old.requester_name == "x"
        assert old.requested_delivery_date == date(2026, 12, 1)
        assert Request.objects.get(external_id="2").status == "DELETED_UPSTREAM"
        new = Request.objects.get(external_id="3")
        assert new.request_number == f"REQ-{date.today().year}-003"
        assert any("DELETED_UPSTREAM" in e for e in result.errors)
        assert IntegrationConfig.objects.get(system_name="REDMINE").last_sync_at is not None

    def test_sync_import_failed_does_not_mark_stale(self):
        _config()
        Request.objects.create(request_number="R", title="t", requester_name="x",
                               received_date=date.today(), request_source="REDMINE", external_id="9")
        failed = _import_result([], status=SyncStatus.FAILED)
        with patch.object(RedmineAdapter, "import_requests", return_value=failed):
            service.sync_import("REDMINE")
        assert Request.objects.get(external_id="9").status == "NEW"

    def test_sync_export_not_configured(self):
        result = service.sync_export("JIRA", {})
        assert result.status == SyncStatus.FAILED
        assert result.errors == ["JIRA is not configured or not enabled."]

    def test_sync_export_task_breakdown_unsupported(self):
        _config("EMAIL")
        result = service.sync_export_task_breakdown("EMAIL", {})
        assert result.status == SyncStatus.SKIPPED

    def test_estimation_to_export_dict(self, make_user):
        user = make_user("ESTIMATOR", display_name="Est")
        est = Estimation.objects.create(
            project_name="P", project_type="NEW", assigned_to=user, grand_total_hours=10,
            wizard_inputs={"pr_fixes": {"simple": 1, "medium": 2}, "pr_details": [{"pr_number": "1"}]},
        )
        EstimationTask.objects.create(estimation=est, task_name="T", task_type="EXECUTION", base_hours=2)
        data = service.estimation_to_export_dict(est)
        assert data["estimation_number"] == f"EST-{est.id}"
        assert data["assigned_to_name"] == "Est"
        assert (data["pr_simple"], data["pr_medium"], data["pr_complex"]) == (1, 2, 0)
        assert data["pr_details"] == [{"pr_number": "1"}]
        assert data["tasks"][0]["task_name"] == "T"
        assert data["start_date"] == ""

    def test_sync_export_all(self):
        _config("JIRA")
        Estimation.objects.create(project_name="A", project_type="NEW")
        Estimation.objects.create(project_name="B", project_type="NEW")
        ok = SyncResult(system="JIRA", direction="EXPORT", status=SyncStatus.SUCCESS, items_created=1)
        bad = SyncResult(system="JIRA", direction="EXPORT", status=SyncStatus.FAILED, errors=["x"])
        with patch.object(JiraAdapter, "export_estimation", side_effect=[ok, bad]):
            result = service.sync_export_all("JIRA")
        assert result.status == SyncStatus.PARTIAL
        assert (result.items_processed, result.items_created, result.items_failed) == (2, 1, 1)

    def test_sync_export_all_empty(self):
        _config("JIRA")
        result = service.sync_export_all("JIRA")
        assert result.status == SyncStatus.SUCCESS
        assert result.errors == ["No estimations found to export."]


class TestSyncRedmineCommand:
    def test_once_runs_import(self):
        _config()
        items = [ExternalRequest(external_id="5", title="t", requester_name="r")]
        out = StringIO()
        with patch.object(RedmineAdapter, "import_requests", return_value=_import_result(items)) as imp:
            call_command("sync_redmine", "--once", stdout=out)
        imp.assert_called_once()
        assert "1 created" in out.getvalue()
        assert Request.objects.filter(external_id="5").exists()

    def test_once_disabled_is_quiet(self):
        out = StringIO()
        call_command("sync_redmine", "--once", stdout=out)
        assert "not configured or not enabled" in out.getvalue()

    def test_once_failure_raises(self):
        _config()
        failed = SyncResult(system="REDMINE", direction="IMPORT", status=SyncStatus.FAILED, errors=["down"])
        with patch.object(RedmineAdapter, "import_requests", return_value=failed):
            with pytest.raises(CommandError, match="down"):
                call_command("sync_redmine", "--once", stdout=StringIO())

    def test_requires_mode(self):
        with pytest.raises(CommandError):
            call_command("sync_redmine")

    def test_poll_interval(self):
        assert sync_redmine.poll_interval_minutes() is None
        cfg = _config(additional_config={"poll_interval_minutes": "15"})
        assert sync_redmine.poll_interval_minutes() == 15
        cfg.enabled = False
        cfg.save()
        assert sync_redmine.poll_interval_minutes() is None

    def test_loop_sleeps_interval_and_rereads_config(self):
        cfg = _config(additional_config={"poll_interval_minutes": 2})
        sleeps = []

        def fake_sleep(seconds):
            sleeps.append(seconds)
            cfg.enabled = False  # disabled between cycles: next cycle must idle
            cfg.save()

        with patch.object(RedmineAdapter, "import_requests", return_value=_import_result([])) as imp, \
             patch.object(sync_redmine.time, "sleep", side_effect=fake_sleep):
            call_command("sync_redmine", "--loop", "--max-cycles", "2", stdout=StringIO())
        assert imp.call_count == 1
        assert sleeps == [120]

    def test_loop_survives_errors(self):
        _config(additional_config={"poll_interval_minutes": 1})
        with patch.object(RedmineAdapter, "import_requests", side_effect=RuntimeError("x")) as imp, \
             patch.object(sync_redmine.time, "sleep", MagicMock()):
            call_command("sync_redmine", "--loop", "--max-cycles", "3", stdout=StringIO())
        assert imp.call_count == 3
