from datetime import timedelta
import math

import pytest

from app.alerts.configuration import load_thresholds
from app.services.processing_service import ProcessingService
from tests.conftest import NOW


def configured():
    rules = load_thresholds()
    for name in ("voltage_deviation", "hv_voltage_deviation"):
        old = rules[name]
        rules[name] = old.model_copy(update={"parameters": {**old.parameters, "measurement_type": "LINE_TO_LINE"}})
    return rules


@pytest.mark.parametrize("name,prefix,nominal,warn,trip", [
    ("voltage_deviation", "lv_voltage", 433, 3, 6),
    ("hv_voltage_deviation", "hv_voltage", 11000, 3, 6),
    ("hv_voltage_deviation", "hv_voltage", 11000, -5, -9),
    ("voltage_deviation", "lv_voltage", 433, -3, -6),
])
@pytest.mark.parametrize("boundary", ["below", "warning", "trip"])
def test_directional_inclusive_boundaries(name, prefix, nominal, warn, trip, boundary):
    deviation = warn * 0.999 if boundary == "below" else warn if boundary == "warning" else trip
    data = {f"{prefix}_{p}": nominal for p in "ryb"}
    data[f"{prefix}_y"] = nominal * (1 + deviation / 100)
    evaluation = ProcessingService(configured()).process("T1", NOW, data).evaluations[name]
    assert evaluation.value == pytest.approx(abs(deviation))
    assert evaluation.details["phase_deviations"]["Y"] == pytest.approx(deviation)
    if boundary == "below":
        assert not evaluation.alerts and evaluation.status == "NORMAL"
    else:
        assert len(evaluation.alerts) == 1
        alert = evaluation.alerts[0]
        direction = "OVERVOLTAGE" if deviation > 0 else "UNDERVOLTAGE"
        assert alert.details["trigger_condition"] == direction + ("_WARNING" if boundary == "warning" else "_TRIP_ALERT")
        assert alert.maintenance_type == "TRANSFORMER_MAINTENANCE"
        assert alert.comparison_operator == ">="
        assert evaluation.details["max_deviation_phase"] == "Y"


def test_20ms_readings_escalation_recovery_and_repeat_breach():
    service = ProcessingService(configured())
    counts = []
    for i, voltage in enumerate((445.99, 445.99, 458.98, 458.98, 433, 458.98)):
        stamp = NOW + timedelta(milliseconds=20 * i)
        result = service.process("T1", stamp, {f"lv_voltage_{p}": voltage for p in "ryb"})
        assert result.accepted and result.timestamp == stamp
        counts.append(len(result.evaluations["voltage_deviation"].alerts))
    assert counts == [1, 0, 1, 0, 0, 1]


def test_missing_measurement_type_skips_and_does_not_guess():
    result = ProcessingService().process("T1", NOW, {f"lv_voltage_{p}": 500 for p in "ryb"})
    evaluation = result.evaluations["voltage_deviation"]
    assert evaluation.evaluation_status == "SKIPPED"
    assert evaluation.missing_features == ["measurement_type"]
    assert not evaluation.alerts


def test_phase_to_neutral_uses_matching_reference():
    rules = configured()
    old = rules["voltage_deviation"]
    rules["voltage_deviation"] = old.model_copy(update={"parameters": {**old.parameters, "measurement_type": "PHASE_TO_NEUTRAL"}})
    data = {f"lv_voltage_{p}": 433 / math.sqrt(3) for p in "ryb"}
    e = ProcessingService(rules).process("T1", NOW, data).evaluations["voltage_deviation"]
    assert e.value == pytest.approx(0)
    assert not e.alerts


def test_hv_and_lv_are_independent_and_mixed_directions_preserved():
    data = {"lv_voltage_r": 458.98, "lv_voltage_y": 407.02, "lv_voltage_b": 433,
            "hv_voltage_r": 11000, "hv_voltage_y": 11000, "hv_voltage_b": 11000}
    e = ProcessingService(configured()).process("T1", NOW, data).evaluations
    assert not e["hv_voltage_deviation"].alerts
    assert {a.details["trigger_condition"] for a in e["voltage_deviation"].alerts} == {"OVERVOLTAGE_TRIP_ALERT", "UNDERVOLTAGE_TRIP_ALERT"}
