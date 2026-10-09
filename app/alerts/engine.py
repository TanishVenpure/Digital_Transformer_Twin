"""Comparison primitives only; no configured thresholds or generated alerts."""
import operator
from datetime import datetime
from typing import Any

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
        threshold_configuration: dict[str, Any], formula_inputs: dict[str, Any],
        transformer_id: str, timestamp: datetime,
    ):
        """Future implementation constructs persistable alerts with complete provenance.

        Define approved severity selection, messages, duplicate/clearance policy, and
        threshold configuration validation before enabling this operation.
        """
        raise NotImplementedError("Alert generation policy is not configured")
