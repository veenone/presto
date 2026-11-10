"""Public Holidays page (/public-holidays): calendar, list, CRUD and ICS import."""

import json
from datetime import date

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile

from apps.catalog.models import PublicHoliday as Holiday
from apps.catalog.pages.holidays import holidays_on, month_grid, parse_month, shift_month
from apps.core.errors import ServiceError

pytestmark = pytest.mark.django_db
HX = {"HX-Request": "true"}
HX_RESULTS = {"HX-Request": "true", "HX-Target": "crud-results"}
TODAY = date.today()

ICS = b"""BEGIN:VCALENDAR
BEGIN:VEVENT
DTSTART;VALUE=DATE:20270101
SUMMARY:New Year
RRULE:FREQ=YEARLY
END:VEVENT
BEGIN:VEVENT
DTSTART;VALUE=DATE:20270501
SUMMARY:Labour Day
END:VEVENT
BEGIN:VEVENT
DTSTART:20271225T000000Z
SUMMARY:Christmas Day
END:VEVENT
BEGIN:VEVENT
SUMMARY:No date
END:VEVENT
END:VCALENDAR
"""


def ics(content=ICS, name="h.ics"):
    return SimpleUploadedFile(name, content, "text/calendar")


@pytest.fixture
def holidays(seeded):
    return [
        Holiday.objects.create(date=date(2026, 12, 25), name="Christmas Day", country="FR", is_recurring=True),
        Holiday.objects.create(date=date(2026, 11, 11), name="Armistice", country="FR"),
        Holiday.objects.create(date=date(2026, 11, 26), name="Thanksgiving", country="US"),
    ]


class TestCalendarHelpers:
    def test_parse_and_shift_month(self):
        today = date(2026, 11, 10)
        assert parse_month("2027-02", today) == (2027, 2)
        assert parse_month("2027-13", today) == parse_month("junk", today) == parse_month(None, today) == (2026, 11)
        assert shift_month(2026, 12, 1) == (2027, 1) and shift_month(2026, 1, -1) == (2025, 12)

    def test_recurring_holidays_match_every_year(self, holidays):
        assert [h.name for h in holidays_on(date(2031, 12, 25), holidays)] == ["Christmas Day"]
        assert holidays_on(date(2027, 11, 11), holidays) == []
        assert [h.name for h in holidays_on(date(2026, 11, 11), holidays)] == ["Armistice"]

    def test_month_grid(self, holidays):
        weeks = month_grid(2026, 11, holidays, date(2026, 11, 9))
        assert [w["number"] for w in weeks] == [44, 45, 46, 47, 48, 49]
        first = weeks[0]["cells"]
        assert first[:6] == [None] * 6 and first[6]["day"] == 1 and first[6]["weekend"]
        cells = {c["day"]: c for w in weeks for c in w["cells"] if c}
        assert len(cells) == 30 and cells[9]["today"] and not cells[10]["today"]
        assert [h.name for h in cells[11]["holidays"]] == ["Armistice"]


