from datetime import timezone

import pytest
from pydantic import ValidationError

from app.processing.ingestion import MAX_UPLOAD_BYTES, load_column_mapping
from app.schemas.sensor import SensorReadingCreate
from app.services.processing_service import ProcessingService


def payload():
    return {
        "transformer_id": "T01", "timestamp": "2026-10-09T10:00:00+05:30",
        "data": {"arbitrary": 123.4, "nested": {"original": True}},
    }


def test_timestamp_normalization_and_raw_preservation():
    reading = SensorReadingCreate(**payload())
    assert reading.timestamp.hour == 4
    assert reading.timestamp.minute == 30
    assert reading.timestamp.tzinfo == timezone.utc
    assert reading.data == payload()["data"]
    naive = SensorReadingCreate(**{**payload(), "timestamp": "2026-10-09T10:00:00"})
    assert naive.timestamp.tzinfo == timezone.utc


@pytest.mark.parametrize("changes", [
    {"transformer_id": ""}, {"transformer_id": " "},
    {"timestamp": "invalid"}, {"data": []}, {"data": {"value": float("nan")}},
    {"data": {"nested": [float("inf")]}},
])
def test_invalid_sensor_schema(changes):
    with pytest.raises(ValidationError):
        SensorReadingCreate(**{**payload(), **changes})


def test_invalid_api_schema(client):
    response = client.post("/api/v1/data/reading", json={})
    assert response.status_code == 422
    assert all("input" not in error for error in response.json()["detail"])


def test_store_single_reading(client, db):
    def assign_generated_fields():
        from app.models.base import utc_now
        reading = db.add.call_args.args[0]
        reading.id = 1
        reading.created_at = utc_now()
    db.flush.side_effect = assign_generated_fields
    response = client.post("/api/v1/data/reading", json=payload())
    assert response.status_code == 201
    assert response.json()["raw_data"] == payload()["data"]
    db.commit.assert_called_once()
    db.refresh.assert_called_once()


def test_batch_uses_bulk_insert(client, db):
    response = client.post("/api/v1/data/batch", json=[payload(), payload()])
    assert response.status_code == 201
    assert response.json()["inserted"] == 2
    assert len(response.json()["evaluations"]) == 2
    assert len(db.execute.call_args.args[1]) == 2
    assert db.execute.call_args.args[1][0]["raw_data"] == payload()["data"]
    db.commit.assert_called_once()


def test_empty_batch_rejected(client):
    assert client.post("/api/v1/data/batch", json=[]).status_code == 422


def test_invalid_batch_never_inserts(client, db):
    response = client.post("/api/v1/data/batch", json=[payload(), {"transformer_id": ""}])
    assert response.status_code == 422
    db.execute.assert_not_called()
    db.commit.assert_not_called()


def test_upload_preview(client, db):
    response = client.post(
        "/api/v1/data/upload", files={"file": ("input.csv", b"external,value\na,1\nb,2\n", "text/csv")}
    )
    assert response.status_code == 200
    assert response.json()["columns"] == ["external", "value"]
    assert response.json()["preview_rows"] == 2
    assert response.json()["stored"] is False
    db.execute.assert_not_called()


@pytest.mark.parametrize("name,content", [
    ("input.txt", b"a,b\n1,2"), ("input.csv", b""),
    ("input.csv", b"a,b\n1,2,3"), ("input.csv", b"a,b\n"),
    ("input.csv", b"a,a\n1,2"), ("input.csv", b"a,b\n\xff,1"),
    ("input.csv", b"a,b\n\x00,1"), ("input.csv", b'a,b\n"unterminated,1'),
])
def test_invalid_csv(client, name, content):
    assert client.post(
        "/api/v1/data/upload", files={"file": (name, content, "text/csv")}
    ).status_code == 400


def test_missing_file(client):
    assert client.post("/api/v1/data/upload").status_code == 422


def test_large_upload_rejected(client):
    response = client.post(
        "/api/v1/data/upload",
        files={"file": ("input.csv", b"x" * (MAX_UPLOAD_BYTES + 1), "text/csv")},
    )
    assert response.status_code == 413


def test_empty_mapping():
    from app.config.settings import PROJECT_ROOT
    assert load_column_mapping(PROJECT_ROOT / "config" / "column_mapping.yaml") == {}


def test_pipeline_placeholder_is_explicit(db):
    with pytest.raises(NotImplementedError):
        ProcessingService(db).normalize_data({})
