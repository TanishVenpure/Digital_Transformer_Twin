"""Pressure measurements and explicit TEST-ONLY engineering settings."""
from datetime import timedelta
from pathlib import Path

import pytest

from app.alerts.configuration import ExtraCheck, load_thresholds
from app.alerts.engine import AlertEngine
from app.formulas.base import EvaluationMode
from app.formulas.context import FormulaContext, HistoricalReading
from app.formulas.pressure import TransformerPressureMonitoring
from app.formulas.registry import formula_registry
from app.processing.validation import FeatureRule, load_validation, validate_values
from app.services.processing_service import ProcessingService
from app.twin3d.selector import load_models, select_model
from tests.conftest import NOW

NAME = "transformer_pressure_monitoring"


def test_rule(warning=10, critical=20, rate=3, unit="test_pressure", rate_unit="test_pressure/hour"):
    rule = load_thresholds()[NAME].model_copy(deep=True)
    rule.warning, rule.critical, rule.unit = warning, critical, unit
    rule.extra_checks[0].threshold, rule.extra_checks[0].unit = rate, rate_unit
    return rule

# Helper, not a pytest test.
test_rule.__test__ = False


def evaluate(pressure, *, previous=None, hours=2, rule=None, optional=None):
    rule = rule or test_rule()
    data = {"oil_pressure": pressure, **(optional or {})}
    history = HistoricalReading(NOW - timedelta(hours=hours), {"oil_pressure": previous}) if previous is not None else None
    result = TransformerPressureMonitoring().evaluate(data, FormulaContext(NOW, history, unit=rule.unit))
    return AlertEngine().evaluate(formula_name=NAME, display_name="Transformer Pressure Monitoring", parameter="oil_pressure",
                                result=result, rule=rule, formula_inputs=data, transformer_id="T1", timestamp=NOW)


def test_registration_and_slot():
    registry = formula_registry.all()
    assert len(registry) == 8 and len(set(registry)) == 8
    formula = registry[NAME]
    assert formula.required_features == ("oil_pressure",)
    assert formula.optional_features == ("top_oil_temperature", "ambient_temperature")
    assert formula.evaluation_mode == EvaluationMode.PER_READING
    rule = load_thresholds()[NAME]
    assert rule.iot_slot == 8 and rule.maintenance_type == "TRANSFORMER_MAINTENANCE"
    assert set(registry) - {NAME} == {"current_measurement_health", "voltage_measurement_health", "current_loading",
                                      "voltage_deviation", "oil_level", "top_oil_temperature", "winding_temperature"}


def test_missing_pressure_skips_without_alert():
    e = ProcessingService().process("T1", NOW, {}).evaluations[NAME]
    assert e.evaluation_status == "SKIPPED" and e.status is None and not e.alerts


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf"), "ERR", True])
def test_invalid_pressure_never_alerts(value):
    direct = TransformerPressureMonitoring().evaluate({"oil_pressure": value})
    assert direct.status == "INVALID"
    processed = ProcessingService().process("T1", NOW, {"oil_pressure": value})
    assert "oil_pressure" not in processed.clean_data
    assert processed.evaluations[NAME].evaluation_status == "SKIPPED"
    assert not processed.evaluations[NAME].alerts


def test_unbounded_numeric_validation_preserves_measured_value():
    config = load_validation()
    feature = config.features["oil_pressure"]
    assert feature.min is None and feature.max is None and feature.unit == "unconfigured"
    for pressure in (-1e6, 0, 1e6):
        clean, _ = validate_values({"oil_pressure": pressure}, config)
        assert clean["oil_pressure"] == pressure
    assert FeatureRule(min=1, unit="x").max is None
    with pytest.raises(ValueError):
        FeatureRule(min=2, max=1, unit="x")


def test_first_reading_static_warning_without_false_rate():
    e = evaluate(10)
    assert e.status == "WARNING" and len(e.alerts) == 1
    assert e.details["current_pressure"] == 10
    assert e.details["pressure_rate"] is None
    assert e.details["rapid_pressure_condition"] is None
    assert e.details["absolute_pressure_condition"] == "HIGH_PRESSURE_WARNING"


def test_rate_uses_actual_elapsed_time_and_signed_difference():
    e = evaluate(16, previous=10, hours=2)
    assert e.details["pressure_rate"] == pytest.approx(3)
    assert e.details["previous_pressure"] == 10
    assert e.details["elapsed_time"] == 2
    assert e.details["elapsed_time_unit"] == "hour"
    assert e.details["pressure_rate_unit"] == "test_pressure/hour"
    irregular = evaluate(16, previous=10, hours=0.5)
    assert irregular.details["pressure_rate"] == pytest.approx(12)
    decreasing = evaluate(15, previous=20)
    assert decreasing.details["pressure_rate"] == pytest.approx(-2.5)
    assert "RAPID_PRESSURE_RISE" not in decreasing.details["trigger_conditions"]


@pytest.mark.parametrize("value,condition,signal", [(9.999999, None, "NORMAL"), (10, "HIGH_PRESSURE_WARNING", "WARNING"),
                                                     (20, "PRESSURE_CRITICAL", "CRITICAL")])
