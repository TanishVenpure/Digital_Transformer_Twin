import pytest


@pytest.mark.parametrize("level,expected", [
    (80, ["NORMAL"]), (40, ["WARNING"]),
    (20, ["WARNING", "CRITICAL", "MAINTENANCE_REQUIRED"]),
    (10, ["WARNING", "CRITICAL", "MAINTENANCE_REQUIRED"]),
])
def test_static_levels_first_reading(run, level, expected):
    evaluation = run("oil_level", {"oil_level": level})
    assert evaluation.signals == expected
    assert evaluation.details["rate"] is None


def test_declining_level_predicts_minimum_breach(run):
    # T_min = (60 - 20) / |-8| = 5 h, within the 72 h lead time.
    evaluation = run("oil_level", {"oil_level": 60}, {"oil_level": 68})
    assert evaluation.details["rate"] == pytest.approx(-8)
    assert evaluation.time_to_critical_hours == pytest.approx(5)
    assert evaluation.signals == ["PREDICTED_MAINTENANCE"]


def test_slow_leak_beyond_lead_time_is_normal(run):
    # 0.1 per hour from 60 -> 400 h to minimum.
    evaluation = run("oil_level", {"oil_level": 60}, {"oil_level": 60.1})
    assert evaluation.time_to_critical_hours == pytest.approx(400)
    assert evaluation.status == "NORMAL"


@pytest.mark.parametrize("hours", [0, -1])
def test_invalid_interval_keeps_static_checks(run, hours):
    evaluation = run("oil_level", {"oil_level": 35}, {"oil_level": 80}, hours=hours)
    assert evaluation.details["rate"] is None
    assert evaluation.signals == ["WARNING"]


def test_below_minimum_has_no_future_prediction(run):
    evaluation = run("oil_level", {"oil_level": 10}, {"oil_level": 30})
    assert evaluation.time_to_critical_hours is None
    assert evaluation.status == "CRITICAL"
