"""Measured tank/oil pressure and its signed rate; no threshold decisions or SQL."""
from app.formulas.context import FormulaContext
from app.formulas.iot import IoTFormula, finite, utc


class TransformerPressureMonitoring(IoTFormula):
    name = "transformer_pressure_monitoring"
    display_name = "Transformer Pressure Monitoring"
    parameter = "oil_pressure"
    required_features = ("oil_pressure",)
    optional_features = ("top_oil_temperature", "ambient_temperature")
    predictive = True  # reuse previous-observation context, not pressure forecasting

    def measure(self, data, parameters):
        pressure = finite(data, "oil_pressure")
        return pressure, {
            "current_pressure": pressure,
            **{feature: data[feature] for feature in self.optional_features if feature in data},
        }

    def calculate_with_context(self, data, context):
        context = context or FormulaContext()
        result = super().calculate_with_context(data, context)
        details = result.details
        # Existing trend infrastructure uses actual elapsed hours. Pressure units remain unset.
        details.update({
            "pressure_rate": details.pop("rate", None),
            "pressure_unit": context.unit,
            "pressure_rate_unit": f"{context.unit}/hour" if context.unit else None,
            "rate_time_unit": "hour",
            "previous_pressure": details.get("previous_value"),
            "current_timestamp": utc(context.timestamp).isoformat() if context.timestamp else None,
            "elapsed_time": details.get("elapsed_hours"),
            "elapsed_time_unit": "hour",
        })
        previous = context.previous_reading
        if previous is not None and previous.unit is not None and previous.unit != (context.unit or "unconfigured"):
            details["pressure_rate"] = None
            details["trend_unavailable_reason"] = "Previous pressure unit differs; no implicit conversion is available"
        details.pop("rate_unit", None)
        return result
