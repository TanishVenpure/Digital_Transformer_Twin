import pytest

from app.formulas.base import EvaluationStatus
from tests.conftest import phases


@pytest.mark.parametrize("i_max,expected", [
    (600, ["NORMAL"]), (757, ["WARNING"]), (841, ["WARNING", "CRITICAL", "MAINTENANCE_REQUIRED"]),
])
def test_loading_static_limits(run, i_max, expected):
    evaluation = run("current_loading", phases("current", 100, i_max, 200))
    assert evaluation.value == pytest.approx(i_max / 841 * 100)
    assert evaluation.details["max_current_phase"] == "Y"
    assert evaluation.signals == expected


def test_loading_trend_predicts_overload(run):
    # 70 % -> 80 % in one hour: 20 % left at 10 %/h = 2 h, within the 24 h lead time.
    evaluation = run("current_loading", phases("current", 0.8 * 841, 0, 0), phases("current", 0.7 * 841, 0, 0))
    assert evaluation.details["rate"] == pytest.approx(10)
    assert evaluation.time_to_critical_hours == pytest.approx(2)
    assert evaluation.signals == ["PREDICTED_MAINTENANCE"]


def test_falling_load_has_no_prediction(run):
    evaluation = run("current_loading", phases("current", 500, 0, 0), phases("current", 600, 0, 0))
    assert evaluation.time_to_critical_hours is None and evaluation.status == "NORMAL"


def test_missing_rated_current_is_skipped(run, thresholds):
    rule = thresholds["current_loading"].model_copy(update={"parameters": {}})
    evaluation = run("current_loading", phases("current", 1, 1, 1), rule=rule)
    assert evaluation.evaluation_status == EvaluationStatus.SKIPPED
    assert evaluation.missing_features == ["rated_current"]


@pytest.mark.parametrize("voltages,deviation,status", [
    ((250, 251, 249), 0.4, "NORMAL"),
    ((250, 237.5, 250), 5.0, "WARNING"),     # under-voltage counts the same as over-voltage
    ((275, 250, 250), 10.0, "CRITICAL"),
])
def test_voltage_deviation_bands(run, voltages, deviation, status):
    evaluation = run("voltage_deviation", phases("voltage", *voltages))
    assert evaluation.value == pytest.approx(deviation)
    assert evaluation.status == status


def test_voltage_direction_detail(run):
    evaluation = run("voltage_deviation", phases("voltage", 250, 230, 250))
    assert evaluation.details["max_deviation_phase"] == "Y"
    assert evaluation.details["direction"] == "under"
