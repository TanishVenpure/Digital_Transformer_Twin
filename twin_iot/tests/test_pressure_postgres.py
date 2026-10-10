"""Real PostgreSQL + FastAPI verification in a disposable, isolated schema.

Opt in with TWIN_TEST_DATABASE_URL. Never creates/deletes demo readings in public.
"""
import json
import os
from contextlib import contextmanager
from datetime import timedelta
from pathlib import Path
from uuid import uuid4

import psycopg2
from psycopg2 import sql
import pytest
from fastapi.testclient import TestClient

from app.database import db
from app.services.processing_service import ProcessingService
from tests.conftest import NOW
from tests.test_pressure import NAME, test_rule


@pytest.fixture
def api_db(monkeypatch):
    url = os.environ.get("TWIN_TEST_DATABASE_URL")
    if not url:
        pytest.skip("Set TWIN_TEST_DATABASE_URL to run isolated PostgreSQL/API tests")
    schema = "rule8_test_" + uuid4().hex
    admin = psycopg2.connect(url)
    admin.autocommit = True
    with admin.cursor() as cursor:
        cursor.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
    @contextmanager
    def isolated_connect(*args, **kwargs):
        connection = psycopg2.connect(url, options=f"-c timezone=UTC -c search_path={schema}")
        try:
            with connection.cursor() as cursor:
                cursor.execute("SELECT current_schema()")
                assert cursor.fetchone()[0] == schema
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()
    monkeypatch.setattr(db, "connect", isolated_connect)
    from app.api import main
    thresholds = main.live_service.thresholds.copy()
    thresholds[NAME] = test_rule()
    service = ProcessingService(thresholds, history_loader=db.load_previous_valid_reading, timestamp_loader=db.load_last_timestamp)
    monkeypatch.setattr(main, "live_service", service)
    try:
        with TestClient(main.app) as client:
            yield client, main, isolated_connect
    finally:
        # Exact schema created by this fixture; never public or a computed user directory.
        assert schema.startswith("rule8_test_") and len(schema) == 43
        with admin.cursor() as cursor:
            cursor.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))
        admin.close()


def post(client, pressure, hours=0, **optional):
    response = client.post("/api/readings", json={"transformer_id": "T1", "timestamp": (NOW + timedelta(hours=hours)).isoformat(),
                                               "data": {"oil_pressure": pressure, **optional}})
    assert response.status_code == 200, response.text
    return response.json()


def test_api_pressure_persistence_and_explainable_common_history(api_db):
    client, main, connect = api_db
    receipt = post(client, 10, top_oil_temperature=80, ambient_temperature=30)
    assert receipt["accepted"] and receipt["reading_id"]
    evaluation = next(e for e in receipt["iots"] if e["slot"] == 8)
    assert evaluation["status"] == "WARNING" and evaluation["evaluation_status"] == "FLAGGED"
    detail = client.get(f"/api/readings/{receipt['reading_id']}").json()
    assert detail["clean_data"]["oil_pressure"] == 10
    persisted = next(e for e in detail["evaluations"] if e["iot_slot"] == 8)
    assert persisted["details"]["pressure_rate"] is None
    alerts = [a for a in client.get("/api/alerts/T1").json() if a["iot_slot"] == 8]
    assert alerts[0]["formula_name"] == NAME and alerts[0]["parameter"] == "oil_pressure"
    assert alerts[0]["maintenance_type"] == "TRANSFORMER_MAINTENANCE"
    assert alerts[0]["trigger_condition"] == "HIGH_PRESSURE_WARNING"
    assert alerts[0]["formula_inputs"]["oil_pressure"] == 10
    sheet = [c for c in client.get("/api/maintenance/T1").json()["rows"] if c["iot"] == 8]
    assert sheet[0]["iot"] == 8 and sheet[0]["details"]["top_oil_temperature"] == 80
    assert sheet[0]["trigger_conditions"] == ["HIGH_PRESSURE_WARNING"]
    with connect() as connection, connection.cursor() as cursor:
        cursor.execute("SELECT count(*) FROM alerts WHERE iot_slot=8")
        assert cursor.fetchone()[0] == 1
        cursor.execute("SELECT tablename FROM pg_tables WHERE schemaname=current_schema()")
        assert {row[0] for row in cursor.fetchall()} == {"readings", "iot_evaluations", "alerts", "maintenance_log", "maintenance_events"}
    for format in ("csv", "jsonl", "md"):
        assert client.get(f"/api/maintenance/T1?format={format}").status_code == 200


