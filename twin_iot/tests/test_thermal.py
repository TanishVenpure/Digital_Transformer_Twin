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


def winding(r, y, b, ambient=None):
    data = phases("winding_temperature", r, y, b)
    if ambient is not None:
        data["ambient_temperature"] = ambient
    return data


TRIP = ["WARNING", "CRITICAL", "MAINTENANCE_REQUIRED"]


# WTI protection: absolute hottest-phase temperature against the CPRI alarm (110) and trip (120) settings.
@pytest.mark.parametrize("hottest,expected", [
    (109.9, ["NORMAL"]), (110.0, ["WARNING"]), (119.9, ["WARNING"]), (120.0, TRIP),
])
def test_wti_alarm_and_trip_boundaries(run, hottest, expected):
    evaluation = run("winding_temperature", winding(80, hottest, 90, 30))
    assert evaluation.value == hottest
    assert evaluation.unit == "degC"
    assert evaluation.details["hottest_phase"] == "Y"
    assert evaluation.signals == expected


def test_wti_status_ignores_the_rise_over_ambient(run):
    # the same 100 degC winding is Normal whatever the ambient: a 75 K rise (old 45 K limit) no longer trips
    for ambient in (0, 25, 55, None):
        evaluation = run("winding_temperature", winding(100, 100, 100, ambient))
        assert evaluation.signals == ["NORMAL"], ambient
    hot = run("winding_temperature", winding(121, 120, 120, 60))
    assert hot.details["winding_rise"] == 61
    assert hot.signals == TRIP


def test_wti_without_ambient_still_protects(run):
    evaluation = run("winding_temperature", winding(125, 120, 118))
    assert evaluation.evaluation_status != EvaluationStatus.SKIPPED
    assert evaluation.details["winding_rise"] is None
    assert evaluation.signals == TRIP


@pytest.mark.parametrize("data", [
    {"winding_temperature_r": 130, "winding_temperature_y": 130},          # one phase missing
    {},                                                                     # no WTI data at all
])
def test_missing_wti_data_is_no_data_never_a_trip(run, data):
    evaluation = run("winding_temperature", {**data, "ambient_temperature": 30})
    assert evaluation.evaluation_status == EvaluationStatus.SKIPPED
    assert evaluation.status is None and evaluation.alerts == []


def test_invalid_wti_reading_is_dropped_not_a_trip():
    from app.services.processing_service import ProcessingService
    from tests.conftest import NOW
    processed = ProcessingService().process("T1", NOW, {**winding(99999, "ERR", 125), "ambient_temperature": 30})
    e = processed.evaluations["winding_temperature"]
    assert e.evaluation_status == "SKIPPED" and not e.alerts
    assert {i.feature for i in processed.issues} >= {"winding_temperature_r", "winding_temperature_y"}


def test_wti_trend_predicts_trip(run):
    # 104 -> 112 degC in 1 h: 8 K to the 120 degC trip = 1 h, within the 6 h lead time.
    evaluation = run("winding_temperature", winding(112, 100, 100, 30), winding(104, 100, 100, 30))
    assert evaluation.time_to_critical_hours == pytest.approx(1)
    assert evaluation.signals == ["WARNING", "PREDICTED_MAINTENANCE"]


@pytest.mark.parametrize("oil,wti,oti_status,wti_status", [
    (106, 90, "CRITICAL", "NORMAL"),     # OTI trips on its own setting; WTI stays Normal
    (70, 121, "NORMAL", "CRITICAL"),     # WTI trips on its own setting; OTI stays Normal
    (96, 111, "WARNING", "WARNING"),
])
def test_oti_and_wti_are_independent(oil, wti, oti_status, wti_status):
    from app.services.processing_service import ProcessingService
    from tests.conftest import NOW
    data = {"top_oil_temperature": oil, "ambient_temperature": 60, **winding(wti, wti, wti)}
    e = ProcessingService().process("T1", NOW, data).evaluations
    assert e["top_oil_temperature"].status == oti_status
    assert e["winding_temperature"].status == wti_status
    # never averaged: each keeps its own measured value
    assert e["top_oil_temperature"].value == oil and e["winding_temperature"].value == wti
