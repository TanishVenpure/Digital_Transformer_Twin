from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, Index, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, utc_now


class MaintenanceRecord(Base):
    """Stored maintenance history; it records supplied facts without interpretation."""

    __tablename__ = "maintenance_records"
    __table_args__ = (Index("ix_maintenance_transformer_occurred", "transformer_id", "occurred_at"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    transformer_id: Mapped[str] = mapped_column(String(255), index=True)
    record_type: Mapped[str] = mapped_column(String(80), index=True)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    details: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
