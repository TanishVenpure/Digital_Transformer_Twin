import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.database.db import get_db
from app.database.repository import Repository
from app.main import create_app
from app.models import Alert, SensorReading
from tests.test_postgres_integration import postgres_session
from tests.test_voltage_instrument_error import CURRENT, RULE, features

pytestmark = pytest.mark.integration


def client_for(session):
    application = create_app()
    application.dependency_overrides[get_db] = lambda: session
    return TestClient(application)


def payload(data):
    return {"transformer_id": "integration", "timestamp": "2026-10-09T10:00:00Z", "data": data}


def test_committed_voltage_alert_and_common_history(postgres_session):
    current = {
        "rated_current": 100, "measured_current_r": 100.4,
        "measured_current_y": 99.7, "measured_current_b": 101.2,
    }
    with client_for(postgres_session) as client:
        current_response = client.post("/api/v1/data/reading", json=payload(current))
        voltage_response = client.post("/api/v1/data/reading", json=payload(features(b=101.2)))
        assert current_response.status_code == voltage_response.status_code == 201
        assert voltage_response.json()["evaluations"][RULE]["status"] == "FLAGGED"
        postgres_session.expunge_all()
        assert postgres_session.scalar(select(func.count()).select_from(Alert)) == 2
        response = client.get("/api/v1/alerts?maintenance_type=SYSTEM_MAINTENANCE")
        assert response.status_code == 200
        alerts = response.json()
        assert {alert["formula_name"] for alert in alerts} == {CURRENT, RULE}
        voltage = next(alert for alert in alerts if alert["formula_name"] == RULE)
        assert voltage["formula_value"] == pytest.approx(1.2)
        assert voltage["formula_inputs"] == features(b=101.2)
        assert voltage["details"]["phase_errors"] == pytest.approx({"R": 0.4, "Y": 0.3, "B": 1.2})
        assert voltage["details"]["max_deviation_phase"] == "B"
        assert voltage["maintenance_type"] == "SYSTEM_MAINTENANCE"
        assert Repository(postgres_session).get_alert(voltage["id"]).formula_name == RULE
        assert client.get(f"/api/v1/alerts/{voltage['id']}").json() == voltage
        history = client.get("/api/v1/alerts/history?maintenance_type=SYSTEM_MAINTENANCE").json()
        assert {alert["formula_name"] for alert in history["items"]} == {CURRENT, RULE}
        context = client.get("/api/v1/transformer/history").json()
        assert {alert["formula_name"] for alert in context["alerts"]} == {CURRENT, RULE}


@pytest.mark.parametrize("data,status", [
    (features(), "PASS"),
    (features(r=101, y=100, b=100), "PASS"),
    (features(reference=0), "INVALID"),
    ({"reference_voltage": 100}, "SKIPPED"),
])
def test_pass_invalid_and_missing_data_do_not_store_alerts(postgres_session, data, status):
    with client_for(postgres_session) as client:
        response = client.post("/api/v1/data/reading", json=payload(data))
        assert response.status_code == 201
        assert response.json()["evaluations"][RULE]["status"] == status
        assert client.get("/api/v1/alerts").json() == []
    assert postgres_session.scalar(select(func.count()).select_from(SensorReading)) == 1


def test_same_row_generates_both_alerts_in_one_history(postgres_session):
    data = {**features(b=101.2), "rated_current": 100,
            "measured_current_r": 100.4, "measured_current_y": 99.7, "measured_current_b": 101.2}
    with client_for(postgres_session) as client:
        response = client.post("/api/v1/data/reading", json=payload(data))
        assert response.status_code == 201
        assert response.json()["evaluations"][CURRENT]["status"] == "FLAGGED"
        assert response.json()["evaluations"][RULE]["status"] == "FLAGGED"
        history = client.get("/api/v1/alerts/history").json()
        assert len(history["items"]) == 2
        assert all(item["maintenance_type"] == "SYSTEM_MAINTENANCE" for item in history["items"])
    assert postgres_session.scalar(select(func.count()).select_from(SensorReading)) == 1
