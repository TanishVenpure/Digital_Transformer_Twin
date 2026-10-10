"""Future-time bound for stored timestamps (readings and maintenance records)."""
from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

import app.schemas.sensor as sensor_schema
from app.models.maintenance import MaintenanceRecord
from app.schemas.maintenance import MaintenanceRecordCreate, MaintenanceRecordResponse
from app.schemas.sensor import MAX_FUTURE_SKEW, SensorReadingCreate

NOW = datetime(2026, 10, 9, 12, 0, 0, tzinfo=timezone.utc)
LIMIT = NOW + MAX_FUTURE_SKEW  # 12:05:00Z


@pytest.fixture(autouse=True)
def frozen_now(monkeypatch):
    monkeypatch.setattr(sensor_schema, "utc_now", lambda: NOW)


def reading(timestamp):
    return {"transformer_id": "TX-001", "timestamp": timestamp, "data": {"value": 1.0}}


def maintenance(occurred_at):
    return {"transformer_id": "TX-001", "record_type": "inspection", "occurred_at": occurred_at}


def test_bound_is_five_minutes():
    assert MAX_FUTURE_SKEW == timedelta(minutes=5)


@pytest.mark.parametrize("timestamp", [
    "2026-10-09T12:05:00Z",              # exactly at the bound
    "2026-10-09T12:04:59.999999+00:00",
    "2026-10-09T17:35:00+05:30",         # = 12:05:00Z
    "2026-10-09T07:05:00-05:00",         # = 12:05:00Z
    "2026-10-09T12:05:00",               # naive is interpreted as UTC
    "2026-10-10T00:00:00+14:00",         # later wall-clock date, but = 2026-10-09T10:00Z
    "2026-10-09T11:00:00Z",              # past
    "2000-01-01T00:00:00Z",
])
@pytest.mark.parametrize("model,build", [
    (SensorReadingCreate, reading), (MaintenanceRecordCreate, maintenance),
])
def test_timestamps_up_to_the_bound_are_accepted_as_utc(model, build, timestamp):
    value = model(**build(timestamp))
    stored = value.timestamp if model is SensorReadingCreate else value.occurred_at
    assert stored.tzinfo == timezone.utc
    assert stored <= LIMIT


@pytest.mark.parametrize("timestamp", [
    "2026-10-09T12:05:00.000001Z",       # one microsecond past the bound
    "2026-10-09T12:05:01Z",
    "2026-10-09T17:35:01+05:30",         # offset form of 12:05:01Z
    "2026-10-09T07:05:01-05:00",
    "2026-10-09T12:05:01",               # naive, as UTC
    "9999-12-31T23:59:59Z",
])
@pytest.mark.parametrize("model,build", [
    (SensorReadingCreate, reading), (MaintenanceRecordCreate, maintenance),
])
def test_timestamps_beyond_the_bound_are_rejected(model, build, timestamp):
    with pytest.raises(ValidationError, match="in the future"):
        model(**build(timestamp))


def test_large_negative_offset_that_lands_in_the_future_is_rejected():
    # 2026-10-09T02:00-12:00 is 14:00Z, two hours ahead despite the earlier wall clock.
    with pytest.raises(ValidationError, match="in the future"):
        SensorReadingCreate(**reading("2026-10-09T02:00:00-12:00"))


def test_response_model_does_not_reject_previously_stored_future_rows():
    stored = MaintenanceRecord(
        id=1, transformer_id="TX-001", record_type="inspection",
        occurred_at=datetime(9999, 1, 1, tzinfo=timezone.utc), details={}, notes=None,
        created_at=NOW,
    )
    assert MaintenanceRecordResponse.model_validate(stored).occurred_at.year == 9999


@pytest.mark.parametrize("path,body", [
    ("/api/v1/data/reading", reading("9999-01-01T00:00:00Z")),
    ("/api/v1/data/reading", reading("2026-10-09T12:05:01Z")),
    ("/api/v1/data/batch", [reading("2026-10-09T11:00:00Z"), reading("9999-01-01T00:00:00Z")]),
    ("/api/v1/maintenance/records", maintenance("9999-01-01T00:00:00Z")),
])
def test_api_rejects_future_timestamps_before_any_write(client, db, path, body):
    response = client.post(path, json=body)
    assert response.status_code == 422
    assert "9999" not in response.text  # validation errors do not echo the payload
    db.add.assert_not_called()
    db.execute.assert_not_called()
    db.commit.assert_not_called()
