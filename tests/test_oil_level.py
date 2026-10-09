from datetime import datetime, timedelta, timezone
import pytest
from app.alerts.configuration import OilThresholdRule, load_thresholds
from app.alerts.engine import AlertEngine
from app.formulas.context import FormulaContext, HistoricalReading
from app.formulas.oil_level import OilLevelPredictiveMaintenance
from app.formulas.base import EvaluationStatus

RULE = "oil_level_predictive_maintenance"
NOW = datetime(2026, 10, 9, tzinfo=timezone.utc)
CONFIG = OilThresholdRule(minimum_oil_level=20, warning_oil_level=40, maintenance_lead_time=5)


def evaluate(level, prior=None, hours=1, configuration=CONFIG):
    context = FormulaContext(timestamp=NOW, parameters=configuration.model_dump(), previous_reading=(
        HistoricalReading(NOW - timedelta(hours=hours), {"oil_level": prior}, 1) if prior is not None else None
    ))
    result = OilLevelPredictiveMaintenance().evaluate({"oil_level": level}, context)
    alerts = []
    if result.status == EvaluationStatus.CALCULATED:
        alerts = AlertEngine().evaluate_oil(
            formula_name=RULE, parameter="oil_level", result=result, configuration=configuration,
            formula_inputs={"oil_level": level}, transformer_id="single", timestamp=NOW,
            test_name="Oil-Level IoT Predictive Maintenance",
        )
    return result, alerts


@pytest.mark.parametrize("level,expected", [
    (80, ["NORMAL"]), (40, ["LOW_OIL_WARNING"]),
    (20, ["LOW_OIL_WARNING", "OIL_LEVEL_CRITICAL", "MAINTENANCE_REQUIRED"]),
    (10, ["LOW_OIL_WARNING", "OIL_LEVEL_CRITICAL", "MAINTENANCE_REQUIRED"]),
])
def test_static_first_reading_and_equality(level, expected):
    result, alerts = evaluate(level)
    assert result.details["outputs"] == expected
    assert len(alerts) == len([x for x in expected if x not in ("NORMAL", "MAINTENANCE_REQUIRED")])
    assert result.details["oil_level_rate"] is None
    assert all(a.maintenance_type.value == "TRANSFORMER_MAINTENANCE" for a in alerts)


def test_rate_prediction_and_lead_equality():
    result, alerts = evaluate(60, 68)
    assert result.details["oil_level_rate"] == pytest.approx(-8)
    assert result.details["estimated_t_min"] == pytest.approx(5)
    assert result.details["trend_direction"] == "declining"
    assert result.details["time_unit"] == "hours"
    assert result.details["outputs"] == ["PREDICTED_MINIMUM_LEVEL_BREACH", "MAINTENANCE_REQUIRED"]
    assert len(alerts) == 1
    assert alerts[0].formula_value == 5 and alerts[0].comparison_operator == "<="


def test_every_crossed_threshold_is_recorded():
    result, alerts = evaluate(20, 30)
    assert {a.details["trigger_condition"] for a in alerts} == {
        "LOW_OIL_WARNING", "OIL_LEVEL_CRITICAL", "PREDICTED_MINIMUM_LEVEL_BREACH",
    }
    assert "MAINTENANCE_REQUIRED" in result.details["outputs"]


@pytest.mark.parametrize("hours", [0, -1])
def test_invalid_time_preserves_static_checks(hours):
    result, alerts = evaluate(35, 80, hours)
    assert result.details["oil_level_rate"] is None
    assert result.details["estimated_t_min"] is None
    assert [a.details["trigger_condition"] for a in alerts] == ["LOW_OIL_WARNING"]


@pytest.mark.parametrize("prior,direction,t_min", [(60, "stable", None), (50, "increasing", 4)])
def test_stable_and_increasing_have_no_future_breach(prior, direction, t_min):
    result, alerts = evaluate(60, prior)
    assert result.details["trend_direction"] == direction
    assert result.details["estimated_t_min"] == t_min
    assert alerts == []


def test_below_minimum_keeps_signed_prediction_and_static_alerts():
    result, alerts = evaluate(10, 30)
    assert result.details["estimated_t_min"] == pytest.approx(-0.5)
    assert len(alerts) == 2


@pytest.mark.parametrize("value", [None, True, "bad", float("inf"), float("nan")])
def test_invalid_input_never_alerts(value):
    result, alerts = evaluate(value)
    assert result.status == EvaluationStatus.INVALID and alerts == []


def test_missing_and_unconfigured():
    assert OilLevelPredictiveMaintenance().evaluate({}).status == EvaluationStatus.SKIPPED
    config = load_thresholds()[RULE]
    assert not config.configured
    assert all(getattr(config, name) is None for name in (
        "minimum_oil_level", "warning_oil_level", "maintenance_lead_time",
    ))
    result, alerts = evaluate(10, configuration=config)
    assert result.status == EvaluationStatus.SKIPPED and alerts == []


def test_invalid_previous_value_preserves_static_checks():
    result, alerts = evaluate(35, "bad")
    assert result.details["oil_level_rate"] is None
    assert len(alerts) == 1
