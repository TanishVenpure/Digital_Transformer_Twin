from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query
from sqlalchemy.orm import Session

from app.database.db import get_db
from app.schemas.alert import AlertHistoryResponse, AlertResponse
from app.services.alert_service import AlertService

router = APIRouter(prefix="/alerts", tags=["alerts"])
AlertID = Annotated[int, Path(gt=0)]
Limit = Annotated[int, Query(ge=1, le=1000)]
Offset = Annotated[int, Query(ge=0)]


@router.get("", response_model=list[AlertResponse])
def list_alerts(
    parameter: str | None = None, severity: str | None = None,
    start_date: datetime | None = None, end_date: datetime | None = None,
    acknowledged: bool | None = None, resolved: bool | None = None,
    limit: Limit = 50, offset: Offset = 0, db: Session = Depends(get_db),
):
    """Use resolved=false to retrieve active alerts for the agent."""
    return AlertService(db).list_alerts(
        parameter=parameter, severity=severity, start_date=start_date, end_date=end_date,
        acknowledged=acknowledged, resolved=resolved, limit=limit, offset=offset,
    )


# Register the fixed history path before /{alert_id}.
@router.get("/history", response_model=AlertHistoryResponse)
def alert_history(
    parameter: str | None = None, severity: str | None = None,
    start_date: datetime | None = None, end_date: datetime | None = None,
    acknowledged: bool | None = None, resolved: bool | None = None,
    limit: Limit = 50, offset: Offset = 0, db: Session = Depends(get_db),
):
    """Paginated stored alert history, including resolved alerts by default."""
    return AlertService(db).history(
        parameter=parameter, severity=severity, start_date=start_date, end_date=end_date,
        acknowledged=acknowledged, resolved=resolved, limit=limit, offset=offset,
    )


@router.get("/{alert_id}", response_model=AlertResponse)
def get_alert(alert_id: AlertID, db: Session = Depends(get_db)):
    return AlertService(db).get_alert(alert_id)


@router.patch("/{alert_id}/acknowledge", response_model=AlertResponse)
def acknowledge_alert(alert_id: AlertID, db: Session = Depends(get_db)):
    return AlertService(db).acknowledge(alert_id)


@router.patch("/{alert_id}/resolve", response_model=AlertResponse)
def resolve_alert(alert_id: AlertID, db: Session = Depends(get_db)):
    return AlertService(db).resolve(alert_id)
