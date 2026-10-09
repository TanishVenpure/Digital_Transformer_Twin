"""PostgreSQL-only migration round-trip checks in a rolled-back temporary schema."""
import importlib.util
from pathlib import Path
import uuid
from unittest.mock import patch

from alembic.migration import MigrationContext
from alembic.operations import Operations
import pytest
from sqlalchemy import inspect, text
from sqlalchemy.schema import CreateSchema

from tests.test_postgres_integration import postgres_session

pytestmark = pytest.mark.integration


def revision(filename):
    path = Path("alembic/versions") / filename
    spec = importlib.util.spec_from_file_location(path.stem, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_existing_alert_survives_migration_and_downgrade(postgres_session):
    baseline = revision("0001_initial_tables.py")
    maintenance = revision("0002_alert_maintenance_details.py")
    engine = postgres_session.get_bind().execution_options(schema_translate_map=None)
    schema = "migration_" + uuid.uuid4().hex
    with engine.connect() as connection:
        transaction = connection.begin()
        try:
            connection.execute(CreateSchema(schema))
            connection.execute(text(f'SET LOCAL search_path TO "{schema}"'))
            operations = Operations(MigrationContext.configure(connection))
            with patch.object(baseline, "op", operations):
                baseline.upgrade()
            connection.execute(text("""
                INSERT INTO alerts (
                    transformer_id, timestamp, parameter, formula_name, formula_value,
                    threshold, comparison_operator, severity, message, formula_inputs,
                    acknowledged, resolved, created_at
                ) VALUES (
                    'legacy', now(), 'fixture', 'fixture', 0, 0, '>', 'fixture',
                    'Legacy fixture', '{"source": 7}'::jsonb, false, false, now()
                )
            """))
            with patch.object(maintenance, "op", operations):
                maintenance.upgrade()
                row = connection.execute(text(
                    "SELECT maintenance_type, details, formula_inputs FROM alerts"
                )).one()
                assert row.maintenance_type is None
                assert row.details == {}
                assert row.formula_inputs == {"source": 7}
                constraints = inspect(connection).get_check_constraints("alerts", schema=schema)
                assert any(item["name"] == "maintenance_type" for item in constraints)
                maintenance.downgrade()
                columns = inspect(connection).get_columns("alerts", schema=schema)
                assert "maintenance_type" not in {item["name"] for item in columns}
                assert connection.scalar(text("SELECT count(*) FROM alerts")) == 1
                maintenance.upgrade()
                assert connection.scalar(text("SELECT count(*) FROM alerts")) == 1
        finally:
            transaction.rollback()
