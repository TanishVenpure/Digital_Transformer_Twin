"""IoT 6 and 7: top-oil (OTI) and winding (WTI) temperature, absolute alarm / trip settings."""
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
    """Winding temperature indicator (WTI): the protection quantity is the absolute hottest-phase
    winding temperature, T_WTI = max(T_R, T_Y, T_B) in degC, compared with the alarm / trip settings.
    The rise over ambient is kept in the details for the thermal analytics only."""
    name = "winding_temperature"
    display_name = "Winding Temperature (WTI) IoT"
    parameter = "winding_temperature"
    required_features = ("winding_temperature_r", "winding_temperature_y", "winding_temperature_b")
    # Ambient is optional: it only feeds the rise analytics, never the WTI status.
    optional_features = ("ambient_temperature",)
    predictive = True

    def measure(self, data: dict[str, Any], parameters: dict[str, Any]) -> tuple[float, dict[str, Any]]:
        windings = phase_values(data, "winding_temperature")
        phase = max_phase(windings)
        hot = windings[phase]
        ambient = None
        if "ambient_temperature" in data:
            try:
                ambient = finite(data, "ambient_temperature")
            except FormulaInputError:
                ambient = None
        # T_WTI = max(T_R, T_Y, T_B);  analytics only: Delta T_winding = T_WTI - T_amb
        return hot, {
            "phase_temperatures": windings, "hottest_phase": phase,
            "hot_temperature": hot, "ambient_temperature": ambient,
            "winding_rise": hot - ambient if ambient is not None else None,
        }
