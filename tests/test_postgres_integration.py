"""Opt-in PostgreSQL storage checks; never substitute SQLite."""
import os
import uuid

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session
from sqlalchemy.schema import CreateSchema, DropSchema

from app.models import Base, SensorReading
from app.schemas.sensor import SensorReadingCreate
from app.services.processing_service import ProcessingService

pytestmark = pytest.mark.integration


@pytest.fixture
def postgres_session():
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.skip("Set TEST_DATABASE_URL to an isolated PostgreSQL test database")
    if not url.startswith("postgresql+psycopg://"):
        pytest.fail("TEST_DATABASE_URL must use PostgreSQL with psycopg 3")
    engine = create_engine(url, hide_parameters=True)
    schema = "test_" + uuid.uuid4().hex
    try:
        with engine.begin() as connection:
            connection.execute(CreateSchema(schema))
        scoped = engine.execution_options(schema_translate_map={None: schema})
        Base.metadata.create_all(scoped)
        with Session(scoped, expire_on_commit=False) as session:
            yield session
    finally:
        with engine.begin() as connection:
            connection.execute(DropSchema(schema, cascade=True, if_exists=True))
        engine.dispose()


def test_postgres_raw_json_and_bulk_storage(postgres_session):
    reading = SensorReadingCreate(
        transformer_id="integration", timestamp="2026-10-09T10:00:00Z",
        data={"external_field": 1.2, "nested": {"preserved": True}},
    )
    service = ProcessingService(postgres_session)
    stored = service.receive_reading(reading)
    assert stored.raw_data == reading.data
    assert stored.timestamp.tzinfo is not None
    assert service.receive_batch([reading, reading]) == 2
    assert postgres_session.scalar(select(func.count()).select_from(SensorReading)) == 3


def current_data(b=101.2):
    return {"rated_current": 100, "measured_current_r": 100.4,
            "measured_current_y": 99.7, "measured_current_b": b}


def test_postgres_alert_commit_and_agent_filter(postgres_session):
    from fastapi.testclient import TestClient
    from app.database.db import get_db
    from app.main import create_app
    from app.models import Alert
    from app.models.base import utc_now
    from app.alerts.types import MaintenanceType
    application = create_app()
    application.dependency_overrides[get_db] = lambda: postgres_session
    with TestClient(application) as client:
        response = client.post("/api/v1/data/reading", json={
            "transformer_id": "integration", "timestamp": "2026-10-09T10:00:00Z",
            "data": current_data(),
        })
        assert response.status_code == 201
        assert response.json()["evaluations"]["3_phase_current_instrument_error"]["status"] == "FLAGGED"
        # Expunge so retrieval cannot depend on an in-memory ORM object.
        postgres_session.expunge_all()
        assert postgres_session.scalar(select(func.count()).select_from(Alert)) == 1
        # Fixture only: a second category, without adding a production formula.
        postgres_session.add(Alert(
            transformer_id="integration", timestamp=utc_now(), parameter="fixture",
            formula_name="fixture", formula_value=0, threshold=0, comparison_operator=">",
            severity="fixture", message="Fixture category", formula_inputs={}, details={},
            maintenance_type=MaintenanceType.TRANSFORMER_MAINTENANCE,
        ))
        postgres_session.commit()
        response = client.get("/api/v1/alerts?maintenance_type=SYSTEM_MAINTENANCE")
        assert response.status_code == 200
        assert len(response.json()) == 1
        alert = response.json()[0]
        assert alert["formula_name"] == "3_phase_current_instrument_error"
        assert alert["formula_value"] == pytest.approx(1.2)
        assert alert["formula_inputs"] == current_data()
        assert alert["details"]["max_deviation_phase"] == "B"
        assert client.get(f"/api/v1/alerts/{alert['id']}").json() == alert
        history = client.get("/api/v1/alerts/history?maintenance_type=SYSTEM_MAINTENANCE").json()
        assert history["items"] == [alert]
        context = client.get("/api/v1/transformer/history").json()
        assert any(item["id"] == alert["id"] for item in context["alerts"])
        other = client.get("/api/v1/alerts?maintenance_type=TRANSFORMER_MAINTENANCE").json()
        assert len(other) == 1 and other[0]["formula_name"] == "fixture"


@pytest.mark.parametrize("data,status", [
    (current_data(b=100.8), "PASS"),
    ({"rated_current": 100, "measured_current_r": 101, "measured_current_y": 100, "measured_current_b": 100}, "PASS"),
    ({"rated_current": 100}, "SKIPPED"),
    ({"rated_current": 0, "measured_current_r": 100, "measured_current_y": 100, "measured_current_b": 100}, "INVALID"),
])
def test_postgres_no_maintenance_for_pass_or_invalid(postgres_session, data, status):
    from app.models import Alert
    stored = ProcessingService(postgres_session).receive_reading(SensorReadingCreate(
        transformer_id="integration", timestamp="2026-10-09T10:00:00Z", data=data
    ))
    assert stored.evaluations["3_phase_current_instrument_error"].status.value == status
    assert postgres_session.scalar(select(func.count()).select_from(Alert)) == 0
    assert postgres_session.scalar(select(func.count()).select_from(SensorReading)) == 1
