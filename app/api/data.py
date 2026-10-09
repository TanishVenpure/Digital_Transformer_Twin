from typing import Annotated

from fastapi import APIRouter, Body, Depends, File, UploadFile
from sqlalchemy.orm import Session

from app.database.db import get_db
from app.processing.ingestion import MAX_UPLOAD_BYTES
from app.schemas.sensor import BatchResponse, SensorReadingCreate, SensorReadingResponse, UploadResponse
from app.services.processing_service import ProcessingService

router = APIRouter(prefix="/data", tags=["data"])


@router.post("/reading", response_model=SensorReadingResponse, status_code=201)
def create_reading(reading: SensorReadingCreate, db: Session = Depends(get_db)):
    return ProcessingService(db).receive_reading(reading)


@router.post("/batch", response_model=BatchResponse, status_code=201)
def create_batch(
    readings: Annotated[list[SensorReadingCreate], Body(min_length=1, max_length=10000)],
    db: Session = Depends(get_db),
):
    service = ProcessingService(db)
    inserted = service.receive_batch(readings)
    return BatchResponse(inserted=inserted, evaluations=service.batch_evaluations)


@router.post("/upload", response_model=UploadResponse)
async def upload_csv(file: UploadFile = File(...)):
    try:
        content = await file.read(MAX_UPLOAD_BYTES + 1)
        return ProcessingService.preview_upload(file.filename, content)
    finally:
        await file.close()
