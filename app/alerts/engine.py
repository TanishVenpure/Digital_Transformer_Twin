"""Configurable threshold comparison and maintenance alert construction."""
import operator
from datetime import datetime
from typing import Any

from app.alerts.configuration import ThresholdRule, OilThresholdRule
from app.formulas.base import FormulaResult, EvaluationStatus
from app.formulas.oil_level import OilOutput
from app.models.alert import Alert

COMPARISONS = {
    ">": operator.gt, ">=": operator.ge, "<": operator.lt, "<=": operator.le,
}


class AlertEngine:
    @staticmethod
    def compare(value: float, threshold: float, comparison_operator: str) -> bool:
        if comparison_operator not in COMPARISONS:
            raise ValueError("Unsupported comparison operator")
        return COMPARISONS[comparison_operator](value, threshold)

    def evaluate(
        self, *, formula_name: str, parameter: str, calculated_value: float,
        threshold_configuration: ThresholdRule | dict[str, Any],
        formula_inputs: dict[str, Any], transformer_id: str, timestamp: datetime,
        details: dict[str, Any] | None = None, test_name: str | None = None,
    ) -> Alert | None:
        rule = ThresholdRule.model_validate(threshold_configuration)
        if not self.compare(calculated_value, rule.threshold, rule.operator):
            return None
        return Alert(
            transformer_id=transformer_id, timestamp=timestamp, parameter=parameter,
            formula_name=formula_name, formula_value=calculated_value,
            threshold=rule.threshold, comparison_operator=rule.operator,
            maintenance_type=rule.maintenance_type, severity=rule.severity,
            message=rule.message, formula_inputs=formula_inputs,
            details={
                **(details or {}), "test_name": test_name or formula_name,
                "threshold_unit": rule.unit,
            },
            acknowledged=False, resolved=False, resolved_at=None,
        )

    def evaluate_oil(
        self, *, formula_name: str, parameter: str, result: FormulaResult,
        configuration: OilThresholdRule, formula_inputs: dict[str, Any],
        transformer_id: str, timestamp: datetime, test_name: str,
    ) -> list[Alert]:
        if not configuration.configured:
            result.status = EvaluationStatus.SKIPPED
            result.reason = "Rule 3 oil thresholds are not fully configured"
            result.details["outputs"] = []
            return []
        details = result.details
        current = details["current_oil_level"]
        t_min = details["estimated_t_min"]
        rate = details["oil_level_rate"]
        conditions = [
            (OilOutput.LOW_OIL_WARNING, current, configuration.warning_oil_level,
             configuration.level_operator, "source_oil_level_units", "WARNING",
             "Oil level crossed the configured warning threshold."),
            (OilOutput.OIL_LEVEL_CRITICAL, current, configuration.minimum_oil_level,
             configuration.level_operator, "source_oil_level_units", "CRITICAL",
             "Oil level crossed the minimum permissible oil-level threshold."),
        ]
        # A signed T_min is preserved, but only a future time while declining
        # represents an upcoming minimum-level breach. Static checks always run.
        if rate is not None and rate < 0 and t_min is not None and t_min >= 0:
            conditions.append((
                OilOutput.PREDICTED_MINIMUM_LEVEL_BREACH, t_min,
                configuration.maintenance_lead_time, configuration.lead_time_operator,
                "hours", "WARNING",
                "Oil level is predicted to reach the minimum level within the configured maintenance lead time.",
            ))
        triggered = [condition for condition in conditions if self.compare(condition[1], condition[2], condition[3])]
        outputs = [condition[0].value for condition in triggered]
        maintenance_required = any(condition[0] in (
            OilOutput.OIL_LEVEL_CRITICAL, OilOutput.PREDICTED_MINIMUM_LEVEL_BREACH,
        ) for condition in triggered)
        if maintenance_required:
            outputs.append(OilOutput.MAINTENANCE_REQUIRED.value)
        if not outputs:
            outputs = [OilOutput.NORMAL.value]
        details["outputs"] = outputs
        details["maintenance_required"] = maintenance_required
        result.status = EvaluationStatus.FLAGGED if triggered else EvaluationStatus.PASS
        alerts = []
        for trigger, value, threshold, comparison, unit, severity, message in triggered:
            alert = self.evaluate(
                formula_name=formula_name, parameter=parameter, calculated_value=value,
                threshold_configuration=ThresholdRule(
                    operator=comparison, threshold=threshold, unit=unit,
                    maintenance_type=configuration.maintenance_type, severity=severity, message=message,
                ),
                formula_inputs=formula_inputs, transformer_id=transformer_id,
                timestamp=timestamp, test_name=test_name,
                details={**details, "trigger_condition": trigger.value},
            )
            if alert is not None:
                alerts.append(alert)
        return alerts
