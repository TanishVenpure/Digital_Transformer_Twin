"""Deterministic Maintenance Risk Index from documented maintenance history.

Inputs are maintenance-record evidence items (``EvidenceKind.MAINTENANCE_RECORD``)
for one transformer. Structured fields are read only from these ``details`` keys,
each validated strictly; free text (``notes``, ``details.description``) is never
interpreted:

* ``event_id``       stable identifier shared by records of the same event
* ``component``      affected component, e.g. ``bushing``
* ``fault_code``     documented fault code
* ``severity``       a label from the policy ``severity_scale``
* ``repair_outcome`` ``completed`` | ``partial`` | ``unresolved`` | ``unknown``

The policy (config/maintenance_risk_policy.yaml) supplies every weight, scale and
window; it is versioned and explicitly unapproved. No LLM is involved, and the
index is not a failure probability.
"""
from __future__ import annotations

import hashlib
import json
import re
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.agent.models import (
    AgentEvidence,
    AssessmentCompleteness,
    EvidenceKind,
    MaintenanceRiskAssessment,
    ReviewFlag,
    ReviewFlagCode,
    RiskCategory,
    RiskFactorName,
    RiskFactorResult,
    RiskLimitation,
    RiskLimitationCode,
)

POLICY_PATH = Path(__file__).resolve().parents[2] / "config" / "maintenance_risk_policy.yaml"
METHODOLOGY_DISCLAIMER = (
    "Maintenance Risk Index: a relative 0-100 summary of documented maintenance history "
    "under a provisional, versioned policy. It is not a failure probability, is not a "
    "validated engineering standard, and does not authorize operation, shutdown, repair "
    "or return to service. Missing records are not evidence of good condition."
)
_EVENT_ID = re.compile(r"^[A-Za-z0-9_:\-]{1,128}$")
# No "." (field-path separator for citations) and no free-form punctuation.
_CODE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 _/\-]{0,63}$")
_UNKNOWN_OUTCOME = "unknown"
_CONFLICTING_OUTCOME = "conflicting"


class EscalationPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    unresolved_min_severity: str
    unresolved_category_floor: RiskCategory


class MaintenanceRiskPolicy(BaseModel):
    """Versioned, explicitly unapproved scoring policy."""

    model_config = ConfigDict(extra="forbid")

    version: str = Field(min_length=1, max_length=100)
    approved: bool
    weights: dict[RiskFactorName, float]
    fault_record_types: list[str] = Field(min_length=1)
    severity_scale: dict[str, float]
    repair_outcome_scores: dict[str, float]
    unknown_outcome_score: float = Field(ge=0, le=100)
    decay_half_life_days: float = Field(gt=0)
    no_decay_min_severity: str
    recurrence_window_days: float = Field(gt=0)
    recurrence_saturation_repeats: int = Field(ge=1)
    trend_window_days: float = Field(gt=0)
    trend_min_fault_events: int = Field(ge=2)
    min_factor_field_coverage: float = Field(ge=0, le=1)
    min_weight_coverage: float = Field(gt=0, le=100)
    category_bands: dict[RiskCategory, float]
    escalation: EscalationPolicy
    max_records: int = Field(ge=1, le=100_000)

    @model_validator(mode="after")
    def consistent(self):
        if set(self.weights) != set(RiskFactorName) or abs(sum(self.weights.values()) - 100) > 1e-9:
            raise ValueError("weights must cover every factor and sum to 100")
        if any(not 0 <= value <= 100 for value in (
            *self.weights.values(), *self.severity_scale.values(), *self.repair_outcome_scores.values(),
            *self.category_bands.values(),
        )):
            raise ValueError("policy scores, weights and bands must be within 0-100")
        self.severity_scale = {key.casefold(): value for key, value in self.severity_scale.items()}
        self.repair_outcome_scores = {key.casefold(): value for key, value in self.repair_outcome_scores.items()}
        self.fault_record_types = [value.casefold() for value in self.fault_record_types]
        for label in (self.no_decay_min_severity, self.escalation.unresolved_min_severity):
            if label.casefold() not in self.severity_scale:
                raise ValueError(f"severity {label!r} is not in severity_scale")
        if {"completed", "unresolved"} - set(self.repair_outcome_scores):
            raise ValueError("repair_outcome_scores needs completed and unresolved")
        if not set(self.category_bands) <= {
            RiskCategory.HIGH_MAINTENANCE_RISK, RiskCategory.MODERATE_MAINTENANCE_RISK,
        }:
            raise ValueError("category_bands may only define HIGH and MODERATE lower bounds")
        if self.escalation.unresolved_category_floor == RiskCategory.INSUFFICIENT_EVIDENCE:
            raise ValueError("escalation floor must be a scored category")
        return self


