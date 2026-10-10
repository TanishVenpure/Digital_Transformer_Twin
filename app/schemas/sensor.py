import json
from datetime import datetime, timedelta, timezone
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.formulas.base import FormulaResult


def as_utc(value: datetime) -> datetime:
    """Legacy naive timestamps are explicitly interpreted as UTC."""
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


# Clock-skew tolerance between a source and this server; not a transformer threshold.
# A timestamp later than now (UTC) + this bound is rejected before any database write,
# so it can never become the "latest" reading, a rate-history input, or agent evidence.
MAX_FUTURE_SKEW = timedelta(minutes=5)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def reject_future(value: datetime) -> datetime:
    """Normalize to UTC and reject timestamps beyond the allowed future skew."""
    value = as_utc(value)
    if value > utc_now() + MAX_FUTURE_SKEW:
        raise ValueError(
            f"timestamp must not be more than {int(MAX_FUTURE_SKEW.total_seconds())} seconds in the future"
        )
    return value


class SensorReadingCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    transformer_id: str = Field(min_length=1, max_length=255)
    timestamp: datetime
    data: dict[str, Any]

    @field_validator("transformer_id")
    @classmethod
    def nonblank_identifier(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("transformer_id must not be blank")
        return value

    @field_validator("timestamp")
    @classmethod
    def normalize_timestamp(cls, value: datetime) -> datetime:
        return reject_future(value)

    @field_validator("data")
    @classmethod
    def json_data(cls, value: dict[str, Any]) -> dict[str, Any]:
        try:
            json.dumps(value, allow_nan=False)
        except (TypeError, ValueError) as exc:
            raise ValueError("data must contain finite JSON-compatible values") from exc
        return value


class SensorReadingResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    transformer_id: str
    timestamp: datetime
    raw_data: dict[str, Any]
    created_at: datetime
    evaluations: dict[str, FormulaResult] = Field(default_factory=dict)


class BatchResponse(BaseModel):
    inserted: int
    evaluations: list[dict[str, FormulaResult]] = Field(default_factory=list)


class UploadResponse(BaseModel):
    message: str
    columns: list[str]
    preview_rows: int
    stored: bool = False
