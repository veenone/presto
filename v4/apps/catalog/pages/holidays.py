"""Public Holidays page (v3 ``pages/holidays.py``): month calendar, list, ICS import.

Writes go through ``apps.catalog.holidays`` and need ADMIN, like
``POST/PUT/DELETE /api/public-holidays`` and ``POST /api/public-holidays/import-ics``.
"""

from __future__ import annotations

import calendar
from collections import Counter
from datetime import date

from django import forms
from django.http import Http404, HttpResponse
from django.shortcuts import render
from django.urls import path as url_path
from django.urls import reverse
from django.views.decorators.http import require_POST

from apps.catalog import holidays as services
from apps.catalog.models import PublicHoliday
from apps.web.crud import Column, CrudPage, ListState, ResultPage, Tile
from apps.web.forms import StyledForm
from apps.web.htmx import is_htmx, trigger

MAX_ICS_BYTES = 2 * 1024 * 1024


def parse_month(value: str | None, today: date) -> tuple[int, int]:
    """``YYYY-MM`` from the query string; the current month when absent or invalid."""
    try:
        y, m = (int(p) for p in (value or "").split("-", 1))
        if 1 <= m <= 12 and 1900 <= y <= 9999:
            return y, m
    except ValueError:
        pass
    return today.year, today.month


def shift_month(year: int, month: int, delta: int) -> tuple[int, int]:
    index = year * 12 + (month - 1) + delta
    return index // 12, index % 12 + 1


def holidays_on(day: date, holidays: list[PublicHoliday]) -> list[PublicHoliday]:
    """Holidays on ``day``: same date, or a recurring holiday with the same month and day."""
    return [h for h in holidays
            if h.date == day or (h.is_recurring and (h.date.month, h.date.day) == (day.month, day.day))]


def month_grid(year: int, month: int, holidays: list[PublicHoliday], today: date) -> list[dict]:
    """Weeks (Monday first) with the ISO week number and 7 cells (None outside the month)."""
    weeks = []
    for week in calendar.Calendar(firstweekday=0).monthdatescalendar(year, month):
        cells = []
        for d in week:
            if d.month != month:
                cells.append(None)
                continue
            cells.append({"date": d, "iso": d.isoformat(), "day": d.day, "weekend": d.weekday() >= 5,
                          "today": d == today, "holidays": holidays_on(d, holidays)})
        weeks.append({"number": week[0].isocalendar()[1], "cells": cells})
    return weeks


