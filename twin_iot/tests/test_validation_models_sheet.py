from datetime import timedelta

from app.alerts.engine import IoTEvaluation
from app.maintenance.sheet import build_checks
from app.processing.validation import load_validation, validate_timestamp, validate_values
from app.twin3d.blender_bridge import BlenderBridge
from app.twin3d.selector import load_models, select_model
from tests.conftest import NOW

CONFIG = load_validation()


def test_every_iot_input_has_a_validation_rule():
    from app.formulas.registry import formula_registry
    features = {f for formula in formula_registry.all().values()
                for f in formula.required_features + formula.optional_features}
    assert features == set(CONFIG.features)


def test_bad_values_are_dropped_with_one_issue_each():
    clean, issues = validate_values(
        {"oil_level": 150, "top_oil_temperature": "ERR", "ambient_temperature": float("nan"), "current_r": 400}, CONFIG)
    codes = {i.feature: i.code.value for i in issues}
    assert clean == {"current_r": 400.0}
    assert codes["oil_level"] == "OUT_OF_RANGE"
    assert codes["top_oil_temperature"] == "NOT_NUMERIC"
    assert codes["ambient_temperature"] == "NOT_FINITE"
    assert codes["voltage_r"] == "MISSING"


def test_timestamp_checks():
    assert [i.code.value for i in validate_timestamp(NOW + timedelta(minutes=5), NOW, CONFIG)] == ["NOT_ON_HOUR"]
    assert [i.code.value for i in validate_timestamp(NOW + timedelta(hours=3), NOW, CONFIG)] == ["GAP"]
    assert validate_timestamp(NOW + timedelta(hours=1), NOW, CONFIG) == []


def evaluation(slot, status):
    return IoTEvaluation(formula_name=f"f{slot}", display_name=f"f{slot}", iot_slot=slot,
                         evaluation_status="PASS", status=status, unit="x")


def test_base_model_when_all_normal_and_worst_iot_otherwise():
    models = load_models()
    assert select_model([evaluation(1, "NORMAL"), evaluation(2, None)], models).model_id == "base"
    chosen = select_model([evaluation(3, "WARNING"), evaluation(6, "CRITICAL"), evaluation(5, "CRITICAL")], models)
    assert (chosen.model_id, chosen.signal) == ("iot5", "CRITICAL")  # worst signal, then lowest slot


def test_bridge_only_calls_on_model_change():
    models = load_models()
    bridge = BlenderBridge(models)
    bridge.load_base_model()
    selection = select_model([evaluation(4, "WARNING")], models)
    assert bridge.signal(selection, source="test")["status"] == "DONE"
    assert bridge.signal(selection, source="test") is None


def test_sheet_opens_on_threshold_and_resolves_on_normal():
    rows = [
        (1, NOW, [(5, "NORMAL")]),
        (2, NOW + timedelta(hours=1), [(5, "WARNING")]),
        (3, NOW + timedelta(hours=2), [(5, "WARNING")]),      # same status: no new check
        (4, NOW + timedelta(hours=3), [(5, None)]),           # not evaluated: ignored
        (5, NOW + timedelta(hours=4), [(5, "CRITICAL")]),     # escalation: new check
        (6, NOW + timedelta(hours=6), [(5, "NORMAL")]),
    ]
    alerts = {(5, 5): [{"signal": "CRITICAL", "value": 15.0, "threshold": 20.0, "comparison_operator": "<=",
                        "unit": "percent_of_gauge", "maintenance_type": "TRANSFORMER_MAINTENANCE"}]}
    checks = build_checks(rows, alerts, transformer_id="T1", actions={"oil_level": {"CRITICAL": "Top up oil."}},
                          names={5: ("oil_level", "Oil-Level IoT", "percent_of_gauge")})
    assert [(c["check_no"], c["signal"], c["status"]) for c in checks] == [(1, "WARNING", "RESOLVED"), (2, "CRITICAL", "RESOLVED")]
    assert checks[0]["duration_hours"] == 3 and checks[1]["duration_hours"] == 2
    assert "measured 15 percent_of_gauge <= threshold 20" in checks[1]["text"]
