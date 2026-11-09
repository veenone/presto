"""Import requests from Redmine once or on a polling loop.

Replaces the v3 in-process asyncio scheduler (``integrations/sync_scheduler.py``).

    manage.py sync_redmine --once
    manage.py sync_redmine --loop

``--loop`` re-reads the REDMINE integration every cycle and sleeps for
``poll_interval_minutes`` (additional config). While the integration is disabled
or has no interval it idles and checks again later.
"""

from __future__ import annotations

import logging
import time

from django.core.management.base import BaseCommand, CommandError
from django.db import close_old_connections

from apps.integrations import service
from apps.integrations.models import IntegrationConfig

logger = logging.getLogger(__name__)

SYSTEM = "REDMINE"
# How long to wait before re-checking when polling is disabled or unset.
IDLE_SECONDS = 300


def poll_interval_minutes() -> int | None:
    """The configured interval, or None when Redmine is disabled or polling is unset."""
    cfg = IntegrationConfig.objects.filter(system_name=SYSTEM).first()
    if not cfg or not cfg.enabled:
        return None
    additional = cfg.additional_config if isinstance(cfg.additional_config, dict) else {}
    try:
        interval = int(additional.get("poll_interval_minutes") or 0)
    except (TypeError, ValueError):
        return None
    return interval if interval > 0 else None


class Command(BaseCommand):
    help = "Import requests from Redmine (--once) or poll every poll_interval_minutes (--loop)."

    def add_arguments(self, parser):
        group = parser.add_mutually_exclusive_group(required=True)
        group.add_argument("--once", action="store_true", help="Run one import and exit.")
        group.add_argument("--loop", action="store_true", help="Poll using the configured interval.")
        parser.add_argument(
            "--max-cycles", type=int, default=0,
            help="Stop --loop after this many cycles (0 = forever; mainly for tests).",
        )

    def handle(self, *args, **options):
        if options["once"]:
            self._sync_once()
            return
        self._loop(options["max_cycles"])

    def _sync_once(self) -> None:
        if service.get_adapter(SYSTEM) is None:
            self.stdout.write(f"{SYSTEM} is not configured or not enabled; nothing to do.")
            return
        result = service.sync_import(SYSTEM)
        status = result.status.value if hasattr(result.status, "value") else str(result.status)
        self.stdout.write(
            f"Redmine sync {status}: {result.items_created} created, "
            f"{result.items_updated} updated"
        )
        for err in result.errors:
            self.stdout.write(f"  {err}")
        if status == "FAILED":
            raise CommandError(result.errors[0] if result.errors else "Redmine sync failed")

    def _loop(self, max_cycles: int) -> None:
        cycles = 0
        while True:
            close_old_connections()
            interval = poll_interval_minutes()
            if interval is None:
                logger.info("Redmine polling disabled or unset; checking again in %ss", IDLE_SECONDS)
                sleep_for = IDLE_SECONDS
            else:
                try:
                    self._sync_once()
                except CommandError as exc:
                    logger.warning("Redmine auto-sync failed: %s", exc)
                except Exception:
                    logger.exception("Redmine auto-sync failed")
                sleep_for = interval * 60
            cycles += 1
            if max_cycles and cycles >= max_cycles:
                return
            time.sleep(sleep_for)
