from datetime import date
from typing import Any

from sqlalchemy.orm import Session

from app.alerts.configuration import load_thresholds, OilThresholdRule
from app.alerts.engine import AlertEngine
from app.database.repository import Repository
from app.formulas.base import EvaluationMode, EvaluationStatus, FormulaResult
from app.formulas.registry import formula_registry
from app.formulas.context import FormulaContext, HistoricalReading
from app.processing.ingestion import preview_csv
from app.processing.validation import validate_reading
from app.schemas.sensor import SensorReadingCreate, UploadResponse


class ProcessingService:
    """Shared real/synthetic ingestion; per-reading rules run before one atomic commit.

    Daily aggregation and normalization remain future extension points.
    Missing/invalid rule features do not prevent raw storage or generate alerts.
    """

    def __init__(self, session: Session):
        self.session = session
        self.repository = Repository(session)
        self.thresholds = load_thresholds()
        self.alert_engine = AlertEngine()
        self.batch_evaluations: list[dict[str, FormulaResult]] = []

    def receive_reading(self, reading: SensorReadingCreate):
        reading = validate_reading(reading)
        try:
            stored = self.repository.add_reading(
                transformer_id=reading.transformer_id, timestamp=reading.timestamp,
                raw_data=reading.data,
            )
            results = self.run_formulas(stored.raw_data, timestamp=stored.timestamp, transformer_id=stored.transformer_id, reading_id=stored.id)
            alerts = self.evaluate_thresholds(
                results, data=stored.raw_data, transformer_id=stored.transformer_id,
                timestamp=stored.timestamp,
            )
            if alerts:
                self.repository.store_alerts(alerts)
            self.session.refresh(stored)
            self.session.commit()
        except Exception:
            self.session.rollback()
            raise
        # SQLAlchemy's expire_on_commit=False retains this response without another query.
        stored.evaluations = results
        return stored

    def receive_batch(self, readings: list[SensorReadingCreate]) -> int:
        validated = [validate_reading(reading) for reading in readings]
        rows = [
            {"transformer_id": item.transformer_id, "timestamp": item.timestamp, "raw_data": item.data}
            for item in validated
        ]
        results_by_reading = []
        try:
            needs_history = any(
                formula.history_feature and any(formula.history_feature in item.data for item in validated)
                for formula in formula_registry.all().values()
            )
            inserted = len(validated) if needs_history else self.repository.add_readings(rows)
            for reading in validated:
                stored = self.repository.add_reading(
                    transformer_id=reading.transformer_id, timestamp=reading.timestamp, raw_data=reading.data,
                ) if needs_history else None
                results = self.run_formulas(
                    reading.data, timestamp=reading.timestamp, transformer_id=reading.transformer_id,
                    reading_id=stored.id if stored is not None else None,
                )
                alerts = self.evaluate_thresholds(
                    results, data=reading.data, transformer_id=reading.transformer_id,
                    timestamp=reading.timestamp,
                )
                if alerts:
                    self.repository.store_alerts(alerts)
                results_by_reading.append(results)
            self.session.commit()
        except Exception:
            self.session.rollback()
            raise
        self.batch_evaluations = results_by_reading
        return inserted

    @staticmethod
    def preview_upload(filename: str | None, content: bytes) -> UploadResponse:
        return preview_csv(filename, content)

    def normalize_data(self, data: dict[str, Any]) -> dict[str, Any]:
        """Apply approved column mapping/units later; raw inputs remain preserved."""
        raise NotImplementedError("Normalization is not configured")

    def aggregate_daily_data(self, transformer_id: str, day: date):
        """Invoke an approved aggregator and persist its DailyMetric later."""
        raise NotImplementedError("Daily aggregation is not configured")

    def run_formulas(
        self, data: dict[str, Any], mode: EvaluationMode = EvaluationMode.PER_READING,
        *, timestamp=None, transformer_id=None, reading_id=None,
    ) -> dict[str, FormulaResult]:
        results = {}
        for name, formula in formula_registry.all().items():
            if formula.evaluation_mode != mode:
                continue
            previous = None
            if (formula.history_feature in data and timestamp is not None
                    and reading_id is not None and transformer_id is not None):
                record = self.repository.previous_reading_with_feature(
                    transformer_id, timestamp, formula.history_feature, reading_id,
                )
                if record is not None:
                    previous = HistoricalReading(record.timestamp, record.raw_data, record.id)
            configuration = self.thresholds.get(name)
            context = FormulaContext(
                timestamp=timestamp, previous_reading=previous,
                parameters=configuration.model_dump() if isinstance(configuration, OilThresholdRule) else {},
            )
            results[name] = formula.evaluate(data, context)
        return results

    def evaluate_thresholds(
        self, formula_results: dict[str, FormulaResult], *, data: dict[str, Any],
        transformer_id: str, timestamp,
    ):
        alerts = []
        for name, result in formula_results.items():
            if result.status != EvaluationStatus.CALCULATED:
                continue
            if name not in self.thresholds:
                raise ValueError(f"No threshold configuration for registered formula: {name}")
            formula = formula_registry.get(name)
            if isinstance(self.thresholds[name], OilThresholdRule):
                inputs = {feature: data[feature] for feature in formula.required_features}
                inputs["timestamp"] = timestamp.isoformat()
                inputs["previous_reading"] = result.details.get("previous_reading")
                alerts.extend(self.alert_engine.evaluate_oil(
                    formula_name=name, parameter=formula.parameter, result=result,
                    configuration=self.thresholds[name], formula_inputs=inputs,
                    transformer_id=transformer_id, timestamp=timestamp, test_name=formula.display_name,
                ))
                continue
            alert = self.alert_engine.evaluate(
                formula_name=name, parameter=formula.parameter, calculated_value=result.value,
                threshold_configuration=self.thresholds[name],
                formula_inputs={feature: data[feature] for feature in formula.required_features},
                transformer_id=transformer_id, timestamp=timestamp,
                details=result.details, test_name=formula.display_name,
            )
            result.status = EvaluationStatus.FLAGGED if alert else EvaluationStatus.PASS
            if alert is not None:
                alerts.append(alert)
        return alerts

    def process_completed_day(self, transformer_id: str, day: date):
        """Future daily orchestration; per-reading rules never wait for this stage."""
        raise NotImplementedError("Completed-day processing is not configured")
