from datetime import date, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict

from app.schemas.alert import AlertResponse
from app.schemas.sensor import SensorReadingResponse


class DailyMetricResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    transformer_id: str
    date: date
    aggregated_data: dict[str, Any]
    formula_results: dict[str, Any]
    created_at: datetime


class TransformerResponse(BaseModel):
    transformer_id: str | None


class TransformerLatestResponse(TransformerResponse):
    latest_reading: SensorReadingResponse | None
    latest_daily_metric: DailyMetricResponse | None


class TransformerHistoryResponse(TransformerResponse):
    recent_readings: list[SensorReadingResponse]
    daily_metrics: list[DailyMetricResponse]
    alerts: list[AlertResponse]
