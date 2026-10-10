"""Read-only adapter from backend repository records to agent evidence."""

from collections.abc import Sequence
from datetime import datetime, timedelta, timezone
import re
from typing import Any

from app.agent.models import AgentEvent, AgentEvidence, EvidenceBundle, EvidenceKind


class BackendEvidenceProvider:
    """Retrieve transformer-scoped backend evidence without recalculating anything.

    The repository is injected so this adapter remains independent of HTTP routes,
    database configuration, and any future backend endpoint contract.
    """

    _instruction_pattern = re.compile(
        r"(?:ignore|disregard)\s+(?:(?:all|the|any|previous)\s+){0,2}instructions|"
        r"system\s+prompt|reveal\s+secrets|shut\s+down\s+the\s+transformer",
        re.IGNORECASE,
    )

    def __init__(
        self,
        repository: Any,
        *,
        stale_after: timedelta | None = None,
        page_size: int = 50,
    ):
        if not 1 <= page_size <= 100:
            raise ValueError("page_size must be between 1 and 100")
        self.repository = repository
        self.stale_after = stale_after
        self.page_size = page_size

    def retrieve(self, event: AgentEvent, *, now: datetime | None = None) -> EvidenceBundle:
        """Return only readings and alerts queried for ``event.transformer_id``."""
        transformer_id = event.transformer_id
        readings = self.repository.readings(
            transformer_id, limit=self.page_size + 1, offset=0
        )
        alerts = self.repository.alerts(
            transformer_id=transformer_id, limit=self.page_size + 1, offset=0
        )
        history_query = getattr(self.repository, "maintenance_history", None)
        maintenance_history = (
            history_query(transformer_id, limit=self.page_size + 1, offset=0)
            if history_query is not None else []
        )
        # Keep the evidence boundary defensive even if a repository implementation
        # accidentally returns rows outside the requested scope.  The repository
        # query is an optimization; it is not an authorization guarantee.
        readings = [item for item in readings if getattr(item, "transformer_id", None) == transformer_id]
        alerts = [item for item in alerts if getattr(item, "transformer_id", None) == transformer_id]
        maintenance_history = [
            item for item in maintenance_history
            if getattr(item, "transformer_id", None) == transformer_id
        ]
        readings_complete = len(readings) <= self.page_size
        alerts_complete = len(alerts) <= self.page_size
        history_complete = len(maintenance_history) <= self.page_size
        readings = list(readings[:self.page_size])
        alerts = list(alerts[:self.page_size])
        maintenance_history = list(maintenance_history[:self.page_size])
        retrieval_complete = readings_complete and alerts_complete and history_complete

        items = [self._reading_evidence(reading) for reading in readings]
        items.extend(self._alert_evidence(alert) for alert in alerts)
        items.extend(self._maintenance_evidence(record) for record in maintenance_history)
        now_utc = _as_utc(now or datetime.now(timezone.utc))
        stale_ids = [
            item.source_id
            for item in items
            if self.stale_after is not None
            and item.observed_at is not None
            and now_utc - item.observed_at > self.stale_after
        ]
        conflicting_ids = self._conflicts(items)

        warnings: list[str] = []
        if not readings:
            warnings.append("No backend telemetry readings are available for this transformer.")
        if not alerts:
            warnings.append("No backend alert history is available for this transformer.")
        if history_query is None:
            warnings.append("Backend maintenance-history records are unavailable from this adapter.")
        elif not maintenance_history:
            warnings.append("No backend maintenance-history records are available for this transformer.")
        warnings.append("Health Engine and ML outputs are unavailable from this adapter.")
        if stale_ids:
            warnings.append("Some backend evidence is stale under the configured policy.")
        if conflicting_ids:
            warnings.append("Conflicting backend evidence was returned; do not resolve it by guessing.")
        if any(
            item.kind == EvidenceKind.MEASUREMENT
            and item.content.get("data_quality") == "missing"
            for item in items
        ):
            warnings.append("Some backend readings contain no usable measurements; absence is not treated as healthy.")
        if any(item.kind == EvidenceKind.ALERT and not item.supports_factual_claims for item in items):
            warnings.append("Some backend alert records are malformed and cannot support factual claims.")
        if not retrieval_complete:
            warnings.append(
                "Backend evidence retrieval is limited to a bounded window; the complete history was not reviewed."
            )
        if any(item.untrusted_instruction_detected for item in items):
            warnings.append("Retrieved backend text contains instruction-like content and is treated as untrusted data.")

        return EvidenceBundle(
            transformer_id=transformer_id,
            items=items,
            warnings=warnings,
            stale_source_ids=stale_ids,
            conflicting_source_ids=conflicting_ids,
            retrieval_complete=retrieval_complete,
        )

    def maintenance_history_evidence(
        self, transformer_id: str, *, max_records: int = 1000, batch_size: int = 100,
    ) -> tuple[list[AgentEvidence], bool]:
        """Read up to ``max_records`` maintenance records via the existing repository query.

        Returns (evidence, complete); complete is False when more records exist.
        """
        records: list[Any] = []
        offset = 0
        while len(records) <= max_records:
            batch = [
                record for record in self.repository.maintenance_history(
                    transformer_id, limit=batch_size, offset=offset
                )
                if getattr(record, "transformer_id", None) == transformer_id
            ]
            records.extend(batch)
            if len(batch) < batch_size:
                break
            offset += batch_size
        complete = len(records) <= max_records
        return [self._maintenance_evidence(record) for record in records[:max_records]], complete

    @classmethod
    def _reading_evidence(cls, reading: Any) -> AgentEvidence:
        transformer_id = _required_text(reading, "transformer_id", "reading")
        raw_data = getattr(reading, "raw_data", None)
        measurements = dict(raw_data) if isinstance(raw_data, dict) else {}
        usable = _usable_measurements(measurements)
        data_quality = "valid" if usable else "missing"
        observed_at = _as_utc(getattr(reading, "timestamp", None))
        source_id = _source_id("reading", getattr(reading, "id", None))
        return AgentEvidence(
            source_id=source_id,
            transformer_id=transformer_id,
            kind=EvidenceKind.MEASUREMENT,
            observed_at=observed_at,
            content={
                "measurements": measurements,
                "units": {},
                "data_quality": data_quality,
            },
            source_label="backend_sensor_reading",
            is_synthetic=False,
            supports_factual_claims=usable,
            untrusted_fields=_string_paths(measurements, "measurements"),
        )

    @classmethod
    def _alert_evidence(cls, alert: Any) -> AgentEvidence:
        transformer_id = _required_text(alert, "transformer_id", "alert")
        details = getattr(alert, "details", None)
        formula_inputs = getattr(alert, "formula_inputs", None)
        content = {
            "transformer_id": getattr(alert, "transformer_id", None),
            "timestamp": _as_utc(getattr(alert, "timestamp", None)),
            "parameter": getattr(alert, "parameter", None),
            "formula_name": getattr(alert, "formula_name", None),
            "formula_value": getattr(alert, "formula_value", None),
            "threshold": getattr(alert, "threshold", None),
            "comparison_operator": getattr(alert, "comparison_operator", None),
            "severity": getattr(alert, "severity", None),
            "message": getattr(alert, "message", None),
            "maintenance_type": _enum_value(getattr(alert, "maintenance_type", None)),
            "details": details if isinstance(details, dict) else {},
            "formula_inputs": formula_inputs if isinstance(formula_inputs, dict) else {},
            "acknowledged": getattr(alert, "acknowledged", None),
            "resolved": getattr(alert, "resolved", None),
        }
        text = " ".join(
            value for value in (content["message"], repr(content["details"])) if isinstance(value, str)
        )
        untrusted = bool(cls._instruction_pattern.search(text))
        malformed_fields = _missing_alert_fields(alert)
        untrusted_fields = ["message", "details"]
        if isinstance(formula_inputs, dict):
            untrusted_fields.extend(_string_paths(formula_inputs, "formula_inputs"))
        return AgentEvidence(
            source_id=_source_id("alert", getattr(alert, "id", None)),
            transformer_id=transformer_id,
            kind=EvidenceKind.ALERT,
            observed_at=content["timestamp"],
            content=content,
            source_label="backend_alert",
            is_synthetic=False,
            supports_factual_claims=not malformed_fields and not untrusted,
            untrusted_instruction_detected=untrusted,
            untrusted_fields=untrusted_fields,
        )

    @classmethod
    def _maintenance_evidence(cls, record: Any) -> AgentEvidence:
        transformer_id = _required_text(record, "transformer_id", "maintenance record")
        details = getattr(record, "details", None)
        notes = getattr(record, "notes", None)
        text = " ".join(value for value in (notes, repr(details)) if isinstance(value, str))
        untrusted = bool(cls._instruction_pattern.search(text))
        content = {
            "record_type": getattr(record, "record_type", None),
            "occurred_at": _as_utc(getattr(record, "occurred_at", None)),
            "details": details if isinstance(details, dict) else {},
            "notes": notes,
        }
        return AgentEvidence(
            source_id=_source_id("maintenance", getattr(record, "id", None)),
            transformer_id=transformer_id,
            kind=EvidenceKind.MAINTENANCE_RECORD,
            observed_at=content["occurred_at"],
            content=content,
            source_label="backend_maintenance_record",
            is_synthetic=False,
            supports_factual_claims=not untrusted,
            untrusted_instruction_detected=untrusted,
            untrusted_fields=["notes", "details"],
        )

    @staticmethod
    def _conflicts(items: Sequence[AgentEvidence]) -> list[str]:
        grouped: dict[tuple[datetime | None, str], set[str]] = {}
        for item in items:
            if item.kind != EvidenceKind.MEASUREMENT:
                continue
            measurements = item.content.get("measurements", {})
            if not isinstance(measurements, dict):
                continue
            for field, value in measurements.items():
                grouped.setdefault((item.observed_at, field), set()).add(repr(value))
        result: list[str] = []
        for (observed_at, field), values in grouped.items():
            if len(values) > 1:
                result.extend(
                    item.source_id
                    for item in items
                    if item.kind == EvidenceKind.MEASUREMENT
                    and item.observed_at == observed_at
                    and field in item.content.get("measurements", {})
                )
        return sorted(set(result))


