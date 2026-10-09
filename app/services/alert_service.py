from sqlalchemy.orm import Session

from app.database.repository import Repository
from app.models.base import utc_now
from app.processing.validation import validate_date_range
from app.services.errors import ResourceNotFound
from app.schemas.alert import AlertHistoryResponse


class AlertService:
    def __init__(self, session: Session):
        self.session = session
        self.repository = Repository(session)

    def list_alerts(self, **filters):
        start, end = validate_date_range(filters.get("start_date"), filters.get("end_date"))
        return self.repository.alerts(**{**filters, "start_date": start, "end_date": end})

    def history(self, *, limit=50, offset=0, **filters):
        # One extra record determines whether another page is available.
        records = self.list_alerts(limit=limit + 1, offset=offset, **filters)
        return AlertHistoryResponse(
            items=records[:limit], limit=limit, offset=offset,
            next_offset=offset + limit if len(records) > limit else None,
        )

    def get_alert(self, alert_id: int):
        alert = self.repository.get_alert(alert_id)
        if alert is None:
            raise ResourceNotFound("Alert not found")
        return alert

    def acknowledge(self, alert_id: int):
        alert = self.get_alert(alert_id)
        alert.acknowledged = True
        self.session.commit()
        self.session.refresh(alert)
        return alert

    def resolve(self, alert_id: int):
        alert = self.get_alert(alert_id)
        if not alert.resolved:
            alert.resolved = True
            alert.resolved_at = utc_now()
        self.session.commit()
        self.session.refresh(alert)
        return alert
