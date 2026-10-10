import json
import re
from collections.abc import Sequence
from datetime import date, datetime
from typing import Any

from app.agent.models import AgentResponse, EvidenceBundle, Inference, MaintenanceRiskAssessment, SupportedFact


class GuardrailViolation(ValueError):
    """Raised when a structured response is not supported by supplied evidence."""


def evidence_json_default(value: Any) -> str:
    """JSON encoding shared by the prompt and the validator (dates as ISO 8601)."""
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return str(value)


def _as_json(value: Any) -> Any:
    """The value exactly as the model is shown it."""
    return json.loads(json.dumps(value, default=evidence_json_default))


def _json_equal(actual: Any, expected: Any) -> bool:
    actual, expected = _as_json(actual), _as_json(expected)
    if isinstance(actual, bool) or isinstance(expected, bool):
        # In Python True == 1; a boolean must match a boolean exactly.
        return isinstance(actual, bool) and isinstance(expected, bool) and actual is expected
    if isinstance(actual, dict) and isinstance(expected, dict):
        return actual.keys() == expected.keys() and all(_json_equal(actual[k], expected[k]) for k in actual)
    if isinstance(actual, list) and isinstance(expected, list):
        return len(actual) == len(expected) and all(_json_equal(a, e) for a, e in zip(actual, expected))
    if type(actual) in (int, float) and type(expected) in (int, float):
        return actual == expected  # JSON has one number type: 2 and 2.0 are equal
    return type(actual) is type(expected) and actual == expected


def _read_field(content: dict, field: str):
    value = content
    for part in field.split("."):
        if not isinstance(value, dict) or part not in value:
            raise KeyError(field)
        value = value[part]
    return value


def validate_response(response: AgentResponse, evidence: EvidenceBundle) -> AgentResponse:
    """Validate transformer scope and citation IDs without judging engineering safety."""

    if response.transformer_id != evidence.transformer_id:
        raise GuardrailViolation("response transformer_id does not match evidence scope")

    available = {item.source_id for item in evidence.items}
    factual_sources = {
        item.source_id for item in evidence.items if item.supports_factual_claims
    }
    cited = set(response.evidence_source_ids)
    if not cited.issubset(available):
        raise GuardrailViolation("response cites an unavailable evidence source")

    _validate_facts(response.facts, evidence, cited)

    for recommendation in response.recommended_checks:
        if not set(recommendation.source_ids).issubset(available):
            raise GuardrailViolation("recommendation cites an unavailable evidence source")
        if not set(recommendation.source_ids).issubset(factual_sources):
            raise GuardrailViolation("recommendation cites untrusted retrieved instructions")

    return response


def _validate_facts(facts: Sequence[SupportedFact], evidence: EvidenceBundle, cited: set[str]) -> None:
    """Every fact assertion must exactly match a field of declared, trusted evidence."""
    factual_sources = {
        item.source_id for item in evidence.items if item.supports_factual_claims
    }
    for fact in facts:
        assertion_sources = {assertion.source_id for assertion in fact.assertions}
        if not assertion_sources.issubset(factual_sources):
            raise GuardrailViolation("fact cites evidence that cannot support factual claims")
        if not assertion_sources.issubset(cited):
            raise GuardrailViolation("fact assertion source is not declared")
        for assertion in fact.assertions:
            source = next(item for item in evidence.items if item.source_id == assertion.source_id)
            where = f"'{_safe_path(assertion.field)}' in {source.kind.value}"
            if any(
                assertion.field == untrusted
                or assertion.field.startswith(untrusted + ".")
                for untrusted in source.untrusted_fields
            ):
                raise GuardrailViolation(f"fact assertion targets untrusted retrieved text: {where}")
            try:
                actual = _read_field(source.content, assertion.field)
            except KeyError as error:
                raise GuardrailViolation(f"fact field is absent from cited evidence: {where}") from error
            if not _json_equal(actual, assertion.expected_value):
                # Values are never included: they may be evidence or model text.
                raise GuardrailViolation(f"fact assertion does not match cited evidence: {where}")