class TestPage:
    @pytest.mark.parametrize("role", ["VIEWER", "ESTIMATOR", "APPROVER"])
    def test_any_user_sees_calendar_read_only(self, web_login, make_user, holidays, role):
        resp = web_login(make_user(role)).get("/public-holidays?month=2026-11")
        body = resp.content.decode()
        assert resp.status_code == 200 and "Public Holidays" in body and "November 2026" in body
        assert "Armistice" in body and "All Holidays" in body
        assert "Add Holiday" not in body and "Import ICS" not in body and 'class="cal-add"' not in body
        assert 'title="Edit"' not in body

    def test_admin_sees_actions(self, admin_client, holidays):
        body = admin_client.get("/public-holidays?month=2026-11").content.decode()
        assert "Add Holiday" in body and "Import ICS" in body and 'hx-encoding="multipart/form-data"' in body
        assert 'hx-get="/public-holidays/new?date=2026-11-03"' in body
        assert f'hx-get="/public-holidays/{holidays[1].pk}/edit"' in body

    def test_defaults_to_current_month_and_marks_today(self, admin_client, seeded):
        resp = admin_client.get("/public-holidays")
        assert resp.context["cal"]["key"] == f"{TODAY.year:04d}-{TODAY.month:02d}"
        assert b'aria-current="date"' in resp.content

    def test_month_navigation_links_keep_state(self, admin_client, holidays):
        resp = admin_client.get("/public-holidays?month=2026-12&sort=name&dir=desc")
        links = resp.context["links"]
        assert links["month"]["prev"] == "/public-holidays?sort=name&dir=desc&month=2026-11"
        assert links["month"]["next"] == "/public-holidays?sort=name&dir=desc&month=2027-01"
        assert links["self"] == "/public-holidays?sort=name&dir=desc&month=2026-12"
        assert links["sort"]["date"].endswith("month=2026-12")
        assert "December 2026" in resp.content.decode() and "Christmas Day" in resp.content.decode()

    def test_recurring_holiday_shows_in_later_years(self, admin_client, holidays):
        body = admin_client.get("/public-holidays?month=2030-12").content.decode()
        assert "December 2030" in body and 'class="cal-badge"' in body and "Christmas Day" in body

    def test_tiles_and_list(self, admin_client, holidays):
        resp = admin_client.get("/public-holidays")
        tiles = {t.label: t for t in resp.context["results"].tiles}
        assert tiles["Total Holidays"].value == "3" and tiles["Total Holidays"].sub == "2 countries"
        assert tiles[f"This Year ({TODAY.year})"].value == str(sum(h.date.year == TODAY.year for h in holidays))
        assert tiles["Recurring"].value == "1"
        assert tiles["Countries"].value == "2" and tiles["Countries"].sub == "top: FR"
        rows = resp.context["results"].rows
        assert [r["name"] for r in rows] == ["Armistice", "Thanksgiving", "Christmas Day"]  # by date
        assert b'class="badge tone-yes">Yes<' in resp.content and b"3 entries" in resp.content

    def test_empty(self, admin_client, seeded):
        resp = admin_client.get("/public-holidays")
        assert b"No public holidays yet" in resp.content and "Countries" not in [
            t.label for t in resp.context["results"].tiles]

    def test_htmx_partial(self, admin_client, holidays):
        resp = admin_client.get("/public-holidays?month=2026-11", headers=HX_RESULTS)
        assert resp.status_code == 200 and b'id="crud-results"' in resp.content
        assert b"ed-side-nav" not in resp.content and b"November 2026" in resp.content


class TestWrite:
    def test_add_prefilled_from_calendar_day(self, admin_client, seeded):
        body = admin_client.get("/public-holidays/new?date=2026-11-03", headers=HX).content.decode()
        assert 'value="2026-11-03"' in body and "Recurring annually" in body
        assert "Delete this holiday" not in body
        assert admin_client.get("/public-holidays/new?date=bogus", headers=HX).status_code == 200

    def test_create(self, admin_client, seeded):
        resp = admin_client.post("/public-holidays/new", {"date": "2026-07-14", "name": " Bastille Day ",
                                                          "country": " FR ", "is_recurring": "on"}, headers=HX)
        assert resp.status_code == 204
        assert json.loads(resp["HX-Trigger"])["toast"]["message"] == "Holiday created."
        h = Holiday.objects.get()
        assert (h.date, h.name, h.country, h.is_recurring) == (date(2026, 7, 14), "Bastille Day", "FR", True)

    def test_validation(self, admin_client, seeded):
        resp = admin_client.post("/public-holidays/new", {"date": "2026-02-30", "name": ""}, headers=HX)
        assert resp.status_code == 422 and b"Enter a valid date" in resp.content
        assert b"This field is required." in resp.content and not Holiday.objects.exists()

    def test_edit_dialog_has_delete_and_saves(self, admin_client, holidays):
        h = holidays[0]
        body = admin_client.get(f"/public-holidays/{h.pk}/edit", headers=HX).content.decode()
        assert 'value="2026-12-25"' in body and "checked" in body
        assert f'hx-get="/public-holidays/{h.pk}/delete"' in body and "Delete this holiday" in body
        resp = admin_client.post(f"/public-holidays/{h.pk}/edit", {"date": "2026-12-26", "name": "Boxing Day",
                                                                    "country": ""}, headers=HX)
        assert resp.status_code == 204
        h.refresh_from_db()
        assert (h.date, h.name, h.country, h.is_recurring) == (date(2026, 12, 26), "Boxing Day", "", False)

    def test_service_error(self, admin_client, holidays, monkeypatch):
        def boom(holiday_id, updates):
            raise ServiceError(404, "Holiday not found")
        monkeypatch.setattr("apps.catalog.holidays.update_public_holiday", boom)
        resp = admin_client.post(f"/public-holidays/{holidays[0].pk}/edit", {"date": "2026-12-25", "name": "X"},
                                 headers=HX)
        assert resp.status_code == 422 and b"Holiday not found" in resp.content

    def test_delete(self, admin_client, holidays):
        h = holidays[1]
        assert b"Armistice" in admin_client.get(f"/public-holidays/{h.pk}/delete", headers=HX).content
        resp = admin_client.post(f"/public-holidays/{h.pk}/delete", headers=HX)
        assert resp.status_code == 204 and not Holiday.objects.filter(pk=h.pk).exists()
        assert admin_client.get("/public-holidays/999/edit").status_code == 404

    @pytest.mark.parametrize("role", ["VIEWER", "ESTIMATOR", "APPROVER"])
    def test_write_requires_admin(self, web_login, make_user, holidays, role):
        client = web_login(make_user(role))
        h = holidays[0]
        data = {"date": "2026-01-01", "name": "x"}
        assert client.get("/public-holidays/new").status_code == 403
        assert client.post("/public-holidays/new", data).status_code == 403
        assert client.post(f"/public-holidays/{h.pk}/edit", data).status_code == 403
        assert client.post(f"/public-holidays/{h.pk}/delete").status_code == 403
        assert client.post("/public-holidays/import-ics", {"file": ics()}).status_code == 403
        assert Holiday.objects.count() == 3