def _source_id(kind: str, identifier: Any) -> str:
    if isinstance(identifier, bool) or not isinstance(identifier, int) or identifier <= 0:
        raise ValueError(f"backend {kind} record is missing its database identifier")
    return f"{kind}:{identifier}"


def _required_text(record: Any, field: str, kind: str) -> str:
    value = getattr(record, field, None)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"backend {kind} record is missing its {field}")
    return value


def _usable_measurements(measurements: dict[Any, Any]) -> bool:
    return any(
        isinstance(field, str) and field.strip() and value is not None
        for field, value in measurements.items()
    )


def _string_paths(value: Any, prefix: str) -> list[str]:
    if isinstance(value, str):
        return [prefix]
    if isinstance(value, dict):
        paths: list[str] = []
        for field, child in value.items():
            if isinstance(field, str):
                paths.extend(_string_paths(child, f"{prefix}.{field}"))
        return paths
    if isinstance(value, list) and any(isinstance(child, str) for child in value):
        return [prefix]
    return []


def _missing_alert_fields(alert: Any) -> list[str]:
    required = (
        "timestamp", "parameter", "formula_name", "formula_value", "threshold",
        "comparison_operator", "severity", "message", "formula_inputs",
        "acknowledged", "resolved",
    )
    missing = []
    for field in required:
        value = getattr(alert, field, None)
        if value is None or (field == "formula_inputs" and not isinstance(value, dict)):
            missing.append(field)
    return missing


def _as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def _enum_value(value: Any) -> Any:
    return getattr(value, "value", value)
