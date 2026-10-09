from typing import Any

from app.formulas.base import EvaluationMode, Formula, FormulaResult
from app.formulas.instrument_error import calculate_phase_instrument_error


class CurrentInstrumentError(Formula):
    name = "3_phase_current_instrument_error"
    display_name = "3-Phase Current Instrument Error"
    parameter = "current_measurement"
    required_features = (
        "rated_current", "measured_current_r", "measured_current_y", "measured_current_b",
    )
    evaluation_mode = EvaluationMode.PER_READING

    def calculate(self, data: dict[str, Any]) -> FormulaResult:
        return calculate_phase_instrument_error(data, self.required_features)
