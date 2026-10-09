"""IoT 6 and 7: top-oil and winding heating."""
from typing import Any

from app.formulas.base import FormulaInputError
from app.formulas.iot import IoTFormula, finite, max_phase, phase_values


class TopOilTemperature(IoTFormula):
    name = "top_oil_temperature"
    display_name = "Top-Oil Temperature IoT"
    parameter = "top_oil_temperature"
    required_features = ("top_oil_temperature",)
    # Ambient is optional: without it the absolute alarm/trip checks still run.
    optional_features = ("ambient_temperature",)
    predictive = True

    def measure(self, data: dict[str, Any], parameters: dict[str, Any]) -> tuple[float, dict[str, Any]]:
        oil = finite(data, "top_oil_temperature")
        ambient = None
        if "ambient_temperature" in data:
            try:
                ambient = finite(data, "ambient_temperature")
            except FormulaInputError:
                ambient = None
        # Delta T_oil = T_oil - T_amb
        return oil, {
            "top_oil_temperature": oil, "ambient_temperature": ambient,
            "top_oil_rise": oil - ambient if ambient is not None else None,
        }


class WindingTemperature(IoTFormula):
    name = "winding_temperature"
    display_name = "Winding Temperature IoT"
    parameter = "winding_temperature"
    required_features = (
        "winding_temperature_r", "winding_temperature_y", "winding_temperature_b", "ambient_temperature",
    )
    predictive = True

    def measure(self, data: dict[str, Any], parameters: dict[str, Any]) -> tuple[float, dict[str, Any]]:
        windings = phase_values(data, "winding_temperature")
        ambient = finite(data, "ambient_temperature")
        phase = max_phase(windings)
        # T_hot = max(T_R, T_Y, T_B);  Delta T_winding = T_hot - T_amb
        return windings[phase] - ambient, {
            "phase_temperatures": windings, "hottest_phase": phase,
            "hot_temperature": windings[phase], "ambient_temperature": ambient,
        }
