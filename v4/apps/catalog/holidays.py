"""Public holidays: CRUD, ICS import and the working-weeks calculator (v3 parity)."""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from django.db import transaction
from django.db.models import Q

from apps.core.services import ServiceError
from apps.estimations.calc import holiday_dates

from .models import PublicHoliday


def list_public_holidays(year: int | None = None):
    qs = PublicHoliday.objects.order_by("date")
    if year is not None:
        qs = qs.filter(Q(date__year=year) | Q(is_recurring=True))
    return list(qs)


def create_public_holiday(data: dict[str, Any]) -> PublicHoliday:
    return PublicHoliday.objects.create(
        date=data["date"], name=data["name"], country=data.get("country", ""),
        is_recurring=data.get("is_recurring", False),
    )


def _get(holiday_id: int) -> PublicHoliday:
    holiday = PublicHoliday.objects.filter(pk=holiday_id).first()
    if holiday is None:
        raise ServiceError(404, "Holiday not found")
    return holiday


def update_public_holiday(holiday_id: int, updates: dict[str, Any]) -> PublicHoliday:
    holiday = _get(holiday_id)
    for key in ("date", "name", "country", "is_recurring"):
        if updates.get(key) is not None:
            setattr(holiday, key, updates[key])
    holiday.save()
    return holiday


def delete_public_holiday(holiday_id: int) -> dict:
    _get(holiday_id).delete()
    return {"detail": "Deleted"}


def parse_ics(content: str) -> tuple[list[tuple[date, str, bool]], int, list[str]]:
    """Extract (date, summary, recurring) from VEVENT blocks.

    Returns the events, the number of blocks skipped (no date or summary) and
    date parse errors. Same minimal parser as v3.
    """
    events: list[tuple[date, str, bool]] = []
    skipped = 0
    errors: list[str] = []
    for event_block in content.split("BEGIN:VEVENT")[1:]:
        end_idx = event_block.find("END:VEVENT")
        if end_idx == -1:
            continue
        block = event_block[:end_idx]
        lines = [line.strip() for line in block.splitlines()]

        event_date = None
        for stripped in lines:
            if stripped.startswith("DTSTART"):
                # DTSTART;VALUE=DATE:20260101 or DTSTART:20260101T000000Z
                val = stripped.split(":", 1)[-1].strip()
                try:
                    event_date = date(int(val[:4]), int(val[4:6]), int(val[6:8]))
                except (ValueError, IndexError):
                    errors.append(f"Invalid date: {val}")
                break

        event_name = None
        for stripped in lines:
            if stripped.startswith("SUMMARY"):
                event_name = stripped.split(":", 1)[-1].strip()
                break

        if not event_date or not event_name:
            skipped += 1
            continue
        events.append((event_date, event_name, "RRULE:" in block and "FREQ=YEARLY" in block))
    return events, skipped, errors


@transaction.atomic
def import_ics(raw: bytes) -> dict:
    """Import holidays from an iCalendar file, skipping (date, name) duplicates."""
    events, skipped, errors = parse_ics(raw.decode("utf-8", errors="replace"))
    imported = 0
    for event_date, name, recurring in events:
        if PublicHoliday.objects.filter(date=event_date, name=name).exists():
            skipped += 1
            continue
        PublicHoliday.objects.create(date=event_date, name=name, country="", is_recurring=recurring)
        imported += 1
    return {"imported": imported, "skipped": skipped, "errors": errors}


def working_weeks(total_days: float, start_date: str | None = None) -> dict:
    """Working weeks for ``total_days``; with ``start_date``, holidays are skipped."""
    if not start_date:
        return {
            "total_days": total_days,
            "working_days": total_days,
            "working_weeks": round(total_days / 5.0, 1),
            "holidays_excluded": 0,
        }
    try:
        start = date.fromisoformat(start_date)
    except ValueError as exc:
        raise ServiceError(400, f"Invalid start_date: {start_date}") from exc
    holidays = holiday_dates(start.year)

    remaining = total_days
    current = start
    working_days = 0
    holidays_excluded = 0
    while remaining > 0:
        if current.weekday() < 5:
            if current in holidays:
                holidays_excluded += 1
            else:
                remaining -= 1
                working_days += 1
        current += timedelta(days=1)

    end_date = current - timedelta(days=1)
    return {
        "total_days": total_days,
        "working_days": working_days,
        "working_weeks": round(working_days / 5.0, 1),
        "holidays_excluded": holidays_excluded,
        "start_date": str(start),
        "end_date": str(end_date),
        "calendar_days": (end_date - start).days + 1,
    }
