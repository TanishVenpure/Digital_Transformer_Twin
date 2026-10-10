from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query, Request
from sqlalchemy.orm import Session

from app.api.agent import authorize_transformer, require_agent_access, require_alert_management
from app.database.db import get_db
from app.alerts.types import MaintenanceType
from app.schemas.alert import AlertHistoryResponse, AlertResponse
from app.services.alert_service import AlertService

router = APIRouter(prefix="/alerts", tags=["alerts"])
AlertID = Annotated[int, Path(gt=0)]
Limit = Annotated[int, Query(ge=1, le=1000)]
Offset = Annotated[int, Query(ge=0)]


def _scope(request: Request, transformer_id: str | None, authorized_transformers: set[str]) -> dict:
    """Narrow to one authorized transformer, or to the credential's whole scope."""
    if transformer_id is not None:
        authorize_transformer(request, transformer_id, authorized_transformers)
        return {"transformer_id": transformer_id}
    return {"transformer_id": None, "transformer_ids": sorted(authorized_transformers)}


@router.get("", response_model=list[AlertResponse])
def list_alerts(
    request: Request,
    maintenance_type: MaintenanceType | None = None,
    transformer_id: str | None = None,
    parameter: str | None = None, severity: str | None = None,
    start_date: datetime | None = None, end_date: datetime | None = None,
    acknowledged: bool | None = None, resolved: bool | None = None,
    limit: Limit = 50, offset: Offset = 0,
    authorized_transformers: set[str] = Depends(require_agent_access),
    db: Session = Depends(get_db),
):
    """Use resolved=false to retrieve active alerts for the agent.

    Requires X-Agent-API-Key. transformer_id must be within the credential's
    scope; when omitted, results are limited to every authorized transformer.
    """
    return AlertService(db).list_alerts(
        maintenance_type=maintenance_type, parameter=parameter, severity=severity, start_date=start_date, end_date=end_date,
        acknowledged=acknowledged, resolved=resolved, limit=limit, offset=offset,
        **_scope(request, transformer_id, authorized_transformers),
    )


# Register the fixed history path before /{alert_id}.
@router.get("/history", response_model=AlertHistoryResponse)
def alert_history(
    request: Request,
    maintenance_type: MaintenanceType | None = None,
    transformer_id: str | None = None,
    parameter: str | None = None, severity: str | None = None,
    start_date: datetime | None = None, end_date: datetime | None = None,
    acknowledged: bool | None = None, resolved: bool | None = None,
    limit: Limit = 50, offset: Offset = 0,
    authorized_transformers: set[str] = Depends(require_agent_access),
    db: Session = Depends(get_db),
):
    """Paginated stored alert history, including resolved alerts by default.

    Requires X-Agent-API-Key and is scoped like the alert list.
    """
    return AlertService(db).history(
        maintenance_type=maintenance_type, parameter=parameter, severity=severity, start_date=start_date, end_date=end_date,
        acknowledged=acknowledged, resolved=resolved, limit=limit, offset=offset,
        **_scope(request, transformer_id, authorized_transformers),
    )


@router.get("/{alert_id}", response_model=AlertResponse)
def get_alert(
    alert_id: AlertID,
    authorized_transformers: set[str] = Depends(require_agent_access),
    db: Session = Depends(get_db),
):
    return AlertService(db).get_alert(alert_id, authorized_transformers=authorized_transformers)


@router.patch("/{alert_id}/acknowledge", response_model=AlertResponse)
def acknowledge_alert(
    alert_id: AlertID,
    authorized_transformers: set[str] = Depends(require_alert_management),
    db: Session = Depends(get_db),
):
    return AlertService(db).acknowledge(alert_id, authorized_transformers=authorized_transformers)


@router.patch("/{alert_id}/resolve", response_model=AlertResponse)
def resolve_alert(
    alert_id: AlertID,
    authorized_transformers: set[str] = Depends(require_alert_management),
    db: Session = Depends(get_db),
):
    return AlertService(db).resolve(alert_id, authorized_transformers=authorized_transformers)
