import pytest
import yaml

from app.config.settings import PROJECT_ROOT
from app.formulas.registry import FormulaRegistry, formula_registry


def test_empty_registry():
    assert formula_registry.all() == {}
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


def test_thresholds_are_empty():
    content = (PROJECT_ROOT / "config" / "thresholds.yaml").read_text(encoding="utf-8")
    assert yaml.safe_load(content) == {"formulas": {}}
