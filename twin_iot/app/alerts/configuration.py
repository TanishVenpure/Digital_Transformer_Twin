from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.alerts.types import MaintenanceType, Signal

PROJECT_ROOT = Path(__file__).resolve().parents[2]
Operator = Literal[">", ">=", "<", "<="]


class ExtraCheck(BaseModel):
    """A secondary threshold on a calculated detail, e.g. top-oil rise over ambient."""
    model_config = ConfigDict(extra="forbid")
    detail: str = Field(min_length=1)
    operator: Operator
    threshold: float = Field(allow_inf_nan=False)
    unit: str = Field(min_length=1)
    signal: Literal[Signal.WARNING, Signal.CRITICAL]
    message: str = Field(min_length=1)


class IoTRule(BaseModel):
    model_config = ConfigDict(extra="forbid")
    iot_slot: int = Field(ge=1, le=7)
    operator: Operator
    unit: str = Field(min_length=1)
    warning: float | None = Field(default=None, allow_inf_nan=False)
    critical: float | None = Field(default=None, allow_inf_nan=False)
    # Measurement-device IoTs report MAINTENANCE_REQUIRED rather than CRITICAL.
    breach_signal: Literal[Signal.CRITICAL, Signal.MAINTENANCE_REQUIRED] = Signal.CRITICAL
    lead_time_hours: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    maintenance_type: MaintenanceType
    warning_message: str | None = None
    critical_message: str = Field(min_length=1)
    predicted_message: str | None = None
    parameters: dict[str, float] = Field(default_factory=dict)
    extra_checks: list[ExtraCheck] = Field(default_factory=list)
    # False = monitored for quality only (alerts + status), never written to the maintenance sheet
    maintenance_sheet: bool = True

    @property
    def worse_when_higher(self) -> bool:
        return self.operator in (">", ">=")

    @model_validator(mode="after")
    def ordered_limits(self):
        if self.warning is not None and self.critical is not None:
            if self.worse_when_higher and self.warning > self.critical:
                raise ValueError("warning must not exceed critical for an upper limit")
            if not self.worse_when_higher and self.warning < self.critical:
                raise ValueError("warning must not be below critical for a lower limit")
        return self


def load_thresholds(path: Path | None = None) -> dict[str, IoTRule]:
    with (path or PROJECT_ROOT / "config" / "thresholds.yaml").open(encoding="utf-8") as stream:
        config = yaml.safe_load(stream)
    if not isinstance(config, dict) or not isinstance(config.get("formulas"), dict):
        raise ValueError("Threshold configuration requires a formulas mapping")
    rules = {name: IoTRule.model_validate(rule) for name, rule in config["formulas"].items()}
    slots = [rule.iot_slot for rule in rules.values()]
    if len(slots) != len(set(slots)):
        raise ValueError("Each IoT must have a unique iot_slot")
    return rules
