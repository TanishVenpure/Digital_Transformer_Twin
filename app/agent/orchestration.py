"""Deterministic, provider-backed orchestration.

Risk is computed by the deterministic engine; an optional, injected LLM only
explains it in chat and is validated against the evidence before use.
"""

from datetime import datetime, timezone
from typing import Any

from pydantic import ValidationError

from app.agent.flows import EventAlertFlow, MaintenanceChatFlow
from app.agent.guardrails import GuardrailViolation, validate_chat_output, validate_response
from app.agent.llm import LLMClient, LLMUnavailable
from app.agent.maintenance_risk import MaintenanceRiskPolicy, assess_maintenance_risk, assessment_evidence
from app.agent.prompting import build_chat_prompt, parse_chat_output
from app.agent.models import (
    AgentEvent,
    AgentEvidence,
    AgentResponse,
    ChatRequest,
    ChatResponse,
    EvidenceBundle,
    EvidenceKind,
    MaintenanceRiskAssessment,
    MaintenanceStatus,
    MonitoringResult,
    MonitoringStatus,
)
from app.agent.providers import EvidenceProvider


def _event_evidence(event: AgentEvent, *, trusted: bool = False) -> list[AgentEvidence]:
    """Convert only explicitly supplied external results into traceable evidence."""
    results = []
    for result in event.monitoring_results:
        if result.transformer_id != event.transformer_id:
            raise GuardrailViolation("monitoring result transformer_id does not match event")
        results.append(AgentEvidence(
            source_id=result.source_id,
            transformer_id=result.transformer_id,
            kind=EvidenceKind.MONITORING_RESULT,
            observed_at=result.timestamp,
            content=result.model_dump(mode="json"),
            source_label=result.source,
            is_synthetic=result.source.startswith("synthetic"),
            supports_factual_claims=trusted,
        ))
    if event.health_engine_output is not None:
        output = event.health_engine_output
        if isinstance(output, dict) and output.get("source_id") and output.get("transformer_id") == event.transformer_id:
            results.append(AgentEvidence(
                source_id=str(output["source_id"]),
                transformer_id=event.transformer_id,
                kind=EvidenceKind.HEALTH_ENGINE,
                observed_at=event.timestamp,
                content=dict(output),
                source_label="supplied_health_engine",
                is_synthetic=str(output.get("source", "")).startswith("synthetic"),
                supports_factual_claims=trusted,
            ))
    return results


class DeterministicEventAlertOrchestrator:
    """Builds alerts from supplied evidence and deterministic result statuses only."""

    def __init__(self, provider: EvidenceProvider):
        self.provider = provider

    def handle_event(self, event: AgentEvent | dict, evidence: EvidenceBundle | None = None) -> AgentResponse:
        validated_event = AgentEvent.model_validate(event)
        bundle = evidence or self.provider.retrieve(validated_event)
        if bundle.transformer_id != validated_event.transformer_id:
            raise GuardrailViolation("retrieved evidence transformer scope does not match event")

        supplied = _event_evidence(
            validated_event,
            trusted=bool(getattr(self.provider, "trusts_supplied_results", False)),
        )
        if supplied:
            bundle = bundle.model_copy(update={"items": [*bundle.items, *supplied]})
        critical = next(
            (item for item in supplied
             if item.supports_factual_claims
             and item.content.get("status") == MonitoringStatus.CRITICAL.value),
            None,
        )
        status = MaintenanceStatus.REVIEW_REQUIRED if (bundle.items or critical) else MaintenanceStatus.INSUFFICIENT_EVIDENCE
        deterministic_status = MonitoringStatus.CRITICAL if critical else None
        if critical:
            title = "Critical deterministic monitoring result"
            summary = "Verified deterministic evidence supplied a CRITICAL status; qualified engineer review is required."
        elif bundle.items:
            title = "Transformer event requires review"
            summary = "Evidence was retrieved, but this foundation does not infer thresholds, causes, predictions, or maintenance procedures."
        else:
            title = "Insufficient evidence"
            summary = "No transformer-scoped evidence was available for this event."

        source_ids = [item.source_id for item in bundle.items]
        response = AgentResponse(
            event_id=validated_event.event_id,
            transformer_id=validated_event.transformer_id,
            status=status,
            title=title,
            summary=summary,
            evidence_source_ids=source_ids,
            recommended_checks=[],
            requires_engineer_review=True,
            deterministic_status=deterministic_status,
        )
        return validate_response(response, bundle)


