from datetime import date
from typing import Any

from sqlalchemy.orm import Session

from app.database.repository import Repository
from app.processing.ingestion import preview_csv
from app.processing.validation import validate_reading
from app.schemas.sensor import SensorReadingCreate, UploadResponse


class ProcessingService:
    """Shared ingestion boundary for real data and future synthetic readings.

    Future pipeline: receive -> validate -> normalize -> store hourly reading ->
    check completed day -> aggregate -> formulas -> thresholds -> alerts -> persist.
    Only receive, schema validation, and storage are enabled now.
    """

    def __init__(self, session: Session):
        self.session = session
        self.repository = Repository(session)

    def receive_reading(self, reading: SensorReadingCreate):
        reading = validate_reading(reading)
        stored = self.repository.add_reading(
            transformer_id=reading.transformer_id, timestamp=reading.timestamp, raw_data=reading.data
        )
        self.session.commit()
        self.session.refresh(stored)
        return stored

    def receive_batch(self, readings: list[SensorReadingCreate]) -> int:
        rows = [
            {"transformer_id": item.transformer_id, "timestamp": item.timestamp, "raw_data": item.data}
            for item in (validate_reading(reading) for reading in readings)
        ]
        inserted = self.repository.add_readings(rows)
        self.session.commit()
        return inserted

    @staticmethod
    def preview_upload(filename: str | None, content: bytes) -> UploadResponse:
        return preview_csv(filename, content)

    def normalize_data(self, data: dict[str, Any]) -> dict[str, Any]:
        """Apply approved column mapping/units later; raw inputs remain preserved."""
        raise NotImplementedError("Normalization is not configured")

    def aggregate_daily_data(self, transformer_id: str, day: date):
        """Invoke an approved aggregator and persist its DailyMetric later."""
        raise NotImplementedError("Daily aggregation is not configured")

    def run_formulas(self, data: dict[str, Any]):
        """Execute registered, approved formulas and record their actual inputs/results."""
        raise NotImplementedError("Transformer formulas are not configured")

    def evaluate_thresholds(self, formula_results: dict[str, Any]):
        """Delegate comparisons and alert construction to the alert engine later."""
        raise NotImplementedError("Threshold evaluation is not configured")

    def process_completed_day(self, transformer_id: str, day: date):
        """Orchestrate aggregation, formulas, threshold evaluation, and alert persistence later."""
        raise NotImplementedError("Completed-day processing is not configured")
