"""API routes: /public-holidays/*, /working-weeks."""

from ninja import File, Router, Status, UploadedFile

from apps.api.auth import authenticated, role
from apps.api.schemas.holidays import (
    DeletedOut,
    IcsImportOut,
    PublicHolidayCreate,
    PublicHolidayOut,
    PublicHolidayUpdate,
)
from apps.catalog import holidays as services

from .catalog import call

router = Router(auth=authenticated, tags=["holidays"])


@router.get("/public-holidays", response=list[PublicHolidayOut])
def list_public_holidays(request, year: int | None = None):
    """List all public holidays; with ``year``, that year's plus recurring ones."""
    return services.list_public_holidays(year)


@router.post("/public-holidays", response={201: PublicHolidayOut}, auth=role("ADMIN"))
def create_public_holiday(request, data: PublicHolidayCreate):
    return Status(201, services.create_public_holiday(data.model_dump()))


@router.post("/public-holidays/import-ics", response=IcsImportOut, auth=role("ADMIN"))
def import_holidays_ics(request, file: File[UploadedFile]):
    """Import public holidays from an ICS (iCalendar) file."""
    return services.import_ics(file.read())


@router.put("/public-holidays/{holiday_id}", response=PublicHolidayOut, auth=role("ADMIN"))
def update_public_holiday(request, holiday_id: int, data: PublicHolidayUpdate):
    return call(services.update_public_holiday, holiday_id, data.model_dump())


@router.delete("/public-holidays/{holiday_id}", response=DeletedOut, auth=role("ADMIN"))
def delete_public_holiday(request, holiday_id: int):
    return call(services.delete_public_holiday, holiday_id)


@router.get("/working-weeks")
def calculate_working_weeks(request, total_days: float, start_date: str | None = None):
    """Working weeks from total days; with ``start_date``, holidays in the period are excluded."""
    return call(services.working_weeks, total_days, start_date)
