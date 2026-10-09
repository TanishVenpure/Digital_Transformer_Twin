from enum import Enum


class MaintenanceType(str, Enum):
    SYSTEM_MAINTENANCE = "SYSTEM_MAINTENANCE"
    TRANSFORMER_MAINTENANCE = "TRANSFORMER_MAINTENANCE"


class Signal(str, Enum):
    """Standardised output every IoT produces for the future Health Engine."""
    NORMAL = "NORMAL"
    WARNING = "WARNING"
    CRITICAL = "CRITICAL"
    MAINTENANCE_REQUIRED = "MAINTENANCE_REQUIRED"
    PREDICTED_MAINTENANCE = "PREDICTED_MAINTENANCE"


# Worst-first ranking used to pick one overall status per IoT.
SIGNAL_RANK = {
    Signal.NORMAL: 0, Signal.WARNING: 1, Signal.PREDICTED_MAINTENANCE: 2,
    Signal.MAINTENANCE_REQUIRED: 3, Signal.CRITICAL: 4,
}
