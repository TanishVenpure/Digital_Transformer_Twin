import math
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from app.formulas.base import EvaluationMode, EvaluationStatus, Formula, FormulaInputError, FormulaResult
from app.formulas.context import FormulaContext


class OilOutput(str, Enum):
    NORMAL = "NORMAL"
    LOW_OIL_WARNING = "LOW_OIL_WARNING"
    OIL_LEVEL_CRITICAL = "OIL_LEVEL_CRITICAL"
    PREDICTED_MINIMUM_LEVEL_BREACH = "PREDICTED_MINIMUM_LEVEL_BREACH"
    MAINTENANCE_REQUIRED = "MAINTENANCE_REQUIRED"


def finite_oil_level(value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise FormulaInputError("oil_level must be a finite number")
    try:
        number = float(value)
    except (OverflowError, ValueError) as exc:
        raise FormulaInputError("oil_level must be a finite number") from exc
    if not math.isfinite(number):
        raise FormulaInputError("oil_level must be a finite number")
    return number


def utc(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


class OilLevelPredictiveMaintenance(Formula):
    name = "oil_level_predictive_maintenance"
    display_name = "Oil-Level IoT Predictive Maintenance"
    parameter = "oil_level"
    required_features = ("oil_level",)
    evaluation_mode = EvaluationMode.PER_READING
    history_feature = "oil_level"

    def calculate(self, data: dict[str, Any]) -> FormulaResult:
        return self.calculate_with_context(data, None)

    def calculate_with_context(
        self, data: dict[str, Any], context: FormulaContext | None,
    ) -> FormulaResult:
        current = finite_oil_level(data["oil_level"])
        context = context or FormulaContext()
        minimum = context.parameters.get("minimum_oil_level")
        details = {
            "current_oil_level": current,
            "minimum_oil_level": minimum,
            "warning_oil_level": context.parameters.get("warning_oil_level"),
            "maintenance_lead_time": context.parameters.get("maintenance_lead_time"),
            "oil_level_rate": None,
            "rate_unit": "source_oil_level_units/hour",
            "time_unit": "hours",
            "estimated_t_min": None,
            "trend_direction": None,
            "trend_unavailable_reason": "Insufficient historical oil readings",
            "previous_reading": None,
            "elapsed_hours": None,
        }
        previous = context.previous_reading
        if previous is not None:
            details["previous_reading"] = {
                "reading_id": previous.reading_id,
                "timestamp": utc(previous.timestamp).isoformat(),
                "oil_level": previous.data.get("oil_level"),
            }
            try:
                prior_level = finite_oil_level(previous.data.get("oil_level"))
            except FormulaInputError:
                details["trend_unavailable_reason"] = "Previous oil_level is not a finite number"
            else:
                if context.timestamp is None:
                    details["trend_unavailable_reason"] = "Current timestamp is unavailable"
                else:
                    hours = (utc(context.timestamp) - utc(previous.timestamp)).total_seconds() / 3600
                    details["elapsed_hours"] = hours
                    if hours <= 0:
                        details["trend_unavailable_reason"] = "Historical time interval must be positive"
                    else:
                        rate = (current - prior_level) / hours
                        if not math.isfinite(rate):
                            details["trend_unavailable_reason"] = "Oil-level rate is not finite"
                        else:
                            details["oil_level_rate"] = rate
                            details["trend_direction"] = (
                                "declining" if rate < 0 else "increasing" if rate > 0 else "stable"
                            )
                            details["trend_unavailable_reason"] = None
                            if rate != 0 and minimum is not None:
                                t_min = (current - minimum) / abs(rate)
                                if math.isfinite(t_min):
                                    details["estimated_t_min"] = t_min
        return FormulaResult(status=EvaluationStatus.CALCULATED, value=current, details=details)
