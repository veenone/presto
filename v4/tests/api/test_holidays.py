"""Public holidays, ICS import and working weeks."""

from __future__ import annotations

from apps.catalog.models import PublicHoliday

ICS = b"""BEGIN:VCALENDAR
VERSION:2.0
BEGIN:VEVENT
DTSTART;VALUE=DATE:20260101
SUMMARY:New Year
RRULE:FREQ=YEARLY
END:VEVENT
BEGIN:VEVENT
DTSTART:20260501T000000Z
SUMMARY:Labour Day
END:VEVENT
BEGIN:VEVENT
DTSTART;VALUE=DATE:2026XX01
SUMMARY:Broken
END:VEVENT
BEGIN:VEVENT
DTSTART;VALUE=DATE:20260714
END:VEVENT
END:VCALENDAR
"""


class TestPublicHolidays:
    def test_crud(self, api):
        resp = api.post(
            "/api/public-holidays", json={"date": "2026-12-25", "name": "Christmas", "country": "FR"}
        )
        assert resp.status_code == 201
        hid = resp.json()["id"]
        assert resp.json() == {
            "id": hid,
            "date": "2026-12-25",
            "name": "Christmas",
            "country": "FR",
            "is_recurring": False,
        }
        resp = api.put(f"/api/public-holidays/{hid}", json={"is_recurring": True, "date": ""})
        assert resp.json()["is_recurring"] is True and resp.json()["date"] == "2026-12-25"
        resp = api.delete(f"/api/public-holidays/{hid}")
        assert resp.status_code == 200 and resp.json() == {"detail": "Deleted"}
        assert api.delete(f"/api/public-holidays/{hid}").json()["detail"] == "Holiday not found"
        assert api.put(f"/api/public-holidays/{hid}", json={}).status_code == 404

    def test_year_filter_keeps_recurring(self, api):
        PublicHoliday.objects.create(date="2025-01-01", name="NY", is_recurring=True)
        PublicHoliday.objects.create(date="2025-05-08", name="V-Day")
        PublicHoliday.objects.create(date="2026-07-14", name="Bastille")
        names = [h["name"] for h in api.get("/api/public-holidays", params={"year": 2026}).json()]
        assert names == ["NY", "Bastille"]
        assert len(api.get("/api/public-holidays").json()) == 3

    def test_admin_only(self, api_as, make_user):
        client = api_as(make_user("APPROVER"))
        assert (
            client.post("/api/public-holidays", json={"date": "2026-01-01", "name": "x"}).status_code == 403
        )
        assert client.get("/api/public-holidays").status_code == 200


class TestIcsImport:
    def test_import(self, api):
        resp = api.post("/api/public-holidays/import-ics", files={"file": ("h.ics", ICS, "text/calendar")})
        assert resp.status_code == 200, resp.content
        assert resp.json() == {"imported": 2, "skipped": 2, "errors": ["Invalid date: 2026XX01"]}
        ny = PublicHoliday.objects.get(name="New Year")
        assert ny.is_recurring is True and str(ny.date) == "2026-01-01"
        assert PublicHoliday.objects.get(name="Labour Day").is_recurring is False
        # Re-import skips duplicates.
        again = api.post("/api/public-holidays/import-ics", files={"file": ("h.ics", ICS, "text/calendar")})
        assert again.json()["imported"] == 0 and again.json()["skipped"] == 4

    def test_admin_only(self, api_as, make_user):
        resp = api_as(make_user("APPROVER")).post(
            "/api/public-holidays/import-ics", files={"file": ("h.ics", ICS, "text/calendar")}
        )
        assert resp.status_code == 403


class TestWorkingWeeks:
    def test_simple(self, api):
        resp = api.get("/api/working-weeks", params={"total_days": 12})
        assert resp.json() == {
            "total_days": 12.0,
            "working_days": 12.0,
            "working_weeks": 2.4,
            "holidays_excluded": 0,
        }

    def test_with_start_date_and_holidays(self, api):
        # 2026-05-01 is a Friday; recurring holiday stored with another year still applies.
        PublicHoliday.objects.create(date="2020-05-01", name="Labour Day", is_recurring=True)
        PublicHoliday.objects.create(date="2026-05-08", name="Victory")
        resp = api.get("/api/working-weeks", params={"total_days": 5, "start_date": "2026-05-01"})
        assert resp.status_code == 200
        assert resp.json() == {
            "total_days": 5.0,
            "working_days": 5,
            "working_weeks": 1.0,
            "holidays_excluded": 2,
            "start_date": "2026-05-01",
            "end_date": "2026-05-11",
            "calendar_days": 11,
        }

    def test_requires_total_days(self, api):
        assert api.get("/api/working-weeks").status_code == 422

    def test_invalid_start_date(self, api):
        assert (
            api.get("/api/working-weeks", params={"total_days": 1, "start_date": "nope"}).status_code == 400
        )

    def test_requires_auth(self, anon_api):
        assert anon_api.get("/api/working-weeks", params={"total_days": 1}).status_code == 401