@lru_cache
def load_policy(path: Path = POLICY_PATH) -> MaintenanceRiskPolicy:
    with Path(path).open(encoding="utf-8") as stream:
        return MaintenanceRiskPolicy.model_validate(yaml.safe_load(stream))


@dataclass
class _Record:
    source_id: str
    occurred_at: datetime
    record_type: str
    event_id: str | None
    component: str | None
    fault_code: str | None
    severity: str | None
    repair_outcome: str | None


@dataclass
class _Event:
    key: str
    records: list[_Record]
    is_fault: bool = False
    occurred_at: datetime | None = None
    component: str | None = None
    fault_code: str | None = None
    severity: str | None = None
    outcome: str | None = None
    conflicts: list[str] = field(default_factory=list)

    @property
    def source_ids(self) -> list[str]:
        return sorted(record.source_id for record in self.records)

    @property
    def recurrence_keys(self) -> list[str]:
        """An event recurs with others sharing its fault_code OR its component."""
        keys = []
        if self.fault_code:
            keys.append(f"fault_code:{self.fault_code}")
        if self.component:
            keys.append(f"component:{self.component}")
        return keys


class _Builder:
    def __init__(self):
        self.limitations: dict[RiskLimitationCode, tuple[list[str], set[str]]] = {}
        self.flags: dict[ReviewFlagCode, tuple[list[str], set[str]]] = {}

    def limit(self, code: RiskLimitationCode, detail: str, source_ids=()) -> None:
        details, ids = self.limitations.setdefault(code, ([], set()))
        if detail not in details:
            details.append(detail)
        ids.update(source_ids)

    def flag(self, code: ReviewFlagCode, detail: str, source_ids=()) -> None:
        details, ids = self.flags.setdefault(code, ([], set()))
        if detail not in details:
            details.append(detail)
        ids.update(source_ids)

    def limitation_models(self) -> list[RiskLimitation]:
        return [RiskLimitation(code=code, detail=" ".join(details)[:500], source_ids=sorted(ids))
                for code, (details, ids) in sorted(self.limitations.items())]

    def flag_models(self) -> list[ReviewFlag]:
        return [ReviewFlag(code=code, detail=" ".join(details)[:500], source_ids=sorted(ids))
                for code, (details, ids) in sorted(self.flags.items())]


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def _clean_code(value: Any) -> str | None:
    if not isinstance(value, str) or not _CODE.match(value.strip()):
        return None
    return " ".join(value.strip().casefold().split())


def _normalize(item: AgentEvidence, policy: MaintenanceRiskPolicy, out: _Builder) -> _Record:
    details = item.content.get("details")
    details = details if isinstance(details, dict) else {}
    record_type = item.content.get("record_type")
    record_type = record_type.strip().casefold() if isinstance(record_type, str) else ""

    def text_field(name: str, cleaner):
        raw = details.get(name)
        if raw is None:
            return None
        value = cleaner(raw)
        if value is None:
            out.limit(RiskLimitationCode.INVALID_FIELD,
                      f"details.{name} is present but not in the documented format; it was ignored.",
                      [item.source_id])
        return value

    event_id = text_field("event_id", lambda v: v.strip() if isinstance(v, str) and _EVENT_ID.match(v.strip()) else None)
    component = text_field("component", _clean_code)
    fault_code = text_field("fault_code", _clean_code)

    severity = None
    raw_severity = details.get("severity")
    if raw_severity is not None:
        label = raw_severity.strip().casefold() if isinstance(raw_severity, str) else None
        if label in policy.severity_scale:
            severity = label
        else:
            out.limit(RiskLimitationCode.UNKNOWN_SEVERITY_LABEL,
                      "details.severity is not a documented severity label; severity was not inferred.",
                      [item.source_id])

    outcome = None
    raw_outcome = details.get("repair_outcome")
    if raw_outcome is not None:
        label = raw_outcome.strip().casefold() if isinstance(raw_outcome, str) else None
        if label in policy.repair_outcome_scores or label == _UNKNOWN_OUTCOME:
            outcome = None if label == _UNKNOWN_OUTCOME else label
        else:
            out.limit(RiskLimitationCode.INVALID_FIELD,
                      "details.repair_outcome is not a documented outcome; it was treated as unknown.",
                      [item.source_id])

    return _Record(
        source_id=item.source_id, occurred_at=_utc(item.observed_at), record_type=record_type,
        event_id=event_id, component=component, fault_code=fault_code,
        severity=severity, repair_outcome=outcome,
    )


