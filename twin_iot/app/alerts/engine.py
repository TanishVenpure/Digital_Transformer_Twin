"""Configurable threshold comparison, time-to-limit prediction and alert construction."""
import operator
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from app.alerts.configuration import IoTRule
from app.alerts.types import MaintenanceType, Signal, SIGNAL_RANK
from app.formulas.base import EvaluationStatus, FormulaResult

COMPARISONS = {
    ">": operator.gt, ">=": operator.ge, "<": operator.lt, "<=": operator.le,
}


class Alert(BaseModel):
    transformer_id: str
    timestamp: datetime
    iot_slot: int
    formula_name: str
    parameter: str
    signal: Signal
    value: float
    threshold: float
    comparison_operator: str
    unit: str
    maintenance_type: MaintenanceType
    message: str
    formula_inputs: dict[str, Any] = Field(default_factory=dict)
    details: dict[str, Any] = Field(default_factory=dict)


class IoTEvaluation(BaseModel):
    formula_name: str
    display_name: str
    iot_slot: int
    evaluation_status: EvaluationStatus
    status: Signal | None = None  # None when the IoT could not be evaluated
    signals: list[Signal] = Field(default_factory=list)
    value: float | None = None
    unit: str
    time_to_critical_hours: float | None = None
    reason: str | None = None
    missing_features: list[str] = Field(default_factory=list)
    details: dict[str, Any] = Field(default_factory=dict)
    alerts: list[Alert] = Field(default_factory=list)


class AlertEngine:
    @staticmethod
    def compare(value: float, threshold: float, comparison_operator: str) -> bool:
        if comparison_operator not in COMPARISONS:
            raise ValueError("Unsupported comparison operator")
        return COMPARISONS[comparison_operator](value, threshold)

    @staticmethod
    def time_to_critical(value: float, rate: float | None, rule: IoTRule) -> float | None:
        """T = (limit - value) / rate, only while moving toward a limit not yet reached."""
        if rate is None or rule.critical is None or rate == 0:
            return None
        hours = (rule.critical - value) / rate
        return hours if hours > 0 else None

    def evaluate(
        self, *, formula_name: str, display_name: str, parameter: str, result: FormulaResult,
        rule: IoTRule, formula_inputs: dict[str, Any], transformer_id: str, timestamp: datetime,
    ) -> IoTEvaluation:
        evaluation = IoTEvaluation(
            formula_name=formula_name, display_name=display_name, iot_slot=rule.iot_slot,
            evaluation_status=result.status, value=result.value, unit=rule.unit,
            reason=result.reason, missing_features=result.missing_features, details=result.details,
        )
        if result.status != EvaluationStatus.CALCULATED:
            return evaluation
        value = result.value
        # (signal, compared value, threshold, operator, unit, message)
        triggered: list[tuple[Signal, float, float, str, str, str]] = []
        if rule.warning is not None and self.compare(value, rule.warning, rule.operator):
            triggered.append((Signal.WARNING, value, rule.warning, rule.operator, rule.unit,
                              rule.warning_message or f"{display_name} crossed the warning limit."))
        critical = rule.critical is not None and self.compare(value, rule.critical, rule.operator)
        if critical:
            triggered.append((rule.breach_signal, value, rule.critical, rule.operator, rule.unit, rule.critical_message))
        for check in rule.extra_checks:
            detail = result.details.get(check.detail)
            if detail is not None and self.compare(detail, check.threshold, check.operator):
                triggered.append((check.signal, detail, check.threshold, check.operator, check.unit, check.message))
        t_critical = self.time_to_critical(value, result.details.get("rate"), rule)
        evaluation.time_to_critical_hours = t_critical
        if (not critical and t_critical is not None and rule.lead_time_hours is not None
                and t_critical <= rule.lead_time_hours):
            triggered.append((Signal.PREDICTED_MAINTENANCE, t_critical, rule.lead_time_hours, "<=", "hours",
                              rule.predicted_message or f"{display_name} is predicted to reach its critical limit within the maintenance lead time."))
        signals = list(dict.fromkeys(trigger[0] for trigger in triggered))
        if Signal.CRITICAL in signals:
            signals.append(Signal.MAINTENANCE_REQUIRED)
        evaluation.signals = signals or [Signal.NORMAL]
        evaluation.status = max(evaluation.signals, key=SIGNAL_RANK.get)
        evaluation.evaluation_status = EvaluationStatus.FLAGGED if triggered else EvaluationStatus.PASS
        evaluation.alerts = [
            Alert(
                transformer_id=transformer_id, timestamp=timestamp, iot_slot=rule.iot_slot,
                formula_name=formula_name, parameter=parameter, signal=signal, value=compared,
                threshold=threshold, comparison_operator=comparison, unit=unit,
                maintenance_type=rule.maintenance_type, message=message,
                formula_inputs=formula_inputs, details=result.details,
            )
            for signal, compared, threshold, comparison, unit, message in triggered
        ]
        return evaluation
