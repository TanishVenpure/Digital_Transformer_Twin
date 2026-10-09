from datetime import date, datetime
from typing import Any

from sqlalchemy import Date, DateTime, Index, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, utc_now


class DailyMetric(Base):
    __tablename__ = "daily_metrics"
    __table_args__ = (
        UniqueConstraint("transformer_id", "date", name="uq_daily_transformer_date"),
        Index("ix_daily_transformer_date", "transformer_id", "date"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    transformer_id: Mapped[str] = mapped_column(String(255), index=True)
    date: Mapped[date] = mapped_column(Date, index=True)
    aggregated_data: Mapped[dict[str, Any]] = mapped_column(JSONB)
    formula_results: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
