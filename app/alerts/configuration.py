from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.alerts.types import MaintenanceType
from app.config.settings import PROJECT_ROOT


class ThresholdRule(BaseModel):
    model_config = ConfigDict(extra="forbid")
    operator: Literal[">", ">=", "<", "<="]
    threshold: float = Field(allow_inf_nan=False)
    unit: str = Field(min_length=1)
    maintenance_type: MaintenanceType
    # Operational severity label, separate from maintenance category.
    severity: str = Field(min_length=1)
    message: str = Field(min_length=1)


class OilThresholdRule(BaseModel):
    model_config = ConfigDict(extra="forbid")
    minimum_oil_level: float | None = Field(default=None, allow_inf_nan=False)
    warning_oil_level: float | None = Field(default=None, allow_inf_nan=False)
    maintenance_lead_time: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    time_unit: Literal["hours"] = "hours"
    level_operator: Literal["<", "<="] = "<="
    lead_time_operator: Literal["<", "<="] = "<="
    maintenance_type: Literal[MaintenanceType.TRANSFORMER_MAINTENANCE] = MaintenanceType.TRANSFORMER_MAINTENANCE

    @model_validator(mode="after")
    def ordered_levels(self):
        if self.minimum_oil_level is not None and self.warning_oil_level is not None:
            if self.minimum_oil_level > self.warning_oil_level:
                raise ValueError("minimum_oil_level must not exceed warning_oil_level")
        return self

    @property
    def configured(self) -> bool:
        return all(value is not None for value in (
            self.minimum_oil_level, self.warning_oil_level, self.maintenance_lead_time,
        ))


def load_thresholds(path: Path | None = None) -> dict[str, ThresholdRule | OilThresholdRule]:
    with (path or PROJECT_ROOT / "config" / "thresholds.yaml").open(encoding="utf-8") as stream:
        config = yaml.safe_load(stream)
    if not isinstance(config, dict) or not isinstance(config.get("formulas"), dict):
        raise ValueError("Threshold configuration requires a formulas mapping")
    return {
        name: (OilThresholdRule if name == "oil_level_predictive_maintenance" else ThresholdRule).model_validate(rule)
        for name, rule in config["formulas"].items()
    }
