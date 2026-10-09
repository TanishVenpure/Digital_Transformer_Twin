from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest
from sqlalchemy.orm import Session

from app.alerts.configuration import load_thresholds
from app.alerts.types import MaintenanceType
from app.formulas.base import EvaluationMode, EvaluationStatus
from app.formulas.registry import formula_registry
from app.schemas.sensor import SensorReadingCreate
from app.services.processing_service import ProcessingService

RULE = "3_phase_voltage_instrument_error"
CURRENT = "3_phase_current_instrument_error"


def features(r=100.4, y=99.7, b=100.8, reference=100):
    return {
        "reference_voltage": reference, "measured_voltage_r": r,
        "measured_voltage_y": y, "measured_voltage_b": b,
    }


def evaluate(data):
    service = ProcessingService(MagicMock(spec=Session))
    results = service.run_formulas(data)
    alerts = service.evaluate_thresholds(
        results, data=data, transformer_id="T01",
        timestamp=datetime(2026, 10, 9, tzinfo=timezone.utc),
    )
    return results, alerts


def test_existing_registry_and_configuration():
    formula = formula_registry.get(RULE)
    assert formula.display_name == "3-Phase Voltage Instrument Error"
    assert formula.parameter == "voltage_measurement"
    assert formula.evaluation_mode == EvaluationMode.PER_READING
    assert formula.required_features == (
        "reference_voltage", "measured_voltage_r", "measured_voltage_y", "measured_voltage_b"
    )
    rule = load_thresholds()[RULE]
    assert rule.threshold == 1.0 and rule.operator == ">" and rule.unit == "percent"
    assert rule.maintenance_type == MaintenanceType.SYSTEM_MAINTENANCE


def test_pass_with_phase_diagnostics():
    results, alerts = evaluate(features())
    result = results[RULE]
    assert result.status == EvaluationStatus.PASS
    assert result.value == pytest.approx(0.8)
    assert result.details["phase_errors"] == pytest.approx({"R": 0.4, "Y": 0.3, "B": 0.8})
    assert result.details["max_deviation_phase"] == "B"
    assert results[CURRENT].status == EvaluationStatus.SKIPPED
    assert alerts == []


@pytest.mark.parametrize("error,status,count", [
    (0.99, EvaluationStatus.PASS, 0), (1.0, EvaluationStatus.PASS, 0),
    (1.01, EvaluationStatus.FLAGGED, 1), (1.0000001, EvaluationStatus.FLAGGED, 1),
])
def test_strict_threshold_without_rounding(error, status, count):
    results, alerts = evaluate(features(r=100 + error, y=100, b=100))
    assert results[RULE].value == pytest.approx(error)
    assert results[RULE].status == status
    assert len(alerts) == count


def test_flagged_voltage_alert_inputs_and_details():
    data = features(b=101.2)
    results, alerts = evaluate(data)
    assert results[RULE].status == EvaluationStatus.FLAGGED
    assert len(alerts) == 1
    alert = alerts[0]
    assert alert.maintenance_type == MaintenanceType.SYSTEM_MAINTENANCE
    assert alert.formula_name == RULE and alert.parameter == "voltage_measurement"
    assert alert.formula_value == pytest.approx(1.2)
    assert alert.threshold == 1.0 and alert.comparison_operator == ">"
    assert alert.formula_inputs == data
    assert alert.details["phase_errors"] == pytest.approx({"R": 0.4, "Y": 0.3, "B": 1.2})
    assert alert.details["max_deviation_phase"] == "B"
    assert alert.details["test_name"] == "3-Phase Voltage Instrument Error"
    assert alert.details["threshold_unit"] == "percent"
    assert not alert.acknowledged and not alert.resolved


@pytest.mark.parametrize("r,y,b,phase", [
    (103, 100, 100, "R"), (100, 103, 100, "Y"), (100, 100, 103, "B"),
    (102, 102, 102, "R"),
])
def test_maximum_phase_and_ties(r, y, b, phase):
    results, alerts = evaluate(features(r, y, b))
    assert results[RULE].details["max_deviation_phase"] == phase
    assert len(alerts) == 1
    assert alerts[0].maintenance_type == MaintenanceType.SYSTEM_MAINTENANCE


def test_one_common_reference_for_all_phases():
    results, alerts = evaluate(features(r=201, y=198, b=204, reference=200))
    assert results[RULE].details["phase_errors"] == pytest.approx({"R": 0.5, "Y": 1, "B": 2})
    assert results[RULE].value == pytest.approx(2)
    assert results[RULE].details["max_deviation_phase"] == "B"
    assert len(alerts) == 1


