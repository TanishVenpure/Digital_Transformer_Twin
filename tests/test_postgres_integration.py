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
