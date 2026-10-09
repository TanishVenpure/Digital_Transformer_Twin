from datetime import timedelta
import pytest
from sqlalchemy import select, func
from app.models import SensorReading, Alert
from app.services.processing_service import ProcessingService
from app.schemas.sensor import SensorReadingCreate
from tests.test_postgres_integration import postgres_session
from tests.test_voltage_postgres_integration import client_for
from tests.test_oil_level import RULE, CONFIG, NOW

pytestmark = pytest.mark.integration


def reading(level, hours=0, extra=None):
    return SensorReadingCreate(transformer_id="integration", timestamp=NOW+timedelta(hours=hours),
                               data={"oil_level": level, **(extra or {})})


def service(session):
    result = ProcessingService(session)
    result.thresholds[RULE] = CONFIG
    return result


def test_persist_and_retrieve_all_rules_common_history(postgres_session):
    processor = service(postgres_session)
    extra = {"rated_current":100, "measured_current_r":102, "measured_current_y":100,
             "measured_current_b":100, "reference_voltage":100, "measured_voltage_r":102,
             "measured_voltage_y":100, "measured_voltage_b":100}
    processor.receive_reading(reading(80))
    stored = processor.receive_reading(reading(35, 1, extra))
    result = stored.evaluations[RULE]
    assert result.details["oil_level_rate"] == pytest.approx(-45)
    assert result.details["estimated_t_min"] == pytest.approx(1/3)
    postgres_session.expunge_all()
    with client_for(postgres_session) as client:
        history = client.get("/api/v1/alerts/history").json()["items"]
        assert {a["formula_name"] for a in history} == {
            RULE, "3_phase_current_instrument_error", "3_phase_voltage_instrument_error"}
        oil = client.get("/api/v1/alerts?maintenance_type=TRANSFORMER_MAINTENANCE").json()
        assert len(oil) == 2
        for alert in oil:
            assert alert["formula_inputs"]["oil_level"] == 35
            assert alert["formula_inputs"]["previous_reading"]["oil_level"] == 80
            assert alert["details"]["minimum_oil_level"] == 20
            assert alert["details"]["maintenance_required"]
            assert client.get(f"/api/v1/alerts/{alert['id']}").json() == alert
        system = client.get("/api/v1/alerts?maintenance_type=SYSTEM_MAINTENANCE").json()
        assert len(system) == 2
        assert len(client.get("/api/v1/transformer/history").json()["alerts"]) == 4


def test_batch_history_uses_previous_row_without_future_lookahead(postgres_session):
    processor = service(postgres_session)
    assert processor.receive_batch([reading(80), reading(60, 1), reading(60, 1)]) == 3
    first, second, third = [r[RULE] for r in processor.batch_evaluations]
    assert first.details["oil_level_rate"] is None
    assert second.details["oil_level_rate"] == -20
    assert second.details["estimated_t_min"] == 2
    assert third.details["oil_level_rate"] is None
    assert postgres_session.scalar(select(func.count()).select_from(Alert)) == 1
    assert postgres_session.scalar(select(func.count()).select_from(SensorReading)) == 3


def test_future_timestamp_is_not_used_as_history(postgres_session):
    processor = service(postgres_session)
    processor.receive_reading(reading(80, 10))
    stored = processor.receive_reading(reading(60, 1))
    assert stored.evaluations[RULE].details["previous_reading"] is None
    assert stored.evaluations[RULE].details["outputs"] == ["NORMAL"]


@pytest.mark.parametrize("level,count", [(80,0),(40,1),(20,2),(10,2)])
def test_first_reading_static_alert_persistence(postgres_session,level,count):
    service(postgres_session).receive_reading(reading(level))
    assert postgres_session.scalar(select(func.count()).select_from(Alert)) == count


def test_unconfigured_still_stores_reading(postgres_session):
    stored = ProcessingService(postgres_session).receive_reading(reading(10))
    assert stored.evaluations[RULE].status.value == "SKIPPED"
    assert postgres_session.scalar(select(func.count()).select_from(Alert)) == 0
    assert postgres_session.scalar(select(func.count()).select_from(SensorReading)) == 1


def test_api_ingestion_evaluates_history(monkeypatch, postgres_session):
    import app.services.processing_service as module
    original = module.load_thresholds
    def configured():
        values = original()
        values[RULE] = CONFIG
        return values
    monkeypatch.setattr(module, "load_thresholds", configured)
    with client_for(postgres_session) as client:
        for level, hours in ((80,0),(60,1)):
            response = client.post("/api/v1/data/reading", json=reading(level,hours).model_dump(mode="json"))
            assert response.status_code == 201
        result = response.json()["evaluations"][RULE]
        assert result["details"]["estimated_t_min"] == 2
        assert result["details"]["outputs"] == ["PREDICTED_MINIMUM_LEVEL_BREACH", "MAINTENANCE_REQUIRED"]
        assert len(client.get("/api/v1/alerts?maintenance_type=TRANSFORMER_MAINTENANCE").json()) == 1


def test_alert_storage_failure_rolls_back_reading(monkeypatch, postgres_session):
    processor = service(postgres_session)
    def fail(_):
        raise RuntimeError("storage unavailable")
    monkeypatch.setattr(processor.repository,"store_alerts",fail)
    with pytest.raises(RuntimeError,match="storage unavailable"):
        processor.receive_reading(reading(10))
    assert postgres_session.scalar(select(func.count()).select_from(SensorReading)) == 0
    assert postgres_session.scalar(select(func.count()).select_from(Alert)) == 0