@pytest.mark.parametrize("missing", [
    "reference_voltage", "measured_voltage_r", "measured_voltage_y", "measured_voltage_b",
])
def test_each_missing_feature_skips_without_alert(missing):
    data = features()
    del data[missing]
    results, alerts = evaluate(data)
    assert results[RULE].status == EvaluationStatus.SKIPPED
    assert results[RULE].missing_features == [missing]
    assert alerts == []


@pytest.mark.parametrize("reference", [0, 0.0, -0.0])
def test_zero_reference_is_invalid(reference):
    results, alerts = evaluate(features(reference=reference))
    assert results[RULE].status == EvaluationStatus.INVALID
    assert results[RULE].reason == "reference_voltage must be nonzero"
    assert alerts == []


@pytest.mark.parametrize("value", [None, True, "100", [], float("nan"), float("inf")])
def test_invalid_numeric_inputs_never_create_alert(value):
    results, alerts = evaluate(features(y=value))
    assert results[RULE].status == EvaluationStatus.INVALID
    assert alerts == []


def test_absolute_error():
    results, _ = evaluate(features(r=98, y=100, b=100))
    assert results[RULE].details["phase_errors"]["R"] == pytest.approx(2)


def test_negative_nonzero_reference_uses_absolute_denominator():
    results, _ = evaluate(features(r=-98, y=-100, b=-100, reference=-100))
    assert results[RULE].value == pytest.approx(2)


def test_no_daily_execution(db):
    assert ProcessingService(db).run_formulas(features(), EvaluationMode.DAILY) == {}


def test_formula_calculates_without_threshold_decision():
    result = formula_registry.get(RULE).calculate(features(b=101.2))
    assert result.status == EvaluationStatus.CALCULATED
    assert result.value == pytest.approx(1.2)


def test_threshold_is_configurable(db):
    service = ProcessingService(db)
    service.thresholds[RULE] = service.thresholds[RULE].model_copy(update={"threshold": 2.0})
    data = features(b=101.2)
    results = service.run_formulas(data)
    assert service.evaluate_thresholds(
        results, data=data, transformer_id="T01", timestamp=datetime.now(timezone.utc)
    ) == []
    assert results[RULE].status == EvaluationStatus.PASS


def test_normal_processing_persists_voltage_alert(db):
    data = features(b=101.2)
    stored = ProcessingService(db).receive_reading(SensorReadingCreate(
        transformer_id="T01", timestamp="2026-10-09T10:00:00Z", data=data,
    ))
    assert stored.raw_data == data
    assert stored.evaluations[RULE].status == EvaluationStatus.FLAGGED
    assert stored.evaluations[CURRENT].status == EvaluationStatus.SKIPPED
    db.add_all.assert_called_once()
    assert db.add_all.call_args.args[0][0].formula_name == RULE
    db.commit.assert_called_once()


def test_both_rules_run_on_the_same_reading(db):
    data = {**features(b=101.2), "rated_current": 100,
            "measured_current_r": 100.4, "measured_current_y": 99.7, "measured_current_b": 101.2}
    stored = ProcessingService(db).receive_reading(SensorReadingCreate(
        transformer_id="T01", timestamp="2026-10-09T10:00:00Z", data=data,
    ))
    assert all(stored.evaluations[name].status == EvaluationStatus.FLAGGED for name in (RULE, CURRENT))
    assert stored.evaluations["oil_level_predictive_maintenance"].status == EvaluationStatus.SKIPPED
    alerts = db.add_all.call_args.args[0][0:2]
    assert {alert.formula_name for alert in alerts} == {CURRENT, RULE}
    assert all(alert.maintenance_type == MaintenanceType.SYSTEM_MAINTENANCE for alert in alerts)
    db.commit.assert_called_once()


def test_current_rule_runs_when_voltage_features_are_missing():
    data = {"rated_current": 100, "measured_current_r": 100, "measured_current_y": 100, "measured_current_b": 102}
    results, alerts = evaluate(data)
    assert results[CURRENT].status == EvaluationStatus.FLAGGED
    assert results[RULE].status == EvaluationStatus.SKIPPED
    assert len(alerts) == 1 and alerts[0].formula_name == CURRENT


def test_batch_evaluates_rules_independently(db):
    rows = [features(b=101.2), features(), features(reference=0), {}]
    service = ProcessingService(db)
    assert service.receive_batch([
        SensorReadingCreate(transformer_id="T01", timestamp="2026-10-09T10:00:00Z", data=data)
        for data in rows
    ]) == 4
    assert [result[RULE].status for result in service.batch_evaluations] == [
        EvaluationStatus.FLAGGED, EvaluationStatus.PASS, EvaluationStatus.INVALID, EvaluationStatus.SKIPPED,
    ]
    db.add_all.assert_called_once()
    db.execute.assert_called_once()
    db.commit.assert_called_once()