class TestIcsImport:
    def test_import_valid_file(self, admin_client, seeded):
        resp = admin_client.post("/public-holidays/import-ics", {"file": ics()}, headers=HX)
        assert resp.status_code == 204
        events = json.loads(resp["HX-Trigger"])
        assert events["crud-refresh"] and "close-modal" not in events
        assert events["toast"] == {"message": "Imported 3 holiday(s).\nSkipped 1 (duplicates or invalid).",
                                   "level": "success"}
        new_year = Holiday.objects.get(name="New Year")
        assert new_year.date == date(2027, 1, 1) and new_year.is_recurring
        assert Holiday.objects.get(name="Christmas Day").date == date(2027, 12, 25)

    def test_reimport_skips_duplicates(self, admin_client, seeded):
        admin_client.post("/public-holidays/import-ics", {"file": ics()}, headers=HX)
        resp = admin_client.post("/public-holidays/import-ics", {"file": ics()}, headers=HX)
        toast = json.loads(resp["HX-Trigger"])["toast"]
        assert toast["message"] == "Skipped 4 (duplicates or invalid)." and toast["level"] == "warning"
        assert Holiday.objects.count() == 3

    def test_invalid_dates_reported(self, admin_client, seeded):
        bad = b"BEGIN:VEVENT\nDTSTART:2027AB01\nSUMMARY:Broken\nEND:VEVENT\n"
        toast = json.loads(admin_client.post("/public-holidays/import-ics", {"file": ics(bad)},
                                             headers=HX)["HX-Trigger"])["toast"]
        assert "Errors: Invalid date: 2027AB01" in toast["message"] and toast["level"] == "warning"

    def test_not_a_calendar(self, admin_client, seeded):
        resp = admin_client.post("/public-holidays/import-ics", {"file": ics(b"\x89PNG\x00garbage")}, headers=HX)
        toast = json.loads(resp["HX-Trigger"])["toast"]
        assert toast == {"message": "No holidays found in the file.", "level": "error"}
        assert not Holiday.objects.exists()

    def test_missing_file_and_get(self, admin_client, seeded):
        resp = admin_client.post("/public-holidays/import-ics", {}, headers=HX)
        assert resp.status_code == 400 and b"Choose an .ics file" in resp.content
        assert admin_client.get("/public-holidays/import-ics").status_code == 405

    def test_too_large(self, admin_client, seeded, monkeypatch):
        monkeypatch.setattr("apps.catalog.pages.holidays.MAX_ICS_BYTES", 10)
        resp = admin_client.post("/public-holidays/import-ics", {"file": ics()}, headers=HX)
        assert resp.status_code == 400 and b"too large" in resp.content
