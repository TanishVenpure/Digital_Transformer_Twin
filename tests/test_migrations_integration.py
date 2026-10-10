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


def test_maintenance_records_migration_round_trip(postgres_session):
    chain = [revision(name) for name in (
        "0001_initial_tables.py", "0002_alert_maintenance_details.py", "0003_maintenance_records.py",
    )]
    records = chain[-1]
    assert (records.revision, records.down_revision) == ("0003", "0002")
    engine = postgres_session.get_bind().execution_options(schema_translate_map=None)
    schema = "migration_" + uuid.uuid4().hex
    with engine.connect() as connection:
        transaction = connection.begin()
        try:
            connection.execute(CreateSchema(schema))
            connection.execute(text(f'SET LOCAL search_path TO "{schema}"'))
            operations = Operations(MigrationContext.configure(connection))
            for module in chain:
                with patch.object(module, "op", operations):
                    module.upgrade()
            inspector = inspect(connection)
            columns = {item["name"]: item for item in inspector.get_columns("maintenance_records", schema=schema)}
            assert set(columns) == {
                "id", "transformer_id", "record_type", "occurred_at", "details", "notes", "created_at",
            }
            assert {name for name, item in columns.items() if item["nullable"]} == {"notes"}
            assert columns["details"]["type"].__class__.__name__ == "JSONB"
            assert columns["occurred_at"]["type"].timezone is True
            assert {item["name"] for item in inspector.get_indexes("maintenance_records", schema=schema)} == {
                "ix_maintenance_records_transformer_id", "ix_maintenance_records_record_type",
                "ix_maintenance_records_occurred_at", "ix_maintenance_transformer_occurred",
            }
            connection.execute(text("""
                INSERT INTO alerts (
                    transformer_id, timestamp, parameter, formula_name, formula_value,
                    threshold, comparison_operator, severity, message, formula_inputs,
                    details, acknowledged, resolved, created_at
                ) VALUES (
                    'legacy', now(), 'fixture', 'fixture', 0, 0, '>', 'fixture',
                    'Legacy fixture', '{}'::jsonb, '{}'::jsonb, false, false, now()
                )
            """))
            connection.execute(text("""
                INSERT INTO maintenance_records (transformer_id, record_type, occurred_at, details, notes, created_at)
                VALUES ('integration', 'fixture', now(), '{"nested": {"preserved": true}}'::jsonb, NULL, now())
            """))
            assert connection.scalar(text("SELECT details FROM maintenance_records")) == {"nested": {"preserved": True}}
            with patch.object(records, "op", operations):
                records.downgrade()
                assert not inspect(connection).has_table("maintenance_records", schema=schema)
                # Downgrading 0003 must leave existing alert history untouched.
                assert connection.scalar(text("SELECT count(*) FROM alerts")) == 1
                records.upgrade()
                assert inspect(connection).has_table("maintenance_records", schema=schema)
        finally:
            transaction.rollback()
