"""Shared numerical calculation only; decisions stay in the existing AlertEngine."""
import math
from typing import Any

from app.formulas.base import EvaluationStatus, FormulaInputError, FormulaResult


def calculate_phase_instrument_error(
    data: dict[str, Any], required_features: tuple[str, ...],
) -> FormulaResult:
    values: dict[str, float] = {}
    for feature in required_features:
        value = data.get(feature)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise FormulaInputError(f"{feature} must be a finite number")
        try:
            values[feature] = float(value)
        except (OverflowError, ValueError) as exc:
            raise FormulaInputError(f"{feature} must be a finite number") from exc
        if not math.isfinite(values[feature]):
            raise FormulaInputError(f"{feature} must be a finite number")
    reference_feature = required_features[0]
    reference = values[reference_feature]
    if reference == 0:
        raise FormulaInputError(f"{reference_feature} must be nonzero")
    errors = {
        phase: abs(values[feature] - reference) / abs(reference) * 100
        for phase, feature in zip(("R", "Y", "B"), required_features[1:])
    }
    if not all(math.isfinite(error) for error in errors.values()):
        raise FormulaInputError("Phase error calculation is not finite")
    # Preserve Rule 1's deterministic tie policy: first maximum in R, Y, B order.
    phase = max(errors, key=errors.get)
    return FormulaResult(
        status=EvaluationStatus.CALCULATED, value=errors[phase],
        details={"phase_errors": errors, "max_deviation_phase": phase},
    )
