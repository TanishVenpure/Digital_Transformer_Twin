import pytest

from app.formulas.base import EvaluationStatus
from tests.conftest import phases


def current(measured, reference=(500, 500, 500)):
    return {**phases("current", *measured), **phases("reference_current", *reference)}


def test_within_accuracy_is_healthy(run):
    evaluation = run("current_measurement_health", current((504, 497, 500)))
    assert evaluation.value == pytest.approx(0.8)
    assert evaluation.details["max_error_phase"] == "R"
    assert evaluation.signals == ["NORMAL"] and evaluation.alerts == []


def test_exactly_at_limit_is_still_healthy(run):
    # Rule is strictly greater than 1 %.
    assert run("current_measurement_health", current((505, 500, 500))).status == "NORMAL"


def test_error_above_limit_requires_sensor_maintenance(run):
    evaluation = run("current_measurement_health", current((500, 500, 490), (500, 500, 500)))
    assert evaluation.value == pytest.approx(2.0)
    assert evaluation.details["max_error_phase"] == "B"
    assert evaluation.signals == ["MAINTENANCE_REQUIRED"]
    assert evaluation.alerts[0].maintenance_type == "SYSTEM_MAINTENANCE"


def test_per_phase_reference_is_used(run):
    evaluation = run("voltage_measurement_health", {
        **phases("voltage", 230, 240, 250), **phases("reference_voltage", 230, 240, 250),
    })
    assert evaluation.value == 0 and evaluation.status == "NORMAL"


def test_zero_reference_is_invalid_not_a_fault(run):
    evaluation = run("current_measurement_health", current((1, 1, 1), (0, 1, 1)))
    assert evaluation.evaluation_status == EvaluationStatus.INVALID
    assert evaluation.status is None and evaluation.alerts == []


def test_missing_reference_is_skipped(run):
    evaluation = run("voltage_measurement_health", phases("voltage", 250, 250, 250))
    assert evaluation.evaluation_status == EvaluationStatus.SKIPPED
    assert "reference_voltage_r" in evaluation.missing_features
