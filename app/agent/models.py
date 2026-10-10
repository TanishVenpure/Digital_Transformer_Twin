from datetime import datetime, timezone
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


class EvidenceKind(StrEnum):
    MEASUREMENT = "measurement"
    MONITORING_RESULT = "monitoring_result"
    HEALTH_ENGINE = "health_engine"
    MODEL_OUTPUT = "model_output"
    ALERT = "alert"
    MAINTENANCE_RECORD = "maintenance_record"
    APPROVED_PROCEDURE = "approved_procedure"
    # Deterministic engine output derived from cited maintenance records.
    RISK_ASSESSMENT = "risk_assessment"


class MonitoringModule(StrEnum):
    CURRENT_SENSOR_HEALTH = "current_sensor_health"
    VOLTAGE_SENSOR_HEALTH = "voltage_sensor_health"
    CURRENT_HEALTH = "current_health"
    VOLTAGE_HEALTH = "voltage_health"
    OIL_LEVEL_HEALTH = "oil_level_health"
    TOP_OIL_HEALTH = "top_oil_health"
    WINDING_HEALTH = "winding_health"


class MonitoringStatus(StrEnum):
    NORMAL = "NORMAL"
    WARNING = "WARNING"
    CRITICAL = "CRITICAL"
    MAINTENANCE_REQUIRED = "MAINTENANCE_REQUIRED"
    PREDICTED_MAINTENANCE = "PREDICTED_MAINTENANCE"


class MonitoringResult(BaseModel):
    """External monitoring result; this foundation does not calculate it."""

    model_config = ConfigDict(extra="forbid")

    transformer_id: str = Field(min_length=1, max_length=200)
    source_id: str = Field(min_length=1, max_length=200)
    module: MonitoringModule
    status: MonitoringStatus
    timestamp: datetime
    measurements: dict[str, Any] = Field(default_factory=dict)
    calculations: dict[str, Any] = Field(default_factory=dict)
    thresholds: dict[str, Any] = Field(default_factory=dict)
    units: dict[str, str] = Field(default_factory=dict)
    reason: str = Field(min_length=1, max_length=1000)
    prediction_available: bool = False
    data_quality: str = Field(min_length=1, max_length=50)
    source: str = Field(min_length=1, max_length=100)
    synthetic: bool = False


class MaintenanceStatus(StrEnum):
    GENERATED = "generated"
    REVIEW_REQUIRED = "review_required"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"
    AGENT_UNAVAILABLE = "agent_unavailable"
    REJECTED_INVALID_INPUT = "rejected_invalid_input"


class AgentEvent(BaseModel):
    """Validated event envelope; payload semantics remain integration-owned."""

    model_config = ConfigDict(extra="forbid")

    event_id: str = Field(min_length=1, max_length=200)
    transformer_id: str = Field(min_length=1, max_length=200)
    timestamp: datetime
    payload: dict[str, Any] = Field(default_factory=dict)
    model_outputs: dict[str, Any] | None = None
    health_engine_output: dict[str, Any] | None = None
    ml_predictions: dict[str, Any] | None = None
    monitoring_results: list[MonitoringResult] = Field(default_factory=list, max_length=7)
    synthetic: bool = False

    @field_validator("timestamp")
    @classmethod
    def timestamp_is_utc(cls, value: datetime) -> datetime:
        return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


