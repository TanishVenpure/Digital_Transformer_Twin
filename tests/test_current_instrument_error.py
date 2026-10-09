from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Session

from app.alerts.configuration import load_thresholds
from app.alerts.types import MaintenanceType
from app.database.repository import Repository
from app.formulas.base import EvaluationMode, EvaluationStatus
from app.formulas.current_instrument_error import CurrentInstrumentError
from app.formulas.registry import formula_registry
from app.schemas.sensor import SensorReadingCreate
from app.services.processing_service import ProcessingService

RULE = "3_phase_current_instrument_error"


def features(r=100.4, y=99.7, b=100.8, rated=100):
    return {
        "rated_current": rated, "measured_current_r": r,
        "measured_current_y": y, "measured_current_b": b,
    }


def evaluate(data):
    service = ProcessingService(MagicMock(spec=Session))
    results = service.run_formulas(data)
    alerts = service.evaluate_thresholds(
        results, data=data, transformer_id="T01",
        timestamp=datetime(2026, 10, 9, tzinfo=timezone.utc),
    )
    return results[RULE], alerts


def test_registration_metadata_and_phase_diagnostics():
    formula = formula_registry.get(RULE)
    assert isinstance(formula, CurrentInstrumentError)
    assert formula.evaluation_mode == EvaluationMode.PER_READING
    assert formula.required_features == (
        "rated_current", "measured_current_r", "measured_current_y", "measured_current_b"
    )
    result, alerts = evaluate(features())
    assert result.status == EvaluationStatus.PASS
    assert result.value == pytest.approx(0.8)
    assert result.details["phase_errors"] == pytest.approx({"R": 0.4, "Y": 0.3, "B": 0.8})
    assert result.details["max_deviation_phase"] == "B"
    assert alerts == []


@pytest.mark.parametrize("error,status,count", [
    (0.99, EvaluationStatus.PASS, 0), (1.0, EvaluationStatus.PASS, 0),
    (1.01, EvaluationStatus.FLAGGED, 1), (1.0000001, EvaluationStatus.FLAGGED, 1),
])
def test_strict_threshold_without_rounding(error, status, count):
    result, alerts = evaluate(features(r=100 + error, y=100, b=100))
    assert result.value == pytest.approx(error)
    assert result.status == status
    assert len(alerts) == count


def test_flagged_system_maintenance_provenance():
    data = features(b=101.2)
    result, alerts = evaluate(data)
    assert result.status == EvaluationStatus.FLAGGED
    assert result.value == pytest.approx(1.2)
    assert len(alerts) == 1
    alert = alerts[0]
    assert alert.maintenance_type == MaintenanceType.SYSTEM_MAINTENANCE
    assert alert.formula_name == RULE
    assert alert.parameter == "current_measurement"
    assert alert.threshold == 1.0 and alert.comparison_operator == ">"
    assert alert.formula_inputs == data
    assert alert.details["max_deviation_phase"] == "B"
    assert alert.details["phase_errors"] == pytest.approx({"R": 0.4, "Y": 0.3, "B": 1.2})
    assert alert.details["threshold_unit"] == "percent"
    assert alert.details["test_name"] == "3-Phase Current Instrument Error"
    assert alert.acknowledged is False and alert.resolved is False


@pytest.mark.parametrize("r,y,b,phase", [
    (103, 100, 100, "R"), (100, 104, 100, "Y"), (100, 100, 105, "B"),
    (102, 102, 102, "R"),
])
def test_maximum_phase_and_deterministic_ties(r, y, b, phase):
    result, _ = evaluate(features(r, y, b))
    assert result.details["max_deviation_phase"] == phase


def test_missing_feature_is_skipped_without_alert():
    data = features()
    del data["measured_current_y"]
    result, alerts = evaluate(data)
    assert result.status == EvaluationStatus.SKIPPED
    assert result.missing_features == ["measured_current_y"]
    assert result.value is None
    assert alerts == []


@pytest.mark.parametrize("rated", [0, 0.0, -0.0])
def test_zero_reference_is_invalid_without_alert(rated):
    result, alerts = evaluate(features(rated=rated))
    assert result.status == EvaluationStatus.INVALID
    assert result.reason == "rated_current must be nonzero"
    assert result.value is None and alerts == []


@pytest.mark.parametrize("bad", [None, True, "100", [], float("nan"), float("inf")])
def test_nonnumeric_and_nonfinite_inputs_are_invalid(bad):
    result, alerts = evaluate(features(y=bad))
    assert result.status == EvaluationStatus.INVALID
    assert alerts == []


def test_negative_difference_uses_absolute_error():
    result, _ = evaluate(features(r=98, y=100, b=100))
    assert result.value == pytest.approx(2)
    assert result.details["phase_errors"]["R"] == pytest.approx(2)


def test_negative_nonzero_reference_uses_absolute_denominator():
    result, _ = evaluate(features(r=-98, y=-100, b=-100, rated=-100))
    assert result.value == pytest.approx(2)


