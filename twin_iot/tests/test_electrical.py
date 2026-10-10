import math

import pytest

from app.formulas.base import EvaluationStatus
from tests.conftest import phases

RATED = 633000 / (math.sqrt(3) * 433)


@pytest.mark.parametrize("loading,expected,condition", [
    (100, ["NORMAL"], "NORMAL"),
    (109.99, ["NORMAL"], "NORMAL"),
    (110, ["WARNING"], "OVERLOAD_WARNING"),
    (112.3, ["WARNING"], "OVERLOAD_WARNING"),
    (119.99, ["WARNING"], "OVERLOAD_WARNING"),
    (120, ["CRITICAL", "MAINTENANCE_REQUIRED"], "TRIP_ALERT"),
    (120.01, ["CRITICAL", "MAINTENANCE_REQUIRED"], "TRIP_ALERT"),
])
def test_loading_static_limits(run, loading, expected, condition):
    evaluation = run("current_loading", phases("current", 100, RATED * loading / 100, 200))
    assert evaluation.value == pytest.approx(loading)
    assert evaluation.details["rated_current"] == pytest.approx(844.0247584)
    assert evaluation.details["max_current_phase"] == "Y"
    assert evaluation.details["measurement_side"] == "LV"
    assert evaluation.details["overload_condition"] == condition
    assert evaluation.signals == expected
    assert len(evaluation.alerts) == (condition != "NORMAL")
    if evaluation.alerts:
        alert = evaluation.alerts[0]
        assert alert.maintenance_type == "TRANSFORMER_MAINTENANCE"
        assert alert.details["trigger_condition"] == condition
        assert alert.comparison_operator == ">="


@pytest.mark.parametrize("phase", ["R", "Y", "B"])
def test_loading_preserves_all_phases(run, phase):
    data = phases("current", RATED, RATED, RATED)
    data[f"current_{phase.lower()}"] = RATED * 1.2
    evaluation = run("current_loading", data)
    assert evaluation.details["max_current_phase"] == phase
    assert evaluation.details["phase_loading_percent"][phase] == pytest.approx(120)
    assert set(evaluation.details["phase_loading_percent"]) == {"R", "Y", "B"}
    assert evaluation.alerts[0].formula_inputs == data


def test_loading_trend_does_not_create_prediction_alert(run):
    evaluation = run("current_loading", phases("current", 0.8 * RATED, 0, 0), phases("current", 0.7 * RATED, 0, 0))
    assert evaluation.details["rate"] == pytest.approx(10)
    assert evaluation.time_to_critical_hours == pytest.approx(4)
    assert evaluation.signals == ["NORMAL"]
    assert not evaluation.alerts


def test_falling_load_has_no_prediction(run):
    evaluation = run("current_loading", phases("current", 500, 0, 0), phases("current", 600, 0, 0))
    assert evaluation.time_to_critical_hours is None and evaluation.status == "NORMAL"


def test_missing_nameplate_is_skipped(run, thresholds):
    rule = thresholds["current_loading"].model_copy(update={"parameters": {}})
    evaluation = run("current_loading", phases("current", 1, 1, 1), rule=rule)
    assert evaluation.evaluation_status == EvaluationStatus.SKIPPED
    assert evaluation.missing_features == ["rated_kva", "rated_voltage_ll"]


@pytest.mark.parametrize("feature", ["rated_kva", "rated_voltage_ll"])
@pytest.mark.parametrize("value", [0, -1, float("nan"), float("inf"), True])
def test_invalid_nameplate_does_not_alert(run, thresholds, feature, value):
    original = thresholds["current_loading"]
    rule = original.model_copy(update={"parameters": {**original.parameters, feature: value}})
    evaluation = run("current_loading", phases("current", 2000, 2000, 2000), rule=rule)
    assert evaluation.evaluation_status == EvaluationStatus.INVALID
    assert not evaluation.alerts


def test_loading_absolute_magnitude(run):
    evaluation = run("current_loading", phases("current", -RATED * 1.2, RATED, RATED))
    assert evaluation.value == pytest.approx(120)
    assert evaluation.details["max_current_phase"] == "R"
