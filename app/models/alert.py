from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, DateTime, Float, Index, String, Text, Enum, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, utc_now
from app.alerts.types import MaintenanceType


class Alert(Base):
    __tablename__ = "alerts"
    __table_args__ = (Index("ix_alert_transformer_timestamp", "transformer_id", "timestamp"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    transformer_id: Mapped[str] = mapped_column(String(255), index=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    parameter: Mapped[str] = mapped_column(String(255))
    formula_name: Mapped[str] = mapped_column(String(255))
    formula_value: Mapped[float] = mapped_column(Float)
    threshold: Mapped[float] = mapped_column(Float)
    comparison_operator: Mapped[str] = mapped_column(String(2))
    severity: Mapped[str] = mapped_column(String(50), index=True)
    message: Mapped[str] = mapped_column(Text)
    maintenance_type: Mapped[MaintenanceType | None] = mapped_column(
        Enum(MaintenanceType, native_enum=False, create_constraint=True, name="maintenance_type"),
        nullable=True, index=True,
    )
    details: Mapped[dict[str, Any]] = mapped_column(
        JSONB, default=dict, server_default=text("'{}'::jsonb"),
    )
    formula_inputs: Mapped[dict[str, Any]] = mapped_column(JSONB)
    acknowledged: Mapped[bool] = mapped_column(Boolean, default=False)
    resolved: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
