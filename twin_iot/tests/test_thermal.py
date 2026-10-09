import pytest

from app.formulas.base import EvaluationStatus
from tests.conftest import phases


@pytest.mark.parametrize("oil,expected", [
    (70, ["NORMAL"]), (95, ["WARNING"]), (105, ["WARNING", "CRITICAL", "MAINTENANCE_REQUIRED"]),
])
def test_top_oil_alarm_and_trip_without_ambient(run, oil, expected):
    evaluation = run("top_oil_temperature", {"top_oil_temperature": oil})
    assert evaluation.details["top_oil_rise"] is None
    assert evaluation.signals == expected


def test_top_oil_rise_over_ambient_warns(run):
    evaluation = run("top_oil_temperature", {"top_oil_temperature": 75, "ambient_temperature": 30})
    assert evaluation.details["top_oil_rise"] == 45
    assert evaluation.signals == ["WARNING"]
    assert evaluation.alerts[0].unit == "K"


def test_top_oil_trend_predicts_trip(run):
    # 90 -> 100 °C in 2 h = 5 °C/h; 5 °C to trip = 1 h, within the 6 h lead time.
    evaluation = run("top_oil_temperature", {"top_oil_temperature": 100}, {"top_oil_temperature": 90}, hours=2)
    assert evaluation.time_to_critical_hours == pytest.approx(1)
    assert evaluation.signals == ["WARNING", "PREDICTED_MAINTENANCE"]


def winding(r, y, b, ambient):
    return {**phases("winding_temperature", r, y, b), "ambient_temperature": ambient}


@pytest.mark.parametrize("hottest,expected", [
    (60, ["NORMAL"]), (71, ["WARNING"]), (75, ["WARNING"]),     # 45 K exactly is not over the limit
    (76, ["WARNING", "CRITICAL", "MAINTENANCE_REQUIRED"]),
])
def test_winding_rise_uses_hottest_phase(run, hottest, expected):
    evaluation = run("winding_temperature", winding(50, hottest, 55, 30))
    assert evaluation.value == hottest - 30
    assert evaluation.details["hottest_phase"] == "Y"
    assert evaluation.signals == expected


def test_winding_requires_ambient(run):
    evaluation = run("winding_temperature", phases("winding_temperature", 60, 60, 60))
    assert evaluation.evaluation_status == EvaluationStatus.SKIPPED
    assert evaluation.missing_features == ["ambient_temperature"]


def test_winding_trend_predicts_breach(run):
    # rise 30 -> 35 K in 1 h; 10 K left = 2 h.
    evaluation = run("winding_temperature", winding(65, 60, 60, 30), winding(60, 60, 60, 30))
    assert evaluation.time_to_critical_hours == pytest.approx(2)
    assert evaluation.signals == ["PREDICTED_MAINTENANCE"]