class EvidenceBoundMaintenanceChatbot:
    """Answers from bounded evidence only; it cannot create alerts or commands.

    With a ``risk_policy`` the deterministic Maintenance Risk Index is computed
    first and attached unchanged. With an ``llm`` the question is explained from
    that evidence; any LLM failure or citation/consistency violation falls back to
    the deterministic summary, and the computed assessment is preserved either way.
    """

    def __init__(
        self, provider: EvidenceProvider, *, llm: LLMClient | None = None,
        risk_policy: MaintenanceRiskPolicy | None = None, now: datetime | None = None,
    ):
        self.provider = provider
        self.llm = llm
        self.risk_policy = risk_policy
        self.now = now

    def _risk(self, transformer_id: str, bundle: EvidenceBundle, now: datetime):
        history = getattr(self.provider, "maintenance_history_evidence", None)
        if history is not None:
            records, complete = history(transformer_id, max_records=self.risk_policy.max_records)
        else:
            records = [i for i in bundle.items if i.kind == EvidenceKind.MAINTENANCE_RECORD]
            complete = bundle.retrieval_complete
        assessment = assess_maintenance_risk(
            transformer_id, records, self.risk_policy, now=now, history_complete=complete,
        )
        return assessment, records

    def answer(self, request: ChatRequest | dict, evidence: EvidenceBundle | None = None) -> ChatResponse:
        validated_request = ChatRequest.model_validate(request)
        now = self.now or datetime.now(timezone.utc)
        event = AgentEvent(
            event_id="chat-request",
            transformer_id=validated_request.transformer_id,
            timestamp=now,
        )
        bundle = evidence or self.provider.retrieve(event)
        if bundle.transformer_id != validated_request.transformer_id:
            raise GuardrailViolation("retrieved evidence transformer scope does not match chat request")

        assessment = None
        if self.risk_policy is not None:
            assessment, records = self._risk(validated_request.transformer_id, bundle, now)
            if assessment.records_considered:
                known = {item.source_id for item in bundle.items}
                extra = [item for item in records if item.source_id not in known]
                # The assessment item comes first so the 100-item bound can never drop it.
                bundle = bundle.model_copy(update={"items": [
                    assessment_evidence(assessment, records, self.risk_policy), *bundle.items, *extra,
                ][:100]})

        source_ids = [item.source_id for item in bundle.items]
        warnings = list(bundle.warnings)
        if bundle.stale_source_ids:
            warnings.append("Stale evidence is disclosed and is not treated as current status.")
        if bundle.conflicting_source_ids:
            warnings.append("Conflicting evidence is disclosed and is not resolved by guessing.")
        if not bundle.retrieval_complete:
            warnings.append("Only a bounded evidence window was retrieved; this is not a complete history review.")
        if not bundle.items:
            answer = "I cannot answer from evidence: no transformer-scoped telemetry or maintenance history was retrieved."
        else:
            record_types = [
                str(item.content.get("record_type")) for item in bundle.items
                if item.content.get("record_type")
            ]
            kinds = [item.kind.value for item in bundle.items]
            evidence_label = "synthetic" if all(item.is_synthetic for item in bundle.items) else "backend or mixed"
            answer = (
                f"Retrieved {len(bundle.items)} {evidence_label} evidence record(s) for "
                f"{validated_request.transformer_id}. Evidence kinds: {', '.join(kinds)}."
            )
            if record_types:
                answer += f" Historical record types: {', '.join(record_types)}."
            answer += " This is evidence context only; it does not authorize operation, shutdown, repair, or return to service."
        if assessment is not None:
            answer += " " + _risk_summary(assessment)

        response = ChatResponse(
            transformer_id=validated_request.transformer_id,
            answer=answer[:4000],
            evidence_source_ids=source_ids,
            warnings=warnings[:19],
            synthetic_evidence=bool(bundle.items) and all(item.is_synthetic for item in bundle.items),
            answer_source="deterministic",
            risk_assessment=assessment,
        )
        if self.llm is None or not bundle.items:
            return response
        try:
            system, prompt = build_chat_prompt(validated_request.question, bundle, assessment)
            output = parse_chat_output(self.llm.complete(system=system, prompt=prompt))
            validate_chat_output(
                answer=output.answer, facts=output.facts, inferences=output.inferences,
                evidence_source_ids=output.evidence_source_ids, evidence=bundle, assessment=assessment,
            )
        except Exception as exc:  # noqa: BLE001 - any LLM, parsing or validation failure falls back safely
            if isinstance(exc, LLMUnavailable):
                reason = f"The AI explanation was unavailable ({exc.reason})"
            else:
                reason = f"The AI explanation failed evidence validation ({_validation_reason(exc)})"
            return response.model_copy(update={
                "answer_source": "deterministic_fallback",
                "warnings": [*response.warnings, f"{reason}; a deterministic summary is shown instead."][:20],
            })
        # Only the explanation comes from the LLM; the assessment object is the engine's.
        return response.model_copy(update={
            "answer": output.answer,
            "facts": output.facts,
            "inferences": output.inferences,
            "evidence_source_ids": sorted(set(output.evidence_source_ids)),
            "answer_source": "llm",
            "risk_assessment": assessment,
        })


def _validation_reason(exc: Exception) -> str:
    """Name the failed check without echoing model output or evidence values."""
    if isinstance(exc, GuardrailViolation):
        return str(exc)  # fixed internal messages, e.g. "answer contradicts the computed risk index"
    if isinstance(exc, ValidationError):
        first = exc.errors(include_input=False, include_url=False)[0] if exc.errors() else {}
        location = ".".join(str(part) for part in first.get("loc", ())) or "reply"
        return f"reply did not match the required JSON format: {first.get('type', 'invalid')} at {location}"[:200]
    return f"unexpected {type(exc).__name__}"


def _risk_summary(assessment: MaintenanceRiskAssessment) -> str:
    """Deterministic one-paragraph risk summary used without (or instead of) the LLM."""
    if assessment.maintenance_risk_index is None:
        text = (f"Maintenance risk: {assessment.risk_category.value} "
                f"({assessment.records_considered} usable maintenance record(s)).")
    else:
        text = (f"Maintenance Risk Index {assessment.maintenance_risk_index:g}/100, category "
                f"{assessment.risk_category.value}, {assessment.completeness.value.lower()} "
                f"({assessment.weight_coverage:g}% of factor weight available).")
    if assessment.review_flags:
        text += " Engineering review flags: " + ", ".join(f.code.value for f in assessment.review_flags) + "."
    text += (f" Policy {assessment.policy_version} is "
             f"{'approved' if assessment.policy_approved else 'NOT approved'}; the index is not a failure probability.")
    return text
