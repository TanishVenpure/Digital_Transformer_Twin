from datetime import datetime

from app.schemas.sensor import SensorReadingCreate, as_utc
from app.services.errors import BadRequest


def validate_reading(reading: SensorReadingCreate) -> SensorReadingCreate:
    """Revalidate at the shared pipeline boundary, including non-HTTP callers."""
    return SensorReadingCreate.model_validate(reading.model_dump())


def validate_date_range(start: datetime | None, end: datetime | None):
    start = as_utc(start) if start is not None else None
    end = as_utc(end) if end is not None else None
    if start is not None and end is not None and start > end:
        raise BadRequest("start_date must not be after end_date")
    return start, end
