"""Shared IoT calculation: one value per reading plus an optional rate from history.

Formulas only calculate. The AlertEngine owns every threshold decision.
"""
import math
from abc import abstractmethod
from datetime import datetime, timezone
from typing import Any

from app.formulas.base import EvaluationMode, EvaluationStatus, Formula, FormulaInputError, FormulaResult
from app.formulas.context import FormulaContext

PHASES = ("R", "Y", "B")


def finite(data: dict[str, Any], feature: str) -> float:
    value = data.get(feature)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise FormulaInputError(f"{feature} must be a finite number")
    try:
        number = float(value)
    except (OverflowError, ValueError) as exc:
        raise FormulaInputError(f"{feature} must be a finite number") from exc
    if not math.isfinite(number):
        raise FormulaInputError(f"{feature} must be a finite number")
    return number


def phase_values(data: dict[str, Any], prefix: str) -> dict[str, float]:
    return {phase: finite(data, f"{prefix}_{phase.lower()}") for phase in PHASES}


def max_phase(values: dict[str, float]) -> str:
    # Deterministic tie policy: first maximum in R, Y, B order.
    return max(values, key=values.get)


def utc(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


class IoTFormula(Formula):
    evaluation_mode = EvaluationMode.PER_READING
    required_parameters: tuple[str, ...] = ()
    optional_features: tuple[str, ...] = ()
    # Predictive IoTs compute d(value)/dt from the previous reading of the same IoT.
    predictive: bool = False

    @abstractmethod
    def measure(self, data: dict[str, Any], parameters: dict[str, Any]) -> tuple[float, dict[str, Any]]:
        """Return the IoT's monitored value and its diagnostic details."""
        raise NotImplementedError

    def calculate(self, data: dict[str, Any]) -> FormulaResult:
        return self.calculate_with_context(data, None)

    def calculate_with_context(self, data: dict[str, Any], context: FormulaContext | None) -> FormulaResult:
        context = context or FormulaContext()
        parameters = context.parameters
        missing = [name for name in self.required_parameters if parameters.get(name) is None]
        if missing:
            return FormulaResult(
                status=EvaluationStatus.SKIPPED, reason="Missing configuration parameters",
                missing_features=missing,
            )
        value, details = self.measure(data, parameters)
        if not math.isfinite(value):
            raise FormulaInputError(f"{self.parameter} calculation is not finite")
        if self.predictive:
            details.update(self.trend(value, parameters, context))
        return FormulaResult(status=EvaluationStatus.CALCULATED, value=value, details=details)

    def trend(self, value: float, parameters: dict[str, Any], context: FormulaContext) -> dict[str, Any]:
        trend = {
            "rate": None, "rate_unit": "value_units/hour", "elapsed_hours": None,
            "previous_value": None, "previous_timestamp": None,
            "trend_unavailable_reason": "No previous reading",
        }
        previous = context.previous_reading
        if previous is None:
            return trend
        trend["previous_timestamp"] = utc(previous.timestamp).isoformat()
        try:
            prior, _ = self.measure(previous.data, parameters)
        except FormulaInputError as exc:
            trend["trend_unavailable_reason"] = f"Previous reading unusable: {exc}"
            return trend
        trend["previous_value"] = prior
        if context.timestamp is None:
            trend["trend_unavailable_reason"] = "Current timestamp is unavailable"
            return trend
        hours = (utc(context.timestamp) - utc(previous.timestamp)).total_seconds() / 3600
        trend["elapsed_hours"] = hours
        if hours <= 0:
            trend["trend_unavailable_reason"] = "Historical time interval must be positive"
            return trend
        rate = (value - prior) / hours
        if not math.isfinite(rate):
            trend["trend_unavailable_reason"] = "Rate is not finite"
            return trend
        trend["rate"] = rate
        trend["trend_unavailable_reason"] = None
        return trend
