from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.database.db import get_db

router = APIRouter(prefix="/health", tags=["health"])


@router.get("")
def health():
    return {"status": "healthy", "service": "digital-transformer-twin"}


@router.get("/db")
def database_health(db: Session = Depends(get_db)):
    db.execute(text("SELECT 1"))
    return {"status": "healthy", "database": "postgresql"}
