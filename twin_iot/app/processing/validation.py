"""Input validation before any IoT runs. Rejected values are dropped, never guessed."""
import math
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.alerts.configuration import PROJECT_ROOT


class IssueCode(str, Enum):
    MISSING = "MISSING"
    NOT_NUMERIC = "NOT_NUMERIC"
    NOT_FINITE = "NOT_FINITE"
    OUT_OF_RANGE = "OUT_OF_RANGE"
    NOT_ON_HOUR = "NOT_ON_HOUR"
    DUPLICATE_TIMESTAMP = "DUPLICATE_TIMESTAMP"
    OUT_OF_ORDER = "OUT_OF_ORDER"
    GAP = "GAP"


class ValidationIssue(BaseModel):
    feature: str
    code: IssueCode
    value: Any = None
    message: str


class FeatureRule(BaseModel):
    model_config = ConfigDict(extra="forbid")
    min: float = Field(allow_inf_nan=False)
    max: float = Field(allow_inf_nan=False)
    unit: str = Field(min_length=1)

    @model_validator(mode="after")
    def ordered(self):
        if self.min > self.max:
            raise ValueError("min must not exceed max")
        return self


class ValidationConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_interval_hours: float = Field(default=1, gt=0)
    features: dict[str, FeatureRule]


def load_validation(path: Path | None = None) -> ValidationConfig:
    with (path or PROJECT_ROOT / "config" / "validation.yaml").open(encoding="utf-8") as stream:
        config = yaml.safe_load(stream)
    return ValidationConfig(
        expected_interval_hours=(config.get("readings") or {}).get("expected_interval_hours", 1),
        features=config["features"],
    )


def validate_values(data: dict[str, Any], config: ValidationConfig) -> tuple[dict[str, float], list[ValidationIssue]]:
    """Return only the valid, known features plus one issue per rejected/missing feature."""
    clean: dict[str, float] = {}
    issues: list[ValidationIssue] = []
    for feature, rule in config.features.items():
        if feature not in data or data[feature] is None:
            issues.append(ValidationIssue(feature=feature, code=IssueCode.MISSING, message=f"{feature} is missing"))
            continue
        raw = data[feature]
        try:
            if isinstance(raw, bool):
                raise ValueError
            number = float(raw)
        except (TypeError, ValueError, OverflowError):
            issues.append(ValidationIssue(feature=feature, code=IssueCode.NOT_NUMERIC, value=str(raw),
                                          message=f"{feature} is not a number"))
            continue
        if not math.isfinite(number):
            issues.append(ValidationIssue(feature=feature, code=IssueCode.NOT_FINITE, value=str(raw),
                                          message=f"{feature} is not finite"))
            continue
        if not rule.min <= number <= rule.max:
            issues.append(ValidationIssue(
                feature=feature, code=IssueCode.OUT_OF_RANGE, value=number,
                message=f"{feature}={number:g} {rule.unit} outside [{rule.min:g}, {rule.max:g}]",
            ))
            continue
        clean[feature] = number
    return clean, issues


def validate_timestamp(
    timestamp: datetime, previous: datetime | None, config: ValidationConfig,
) -> list[ValidationIssue]:
    issues = []
    if (timestamp.minute, timestamp.second, timestamp.microsecond) != (0, 0, 0):
        issues.append(ValidationIssue(feature="timestamp", code=IssueCode.NOT_ON_HOUR, value=timestamp.isoformat(),
                                      message="Timestamp is not on an hourly boundary"))
    if previous is not None:
        hours = (timestamp - previous).total_seconds() / 3600
        if hours == 0:
            issues.append(ValidationIssue(feature="timestamp", code=IssueCode.DUPLICATE_TIMESTAMP,
                                          value=timestamp.isoformat(), message="Duplicate timestamp"))
        elif hours < 0:
            issues.append(ValidationIssue(feature="timestamp", code=IssueCode.OUT_OF_ORDER,
                                          value=timestamp.isoformat(), message="Timestamp is earlier than the previous reading"))
        elif hours > config.expected_interval_hours:
            issues.append(ValidationIssue(feature="timestamp", code=IssueCode.GAP, value=hours,
                                          message=f"{hours:g} h gap since the previous reading"))
    return issues
