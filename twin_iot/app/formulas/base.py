from abc import ABC, abstractmethod
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field

from app.formulas.context import FormulaContext


class EvaluationMode(str, Enum):
    PER_READING = "PER_READING"
    DAILY = "DAILY"


class EvaluationStatus(str, Enum):
    CALCULATED = "CALCULATED"
    PASS = "PASS"
    FLAGGED = "FLAGGED"
    SKIPPED = "SKIPPED"
    INVALID = "INVALID"


class FormulaResult(BaseModel):
    status: EvaluationStatus
    value: float | None = Field(default=None, allow_inf_nan=False)
    details: dict[str, Any] = Field(default_factory=dict)
    reason: str | None = None
    missing_features: list[str] = Field(default_factory=list)


class FormulaInputError(ValueError):
    """Unavailable or invalid instrument inputs, never a maintenance failure."""


class Formula(ABC):
    name: str
    display_name: str
    parameter: str
    required_features: tuple[str, ...] = ()
    evaluation_mode: EvaluationMode = EvaluationMode.DAILY
    history_feature: str | None = None

    def evaluate(self, data: dict[str, Any], context: FormulaContext | None = None) -> FormulaResult:
        missing = [feature for feature in self.required_features if feature not in data]
        if missing:
            return FormulaResult(
                status=EvaluationStatus.SKIPPED, reason="Missing required features",
                missing_features=missing,
            )
        try:
            return self.calculate_with_context(data, context)
        except FormulaInputError as exc:
            return FormulaResult(status=EvaluationStatus.INVALID, reason=str(exc))

    def calculate_with_context(self, data: dict[str, Any], context: FormulaContext | None) -> FormulaResult:
        return self.calculate(data)

    @abstractmethod
    def calculate(self, data: dict[str, Any]) -> FormulaResult:
        """Calculate diagnostics only; the alert layer owns the threshold decision."""
        raise NotImplementedError
