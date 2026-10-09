from typing import Any

from app.formulas.base import EvaluationMode, Formula, FormulaResult
from app.formulas.instrument_error import calculate_phase_instrument_error


class VoltageInstrumentError(Formula):
    name = "3_phase_voltage_instrument_error"
    display_name = "3-Phase Voltage Instrument Error"
    parameter = "voltage_measurement"
    required_features = (
        "reference_voltage", "measured_voltage_r", "measured_voltage_y", "measured_voltage_b",
    )
    evaluation_mode = EvaluationMode.PER_READING

    def calculate(self, data: dict[str, Any]) -> FormulaResult:
        return calculate_phase_instrument_error(data, self.required_features)