def _single(values: set[str], name: str, event: _Event) -> str | None:
    if len(values) > 1:
        event.conflicts.append(name)
        return None
    return next(iter(values), None)


def _merge(key: str, records: list[_Record], policy: MaintenanceRiskPolicy) -> _Event:
    """Merge records of one event; later outcomes supersede earlier ones."""
    records = sorted(records, key=lambda r: (r.occurred_at, r.source_id))
    event = _Event(key=key, records=records)
    fault_records = [r for r in records if r.record_type in policy.fault_record_types]
    event.is_fault = bool(fault_records)
    event.occurred_at = (fault_records or records)[0].occurred_at
    event.component = _single({r.component for r in records if r.component}, "component", event)
    event.fault_code = _single({r.fault_code for r in records if r.fault_code}, "fault_code", event)
    severities = {r.severity for r in records if r.severity}
    if len(severities) > 1:
        event.conflicts.append("severity")
    # Conservative: the most severe documented label is kept and the conflict reported.
    event.severity = max(severities, key=lambda s: (policy.severity_scale[s], s)) if severities else None
    documented = [r for r in records if r.repair_outcome]
    if documented:
        latest_time = documented[-1].occurred_at
        latest = {r.repair_outcome for r in documented if r.occurred_at == latest_time}
        event.outcome = _CONFLICTING_OUTCOME if len(latest) > 1 else latest.pop()
    return event


def _events(records: list[_Record], policy: MaintenanceRiskPolicy, out: _Builder) -> list[_Event]:
    """Deduplicate: same event_id = one event; otherwise identical documented fields = duplicate."""
    groups: dict[str, list[_Record]] = defaultdict(list)
    for record in records:
        if record.event_id:
            key = f"event:{record.event_id}"
        else:
            key = "fingerprint:" + json.dumps([
                record.record_type, record.occurred_at.isoformat(), record.component,
                record.fault_code, record.severity, record.repair_outcome,
            ])
        groups[key].append(record)
    events = []
    for key, members in sorted(groups.items()):
        if key.startswith("fingerprint:") and len(members) > 1:
            out.limit(RiskLimitationCode.DUPLICATE_RECORDS,
                      "Records with identical documented fields and timestamp were counted once.",
                      [m.source_id for m in members])
        event = _merge(key, members, policy)
        if event.conflicts:
            out.limit(RiskLimitationCode.CONFLICTING_FIELDS,
                      f"Records of one event disagree on: {', '.join(sorted(set(event.conflicts)))}.",
                      event.source_ids)
            out.flag(ReviewFlagCode.CONTRADICTORY_RECORD_FIELDS,
                     "Records describing the same event contain contradictory fields.", event.source_ids)
        if event.outcome == _CONFLICTING_OUTCOME:
            out.flag(ReviewFlagCode.CONFLICTING_REPAIR_STATUS,
                     "Records with the same latest timestamp report different repair outcomes; "
                     "the event is treated as unresolved.", event.source_ids)
        events.append(event)
    return sorted(events, key=lambda e: (e.occurred_at, e.key))


def _decay(age_days: float, policy: MaintenanceRiskPolicy) -> float:
    return 0.5 ** (max(age_days, 0.0) / policy.decay_half_life_days)


def _age_days(now: datetime, moment: datetime) -> float:
    return (now - moment).total_seconds() / 86400


def _unresolved(event: _Event) -> bool:
    return event.outcome in ("unresolved", _CONFLICTING_OUTCOME)


def _factor(name: RiskFactorName, policy: MaintenanceRiskPolicy, *, score: float | None, coverage: float,
            sources, method: str, reason: str | None = None) -> RiskFactorResult:
    available = score is not None
    return RiskFactorResult(
        name=name, weight=policy.weights[name], available=available,
        score=round(score, 2) if available else None, coverage=round(coverage, 4),
        contributing_source_ids=sorted(set(sources)), method=method,
        unavailable_reason=None if available else reason,
    )