class HolidayForm(StyledForm):
    date = forms.DateField(widget=forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"))
    name = forms.CharField(max_length=255, label="Holiday Name")
    country = forms.CharField(max_length=64, required=False, label="Country / Region")
    is_recurring = forms.BooleanField(required=False, label="Recurring annually")

    def clean_name(self):
        name = self.cleaned_data["name"].strip()
        if not name:
            raise forms.ValidationError("Name is required.")
        return name


class HolidaysPage(CrudPage):
    url_name = "public_holidays"
    title = "Public Holidays"
    noun = "Holiday"
    empty_text = "No public holidays yet"
    write_role = "ADMIN"  # same as /api/public-holidays writes and import-ics
    form_class = HolidayForm
    template_name = "planning/holidays/page.html"
    results_template = "planning/holidays/_results.html"
    form_template = "planning/holidays/_form_modal.html"
    default_sort = "date"
    columns = [
        Column("date", "Date", sortable=True, kind="mono"),
        Column("name", "Name", sortable=True, kind="strong"),
        Column("country", "Country", sortable=True),
        Column("is_recurring", "Recurring", sortable=True, kind="yesno", align="center"),
    ]
    search_fields = ["name", "country", "date"]
    FIELDS = ["id", "date", "name", "country", "is_recurring"]

    # -- list -----------------------------------------------------------------
    def get_rows(self, request):
        holidays = services.list_public_holidays()
        request._planning_holidays = holidays  # reused by the calendar
        return [{f: getattr(h, f) for f in self.FIELDS} for h in holidays]

    def get_tiles(self, rows):
        year = date.today().year
        countries = Counter(r["country"] for r in rows if r["country"])
        tiles = [
            Tile("Total Holidays", str(len(rows)), f"{len(countries)} countr{'ies' if len(countries) != 1 else 'y'}"),
            Tile(f"This Year ({year})", str(sum(1 for r in rows if r["date"].year == year)), "specific dates"),
            Tile("Recurring", str(sum(1 for r in rows if r["is_recurring"])), "annual repeat"),
        ]
        if countries:
            tiles.append(Tile("Countries", str(len(countries)), f"top: {countries.most_common(1)[0][0]}"))
        return tiles

    def build_results(self, request, state: ListState) -> ResultPage:
        results = super().build_results(request, state)
        today = date.today()
        year, month = parse_month(request.GET.get("month"), today)
        holidays = getattr(request, "_planning_holidays", [])
        request._planning_calendar = {
            "year": year, "month": month, "key": f"{year:04d}-{month:02d}",
            "label": f"{calendar.month_name[month]} {year}",
            "prev": "{:04d}-{:02d}".format(*shift_month(year, month, -1)),
            "next": "{:04d}-{:02d}".format(*shift_month(year, month, 1)),
            "today": f"{today.year:04d}-{today.month:02d}",
            "weekdays": ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"],
            "weeks": month_grid(year, month, holidays, today),
        }
        return results

    def month_links(self, base: str, state: ListState, results: ResultPage, cal: dict) -> dict:
        """The framework links plus the shown month (refresh, sort, paging) and month navigation."""
        out = self.links(base, state, results)

        def add(url: str) -> str:
            return f"{url}{'&' if '?' in url else '?'}month={cal['key']}" if url else url

        out["self"], out["prev"], out["next"] = add(out["self"]), add(out["prev"]), add(out["next"])
        out["sort"] = {k: add(v) for k, v in out["sort"].items()}
        out["segments"] = {k: add(v) for k, v in out["segments"].items()}
        qs = state.query(page=1)
        out["month"] = {k: f"{base}?{qs + '&' if qs else ''}month={cal[k]}" for k in ("prev", "next", "today")}
        return out

    def list_view(self, request):
        # Same as CrudPage.list_view, with the calendar month carried in the links.
        self.check_view(request)
        state = self.parse_state(request)
        results = self.build_results(request, state)
        links = self.month_links(request.path, state, results, request._planning_calendar)
        ctx = self.context(request, state=state, results=results, list_path=request.path, links=links)
        if is_htmx(request) and request.headers.get("HX-Target") == "crud-results":
            return render(request, self.results_template, ctx)
        return render(request, self.template_name, ctx)

    def extra_context(self, request):
        ctx = {"cal": getattr(request, "_planning_calendar", None)}
        match = getattr(request, "resolver_match", None)
        if match is not None and match.url_name == f"{self.url_name}_edit":  # v3 edit dialog has Delete
            ctx["delete_url"] = reverse(f"{self.url_name}_delete", args=[match.kwargs["obj_id"]])
        return ctx

    # -- forms ----------------------------------------------------------------
    def get_form(self, request, data=None, initial=None, obj_id=None):
        if data is None and obj_id is None and request.GET.get("date"):
            try:
                initial = {**(initial or {}), "date": date.fromisoformat(request.GET["date"])}
            except ValueError:
                pass
        return super().get_form(request, data=data, initial=initial, obj_id=obj_id)

    def _get(self, obj_id: int) -> PublicHoliday:
        obj = PublicHoliday.objects.filter(pk=obj_id).first()
        if obj is None:
            raise Http404
        return obj

    def get_initial(self, request, obj_id):
        h = self._get(obj_id)
        return {"date": h.date, "name": h.name, "country": h.country, "is_recurring": h.is_recurring}

    def object_label(self, request, obj_id):
        return f"holiday '{self._get(obj_id).name}'"

    @staticmethod
    def _payload(data: dict) -> dict:
        return {"date": data["date"], "name": data["name"], "country": (data.get("country") or "").strip(),
                "is_recurring": bool(data.get("is_recurring"))}

    def create(self, request, data):
        return services.create_public_holiday(self._payload(data))

    def update(self, request, obj_id, data):
        self._get(obj_id)
        return services.update_public_holiday(obj_id, self._payload(data))

    def delete(self, request, obj_id):
        self._get(obj_id)
        services.delete_public_holiday(obj_id)

    # -- ICS import -----------------------------------------------------------
    def import_view(self, request):
        self.check_write(request)
        upload = request.FILES.get("file")
        if upload is None:
            return HttpResponse("Choose an .ics file to import.", status=400, content_type="text/plain")
        if upload.size > MAX_ICS_BYTES:
            return HttpResponse("The file is too large (2 MB at most).", status=400, content_type="text/plain")
        result = services.import_ics(upload.read())
        imported, skipped, errors = result["imported"], result["skipped"], result["errors"]
        parts = []
        if imported:
            parts.append(f"Imported {imported} holiday(s).")
        if skipped:
            parts.append(f"Skipped {skipped} (duplicates or invalid).")
        if errors:
            parts.append(f"Errors: {'; '.join(errors[:5])}")
        if not parts:
            parts.append("No holidays found in the file.")
        level = "success" if imported and not errors else ("warning" if imported or errors or skipped else "error")
        resp = HttpResponse(status=204)
        return trigger(resp, **{"crud-refresh": True}, toast={"message": "\n".join(parts), "level": level})

    @classmethod
    def urls(cls, prefix: str) -> list:
        page = cls()
        prefix = prefix.strip("/")
        return [
            url_path(f"{prefix}/import-ics", require_POST(page.import_view), name="public_holidays_import"),
            *super().urls(prefix),
        ]