def test_restart_restores_previous_valid_pressure_and_timestamp_guard(api_db):
    client, main, connect = api_db
    post(client, 10)
    post(client, "invalid", hours=1)
    main.live_service.reset()
    second = post(client, 16, hours=2)
    e = next(e for e in second["iots"] if e["slot"] == 8)
    assert e["details"]["previous_pressure"] == 10
    assert e["details"]["pressure_rate"] == pytest.approx(3)
    assert e["details"]["trigger_conditions"] == ["HIGH_PRESSURE_WARNING", "RAPID_PRESSURE_RISE"]
    main.live_service.reset()
    duplicate = post(client, 16, hours=2)
    assert not duplicate["accepted"] and duplicate["reading_id"] is None
    assert duplicate["issues"][0]["code"] == "DUPLICATE_TIMESTAMP"


def test_simultaneous_triggers_persist_individually_and_are_retrievable(api_db):
    client, main, connect = api_db
    first = post(client, 10)
    second = post(client, 20, hours=2)
    alerts = client.get("/api/alerts/T1").json()
    actual = [a for a in alerts if a["reading_id"] == second["reading_id"]]
    assert len(actual) == 2
    assert {a["trigger_condition"] for a in actual} == {"PRESSURE_CRITICAL", "RAPID_PRESSURE_RISE"}
    assert {a["value"] for a in actual} == {20, 5}
    assert all(a["details"]["previous_pressure"] == 10 for a in actual)
    # Capture real isolated records for the implementation report; no production fixture values.
    report_path = os.environ.get("TWIN_RULE8_PROOF_PATH")
    if report_path:
        database_rows = {}
        with connect() as connection, connection.cursor() as cursor:
            for table in ("iot_evaluations", "alerts", "maintenance_log"):
                cursor.execute(f"SELECT row_to_json(t) FROM {table} t WHERE reading_id=%s", (second["reading_id"],))
                database_rows[table] = [row[0] for row in cursor.fetchall()]
        Path(report_path).write_text(json.dumps({"receipt": second, "reading": client.get(f"/api/readings/{second['reading_id']}").json(),
                                               "alerts": actual, "maintenance": client.get("/api/maintenance/T1").json(),
                                               "database_rows": database_rows}, indent=2))


def test_old_timeline_rows_are_padded_without_backfill(api_db):
    client, main, connect = api_db
    from app.twin3d.selector import select_model
    processed = main.live_service.process("T1", NOW, {"oil_level": 80})
    processed.evaluations.pop(NAME)  # emulate a row stored before Rules 8/9 existed
    processed.evaluations.pop("hv_voltage_deviation")
    with connect() as connection:
        rid = db.store(connection, processed, select_model(processed.evaluations, main.models))
    timeline = client.get("/api/timeline/T1").json()
    assert len(timeline["iots"]) == 9 and len(timeline["frames"][0]["v"]) == 9
    frame = timeline["frames"][0]
    assert frame["v"][7] is None and frame["s"][7] is None and "8" not in frame["d"]
    with connect() as connection, connection.cursor() as cursor:
        cursor.execute("SELECT count(*) FROM iot_evaluations WHERE reading_id=%s", (rid,))
        assert cursor.fetchone()[0] == 7
        cursor.execute("SELECT raw_data ? 'oil_pressure' FROM readings WHERE id=%s", (rid,))
        assert cursor.fetchone()[0] is False