def _coverage_gate(name, policy, documented: int, total: int):
    coverage = documented / total if total else 1.0
    if coverage < policy.min_factor_field_coverage:
        return coverage, (f"only {documented} of {total} fault events document the required field; "
                          f"minimum coverage is {policy.min_factor_field_coverage:.0%}")
    return coverage, None


def _recurrence(faults, now, policy) -> RiskFactorResult:
    window = [e for e in faults if _age_days(now, e.occurred_at) <= policy.recurrence_window_days]
    keyed = [e for e in window if e.recurrence_keys]
    method = (f"Faults within {policy.recurrence_window_days:g} days grouped separately by fault_code and by "
              "component; score = min(100, 100 x (largest group size - 1) / "
              f"{policy.recurrence_saturation_repeats}).")
    coverage, reason = _coverage_gate(RiskFactorName.RECURRENCE, policy, len(keyed), len(window))
    if reason:
        return _factor(RiskFactorName.RECURRENCE, policy, score=None, coverage=coverage, sources=[],
                       method=method, reason=reason)
    groups: dict[str, list[_Event]] = defaultdict(list)
    for event in keyed:
        for key in event.recurrence_keys:
            groups[key].append(event)
    repeats = max((len(group) - 1 for group in groups.values()), default=0)
    sources = [sid for group in groups.values() if len(group) > 1 for e in group for sid in e.source_ids]
    score = min(100.0, 100.0 * repeats / policy.recurrence_saturation_repeats)
    return _factor(RiskFactorName.RECURRENCE, policy, score=score, coverage=coverage, sources=sources, method=method)


def _severity(faults, now, policy) -> RiskFactorResult:
    rated = [e for e in faults if e.severity]
    threshold = policy.severity_scale[policy.no_decay_min_severity.casefold()]
    method = ("Max over fault events of severity_scale[label] x 0.5^(age/half_life); "
              f"half_life={policy.decay_half_life_days:g} days; unresolved events at or above "
              f"'{policy.no_decay_min_severity}' are not decayed.")
    coverage, reason = _coverage_gate(RiskFactorName.SEVERITY, policy, len(rated), len(faults))
    if reason:
        return _factor(RiskFactorName.SEVERITY, policy, score=None, coverage=coverage, sources=[],
                       method=method, reason=reason)
    best, sources = 0.0, []
    for event in rated:
        base = policy.severity_scale[event.severity]
        weight = 1.0 if _unresolved(event) and base >= threshold else _decay(_age_days(now, event.occurred_at), policy)
        value = base * weight
        if value > best + 1e-12:
            best, sources = value, list(event.source_ids)
        elif abs(value - best) <= 1e-12 and value > 0:
            sources.extend(event.source_ids)
    return _factor(RiskFactorName.SEVERITY, policy, score=best, coverage=coverage, sources=sources, method=method)


def _recency(faults, now, policy) -> RiskFactorResult:
    method = f"100 x 0.5^(age of most recent fault / {policy.decay_half_life_days:g} days)."
    if not faults:
        return _factor(RiskFactorName.RECENCY, policy, score=0.0, coverage=1.0, sources=[], method=method)
    latest = max(faults, key=lambda e: (e.occurred_at, e.key))
    score = 100.0 * _decay(_age_days(now, latest.occurred_at), policy)
    return _factor(RiskFactorName.RECENCY, policy, score=score, coverage=1.0, sources=latest.source_ids, method=method)


def _repair_outcome(faults, policy) -> RiskFactorResult:
    documented = [e for e in faults if e.outcome]
    method = ("Mean over fault events of repair_outcome_scores[outcome]; conflicting outcomes score as "
              f"unresolved; absent or unknown outcomes score {policy.unknown_outcome_score:g}.")
    coverage, reason = _coverage_gate(RiskFactorName.REPAIR_OUTCOME, policy, len(documented), len(faults))
    if reason:
        return _factor(RiskFactorName.REPAIR_OUTCOME, policy, score=None, coverage=coverage, sources=[],
                       method=method, reason=reason)
    if not faults:
        return _factor(RiskFactorName.REPAIR_OUTCOME, policy, score=0.0, coverage=1.0, sources=[], method=method)
    values, sources = [], []
    for event in faults:
        if event.outcome == _CONFLICTING_OUTCOME:
            value = policy.repair_outcome_scores["unresolved"]
        elif event.outcome:
            value = policy.repair_outcome_scores[event.outcome]
        else:
            value = policy.unknown_outcome_score
        values.append(value)
        if value > 0:
            sources.extend(event.source_ids)
    return _factor(RiskFactorName.REPAIR_OUTCOME, policy, score=sum(values) / len(values),
                   coverage=coverage, sources=sources, method=method)