class TelemetryReference(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_id: str = Field(min_length=1, max_length=200)
    transformer_id: str = Field(min_length=1, max_length=200)
    observed_at: datetime
    measurements: dict[str, Any] = Field(default_factory=dict)
    units: dict[str, str] = Field(default_factory=dict)
    data_quality: str = Field(default="valid", min_length=1, max_length=50)
    is_synthetic: bool = False

    @field_validator("observed_at")
    @classmethod
    def observed_at_is_utc(cls, value: datetime) -> datetime:
        return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


class MaintenanceHistoryEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_id: str = Field(min_length=1, max_length=200)
    transformer_id: str = Field(min_length=1, max_length=200)
    record_type: str = Field(min_length=1, max_length=80)
    occurred_at: datetime
    details: dict[str, Any] = Field(default_factory=dict)
    notes: str | None = Field(default=None, max_length=5000)
    is_synthetic: bool = False

    @field_validator("occurred_at")
    @classmethod
    def occurred_at_is_utc(cls, value: datetime) -> datetime:
        return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


class AgentEvidence(BaseModel):
    """A bounded, traceable fact supplied by an authorized data provider."""

    model_config = ConfigDict(extra="forbid")

    source_id: str = Field(min_length=1, max_length=200)
    transformer_id: str = Field(min_length=1, max_length=200)
    kind: EvidenceKind
    observed_at: datetime | None = None
    content: dict[str, Any] = Field(default_factory=dict)
    source_label: str | None = Field(default=None, max_length=200)
    is_synthetic: bool = False
    supports_factual_claims: bool = True
    untrusted_instruction_detected: bool = False
    untrusted_fields: list[str] = Field(default_factory=list, max_length=100)


class EvidenceBundle(BaseModel):
    model_config = ConfigDict(extra="forbid")

    transformer_id: str = Field(min_length=1, max_length=200)
    items: list[AgentEvidence] = Field(default_factory=list, max_length=100)
    warnings: list[str] = Field(default_factory=list, max_length=20)
    stale_source_ids: list[str] = Field(default_factory=list, max_length=100)
    conflicting_source_ids: list[str] = Field(default_factory=list, max_length=100)
    retrieval_complete: bool = True

    @field_validator("items")
    @classmethod
    def evidence_belongs_to_transformer(cls, items: list[AgentEvidence], info):
        transformer_id = info.data.get("transformer_id")
        if transformer_id and any(item.transformer_id != transformer_id for item in items):
            raise ValueError("evidence contains a different transformer_id")
        return items


class MaintenanceRecommendation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    check: str = Field(min_length=1, max_length=500)
    reason: str = Field(min_length=1, max_length=1000)
    source_ids: list[str] = Field(default_factory=list, max_length=20)
    priority: str = Field(min_length=1, max_length=50)


class EvidenceAssertion(BaseModel):
    """Machine-checkable part of a fact; prose remains semantically unverified."""

    model_config = ConfigDict(extra="forbid")

    source_id: str = Field(min_length=1, max_length=200)
    field: str = Field(min_length=1, max_length=200)
    expected_value: Any


class SupportedFact(BaseModel):
    model_config = ConfigDict(extra="forbid")

    statement: str = Field(min_length=1, max_length=1000)
    source_ids: list[str] = Field(default_factory=list, max_length=20)
    assertions: list[EvidenceAssertion] = Field(min_length=1, max_length=20)


class AgentResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    event_id: str = Field(min_length=1, max_length=200)
    transformer_id: str = Field(min_length=1, max_length=200)
    status: MaintenanceStatus
    title: str = Field(min_length=1, max_length=200)
    summary: str = Field(min_length=1, max_length=2000)
    facts: list[SupportedFact] = Field(default_factory=list, max_length=20)
    evidence_source_ids: list[str] = Field(default_factory=list, max_length=50)
    recommended_checks: list[MaintenanceRecommendation] = Field(default_factory=list, max_length=10)
    requires_engineer_review: bool = True
    deterministic_status: MonitoringStatus | None = None


class ChatRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    transformer_id: str = Field(min_length=1, max_length=200)
    question: str = Field(min_length=1, max_length=2000)


class RiskCategory(StrEnum):
    HIGH_MAINTENANCE_RISK = "HIGH_MAINTENANCE_RISK"
    MODERATE_MAINTENANCE_RISK = "MODERATE_MAINTENANCE_RISK"
    # Few serious events are documented; never a statement that the asset is safe.
    LOW_RECORDED_MAINTENANCE_RISK = "LOW_RECORDED_MAINTENANCE_RISK"
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"


class RiskFactorName(StrEnum):
    RECURRENCE = "recurrence"
    SEVERITY = "severity"
    RECENCY = "recency"
    REPAIR_OUTCOME = "repair_outcome"
    FREQUENCY_TREND = "frequency_trend"


class AssessmentCompleteness(StrEnum):
    COMPLETE = "COMPLETE"
    PROVISIONAL = "PROVISIONAL"
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"


class RiskLimitationCode(StrEnum):
    NO_MAINTENANCE_HISTORY = "NO_MAINTENANCE_HISTORY"
    NO_FAULT_RECORDS = "NO_FAULT_RECORDS"
    HISTORY_TRUNCATED = "HISTORY_TRUNCATED"
    OTHER_TRANSFORMER_EXCLUDED = "OTHER_TRANSFORMER_EXCLUDED"
    SYNTHETIC_EXCLUDED = "SYNTHETIC_EXCLUDED"
    UNTRUSTED_EXCLUDED = "UNTRUSTED_EXCLUDED"
    MISSING_TIMESTAMP = "MISSING_TIMESTAMP"
    FUTURE_DATED_EXCLUDED = "FUTURE_DATED_EXCLUDED"
    INVALID_FIELD = "INVALID_FIELD"
    UNKNOWN_SEVERITY_LABEL = "UNKNOWN_SEVERITY_LABEL"
    MISSING_SEVERITY = "MISSING_SEVERITY"
    MISSING_COMPONENT_OR_FAULT_CODE = "MISSING_COMPONENT_OR_FAULT_CODE"
    UNKNOWN_REPAIR_OUTCOME = "UNKNOWN_REPAIR_OUTCOME"
    CONFLICTING_FIELDS = "CONFLICTING_FIELDS"
    DUPLICATE_RECORDS = "DUPLICATE_RECORDS"
    FACTOR_UNAVAILABLE = "FACTOR_UNAVAILABLE"
    TREND_INSUFFICIENT_HISTORY = "TREND_INSUFFICIENT_HISTORY"
    INSUFFICIENT_COVERAGE = "INSUFFICIENT_COVERAGE"
    POLICY_NOT_APPROVED = "POLICY_NOT_APPROVED"


class ReviewFlagCode(StrEnum):
    POLICY_NOT_APPROVED = "POLICY_NOT_APPROVED"
    UNRESOLVED_SERIOUS_ISSUE = "UNRESOLVED_SERIOUS_ISSUE"
    CONFLICTING_REPAIR_STATUS = "CONFLICTING_REPAIR_STATUS"
    CONTRADICTORY_RECORD_FIELDS = "CONTRADICTORY_RECORD_FIELDS"
    UNVERIFIABLE_RECORD = "UNVERIFIABLE_RECORD"


class RiskFactorResult(BaseModel):
    """One factor: its 0-100 score (None when unavailable) and how it was derived."""

    model_config = ConfigDict(extra="forbid")

    name: RiskFactorName
    weight: float = Field(ge=0, le=100)
    available: bool
    score: float | None = Field(default=None, ge=0, le=100)
    coverage: float = Field(ge=0, le=1)
    contributing_source_ids: list[str] = Field(default_factory=list)
    method: str = Field(min_length=1, max_length=1000)
    unavailable_reason: str | None = Field(default=None, max_length=500)


class RiskLimitation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: RiskLimitationCode
    detail: str = Field(min_length=1, max_length=500)
    source_ids: list[str] = Field(default_factory=list)


class ReviewFlag(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: ReviewFlagCode
    detail: str = Field(min_length=1, max_length=500)
    source_ids: list[str] = Field(default_factory=list)


class MaintenanceRiskAssessment(BaseModel):
    """Deterministic, policy-versioned summary of documented maintenance history.

    The index is a relative 0-100 summary under an explicitly versioned policy; it
    is not a failure probability and never authorizes any operational decision.
    """

    model_config = ConfigDict(extra="forbid")

    transformer_id: str = Field(min_length=1, max_length=200)
    assessment_id: str = Field(min_length=1, max_length=200)
    assessed_at: datetime
    policy_version: str = Field(min_length=1, max_length=100)
    policy_approved: bool
    maintenance_risk_index: float | None = Field(default=None, ge=0, le=100)
    risk_category: RiskCategory
    completeness: AssessmentCompleteness
    category_escalated: bool = False
    factor_scores: dict[RiskFactorName, float | None]
    factor_weights: dict[RiskFactorName, float]
    factor_coverage: dict[RiskFactorName, float]
    weight_coverage: float = Field(ge=0, le=100)
    evidence_source_ids: list[str] = Field(default_factory=list)
    contributing_factors: list[RiskFactorResult]
    limitations: list[RiskLimitation] = Field(default_factory=list)
    engineering_review_required: bool
    review_flags: list[ReviewFlag] = Field(default_factory=list)
    records_considered: int = Field(ge=0)
    fault_events_considered: int = Field(ge=0)
    history_complete: bool
    synthetic: bool
    methodology_disclaimer: str = Field(min_length=1, max_length=1000)


class Inference(BaseModel):
    """An interpretation drawn from cited evidence; not itself a verified fact."""

    model_config = ConfigDict(extra="forbid")

    statement: str = Field(min_length=1, max_length=1000)
    source_ids: list[str] = Field(min_length=1, max_length=20)


class ChatResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    transformer_id: str
    answer: str = Field(min_length=1, max_length=4000)
    evidence_source_ids: list[str] = Field(default_factory=list, max_length=100)
    warnings: list[str] = Field(default_factory=list, max_length=20)
    synthetic_evidence: bool = True
    # Optional additions; existing clients can ignore them.
    facts: list[SupportedFact] = Field(default_factory=list, max_length=20)
    inferences: list[Inference] = Field(default_factory=list, max_length=10)
    answer_source: str = Field(default="deterministic", pattern=r"^(deterministic|llm|deterministic_fallback)$")
    risk_assessment: MaintenanceRiskAssessment | None = None
