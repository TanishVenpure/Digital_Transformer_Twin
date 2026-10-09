from datetime import datetime, timedelta, timezone

import pytest

from app.alerts.configuration import load_thresholds
from app.alerts.engine import AlertEngine
from app.formulas.context import FormulaContext, HistoricalReading
from app.formulas.registry import formula_registry

NOW = datetime(2026, 10, 9, 12, tzinfo=timezone.utc)


@pytest.fixture(scope="session")
def thresholds():
    return load_thresholds()


@pytest.fixture
def run(thresholds):
    """Evaluate one IoT: run(name, data, previous=None, hours=1, rule=None) -> IoTEvaluation."""
    def _run(name, data, previous=None, hours=1, rule=None):
        formula = formula_registry.get(name)
        rule = rule or thresholds[name]
        context = FormulaContext(
            timestamp=NOW, parameters=rule.parameters,
            previous_reading=HistoricalReading(NOW - timedelta(hours=hours), previous) if previous is not None else None,
        )
        result = formula.evaluate(data, context)
        return AlertEngine().evaluate(
            formula_name=name, display_name=formula.display_name, parameter=formula.parameter,
            result=result, rule=rule, formula_inputs=data, transformer_id="T1", timestamp=NOW,
        )
    return _run


def phases(prefix, r, y, b):
    return {f"{prefix}_r": r, f"{prefix}_y": y, f"{prefix}_b": b}
