from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.schemas.sensor import as_utc, reject_future


class MaintenanceRecordCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    transformer_id: str = Field(min_length=1, max_length=255)
    record_type: str = Field(min_length=1, max_length=80)
    occurred_at: datetime
    details: dict[str, Any] = Field(default_factory=dict)
    notes: str | None = Field(default=None, max_length=5000)

    @field_validator("occurred_at")
    @classmethod
    def normalize_utc(cls, value: datetime) -> datetime:
        # Same UTC normalization and future bound as sensor readings.
        return reject_future(value)


class MaintenanceRecordResponse(MaintenanceRecordCreate):
    model_config = ConfigDict(from_attributes=True)

    id: int
    created_at: datetime

    @field_validator("occurred_at")
    @classmethod
    def normalize_utc(cls, value: datetime) -> datetime:
        # Responses describe stored rows; only creation applies the future bound.
        return as_utc(value)