def _trend(faults, records, now, policy, history_complete, out) -> RiskFactorResult:
    window = timedelta(days=policy.trend_window_days)
    method = (f"Fault count in the latest {policy.trend_window_days:g} days (R) vs the window before (P): "
              "P = 0 -> 100 if R > 0 else 0; otherwise min(100, 50 x R / P).")
    span_days = _age_days(now, min(r.occurred_at for r in records)) if records else 0.0
    reason = None
    if not history_complete:
        reason = "maintenance history retrieval was truncated"
    elif len(faults) < policy.trend_min_fault_events:
        reason = f"fewer than {policy.trend_min_fault_events} fault events are documented"
    elif span_days < 2 * policy.trend_window_days:
        reason = f"documented history spans {span_days:.0f} days; {2 * policy.trend_window_days:g} are required"
    if reason:
        out.limit(RiskLimitationCode.TREND_INSUFFICIENT_HISTORY, f"Frequency trend not calculated: {reason}.")
        return _factor(RiskFactorName.FREQUENCY_TREND, policy, score=None, coverage=0.0, sources=[],
                       method=method, reason=reason)
    recent = [e for e in faults if now - window < e.occurred_at <= now]
    previous = [e for e in faults if now - 2 * window < e.occurred_at <= now - window]
    if not previous:
        score = 100.0 if recent else 0.0
    else:
        score = min(100.0, 50.0 * len(recent) / len(previous))
    sources = [sid for e in recent + previous for sid in e.source_ids]
    return _factor(RiskFactorName.FREQUENCY_TREND, policy, score=score, coverage=1.0, sources=sources, method=method)


def _category(index: float, policy: MaintenanceRiskPolicy) -> RiskCategory:
    for category in (RiskCategory.HIGH_MAINTENANCE_RISK, RiskCategory.MODERATE_MAINTENANCE_RISK):
        bound = policy.category_bands.get(category)
        if bound is not None and index >= bound:
            return category
    return RiskCategory.LOW_RECORDED_MAINTENANCE_RISK


_CATEGORY_RANK = {
    RiskCategory.LOW_RECORDED_MAINTENANCE_RISK: 0,
    RiskCategory.MODERATE_MAINTENANCE_RISK: 1,
    RiskCategory.HIGH_MAINTENANCE_RISK: 2,
}


def _assessment_id(transformer_id, policy, now, records: list[_Record]) -> str:
    fingerprint = json.dumps({
        "transformer_id": transformer_id, "policy": policy.version, "as_of": now.isoformat(),
        "records": [[r.source_id, r.occurred_at.isoformat(), r.record_type, r.event_id, r.component,
                     r.fault_code, r.severity, r.repair_outcome] for r in records],
    }, sort_keys=True)
    return "maintenance-risk:" + hashlib.sha256(fingerprint.encode()).hexdigest()[:24]


