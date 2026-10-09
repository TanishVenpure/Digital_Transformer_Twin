"""IoT 3 and 4: transformer loading and voltage deviation."""
from typing import Any

from app.formulas.base import FormulaInputError
from app.formulas.iot import IoTFormula, max_phase, phase_values


class CurrentLoading(IoTFormula):
    name = "current_loading"
    display_name = "Current IoT"
    parameter = "current"
    required_features = ("current_r", "current_y", "current_b")
    required_parameters = ("rated_current",)
    predictive = True

    def measure(self, data: dict[str, Any], parameters: dict[str, Any]) -> tuple[float, dict[str, Any]]:
        rated = float(parameters["rated_current"])
        if rated <= 0:
            raise FormulaInputError("rated_current must be positive")
        currents = phase_values(data, "current")
        phase = max_phase(currents)
        # Loading = I_max / I_rated * 100
        return currents[phase] / rated * 100, {
            "i_max": currents[phase], "max_current_phase": phase, "rated_current": rated,
        }


class VoltageDeviation(IoTFormula):
    name = "voltage_deviation"
    display_name = "Voltage IoT"
    parameter = "voltage"
    required_features = ("voltage_r", "voltage_y", "voltage_b")
    required_parameters = ("nominal_voltage",)
    predictive = True

    def measure(self, data: dict[str, Any], parameters: dict[str, Any]) -> tuple[float, dict[str, Any]]:
        nominal = float(parameters["nominal_voltage"])
        if nominal <= 0:
            raise FormulaInputError("nominal_voltage must be positive")
        voltages = phase_values(data, "voltage")
        # D_i = |V_i - V_N| / V_N * 100
        deviations = {phase: abs(value - nominal) / nominal * 100 for phase, value in voltages.items()}
        phase = max_phase(deviations)
        return deviations[phase], {
            "phase_deviations": deviations, "max_deviation_phase": phase,
            "max_deviation_voltage": voltages[phase],
            "direction": "over" if voltages[phase] > nominal else "under" if voltages[phase] < nominal else "nominal",
            "nominal_voltage": nominal,
        }
