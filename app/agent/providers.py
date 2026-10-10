from collections.abc import Sequence
from datetime import datetime, timedelta, timezone
import re
from typing import Protocol

from app.agent.models import (
    AgentEvent,
    AgentEvidence,
    EvidenceBundle,
    EvidenceKind,
    MaintenanceHistoryEvidence,
    TelemetryReference,
)


class EvidenceProvider(Protocol):
    """Read-only evidence boundary replaceable by the backend integration."""

    def retrieve(self, event: AgentEvent) -> EvidenceBundle:
        """Return bounded evidence for exactly event.transformer_id."""


class SyntheticEvidenceProvider:
    """Clearly labelled in-memory adapter for local tests and development."""

    # This is an explicit local-fixture capability, not a property of event
    # data. The backend provider intentionally does not expose this capability.
    trusts_supplied_results = True

    _instruction_pattern = re.compile(
        r"(?:ignore|disregard)\s+(?:(?:all|the|any|previous)\s+){0,2}instructions|system\s+prompt|reveal\s+secrets",
        re.IGNORECASE,
    )

    def __init__(
        self,
        evidence: Sequence[AgentEvidence] = (),
        telemetry: Sequence[TelemetryReference] = (),
        maintenance_history: Sequence[MaintenanceHistoryEvidence] = (),
        stale_after: timedelta | None = None,
    ):
        self._evidence = tuple(evidence)
        self._telemetry = tuple(telemetry)
        self._maintenance_history = tuple(maintenance_history)
        self._stale_after = stale_after

    @classmethod
    def demo(cls) -> "SyntheticEvidenceProvider":
        """Return labelled demo telemetry and maintenance history for TX-DEMO-001."""
        current = datetime(2026, 10, 9, 8, 30, tzinfo=timezone.utc)
        return cls(
            telemetry=[
                TelemetryReference(
                    source_id="synthetic-reading-001",
                    transformer_id="TX-DEMO-001",
                    observed_at=current,
                    measurements={"I_A": 120.0, "I_B": 118.0, "I_C": 121.0},
                    units={"I_A": "A", "I_B": "A", "I_C": "A"},
                    is_synthetic=True,
                ),
                TelemetryReference(
                    source_id="synthetic-reading-002",
                    transformer_id="TX-DEMO-001",
                    observed_at=current - timedelta(hours=1),
                    measurements={"oil_level": 72.0, "top_oil_temperature": 61.0},
                    units={"oil_level": "%", "top_oil_temperature": "C"},
                    is_synthetic=True,
                ),
            ],
            maintenance_history=[
                MaintenanceHistoryEvidence(
                    source_id="synthetic-alarm-001",
                    transformer_id="TX-DEMO-001",
                    record_type="alarm",
                    occurred_at=current - timedelta(days=2),
                    details={"description": "Synthetic alarm example"},
                    notes="Synthetic demo alarm; not a field record.",
                    is_synthetic=True,
                ),
                MaintenanceHistoryEvidence(
                    source_id="synthetic-trip-001",
                    transformer_id="TX-DEMO-001",
                    record_type="trip",
                    occurred_at=current - timedelta(days=10),
                    details={"description": "Synthetic trip example"},
                    notes="Synthetic demo trip; requires approved interpretation.",
                    is_synthetic=True,
                ),
            ],
        )

    def retrieve(self, event: AgentEvent, *, now: datetime | None = None) -> EvidenceBundle:
        now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
        selected = [
            item for item in self._evidence if item.transformer_id == event.transformer_id
        ]
        selected.extend(self._telemetry_evidence(event.transformer_id))
        selected.extend(self._history_evidence(event.transformer_id))
        stale_source_ids = [
            item.source_id for item in selected
            if self._stale_after is not None
            and item.observed_at is not None
            and now - item.observed_at.astimezone(timezone.utc) > self._stale_after
        ]
        conflicting_source_ids = self._conflicts(selected)
        warnings = ["Evidence supplied by synthetic provider; not field evidence."] if selected else [
            "No evidence available from synthetic provider."
        ]
        if stale_source_ids:
            warnings.append("Some evidence is stale under the caller-supplied policy.")
        if conflicting_source_ids:
            warnings.append("Conflicting evidence was returned; do not resolve it by guessing.")
        if any(item.untrusted_instruction_detected for item in selected):
            warnings.append("Retrieved notes contain instruction-like text and are treated as untrusted data.")
        if any(item.content.get("data_quality") == "missing" for item in selected):
            warnings.append("Retrieved evidence contains missing measurements; absence is not treated as healthy.")
        return EvidenceBundle(
            transformer_id=event.transformer_id,
            items=selected,
            warnings=warnings,
            stale_source_ids=stale_source_ids,
            conflicting_source_ids=conflicting_source_ids,
        )

    def maintenance_history_evidence(
        self, transformer_id: str, *, max_records: int = 1000,
    ) -> tuple[list[AgentEvidence], bool]:
        """Labelled synthetic maintenance records for the risk engine (always is_synthetic)."""
        result = []
        for item in self._maintenance_history:
            if item.transformer_id != transformer_id:
                continue
            injected = bool(self._instruction_pattern.search(
                " ".join(value for value in (item.notes, repr(item.details)) if value)
            ))
            result.append(AgentEvidence(
                source_id=item.source_id, transformer_id=item.transformer_id,
                kind=EvidenceKind.MAINTENANCE_RECORD, observed_at=item.occurred_at,
                content={"record_type": item.record_type, "occurred_at": item.occurred_at,
                         "details": item.details, "notes": item.notes},
                source_label="synthetic_maintenance_history", is_synthetic=True,
                supports_factual_claims=not injected, untrusted_instruction_detected=injected,
                untrusted_fields=["notes", "details"],
            ))
        return result[:max_records], len(result) <= max_records

    def _telemetry_evidence(self, transformer_id: str) -> list[AgentEvidence]:
        return [AgentEvidence(
            source_id=item.source_id, transformer_id=item.transformer_id,
            kind=EvidenceKind.MEASUREMENT, observed_at=item.observed_at,
            content={"measurements": item.measurements, "units": item.units, "data_quality": item.data_quality},
            source_label="synthetic_telemetry", is_synthetic=True,
        ) for item in self._telemetry if item.transformer_id == transformer_id]

    def _history_evidence(self, transformer_id: str) -> list[AgentEvidence]:
        result = []
        for item in self._maintenance_history:
            if item.transformer_id != transformer_id:
                continue
            injected = bool(self._instruction_pattern.search(item.notes or ""))
            result.append(AgentEvidence(
                source_id=item.source_id, transformer_id=item.transformer_id,
                kind=EvidenceKind.ALERT if item.record_type in {"alarm", "trip"}
                else EvidenceKind.MAINTENANCE_RECORD,
                observed_at=item.occurred_at,
                content={"record_type": item.record_type, "details": item.details},
                source_label="synthetic_maintenance_history", is_synthetic=True,
                supports_factual_claims=not injected,
                untrusted_instruction_detected=injected,
            ))
        return result

    @staticmethod
    def _conflicts(items: Sequence[AgentEvidence]) -> list[str]:
        grouped: dict[tuple[datetime | None, str], set[str]] = {}
        for item in items:
            for key, value in item.content.items():
                grouped.setdefault((item.observed_at, key), set()).add(repr(value))
        source_ids = []
        for (observed_at, key), values in grouped.items():
            if len(values) > 1:
                source_ids.extend(
                    item.source_id for item in items
                    if item.observed_at == observed_at and key in item.content
                )
        return sorted(set(source_ids))
