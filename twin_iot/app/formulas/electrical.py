"""IoT 3 and 4: transformer loading and voltage deviation."""
import math
from decimal import Decimal
from typing import Any

from app.formulas.base import FormulaInputError
from app.formulas.iot import IoTFormula, finite, max_phase, phase_values


class CurrentLoading(IoTFormula):
    name = "current_loading"
    display_name = "Current IoT"
    parameter = "current"
    required_features = ("current_r", "current_y", "current_b")
    required_parameters = ("rated_kva", "rated_voltage_ll")
    predictive = True

    def measure(self, data: dict[str, Any], parameters: dict[str, Any]) -> tuple[float, dict[str, Any]]:
        kva = finite(parameters, "rated_kva")
        voltage = finite(parameters, "rated_voltage_ll")
        if kva <= 0 or voltage <= 0:
            raise FormulaInputError("rated_kva and rated_voltage_ll must be positive")
        # Three-phase LV rated line current; voltage is line-to-line, not phase-to-neutral.
        rated = kva * 1000 / (math.sqrt(3) * voltage)
        if not math.isfinite(rated) or rated <= 0:
            raise FormulaInputError("Calculated rated current must be finite and positive")
        currents = phase_values(data, "current")
        loadings = {phase: abs(current) / rated * 100 for phase, current in currents.items()}
        phase = max_phase(loadings)
        return loadings[phase], {
            "phase_currents": currents, "phase_loading_percent": loadings,
            "i_max": abs(currents[phase]), "max_current_phase": phase,
            "rated_current": rated, "rated_current_unit": "A",
            "rated_kva": kva, "rated_voltage_ll": voltage,
            "rated_voltage_unit": "V", "measurement_side": "LV",
        }


class VoltageDeviation(IoTFormula):
    """LV RMS voltage deviation; retain slot-4 identity for historical provenance."""
    name = "voltage_deviation"
    display_name = "LV Voltage Monitoring"
    parameter = "lv_voltage"
    side = "LV"
    prefix = "lv_voltage"
    required_features = ("lv_voltage_r", "lv_voltage_y", "lv_voltage_b")
    required_parameters = ("rated_voltage_ll", "measurement_type")

    def measure(self, data, parameters):
        rated_ll = finite(parameters, "rated_voltage_ll")
        if rated_ll <= 0:
            raise FormulaInputError("rated_voltage_ll must be positive")
        kind = parameters["measurement_type"]
        if kind not in ("LINE_TO_LINE", "PHASE_TO_NEUTRAL"):
            raise FormulaInputError("measurement_type must be LINE_TO_LINE or PHASE_TO_NEUTRAL")
        nominal = rated_ll if kind == "LINE_TO_LINE" else rated_ll / math.sqrt(3)
        voltages = phase_values(data, self.prefix)
        if any(v < 0 for v in voltages.values()):
            raise FormulaInputError("RMS voltages must be nonnegative")
        # Decimal arithmetic preserves inclusive decimal engineering boundaries (e.g. 420.01 V).
        reference = Decimal(str(nominal))
        deviations = {phase: float((Decimal(str(v)) - reference) / reference * 100)
                      for phase, v in voltages.items()}
        phase = max_phase({p: abs(d) for p, d in deviations.items()})
        over = max_phase(deviations)
        under = min(deviations, key=deviations.get)
        return abs(deviations[phase]), {
            "measurement_side": self.side, "measurement_type": kind, "input_kind": "RMS",
            "rated_voltage_ll": rated_ll, "nominal_voltage": nominal, "voltage_unit": "V",
            "phase_voltages": voltages, "phase_deviations": deviations,
            "max_deviation_phase": phase, "max_deviation_voltage": voltages[phase],
            "direction": "OVERVOLTAGE" if deviations[phase] > 0 else "UNDERVOLTAGE" if deviations[phase] < 0 else "NOMINAL",
            "overvoltage_percent": max(0, deviations[over]), "overvoltage_phase": over,
            "undervoltage_percent": max(0, -deviations[under]), "undervoltage_phase": under,
        }


class HVVoltageDeviation(VoltageDeviation):
    name = "hv_voltage_deviation"
    display_name = "HV Voltage Monitoring"
    parameter = "hv_voltage"
    side = "HV"
    prefix = "hv_voltage"
    required_features = ("hv_voltage_r", "hv_voltage_y", "hv_voltage_b")
