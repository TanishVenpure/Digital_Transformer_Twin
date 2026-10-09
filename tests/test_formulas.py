import pytest
import yaml

from app.config.settings import PROJECT_ROOT
from app.formulas.registry import FormulaRegistry, formula_registry


def test_default_registry_and_empty_custom_registry():
    assert set(formula_registry.all()) == {"3_phase_current_instrument_error", "3_phase_voltage_instrument_error", "oil_level_predictive_maintenance"}
    assert FormulaRegistry().all() == {}
    with pytest.raises(KeyError):
        formula_registry.get("not-configured")


def test_registry_returns_copy():
    registry = FormulaRegistry()
    snapshot = registry.all()
    snapshot["unregistered"] = object()
    assert registry.all() == {}


def test_registry_rejects_nonformula():
    with pytest.raises(TypeError):
        FormulaRegistry().register(object())


def test_only_supplied_threshold_is_configured():
    content = (PROJECT_ROOT / "config" / "thresholds.yaml").read_text(encoding="utf-8")
    formulas = yaml.safe_load(content)["formulas"]
    assert set(formulas) == {"3_phase_current_instrument_error", "3_phase_voltage_instrument_error", "oil_level_predictive_maintenance"}
    assert formulas["3_phase_current_instrument_error"]["operator"] == ">"
    assert formulas["3_phase_current_instrument_error"]["threshold"] == 1.0