_PATH_CHARS = re.compile(r"[^A-Za-z0-9_:.\-]")


def _safe_path(field: str) -> str:
    """A field path for diagnostics: restricted characters, bounded length, no values."""
    cleaned = _PATH_CHARS.sub("?", field)
    return cleaned if len(cleaned) <= 80 else cleaned[:77] + "..."


# Prose that would overstate what a maintenance-history index can support.
_FORBIDDEN_PROSE = re.compile(
    r"probabilit|likelihood|chance of (?:failure|fault)|\bsafe\b|\bguarantee|"
    r"\b(?:authori[sz]e|approve)[sd]?\b.{0,40}\b(?:operation|shutdown|repair|return to service)",
    re.IGNORECASE,
)
_CATEGORY_PHRASE = re.compile(
    r"\b(high|moderate|medium|low)(?:[- _]recorded)?[- _](?:maintenance[- _])?risk\b", re.IGNORECASE,
)
_INDEX_NUMBER = re.compile(r"\bindex\b\D{0,25}?(\d{1,3}(?:\.\d+)?)", re.IGNORECASE)
_ISO_DATE = re.compile(r"\b\d{4}-\d{2}-\d{2}\b")
_CATEGORY_WORD = {
    "high": "HIGH_MAINTENANCE_RISK", "moderate": "MODERATE_MAINTENANCE_RISK",
    "medium": "MODERATE_MAINTENANCE_RISK", "low": "LOW_RECORDED_MAINTENANCE_RISK",
}


def validate_chat_output(
    *, answer: str, facts: Sequence[SupportedFact], inferences: Sequence[Inference],
    evidence_source_ids: Sequence[str], evidence: EvidenceBundle,
    assessment: MaintenanceRiskAssessment | None,
) -> None:
    """Reject LLM chat output that cites missing evidence or contradicts the computed assessment.

    Structured facts are checked field-by-field; the prose checks are conservative
    pattern checks, so a rejected answer falls back to the deterministic summary.
    """
    available = {item.source_id for item in evidence.items}
    cited = set(evidence_source_ids)
    if not cited.issubset(available):
        raise GuardrailViolation("answer cites an unavailable evidence source")
    for fact in facts:
        if not set(fact.source_ids).issubset(cited):
            raise GuardrailViolation("fact source is not declared")
    _validate_facts(facts, evidence, cited)
    for inference in inferences:
        if not set(inference.source_ids).issubset(cited):
            raise GuardrailViolation("inference cites an undeclared evidence source")
    if _FORBIDDEN_PROSE.search(answer):
        raise GuardrailViolation("answer states a probability, safety or authorization claim")
    known_dates = {
        value for item in evidence.items
        for value in _ISO_DATE.findall(repr(item.content) + " " + str(item.observed_at))
    }
    if not set(_ISO_DATE.findall(answer)).issubset(known_dates):
        raise GuardrailViolation("answer mentions a date absent from the evidence")
    category = assessment.risk_category.value if assessment else None
    for match in _CATEGORY_PHRASE.finditer(answer):
        if _CATEGORY_WORD[match.group(1).lower()] != category:
            raise GuardrailViolation("answer contradicts the computed risk category")
    index = assessment.maintenance_risk_index if assessment else None
    for value in _INDEX_NUMBER.findall(answer):
        if index is None or abs(float(value) - index) > 0.05:
            raise GuardrailViolation("answer contradicts the computed risk index")
    if (assessment and not assessment.policy_approved
            and re.search(r"\bapproved\b", answer, re.IGNORECASE)
            and not re.search(r"\b(?:not|un)[ -]?approved\b", answer, re.IGNORECASE)):
        raise GuardrailViolation("answer describes an unapproved policy as approved")
