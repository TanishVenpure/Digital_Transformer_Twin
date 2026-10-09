from datetime import datetime

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.alerts import Limit, Offset
from app.database.db import get_db
from app.schemas.transformer import TransformerHistoryResponse, TransformerLatestResponse
from app.services.transformer_service import TransformerService

router = APIRouter(prefix="/transformer", tags=["transformer"])


@router.get("/latest", response_model=TransformerLatestResponse)
def latest(db: Session = Depends(get_db)):
    """Latest available data for the project's sole transformer."""
    return TransformerService(db).latest()


@router.get("/history", response_model=TransformerHistoryResponse)
def history(
    start_date: datetime | None = None, end_date: datetime | None = None,
    limit: Limit = 50, offset: Offset = 0, db: Session = Depends(get_db),
):
    """Readings, daily metrics, and maintenance-alert history for the sole transformer."""
    return TransformerService(db).history(
        start_date=start_date, end_date=end_date, limit=limit, offset=offset
    )