def assess_maintenance_risk(
    transformer_id: str,
    items: Sequence[AgentEvidence],
    policy: MaintenanceRiskPolicy,
    *,
    now: datetime,
    history_complete: bool = True,
) -> MaintenanceRiskAssessment:
    """Pure function: identical inputs, policy and ``now`` give identical output."""
    now = _utc(now)
    out = _Builder()
    if not policy.approved:
        out.limit(RiskLimitationCode.POLICY_NOT_APPROVED,
                  f"Policy {policy.version} is a provisional design assumption awaiting engineering approval.")
        out.flag(ReviewFlagCode.POLICY_NOT_APPROVED,
                 "Scoring policy is unapproved; results require engineering review.")
    if not history_complete:
        out.limit(RiskLimitationCode.HISTORY_TRUNCATED,
                  f"Only the most recent {policy.max_records} maintenance records were assessed.")

    candidates = [item for item in items if item.kind == EvidenceKind.MAINTENANCE_RECORD]
    foreign = [item.source_id for item in candidates if item.transformer_id != transformer_id]
    if foreign:
        out.limit(RiskLimitationCode.OTHER_TRANSFORMER_EXCLUDED,
                  "Records for other transformers were excluded.", foreign)
    candidates = [item for item in candidates if item.transformer_id == transformer_id]
    real = [item for item in candidates if not item.is_synthetic]
    synthetic = not real and bool(candidates)
    if real and len(real) != len(candidates):
        out.limit(RiskLimitationCode.SYNTHETIC_EXCLUDED,
                  "Synthetic records were excluded from a real-world assessment.",
                  [i.source_id for i in candidates if i.is_synthetic])
    pool = real or candidates

    usable_items = []
    for item in sorted(pool, key=lambda i: i.source_id):
        if not item.supports_factual_claims or item.untrusted_instruction_detected:
            out.limit(RiskLimitationCode.UNTRUSTED_EXCLUDED,
                      "Records containing instruction-like or unverifiable text were excluded from scoring.",
                      [item.source_id])
            out.flag(ReviewFlagCode.UNVERIFIABLE_RECORD,
                     "A maintenance record could not be used as verified evidence and needs manual review.",
                     [item.source_id])
        elif item.observed_at is None:
            out.limit(RiskLimitationCode.MISSING_TIMESTAMP, "Records without a timestamp were excluded.",
                      [item.source_id])
        elif _utc(item.observed_at) > now:
            out.limit(RiskLimitationCode.FUTURE_DATED_EXCLUDED,
                      "Records dated after the assessment time were excluded.", [item.source_id])
        else:
            usable_items.append(item)

    records = [_normalize(item, policy, out) for item in usable_items]
    events = _events(records, policy, out)
    faults = [event for event in events if event.is_fault]

    if not records:
        out.limit(RiskLimitationCode.NO_MAINTENANCE_HISTORY,
                  "No usable maintenance records were found; absence of records is not evidence of good condition.")
    elif not faults:
        out.limit(RiskLimitationCode.NO_FAULT_RECORDS,
                  "No fault or failure records are documented; this is not evidence of good condition.")
    missing_key = [sid for e in faults if not e.recurrence_keys for sid in e.source_ids]
    if missing_key:
        out.limit(RiskLimitationCode.MISSING_COMPONENT_OR_FAULT_CODE,
                  "Some fault events document neither component nor fault_code.", missing_key)
    missing_severity = [sid for e in faults if not e.severity for sid in e.source_ids]
    if missing_severity:
        out.limit(RiskLimitationCode.MISSING_SEVERITY, "Some fault events have no documented severity.",
                  missing_severity)
    unknown_outcome = [sid for e in faults if not e.outcome for sid in e.source_ids]
    if unknown_outcome:
        out.limit(RiskLimitationCode.UNKNOWN_REPAIR_OUTCOME,
                  "Some fault events have no documented repair outcome.", unknown_outcome)

    if records:
        factors = [
            _recurrence(faults, now, policy),
            _severity(faults, now, policy),
            _recency(faults, now, policy),
            _repair_outcome(faults, policy),
            _trend(faults, records, now, policy, history_complete, out),
        ]
    else:
        factors = [
            _factor(name, policy, score=None, coverage=0.0, sources=[],
                    method="Not calculated.", reason="no usable maintenance records")
            for name in RiskFactorName
        ]
    for factor in factors:
        if not factor.available and factor.name != RiskFactorName.FREQUENCY_TREND and records:
            out.limit(RiskLimitationCode.FACTOR_UNAVAILABLE,
                      f"Factor '{factor.name.value}' not scored: {factor.unavailable_reason}.")

    available = [f for f in factors if f.available]
    weight_coverage = sum(f.weight for f in available)
    index = None
    if records and weight_coverage >= policy.min_weight_coverage:
        index = round(sum(f.weight * f.score for f in available) / weight_coverage, 1)
    elif records:
        out.limit(RiskLimitationCode.INSUFFICIENT_COVERAGE,
                  f"Available factors cover {weight_coverage:g}% of the weight; "
                  f"{policy.min_weight_coverage:g}% is required for an index.")

    serious = policy.severity_scale[policy.escalation.unresolved_min_severity.casefold()]
    unresolved_serious = [
        sid for e in faults if _unresolved(e) and e.severity and policy.severity_scale[e.severity] >= serious
        for sid in e.source_ids
    ]
    if unresolved_serious:
        out.flag(ReviewFlagCode.UNRESOLVED_SERIOUS_ISSUE,
                 f"Unresolved fault documented at or above '{policy.escalation.unresolved_min_severity}' "
                 "severity (proposed escalation rule).", unresolved_serious)

    escalated = False
    if index is None:
        category = RiskCategory.INSUFFICIENT_EVIDENCE
        completeness = AssessmentCompleteness.INSUFFICIENT_EVIDENCE
    else:
        category = _category(index, policy)
        floor = policy.escalation.unresolved_category_floor
        if unresolved_serious and _CATEGORY_RANK[category] < _CATEGORY_RANK[floor]:
            category, escalated = floor, True
        completeness = (AssessmentCompleteness.COMPLETE
                        if len(available) == len(factors) and history_complete
                        else AssessmentCompleteness.PROVISIONAL)

    flags = out.flag_models()
    evidence_ids = sorted({r.source_id for r in records})
    return MaintenanceRiskAssessment(
        transformer_id=transformer_id,
        assessment_id=_assessment_id(transformer_id, policy, now, sorted(records, key=lambda r: r.source_id)),
        assessed_at=now,
        policy_version=policy.version,
        policy_approved=policy.approved,
        maintenance_risk_index=index,
        risk_category=category,
        completeness=completeness,
        category_escalated=escalated,
        factor_scores={f.name: f.score for f in factors},
        factor_weights={f.name: f.weight for f in factors},
        factor_coverage={f.name: f.coverage for f in factors},
        weight_coverage=weight_coverage,
        evidence_source_ids=evidence_ids,
        contributing_factors=factors,
        limitations=out.limitation_models(),
        engineering_review_required=bool(flags),
        review_flags=flags,
        records_considered=len(records),
        fault_events_considered=len(faults),
        history_complete=history_complete,
        synthetic=synthetic,
        methodology_disclaimer=METHODOLOGY_DISCLAIMER,
    )


