from collections.abc import Iterable
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from app.alerts.configuration import IoTRule, load_thresholds
from app.alerts.engine import AlertEngine, IoTEvaluation
from app.formulas.base import EvaluationStatus
from app.formulas.context import FormulaContext, HistoricalReading
from app.formulas.iot import utc
from app.formulas.registry import FormulaRegistry, formula_registry
from app.processing.validation import IssueCode, ValidationConfig, ValidationIssue, load_validation, validate_timestamp, validate_values

# A reading with one of these timestamp issues is rejected entirely.
REJECTING_ISSUES = {IssueCode.DUPLICATE_TIMESTAMP, IssueCode.OUT_OF_ORDER}


class ProcessedReading(BaseModel):
    transformer_id: str
    timestamp: datetime
    accepted: bool
    raw_data: dict[str, Any]
    clean_data: dict[str, float] = Field(default_factory=dict)
    issues: list[ValidationIssue] = Field(default_factory=list)
    evaluations: dict[str, IoTEvaluation] = Field(default_factory=dict)


class ProcessingService:
    """validate -> run every registered IoT -> standardised evaluations.

    History is kept in memory: the previous successfully calculated reading of each
    IoT, per transformer, supplies the rate used for time-to-limit prediction.
    """

    def __init__(
        self, thresholds: dict[str, IoTRule] | None = None, registry: FormulaRegistry = formula_registry,
        validation: ValidationConfig | None = None,
    ):
        self.registry = registry
        self.thresholds = thresholds if thresholds is not None else load_thresholds()
        self.validation = validation if validation is not None else load_validation()
        missing = [name for name in registry.all() if name not in self.thresholds]
        if missing:
            raise ValueError(f"No threshold configuration for registered formulas: {missing}")
        self.alert_engine = AlertEngine()
        self._previous: dict[tuple[str, str], HistoricalReading] = {}
        self._last_timestamp: dict[str, datetime] = {}

    def process(self, transformer_id: str, timestamp: datetime, data: dict[str, Any]) -> ProcessedReading:
        timestamp = utc(timestamp)
        issues = validate_timestamp(timestamp, self._last_timestamp.get(transformer_id), self.validation)
        if any(issue.code in REJECTING_ISSUES for issue in issues):
            return ProcessedReading(transformer_id=transformer_id, timestamp=timestamp, accepted=False,
                                    raw_data=data, issues=issues)
        clean, value_issues = validate_values(data, self.validation)
        self._last_timestamp[transformer_id] = timestamp
        return ProcessedReading(
            transformer_id=transformer_id, timestamp=timestamp, accepted=True, raw_data=data,
            clean_data=clean, issues=issues + value_issues,
            evaluations=self.evaluate(transformer_id, timestamp, clean),
        )

    def evaluate(self, transformer_id: str, timestamp: datetime, data: dict[str, Any]) -> dict[str, IoTEvaluation]:
        evaluations = {}
        for name, formula in self.registry.all().items():
            rule = self.thresholds[name]
            key = (transformer_id, name)
            previous = self._previous.get(key) if formula.predictive else None
            context = FormulaContext(timestamp=timestamp, previous_reading=previous, parameters=rule.parameters)
            result = formula.evaluate(data, context)
            if result.status == EvaluationStatus.CALCULATED and (
                previous is None or utc(timestamp) > utc(previous.timestamp)
            ):
                self._previous[key] = HistoricalReading(timestamp, data)
            features = formula.required_features + formula.optional_features
            evaluations[name] = self.alert_engine.evaluate(
                formula_name=name, display_name=formula.display_name, parameter=formula.parameter,
                result=result, rule=rule, transformer_id=transformer_id, timestamp=timestamp,
                formula_inputs={feature: data[feature] for feature in features if feature in data},
            )
        return evaluations

    def receive_reading(self, transformer_id: str, timestamp: datetime, data: dict[str, Any]) -> dict[str, IoTEvaluation]:
        return self.process(transformer_id, timestamp, data).evaluations

    def replay(self, readings: Iterable[tuple[str, datetime, dict[str, Any]]]) -> list[ProcessedReading]:
        """Process readings in order, e.g. to drive the dashboard timeline."""
        return [self.process(*reading) for reading in readings]

    def reset(self) -> None:
        self._previous.clear()
        self._last_timestamp.clear()
