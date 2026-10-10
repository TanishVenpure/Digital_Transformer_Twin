from datetime import timedelta
from pathlib import Path

import pytest

from app.alerts.configuration import IoTRule, load_thresholds
from app.formulas.registry import formula_registry
from app.processing.ingestion import read_csv
from app.services.processing_service import ProcessingService
from tests.conftest import NOW

DEMO = Path(__file__).resolve().parents[1] / "data" / "examples" / "escalation_demo.csv"


def test_every_iot_has_a_unique_slot(thresholds):
    assert set(thresholds) == set(formula_registry.all())
    assert sorted(rule.iot_slot for rule in thresholds.values()) == list(range(1, 10))


def test_warning_beyond_critical_is_rejected():
    with pytest.raises(ValueError):
        IoTRule(iot_slot=1, operator=">", unit="x", warning=10, critical=5,
                maintenance_type="SYSTEM_MAINTENANCE", critical_message="m")
    with pytest.raises(ValueError):
        IoTRule(iot_slot=1, operator="<=", unit="x", warning=10, critical=20,
                maintenance_type="SYSTEM_MAINTENANCE", critical_message="m")


def test_service_uses_previous_reading_per_transformer():
    service = ProcessingService()
    service.receive_reading("T1", NOW, {"oil_level": 68})
    service.receive_reading("T2", NOW, {"oil_level": 90})
    t1 = service.receive_reading("T1", NOW + timedelta(hours=1), {"oil_level": 60})["oil_level"]
    assert t1.details["rate"] == pytest.approx(-8)
    assert t1.status == "PREDICTED_MAINTENANCE"


def test_out_of_order_and_duplicate_readings_are_rejected():
    service = ProcessingService()
    service.receive_reading("T1", NOW, {"oil_level": 70})
    late = service.process("T1", NOW - timedelta(hours=1), {"oil_level": 71})
    duplicate = service.process("T1", NOW, {"oil_level": 72})
    assert not late.accepted and late.issues[0].code == "OUT_OF_ORDER"
    assert not duplicate.accepted and duplicate.issues[0].code == "DUPLICATE_TIMESTAMP"
    after = service.receive_reading("T1", NOW + timedelta(hours=1), {"oil_level": 69})["oil_level"]
    assert after.details["previous_value"] == 70


def test_partial_reading_skips_unavailable_iots():
    evaluations = ProcessingService().receive_reading("T1", NOW, {"oil_level": 80})
    assert evaluations["oil_level"].status == "NORMAL"
    assert all(e.status is None for name, e in evaluations.items() if name != "oil_level")


def test_demo_csv_preserves_other_critical_rules_with_revised_loading():
    readings = list(read_csv(DEMO))
    results = ProcessingService(load_thresholds()).replay(readings)
    assert all(r.accepted and all(i.code == "MISSING" for i in r.issues) for r in results)
    assert all(e.status == "NORMAL" for name, e in results[0].evaluations.items()
               if name not in ("transformer_pressure_monitoring", "voltage_deviation", "hv_voltage_deviation"))
    assert all(r.evaluations["transformer_pressure_monitoring"].evaluation_status == "SKIPPED" for r in results)
    final = results[-1].evaluations
    for name in ("oil_level", "top_oil_temperature", "winding_temperature"):
        assert final[name].status == "CRITICAL"

    # Legacy demo currents are below the newly supplied 110% warning; do not alter source data.
    assert final["current_loading"].status == "NORMAL"