def test_all_rules_share_history_without_new_tables(api_db):
    client, main, connect = api_db
    data = {"current_r": 102, "current_y": 100, "current_b": 100,
            "reference_current_r": 100, "reference_current_y": 100, "reference_current_b": 100,
            "voltage_r": 102, "voltage_y": 100, "voltage_b": 100,
            "reference_voltage_r": 100, "reference_voltage_y": 100, "reference_voltage_b": 100,
            "oil_level": 10, "top_oil_temperature": 110, "ambient_temperature": 30,
            "winding_temperature_r": 90, "winding_temperature_y": 90, "winding_temperature_b": 90, "oil_pressure": 20}
    response = client.post("/api/readings", json={"transformer_id": "T1", "timestamp": NOW.isoformat(), "data": data})
    assert response.status_code == 200
    alerts = client.get("/api/alerts/T1").json()
    types = {a["formula_name"]: a["maintenance_type"] for a in alerts}
    assert types["current_measurement_health"] == types["voltage_measurement_health"] == "SYSTEM_MAINTENANCE"
    assert types[NAME] == types["oil_level"] == "TRANSFORMER_MAINTENANCE"
    sheet = client.get("/api/maintenance/T1").json()["rows"]
    assert {1, 2, 5, 6, 7, 8} <= {c["iot"] for c in sheet}


def test_unconfigured_pressure_is_stored_without_alerts(api_db):
    client, main, connect = api_db
    from app.alerts.configuration import load_thresholds
    main.live_service = ProcessingService(load_thresholds(), history_loader=db.load_previous_valid_reading)
    post(client, 10)
    second = post(client, 1000, hours=0.5)
    e = next(e for e in second["iots"] if e["slot"] == 8)
    assert e["status"] == "NORMAL" and e["details"]["pressure_rate"] == pytest.approx(1980)
    assert e["unit"] == "unconfigured" and e["details"]["pressure_unit"] is None
    assert client.get("/api/alerts/T1").json() == []
    assert client.get("/api/maintenance/T1").json()["rows"] == []
    assert client.get(f"/api/readings/{second['reading_id']}").status_code == 200


def test_csv_replacement_does_not_use_deleted_pressure_context(api_db):
    client, main, connect = api_db
    from app.alerts.configuration import load_thresholds
    main.live_service = ProcessingService(load_thresholds(), history_loader=db.load_previous_valid_reading)
    post(client, 10)
    stamp = (NOW + timedelta(hours=2)).isoformat()
    response = client.post("/api/upload", files={"file": ("pressure.csv", f"timestamp,oil_pressure\n{stamp},16\n", "text/csv")},
                           data={"transformer_id": "T1", "replace": "true"})
    assert response.status_code == 200 and response.json()["stored"] == 1
    with connect() as connection, connection.cursor() as cursor:
        cursor.execute("SELECT details FROM iot_evaluations WHERE iot_slot=8")
        assert cursor.fetchone()[0]["pressure_rate"] is None


def test_csv_append_recovers_existing_pressure_history(api_db):
    client, main, connect = api_db
    from app.alerts.configuration import load_thresholds
    main.live_service = ProcessingService(load_thresholds(), history_loader=db.load_previous_valid_reading)
    post(client, 10)
    stamp = (NOW + timedelta(hours=2)).isoformat()
    response = client.post("/api/upload", files={"file": ("pressure.csv", f"timestamp,oil_pressure\n{stamp},16\n", "text/csv")},
                           data={"transformer_id": "T1", "replace": "false"})
    assert response.status_code == 200 and response.json()["stored"] == 1
    with connect() as connection, connection.cursor() as cursor:
        cursor.execute("SELECT details FROM iot_evaluations WHERE iot_slot=8 ORDER BY reading_id DESC LIMIT 1")
        assert cursor.fetchone()[0]["pressure_rate"] == pytest.approx(3)
