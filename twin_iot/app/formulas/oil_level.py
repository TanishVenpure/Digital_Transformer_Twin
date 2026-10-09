"""IoT 5: oil loss / leakage and time until minimum oil level."""
from typing import Any

from app.formulas.iot import IoTFormula, finite


class OilLevel(IoTFormula):
    name = "oil_level"
    display_name = "Oil-Level IoT"
    parameter = "oil_level"
    required_features = ("oil_level",)
    predictive = True

    def measure(self, data: dict[str, Any], parameters: dict[str, Any]) -> tuple[float, dict[str, Any]]:
        level = finite(data, "oil_level")
        return level, {"current_oil_level": level}
