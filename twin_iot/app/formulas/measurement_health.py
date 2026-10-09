"""IoT 1 and 2: measurement-device health against a reference instrument."""
from typing import Any

from app.formulas.base import FormulaInputError
from app.formulas.iot import IoTFormula, max_phase, phase_values


class MeasurementHealth(IoTFormula):
    measured_prefix: str
    reference_prefix: str

    def measure(self, data: dict[str, Any], parameters: dict[str, Any]) -> tuple[float, dict[str, Any]]:
        measured = phase_values(data, self.measured_prefix)
        reference = phase_values(data, self.reference_prefix)
        for phase, value in reference.items():
            if value == 0:
                raise FormulaInputError(f"{self.reference_prefix}_{phase.lower()} must be nonzero")
        # E_i = |measured_i - reference_i| / reference_i * 100
        errors = {phase: abs(measured[phase] - reference[phase]) / abs(reference[phase]) * 100 for phase in measured}
        phase = max_phase(errors)
        return errors[phase], {"phase_errors": errors, "max_error_phase": phase}


class CurrentMeasurementHealth(MeasurementHealth):
    name = "current_measurement_health"
    display_name = "Current Measurement Health IoT"
    parameter = "current_measurement"
    measured_prefix = "current"
    reference_prefix = "reference_current"
    required_features = (
        "current_r", "current_y", "current_b",
        "reference_current_r", "reference_current_y", "reference_current_b",
    )


class VoltageMeasurementHealth(MeasurementHealth):
    name = "voltage_measurement_health"
    display_name = "Voltage Measurement Health IoT"
    parameter = "voltage_measurement"
    measured_prefix = "voltage"
    reference_prefix = "reference_voltage"
    required_features = (
        "voltage_r", "voltage_y", "voltage_b",
        "reference_voltage_r", "reference_voltage_y", "reference_voltage_b",
    )
