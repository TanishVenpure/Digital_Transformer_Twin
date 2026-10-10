from sqlalchemy.orm import Session

from app.database.repository import Repository
from app.processing.validation import validate_date_range
from app.services.errors import BadRequest


class TransformerService:
    """Single-transformer access; preserve source IDs without requiring a selector."""

    def __init__(self, session: Session):
        self.repository = Repository(session)

    def sole_transformer_id(self) -> str | None:
        identifiers = self.repository.transformer_ids()
        if len(identifiers) > 1:
            raise BadRequest("This backend expects one transformer; stored data contains multiple IDs")
        return identifiers[0] if identifiers else None

    def latest(self, transformer_id: str | None):
        """Latest data for a transformer ID already resolved and authorized by the caller."""
        readings = self.repository.readings(transformer_id, limit=1) if transformer_id else []
        metrics = self.repository.daily_metrics(transformer_id, limit=1) if transformer_id else []
        return {
            "transformer_id": transformer_id,
            "latest_reading": readings[0] if readings else None,
            "latest_daily_metric": metrics[0] if metrics else None,
        }

    def history(self, transformer_id: str | None, *, start_date=None, end_date=None, limit=50, offset=0):
        """History for a transformer ID already resolved and authorized by the caller."""
        start, end = validate_date_range(start_date, end_date)
        if transformer_id is None:
            return {"transformer_id": None, "recent_readings": [], "daily_metrics": [], "alerts": []}
        return {
            "transformer_id": transformer_id,
            "recent_readings": self.repository.readings(
                transformer_id, start_date=start, end_date=end, limit=limit, offset=offset
            ),
            "daily_metrics": self.repository.daily_metrics(
                transformer_id, start_date=start.date() if start else None,
                end_date=end.date() if end else None, limit=limit, offset=offset,
            ),
            "alerts": self.repository.alerts(
                transformer_id=transformer_id, start_date=start, end_date=end,
                limit=limit, offset=offset,
            ),
        }
