"""Opt-in PostgreSQL storage checks; never substitute SQLite."""
import os
import secrets
import uuid

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session
from sqlalchemy.schema import CreateSchema, DropSchema

from app.models import Base, SensorReading
from app.schemas.sensor import SensorReadingCreate
from app.services.processing_service import ProcessingService

pytestmark = pytest.mark.integration


INTEGRATION_TRANSFORMER = "integration"
# Generated per test run and scoped to the integration fixture transformer only.
INTEGRATION_API_KEY = secrets.token_urlsafe(32)
_LOCAL_HOSTS = {None, "", "localhost", "127.0.0.1", "::1"}


def _database_identity(url):
    host = None if url.host in _LOCAL_HOSTS else url.host.lower()
    return host, url.port or 5432, url.database


def require_isolated_test_database(url: str) -> str:
    """Fail before connecting unless the URL names a dedicated *_test database.

    Messages never include the URL, which can contain credentials.
    """
    try:
        parsed = make_url(url)
    except Exception:
        pytest.fail("TEST_DATABASE_URL is not a valid database URL")
    if parsed.drivername != "postgresql+psycopg":
        pytest.fail("TEST_DATABASE_URL must use PostgreSQL with psycopg 3")
    if not parsed.database or not parsed.database.endswith("_test"):
        pytest.fail("TEST_DATABASE_URL must name a dedicated database ending in _test")
    from app.config.settings import Settings
    configured = Settings().database_url
    if configured is not None and _database_identity(
        make_url(configured.get_secret_value())
    ) == _database_identity(parsed):
        pytest.fail("TEST_DATABASE_URL must not point at the DATABASE_URL application database")
    return url


def integration_client(session):
    """API client with test-only settings; never the developer's .env credentials or database."""
    from fastapi.testclient import TestClient
    from app.config.settings import Settings
    from app.database.db import get_db
    from app.main import create_app

    application = create_app(Settings(
        database_url=None,
        agent_credentials=f"{INTEGRATION_TRANSFORMER},@ingest,@alerts:{INTEGRATION_API_KEY}",
    ))
    application.dependency_overrides[get_db] = lambda: session
    client = TestClient(application)
    client.headers["X-Agent-API-Key"] = INTEGRATION_API_KEY
    return client


@pytest.fixture
def postgres_session():
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.skip("Set TEST_DATABASE_URL to an isolated PostgreSQL test database")
    engine = create_engine(require_isolated_test_database(url), hide_parameters=True)
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
    from app.models import Alert
    from app.models.base import utc_now
    from app.alerts.types import MaintenanceType
    with integration_client(postgres_session) as client:
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


def test_postgres_rejected_future_reading_never_reaches_latest_history_or_evidence(postgres_session):
    from datetime import datetime, timedelta, timezone
    from app.database.repository import Repository
    now = datetime.now(timezone.utc).replace(microsecond=0)
    with integration_client(postgres_session) as client:
        first = client.post("/api/v1/data/reading", json={
            "transformer_id": "integration", "timestamp": (now - timedelta(hours=2)).isoformat(),
            "data": {"oil_level": 70}})
        rejected = [client.post("/api/v1/data/reading", json={
            "transformer_id": "integration", "timestamp": stamp, "data": {"oil_level": 5}})
            for stamp in ("9999-01-01T00:00:00Z", (now + timedelta(hours=1)).isoformat())]
        rejected_batch = client.post("/api/v1/data/batch", json=[
            {"transformer_id": "integration", "timestamp": (now - timedelta(minutes=30)).isoformat(),
             "data": {"oil_level": 69}},
            {"transformer_id": "integration", "timestamp": "9999-01-01T00:00:00Z", "data": {"oil_level": 5}},
        ])
        second = client.post("/api/v1/data/reading", json={
            "transformer_id": "integration", "timestamp": (now - timedelta(hours=1)).isoformat(),
            "data": {"oil_level": 68}})
        assert first.status_code == second.status_code == 201
        assert [r.status_code for r in rejected] == [422, 422]
        assert rejected_batch.status_code == 422
        postgres_session.expunge_all()
        # Nothing from the rejected requests was stored, not even the batch's valid row.
        assert postgres_session.scalar(select(func.count()).select_from(SensorReading)) == 2
        latest = client.get("/api/v1/transformer/latest").json()["latest_reading"]
        assert latest["id"] == second.json()["id"]
        previous = Repository(postgres_session).previous_reading_with_feature(
            "integration", now, "oil_level", second.json()["id"] + 1)
        assert previous.id == second.json()["id"]
        chat = client.post("/api/v1/agent/chat", json={"transformer_id": "integration", "question": "Status?"})
        assert chat.status_code == 200
        assert sorted(chat.json()["evidence_source_ids"]) == sorted(
            f"reading:{r.json()['id']}" for r in (first, second))


def test_postgres_maintenance_risk_uses_only_stored_transformer_records(postgres_session):
    from datetime import datetime, timedelta, timezone
    from app.models import MaintenanceRecord
    now = datetime.now(timezone.utc).replace(microsecond=0)

    def record(days, **details):
        return {"transformer_id": "integration", "record_type": "fault",
                "occurred_at": (now - timedelta(days=days)).isoformat(), "details": details}

    with integration_client(postgres_session) as client:
        assert client.get("/api/v1/agent/risk", params={"transformer_id": "integration"}).json()[
            "risk_category"] == "INSUFFICIENT_EVIDENCE"
        for body in (record(30, component="bushing", severity="major", repair_outcome="unresolved"),
                     record(200, component="bushing", severity="minor", repair_outcome="completed")):
            assert client.post("/api/v1/maintenance/records", json=body).status_code == 201
        # Another transformer's severe history in the same table must not leak in.
        postgres_session.add(MaintenanceRecord(
            transformer_id="other", record_type="fault", occurred_at=now - timedelta(days=1),
            details={"component": "core", "severity": "critical", "repair_outcome": "unresolved"}))
        postgres_session.commit()
        first = client.get("/api/v1/agent/risk", params={"transformer_id": "integration"})
        second = client.get("/api/v1/agent/risk", params={"transformer_id": "integration"})
        assert first.status_code == 200
        body = first.json()
        assert len(body["evidence_source_ids"]) == 2
        assert body["risk_category"] == "HIGH_MAINTENANCE_RISK"
        assert body["factor_scores"]["recurrence"] is not None
        assert body["policy_approved"] is False
        # Same records + same policy: identical factor scores and index.
        assert second.json()["factor_scores"] == body["factor_scores"]
        assert client.get("/api/v1/agent/risk", params={"transformer_id": "other"}).status_code == 403
