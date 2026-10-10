from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query, Request
from sqlalchemy.orm import Session

from app.api.agent import authorize_transformer, require_agent_access, require_ingest_access
from app.database.db import get_db
from app.database.repository import Repository
from app.models.maintenance import MaintenanceRecord
from app.schemas.maintenance import MaintenanceRecordCreate, MaintenanceRecordResponse

router = APIRouter(prefix="/maintenance", tags=["maintenance"])
Limit = Annotated[int, Query(ge=1, le=100)]
Offset = Annotated[int, Query(ge=0)]


@router.post("/records", response_model=MaintenanceRecordResponse, status_code=201)
def create_record(
    record: MaintenanceRecordCreate,
    request: Request,
    authorized_transformers: set[str] = Depends(require_ingest_access),
    db: Session = Depends(get_db),
):
    authorize_transformer(request, record.transformer_id, authorized_transformers)
    stored = Repository(db).add_maintenance_record(MaintenanceRecord(**record.model_dump()))
    db.commit()
    db.refresh(stored)
    return stored


@router.get("/records", response_model=list[MaintenanceRecordResponse])
def list_records(
    transformer_id: str,
    request: Request,
    limit: Limit = 50,
    offset: Offset = 0,
    authorized_transformers: set[str] = Depends(require_agent_access),
    db: Session = Depends(get_db),
):
    authorize_transformer(request, transformer_id, authorized_transformers)
    return Repository(db).maintenance_history(transformer_id, limit=limit, offset=offset)
