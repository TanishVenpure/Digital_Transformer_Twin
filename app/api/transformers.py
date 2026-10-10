from datetime import datetime

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from app.api.agent import authorize_transformer, require_agent_access
from app.api.alerts import Limit, Offset
from app.database.db import get_db
from app.schemas.transformer import TransformerHistoryResponse, TransformerLatestResponse
from app.services.transformer_service import TransformerService

router = APIRouter(prefix="/transformer", tags=["transformer"])


def _authorized_sole_transformer(
    request: Request, service: TransformerService, authorized_transformers: set[str],
) -> str | None:
    """Resolve the stored transformer server-side and authorize it before any data is read.

    These routes accept no transformer selector; any client-supplied query value is ignored.
    """
    transformer_id = service.sole_transformer_id()
    if transformer_id is not None:
        authorize_transformer(request, transformer_id, authorized_transformers)
    return transformer_id


@router.get("/latest", response_model=TransformerLatestResponse)
def latest(
    request: Request,
    authorized_transformers: set[str] = Depends(require_agent_access),
    db: Session = Depends(get_db),
):
    """Latest available data for the project's sole transformer.

    Requires X-Agent-API-Key authorized for the stored transformer ID.
    """
    service = TransformerService(db)
    transformer_id = _authorized_sole_transformer(request, service, authorized_transformers)
    return service.latest(transformer_id)


@router.get("/history", response_model=TransformerHistoryResponse)
def history(
    request: Request,
    start_date: datetime | None = None, end_date: datetime | None = None,
    limit: Limit = 50, offset: Offset = 0,
    authorized_transformers: set[str] = Depends(require_agent_access),
    db: Session = Depends(get_db),
):
    """Readings, daily metrics, and maintenance-alert history for the sole transformer.

    Requires X-Agent-API-Key authorized for the stored transformer ID.
    """
    service = TransformerService(db)
    transformer_id = _authorized_sole_transformer(request, service, authorized_transformers)
    return service.history(
        transformer_id, start_date=start_date, end_date=end_date, limit=limit, offset=offset
    )