def test_daily_mode_does_not_execute_per_reading_rule(db):
    assert ProcessingService(db).run_formulas(features(), EvaluationMode.DAILY) == {}


def test_threshold_and_category_come_from_configuration():
    data = features(b=101.2)
    service = ProcessingService(MagicMock(spec=Session))
    rule = service.thresholds[RULE]
    service.thresholds[RULE] = rule.model_copy(update={"threshold": 2.0})
    results = service.run_formulas(data)
    assert service.evaluate_thresholds(
        results, data=data, transformer_id="T01", timestamp=datetime.now(timezone.utc)
    ) == []
    assert results[RULE].status == EvaluationStatus.PASS
    assert load_thresholds()[RULE].maintenance_type == MaintenanceType.SYSTEM_MAINTENANCE


def test_single_reading_stores_alert_before_one_commit(db):
    reading = SensorReadingCreate(
        transformer_id="T01", timestamp="2026-10-09T10:00:00Z", data=features(b=101.2)
    )
    stored = ProcessingService(db).receive_reading(reading)
    db.add_all.assert_called_once()
    assert db.add_all.call_args.args[0][0].maintenance_type == MaintenanceType.SYSTEM_MAINTENANCE
    assert stored.raw_data == reading.data
    assert stored.evaluations[RULE].status == EvaluationStatus.FLAGGED
    assert db.method_calls.index(next(call for call in db.method_calls if call[0] == "add_all")) < db.method_calls.index(next(call for call in db.method_calls if call[0] == "commit"))
    db.commit.assert_called_once()


@pytest.mark.parametrize("data,status", [
    ({"rated_current": 100}, EvaluationStatus.SKIPPED),
    (features(rated=0), EvaluationStatus.INVALID),
    (features(), EvaluationStatus.PASS),
])
def test_no_alert_for_pass_or_unavailable_data(db, data, status):
    stored = ProcessingService(db).receive_reading(SensorReadingCreate(
        transformer_id="T01", timestamp="2026-10-09T10:00:00Z", data=data
    ))
    db.add_all.assert_not_called()
    assert stored.evaluations[RULE].status == status
    db.commit.assert_called_once()


def test_batch_runs_each_reading_and_preserves_bulk_ingestion(db):
    readings = [
        SensorReadingCreate(transformer_id="T01", timestamp="2026-10-09T10:00:00Z", data=data)
        for data in (features(), features(b=101.2), features(rated=0), {})
    ]
    service = ProcessingService(db)
    assert service.receive_batch(readings) == 4
    assert [item[RULE].status for item in service.batch_evaluations] == [
        EvaluationStatus.PASS, EvaluationStatus.FLAGGED, EvaluationStatus.INVALID, EvaluationStatus.SKIPPED
    ]
    db.execute.assert_called_once()
    db.add_all.assert_called_once()
    db.commit.assert_called_once()


def test_persistence_failure_rolls_back_reading_and_alert(db):
    db.add_all.side_effect = RuntimeError("persistence failure")
    with pytest.raises(RuntimeError, match="persistence failure"):
        ProcessingService(db).receive_reading(SensorReadingCreate(
            transformer_id="T01", timestamp="2026-10-09T10:00:00Z", data=features(b=101.2)
        ))
    db.commit.assert_not_called()
    db.rollback.assert_called_once()


def test_api_exposes_evaluation_and_system_alert(client, db):
    from app.models.base import utc_now
    def generated_fields():
        obj = db.add.call_args.args[0]
        obj.id = 1
        obj.created_at = utc_now()
    db.flush.side_effect = generated_fields
    response = client.post("/api/v1/data/reading", json={
        "transformer_id": "T01", "timestamp": "2026-10-09T10:00:00Z", "data": features(b=101.2)
    })
    assert response.status_code == 201
    assert response.json()["evaluations"][RULE]["status"] == "FLAGGED"
    alert = db.add_all.call_args.args[0][0]
    alert.id, alert.created_at = 1, utc_now()
    with patch.object(Repository, "alerts", return_value=[alert]) as query:
        response = client.get("/api/v1/alerts?maintenance_type=SYSTEM_MAINTENANCE")
    assert response.status_code == 200
    assert response.json()[0]["maintenance_type"] == "SYSTEM_MAINTENANCE"
    assert response.json()[0]["details"]["max_deviation_phase"] == "B"
    assert query.call_args.kwargs["maintenance_type"] == MaintenanceType.SYSTEM_MAINTENANCE


def test_repository_filters_both_maintenance_categories(db):
    for category in MaintenanceType:
        Repository(db).alerts(maintenance_type=category)
        query = db.scalars.call_args.args[0].compile(dialect=postgresql.dialect())
        assert "alerts.maintenance_type =" in str(query)
        assert category in query.params.values()


def test_unknown_maintenance_category_rejected(client):
    assert client.get("/api/v1/alerts?maintenance_type=OTHER").status_code == 422