def assessment_evidence(
    assessment: MaintenanceRiskAssessment, items: Sequence[AgentEvidence], policy: MaintenanceRiskPolicy,
) -> AgentEvidence:
    """Expose the assessment as citable evidence with dict-keyed, assertion-friendly content.

    Record fields are the engine's validated, normalized values (never free text).
    """
    out = _Builder()
    by_id = {item.source_id: item for item in items}
    records = {}
    for source_id in assessment.evidence_source_ids:
        item = by_id.get(source_id)
        if item is None or item.observed_at is None:
            continue
        record = _normalize(item, policy, out)
        records[source_id] = {
            "occurred_at": record.occurred_at.isoformat(), "record_type": record.record_type,
            "event_id": record.event_id, "component": record.component, "fault_code": record.fault_code,
            "severity": record.severity, "repair_outcome": record.repair_outcome or _UNKNOWN_OUTCOME,
        }
    fault_types = set(policy.fault_record_types)
    component_counts: dict[str, int] = defaultdict(int)
    fault_code_counts: dict[str, int] = defaultdict(int)
    for record in records.values():
        if record["record_type"] in fault_types:
            if record["component"]:
                component_counts[record["component"]] += 1
            if record["fault_code"]:
                fault_code_counts[record["fault_code"]] += 1
    content = {
        "maintenance_risk_index": assessment.maintenance_risk_index,
        "risk_category": assessment.risk_category.value,
        "completeness": assessment.completeness.value,
        "policy_version": assessment.policy_version,
        "policy_approved": assessment.policy_approved,
        "engineering_review_required": assessment.engineering_review_required,
        "category_escalated": assessment.category_escalated,
        "records_considered": assessment.records_considered,
        "fault_events_considered": assessment.fault_events_considered,
        "factors": {
            f.name.value: {"score": f.score, "weight": f.weight, "coverage": f.coverage,
                           "available": f.available, "contributing_source_ids": f.contributing_source_ids,
                           "method": f.method, "unavailable_reason": f.unavailable_reason}
            for f in assessment.contributing_factors
        },
        "records": records,
        "component_fault_counts": dict(sorted(component_counts.items())),
        "fault_code_counts": dict(sorted(fault_code_counts.items())),
        "review_flags": {flag.code.value: flag.source_ids for flag in assessment.review_flags},
        "limitations": {item.code.value: item.detail for item in assessment.limitations},
        "methodology_disclaimer": assessment.methodology_disclaimer,
    }
    return AgentEvidence(
        source_id=assessment.assessment_id, transformer_id=assessment.transformer_id,
        kind=EvidenceKind.RISK_ASSESSMENT, observed_at=assessment.assessed_at, content=content,
        source_label="deterministic_maintenance_risk_engine", is_synthetic=assessment.synthetic,
        supports_factual_claims=True,
    )