def test_absolute_boundaries_and_critical_precedence(value, condition, signal):
    e = evaluate(value)
    assert e.status == signal
    assert e.details["trigger_conditions"] == ([condition] if condition else [])
    assert len(e.alerts) == (1 if condition else 0)
    if condition:
        assert e.alerts[0].details["trigger_condition"] == condition
        assert e.alerts[0].comparison_operator == ">="
        assert e.alerts[0].maintenance_type == "TRANSFORMER_MAINTENANCE"
    if signal == "CRITICAL":
        assert "MAINTENANCE_REQUIRED" in e.signals
        assert "HIGH_PRESSURE_WARNING" not in e.details["trigger_conditions"]


def test_rapid_rise_is_independent_and_equality_triggers():
    e = evaluate(6, previous=0, hours=2)
    assert e.value < 10 and e.details["pressure_rate"] == 3
    assert e.details["trigger_conditions"] == ["RAPID_PRESSURE_RISE"]
    assert e.status == "CRITICAL" and "MAINTENANCE_REQUIRED" in e.signals
    assert len(e.alerts) == 1 and e.alerts[0].value == 3
    assert e.alerts[0].unit == "test_pressure/hour"
    below = evaluate(5.999999, previous=0, hours=2)
    assert not below.alerts
    assert below.details["rapid_pressure_condition"] == "NORMAL"


def test_simultaneous_critical_and_rapid_rise_preserve_both_receipts():
    e = evaluate(20, previous=10, hours=2)
    assert e.details["trigger_conditions"] == ["PRESSURE_CRITICAL", "RAPID_PRESSURE_RISE"]
    assert len(e.alerts) == 2 and "MAINTENANCE_REQUIRED" in e.signals
    assert {a.details["trigger_condition"] for a in e.alerts} == set(e.details["trigger_conditions"])
    assert [a.value for a in e.alerts] == [20, 5]


def test_optional_temperatures_do_not_control_pressure():
    bare = evaluate(10)
    with_context = evaluate(10, optional={"top_oil_temperature": 80, "ambient_temperature": 30})
    assert bare.status == with_context.status and bare.value == with_context.value
    assert with_context.details["top_oil_temperature"] == 80
    assert with_context.details["ambient_temperature"] == 30
    assert "top_oil_temperature" not in bare.details


@pytest.mark.parametrize("hours", [0, -1])
def test_unusable_interval_keeps_static_alert_available(hours):
    e = evaluate(20, previous=10, hours=hours)
    assert e.details["pressure_rate"] is None
    assert e.details["trigger_conditions"] == ["PRESSURE_CRITICAL"]
    assert "positive" in e.details["trend_unavailable_reason"]


def test_no_production_threshold_or_physical_unit_was_invented():
    rule = load_thresholds()[NAME]
    assert rule.warning is None and rule.critical is None and rule.unit is None
    assert rule.extra_checks[0].threshold is None and rule.extra_checks[0].unit is None
    e = evaluate(1000, previous=0, rule=rule)
    assert e.value == 1000 and e.details["pressure_rate"] == 500
    assert e.details["pressure_unit"] is None and e.details["pressure_rate_unit"] is None
    assert e.status == "NORMAL" and not e.alerts
    assert e.time_to_critical_hours is None


def test_physical_units_required_and_rate_units_must_match_hour_basis():
    e = evaluate(20, previous=0, rule=test_rule(unit=None, rate_unit=None))
    assert not e.alerts
    mismatch = evaluate(6, previous=0, rule=test_rule(rate_unit="test_pressure/minute"))
    assert not mismatch.alerts
    assert ExtraCheck(detail="x", operator=">=", threshold=None, unit=None, signal="CRITICAL", message="m").threshold is None


def test_service_uses_previous_valid_pressure_across_missing_observations():
    service = ProcessingService()
    service.process("T1", NOW, {"oil_pressure": 10})
    service.process("T1", NOW + timedelta(hours=1), {"oil_pressure": "ERR"})
    e = service.process("T1", NOW + timedelta(hours=2), {"oil_pressure": 16}).evaluations[NAME]
    assert e.details["pressure_rate"] == pytest.approx(3)
    assert e.details["elapsed_time"] == 2


def test_generic_history_loader_called_only_when_memory_unavailable():
    calls = []
    def loader(source, name, before):
        calls.append((source, name, before))
        return HistoricalReading(NOW - timedelta(hours=2), {"oil_pressure": 10})
    service = ProcessingService(history_loader=loader)
    e = service.process("T1", NOW, {"oil_pressure": 16}).evaluations[NAME]
    assert e.details["pressure_rate"] == 3 and len(calls) == 1
    service.process("T1", NOW + timedelta(hours=1), {"oil_pressure": 17})
    assert len(calls) == 1


def test_pressure_model_mapping_is_explicitly_unresolved():
    models = load_models()
    assert models.models["iot8"].focus_nodes == []
    assert "unresolved" in models.models["iot8"].label
    assert select_model([evaluate(20)], models).iot_slot == 8


def test_changed_pressure_unit_does_not_reuse_incompatible_history():
    previous = HistoricalReading(NOW - timedelta(hours=1), {"oil_pressure": 10}, unit="unconfigured")
    result = TransformerPressureMonitoring().evaluate({"oil_pressure": 16}, FormulaContext(NOW, previous, unit="test_pressure"))
    assert result.value == 16 and result.details["pressure_rate"] is None
    assert "unit differs" in result.details["trend_unavailable_reason"]
