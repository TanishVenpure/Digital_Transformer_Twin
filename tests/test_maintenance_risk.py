"""Deterministic Maintenance Risk Index engine (app/agent/maintenance_risk.py)."""
from datetime import datetime, timedelta, timezone

import pytest

from app.agent.maintenance_risk import (
    METHODOLOGY_DISCLAIMER,
    MaintenanceRiskPolicy,
    assess_maintenance_risk,
    assessment_evidence,
    load_policy,
)
from app.agent.models import (
    AgentEvidence,
    AssessmentCompleteness,
    EvidenceKind,
    ReviewFlagCode,
    RiskCategory,
    RiskFactorName as F,
    RiskLimitationCode as L,
)

NOW = datetime(2026, 10, 9, 12, tzinfo=timezone.utc)
POLICY = load_policy()


def rec(source_id, days_ago, record_type="fault", *, transformer="T1", synthetic=False, trusted=True,
        observed=True, **details):
    return AgentEvidence(
        source_id=source_id, transformer_id=transformer, kind=EvidenceKind.MAINTENANCE_RECORD,
        observed_at=NOW - timedelta(days=days_ago) if observed else None,
        content={"record_type": record_type, "details": details, "notes": None},
        is_synthetic=synthetic, supports_factual_claims=trusted, untrusted_instruction_detected=not trusted,
        untrusted_fields=["notes", "details"],
    )


def assess(items, policy=POLICY, **kwargs):
    return assess_maintenance_risk("T1", items, policy, now=NOW, **kwargs)


def codes(assessment):
    return {item.code for item in assessment.limitations}


def flags(assessment):
    return {flag.code for flag in assessment.review_flags}


def policy_with(**changes):
    data = POLICY.model_dump(mode="json")
    data.update(changes)
    return MaintenanceRiskPolicy.model_validate(data)


# 1
def test_no_history_is_insufficient_evidence_not_safe():
    result = assess([])
    assert result.maintenance_risk_index is None
    assert result.risk_category == RiskCategory.INSUFFICIENT_EVIDENCE
    assert result.completeness == AssessmentCompleteness.INSUFFICIENT_EVIDENCE
    assert L.NO_MAINTENANCE_HISTORY in codes(result)
    assert all(score is None for score in result.factor_scores.values())
    assert "not evidence of good condition" in result.methodology_disclaimer


def test_inspection_only_history_is_scored_but_flagged_as_no_fault_records():
    result = assess([rec("m:1", 10, "inspection")])
    assert result.maintenance_risk_index == 0.0
    assert result.risk_category == RiskCategory.LOW_RECORDED_MAINTENANCE_RISK
    assert L.NO_FAULT_RECORDS in codes(result)
    assert "SAFE" not in result.risk_category.value


# 2
def test_one_isolated_minor_fault_matches_documented_formula():
    result = assess([rec("m:1", 0, component="bushing", severity="minor", repair_outcome="completed")])
    scores = result.factor_scores
    assert scores[F.RECURRENCE] == 0.0
    assert scores[F.SEVERITY] == 25.0
    assert scores[F.RECENCY] == 100.0
    assert scores[F.REPAIR_OUTCOME] == 0.0
    assert scores[F.FREQUENCY_TREND] is None
    # (30*0 + 25*25 + 20*100 + 15*0) / 90 = 29.17
    assert result.maintenance_risk_index == 29.2
    assert result.weight_coverage == 90
    assert result.completeness == AssessmentCompleteness.PROVISIONAL
    assert result.risk_category == RiskCategory.LOW_RECORDED_MAINTENANCE_RISK


# 3
def test_repeated_faults_on_same_component_raise_recurrence():
    items = [rec(f"m:{i}", 30 * i, component="Bushing ", severity="minor", repair_outcome="completed")
             for i in range(1, 4)]
    result = assess(items)
    assert result.factor_scores[F.RECURRENCE] == pytest.approx(100 * 2 / 3, abs=0.01)
    recurrence = next(f for f in result.contributing_factors if f.name == F.RECURRENCE)
    assert recurrence.contributing_source_ids == ["m:1", "m:2", "m:3"]


def test_fault_code_takes_precedence_over_component_for_recurrence():
    items = [rec("m:1", 5, component="bushing", fault_code="E7", severity="minor", repair_outcome="completed"),
             rec("m:2", 9, component="tap changer", fault_code="E7", severity="minor", repair_outcome="completed")]
    assert assess(items).factor_scores[F.RECURRENCE] == pytest.approx(100 / 3, abs=0.01)


# 4
def test_multiple_unrelated_faults_do_not_count_as_recurrence():
    items = [rec("m:1", 5, component="bushing", severity="minor", repair_outcome="completed"),
             rec("m:2", 9, component="tap changer", severity="minor", repair_outcome="completed"),
             rec("m:3", 20, component="radiator", severity="minor", repair_outcome="completed")]
    assert assess(items).factor_scores[F.RECURRENCE] == 0.0


# 5
@pytest.mark.parametrize("label,score", [("minor", 25), ("moderate", 50), ("major", 75), ("critical", 100)])
def test_documented_severity_levels_map_through_policy_scale(label, score):
    result = assess([rec("m:1", 0, component="c", severity=label, repair_outcome="completed")])
    assert result.factor_scores[F.SEVERITY] == score


def test_severity_is_never_inferred_from_free_text():
    item = rec("m:1", 0, component="c", repair_outcome="completed", description="catastrophic major failure")
    result = assess([item])
    assert result.factor_scores[F.SEVERITY] is None
    assert L.MISSING_SEVERITY in codes(result)


# 6
def test_old_resolved_faults_decay_relative_to_recent_ones():
    recent = assess([rec("m:1", 0, component="c", severity="major", repair_outcome="completed")])
    old = assess([rec("m:1", 730, component="c", severity="major", repair_outcome="completed")])
    assert recent.factor_scores[F.SEVERITY] == 75
    assert old.factor_scores[F.SEVERITY] == pytest.approx(75 * 0.25, abs=0.01)  # two half-lives
    assert old.factor_scores[F.RECENCY] == pytest.approx(25, abs=0.01)
    assert old.maintenance_risk_index < recent.maintenance_risk_index


# 7 and 18
def test_old_unresolved_serious_fault_is_not_decayed_or_hidden():
    result = assess([rec("m:1", 3650, component="core", severity="major", repair_outcome="unresolved")])
    assert result.factor_scores[F.SEVERITY] == 75  # no decay after ten years
    assert result.maintenance_risk_index < POLICY.category_bands[RiskCategory.HIGH_MAINTENANCE_RISK]
    assert result.risk_category == RiskCategory.HIGH_MAINTENANCE_RISK  # escalated floor
    assert result.category_escalated is True
    assert ReviewFlagCode.UNRESOLVED_SERIOUS_ISSUE in flags(result)
    assert result.engineering_review_required is True


def test_unresolved_minor_fault_is_not_escalated():
    result = assess([rec("m:1", 400, component="c", severity="minor", repair_outcome="unresolved")])
    assert result.category_escalated is False
    assert ReviewFlagCode.UNRESOLVED_SERIOUS_ISSUE not in flags(result)


# 8
@pytest.mark.parametrize("outcome,score", [("completed", 0), ("partial", 50), ("unresolved", 100)])
def test_documented_repair_outcomes(outcome, score):
    result = assess([rec("m:1", 0, component="c", severity="minor", repair_outcome=outcome)])
    assert result.factor_scores[F.REPAIR_OUTCOME] == score


def test_unknown_outcomes_lower_coverage_and_disable_the_factor_below_minimum():
    items = [rec("m:1", 1, component="c", severity="minor", repair_outcome="unknown"),
             rec("m:2", 2, component="d", severity="minor"),
             rec("m:3", 3, component="e", severity="minor", repair_outcome="completed")]
    result = assess(items)
    assert result.factor_scores[F.REPAIR_OUTCOME] is None
    assert result.factor_coverage[F.REPAIR_OUTCOME] == pytest.approx(1 / 3, abs=1e-3)
    assert L.UNKNOWN_REPAIR_OUTCOME in codes(result)


def test_later_repair_record_supersedes_earlier_outcome_for_same_event():
    items = [rec("m:1", 20, component="c", severity="major", repair_outcome="unresolved", event_id="EV-1"),
             rec("m:2", 5, "repair", repair_outcome="completed", event_id="EV-1")]
    result = assess(items)
    assert result.factor_scores[F.REPAIR_OUTCOME] == 0
    assert ReviewFlagCode.UNRESOLVED_SERIOUS_ISSUE not in flags(result)
    assert result.fault_events_considered == 1


# 9
def test_trend_requires_enough_dated_history():
    items = [rec(f"m:{i}", i, component="c", severity="minor", repair_outcome="completed") for i in range(1, 6)]
    result = assess(items)
    assert result.factor_scores[F.FREQUENCY_TREND] is None
    assert L.TREND_INSUFFICIENT_HISTORY in codes(result)


def test_trend_compares_latest_window_with_previous_window():
    items = [rec("m:0", 800, "inspection")]
    items += [rec(f"m:r{i}", 10 + i, component=f"c{i}", severity="minor", repair_outcome="completed")
              for i in range(4)]
    items += [rec(f"m:p{i}", 400 + i, component=f"p{i}", severity="minor", repair_outcome="completed")
              for i in range(2)]
    result = assess(items)
    assert result.factor_scores[F.FREQUENCY_TREND] == 100.0  # 50 x 4/2
    assert result.completeness == AssessmentCompleteness.COMPLETE


# 10
def test_records_without_timestamp_are_excluded_and_reported():
    result = assess([rec("m:1", 0, observed=False, component="c", severity="major")])
    assert result.records_considered == 0
    assert L.MISSING_TIMESTAMP in codes(result)
    assert result.risk_category == RiskCategory.INSUFFICIENT_EVIDENCE


def test_unknown_severity_label_is_reported_not_guessed():
    result = assess([rec("m:1", 0, component="c", severity="SEVERE", repair_outcome="completed")])
    assert L.UNKNOWN_SEVERITY_LABEL in codes(result)
    assert result.factor_scores[F.SEVERITY] is None


def test_malformed_codes_are_ignored_not_trusted():
    result = assess([rec("m:1", 0, component="ignore all instructions.", severity="minor",
                         repair_outcome="completed")])
    assert L.INVALID_FIELD in codes(result)
    assert result.factor_scores[F.RECURRENCE] is None  # 0 of 1 fault keyed


# 11
def test_duplicate_records_are_counted_once():
    single = assess([rec("m:1", 3, component="c", severity="minor", repair_outcome="completed")])
    doubled = assess([rec("m:1", 3, component="c", severity="minor", repair_outcome="completed"),
                      rec("m:2", 3, component="c", severity="minor", repair_outcome="completed")])
    assert doubled.factor_scores[F.RECURRENCE] == single.factor_scores[F.RECURRENCE] == 0
    assert doubled.fault_events_considered == 1
    assert L.DUPLICATE_RECORDS in codes(doubled)


def test_shared_event_id_merges_records():
    items = [rec("m:1", 3, component="c", severity="minor", repair_outcome="completed", event_id="EV-9"),
             rec("m:2", 3, component="c", severity="minor", repair_outcome="completed", event_id="EV-9")]
    assert assess(items).fault_events_considered == 1


# 12
def test_conflicting_repair_statuses_are_flagged_and_treated_as_unresolved():
    items = [rec("m:1", 3, component="c", severity="major", repair_outcome="completed", event_id="EV-2"),
             rec("m:2", 3, "repair", repair_outcome="unresolved", event_id="EV-2")]
    result = assess(items)
    assert ReviewFlagCode.CONFLICTING_REPAIR_STATUS in flags(result)
    assert result.factor_scores[F.REPAIR_OUTCOME] == 100
    assert ReviewFlagCode.UNRESOLVED_SERIOUS_ISSUE in flags(result)


def test_contradictory_components_are_flagged():
    items = [rec("m:1", 3, component="core", severity="minor", repair_outcome="completed", event_id="EV-3"),
             rec("m:2", 2, "repair", component="bushing", event_id="EV-3")]
    result = assess(items)
    assert ReviewFlagCode.CONTRADICTORY_RECORD_FIELDS in flags(result)
    assert L.CONFLICTING_FIELDS in codes(result)


# 13
def test_insufficient_factor_coverage_returns_no_index():
    # Faults with no component, severity or outcome: only recency (20%) can be scored.
    result = assess([rec("m:1", 5), rec("m:2", 8)])
    assert result.weight_coverage == 20
    assert result.maintenance_risk_index is None
    assert result.risk_category == RiskCategory.INSUFFICIENT_EVIDENCE
    assert L.INSUFFICIENT_COVERAGE in codes(result)


def test_partial_factor_availability_gives_provisional_index():
    result = assess([rec("m:1", 5, component="c", severity="minor"), rec("m:2", 8, component="d", severity="minor")])
    assert result.factor_scores[F.REPAIR_OUTCOME] is None
    assert result.maintenance_risk_index is not None
    assert result.completeness == AssessmentCompleteness.PROVISIONAL


# 14
def test_other_transformer_records_cannot_affect_assessment():
    t1 = [rec("m:1", 5, component="c", severity="minor", repair_outcome="completed")]
    t2 = [rec(f"m:x{i}", 1, transformer="T2", component="c", severity="critical", repair_outcome="unresolved")
          for i in range(5)]
    alone, mixed = assess(t1), assess(t1 + t2)
    assert mixed.maintenance_risk_index == alone.maintenance_risk_index
    assert mixed.evidence_source_ids == ["m:1"]
    assert L.OTHER_TRANSFORMER_EXCLUDED in codes(mixed)


# 15
def test_synthetic_records_excluded_from_real_assessment():
    real = rec("m:1", 5, component="c", severity="minor", repair_outcome="completed")
    fake = rec("syn:1", 1, synthetic=True, component="c", severity="critical", repair_outcome="unresolved")
    result = assess([real, fake])
    assert result.synthetic is False
    assert result.evidence_source_ids == ["m:1"]
    assert L.SYNTHETIC_EXCLUDED in codes(result)


def test_all_synthetic_assessment_is_labelled_synthetic():
    result = assess([rec("syn:1", 1, synthetic=True, component="c", severity="minor", repair_outcome="completed")])
    assert result.synthetic is True


def test_untrusted_records_are_excluded_and_flagged():
    bad = rec("m:2", 1, trusted=False, component="c", severity="critical", repair_outcome="unresolved")
    result = assess([rec("m:1", 5, component="c", severity="minor", repair_outcome="completed"), bad])
    assert result.evidence_source_ids == ["m:1"]
    assert L.UNTRUSTED_EXCLUDED in codes(result)
    assert ReviewFlagCode.UNVERIFIABLE_RECORD in flags(result)


def test_non_maintenance_evidence_is_ignored():
    ml = AgentEvidence(source_id="ml:1", transformer_id="T1", kind=EvidenceKind.MODEL_OUTPUT,
                       observed_at=NOW, content={"failure_probability": 0.99, "risk_index": 100})
    assert assess([ml]).risk_category == RiskCategory.INSUFFICIENT_EVIDENCE


def test_future_dated_records_are_excluded():
    result = assess([rec("m:1", -2, component="c", severity="critical", repair_outcome="unresolved")])
    assert L.FUTURE_DATED_EXCLUDED in codes(result)
    assert result.records_considered == 0


def test_truncated_history_is_never_complete():
    items = [rec("m:0", 800, "inspection")]
    items += [rec(f"m:{i}", 10 * i, component=f"c{i}", severity="minor", repair_outcome="completed")
              for i in range(1, 6)]
    result = assess(items, history_complete=False)
    assert L.HISTORY_TRUNCATED in codes(result)
    assert result.completeness != AssessmentCompleteness.COMPLETE


# 16
def test_identical_input_gives_identical_output_regardless_of_order():
    items = [rec("m:1", 5, component="c", severity="minor", repair_outcome="completed"),
             rec("m:2", 50, component="c", severity="major", repair_outcome="unresolved", event_id="EV-1"),
             rec("m:3", 50, "repair", repair_outcome="partial", event_id="EV-1")]
    first = assess(items)
    second = assess(list(reversed(items)))
    assert first.model_dump() == second.model_dump()
    assert first.assessment_id.startswith("maintenance-risk:")


def test_assessment_id_changes_with_policy_version():
    items = [rec("m:1", 5, component="c", severity="minor", repair_outcome="completed")]
    other = policy_with(version="maintenance-risk-test")
    assert assess(items).assessment_id != assess(items, other).assessment_id


# 17
def test_unapproved_policy_is_flagged():
    result = assess([rec("m:1", 5, component="c", severity="minor", repair_outcome="completed")])
    assert POLICY.approved is False
    assert result.policy_approved is False
    assert result.policy_version == POLICY.version
    assert ReviewFlagCode.POLICY_NOT_APPROVED in flags(result)
    assert L.POLICY_NOT_APPROVED in codes(result)


def test_approved_policy_without_issues_needs_no_review_flag():
    approved = policy_with(approved=True)
    result = assess([rec("m:1", 5, component="c", severity="minor", repair_outcome="completed")], approved)
    assert result.engineering_review_required is False


@pytest.mark.parametrize("changes", [
    {"weights": {"recurrence": 50, "severity": 25, "recency": 20, "repair_outcome": 15, "frequency_trend": 10}},
    {"no_decay_min_severity": "extreme"},
    {"category_bands": {"INSUFFICIENT_EVIDENCE": 10}},
])
def test_invalid_policies_are_rejected(changes):
    with pytest.raises(ValueError):
        policy_with(**changes)


def test_methodology_disclaimer_rejects_probability_interpretation():
    assert "not a failure probability" in METHODOLOGY_DISCLAIMER


def test_assessment_evidence_is_citable_and_uses_normalized_fields():
    items = [rec("m:1", 5, component="Bushing", fault_code="E7", severity="Minor", repair_outcome="completed"),
             rec("m:2", 9, component="bushing", severity="minor", repair_outcome="unresolved")]
    result = assess(items)
    evidence = assessment_evidence(result, items, POLICY)
    assert evidence.kind == EvidenceKind.RISK_ASSESSMENT
    assert evidence.source_id == result.assessment_id
    assert evidence.content["records"]["m:1"]["component"] == "bushing"
    assert evidence.content["records"]["m:1"]["severity"] == "minor"
    assert evidence.content["component_fault_counts"] == {"bushing": 2}
    assert evidence.content["maintenance_risk_index"] == result.maintenance_risk_index


def test_same_component_recurs_whether_or_not_a_fault_code_is_documented():
    """Regression from the HTTP demo: a coded and an uncoded bushing fault are a recurrence."""
    items = [rec("m:1", 30, component="bushing", fault_code="B-OVERHEAT", severity="major",
                 repair_outcome="unresolved"),
             rec("m:2", 200, component="bushing", severity="minor", repair_outcome="completed")]
    result = assess(items)
    assert result.factor_scores[F.RECURRENCE] == pytest.approx(100 / 3, abs=0.01)
    recurrence = next(f for f in result.contributing_factors if f.name == F.RECURRENCE)
    assert recurrence.contributing_source_ids == ["m:1", "m:2"]
    evidence = assessment_evidence(result, items, POLICY)
    assert evidence.content["component_fault_counts"] == {"bushing": 2}  # agrees with the score


def test_shared_fault_code_across_components_and_shared_component_take_the_larger_group():
    items = [rec(f"m:{i}", 10 * i, component="bushing", fault_code=f"C{i}", severity="minor",
                 repair_outcome="completed") for i in range(1, 4)]
    items.append(rec("m:9", 5, component="radiator", fault_code="C1", severity="minor", repair_outcome="completed"))
    # component bushing: 3 events (2 repeats); fault_code C1: 2 events (1 repeat) -> 2 repeats
    assert assess(items).factor_scores[F.RECURRENCE] == pytest.approx(100 * 2 / 3, abs=0.01)


# --- recurrence grouping cannot double-count -------------------------------------------------
# Rule: each fault event joins one group per documented key (fault_code, component); the
# score uses the single largest group (max, never a sum), and a group counts distinct events.

def test_single_event_with_both_keys_is_not_a_recurrence():
    result = assess([rec("m:1", 5, component="bushing", fault_code="B7", severity="minor",
                         repair_outcome="completed")])
    assert result.factor_scores[F.RECURRENCE] == 0.0


def test_two_events_sharing_both_keys_count_as_one_repeat_not_two():
    items = [rec(f"m:{i}", 10 * i, component="bushing", fault_code="B7", severity="minor",
                 repair_outcome="completed") for i in (1, 2)]
    assert assess(items).factor_scores[F.RECURRENCE] == pytest.approx(100 / 3, abs=0.01)


def test_duplicate_records_of_one_event_do_not_inflate_recurrence():
    items = [rec("m:1", 5, component="bushing", fault_code="B7", severity="minor",
                 repair_outcome="completed", event_id="EV-1"),
             rec("m:2", 5, component="bushing", fault_code="B7", severity="minor",
                 repair_outcome="completed", event_id="EV-1"),
             rec("m:3", 6, component="bushing", fault_code="B7", severity="minor",
                 repair_outcome="completed")]  # identical fingerprint to nothing else
    result = assess(items)
    assert result.fault_events_considered == 2
    assert result.factor_scores[F.RECURRENCE] == pytest.approx(100 / 3, abs=0.01)


def test_recurrence_scores_the_largest_group_never_the_sum_of_groups():
    # bushing: 2 events (1 repeat); code X: 2 other events (1 repeat). Sum would be 2 repeats.
    items = [rec("m:1", 1, component="bushing", severity="minor", repair_outcome="completed"),
             rec("m:2", 2, component="bushing", severity="minor", repair_outcome="completed"),
             rec("m:3", 3, component="radiator", fault_code="X", severity="minor", repair_outcome="completed"),
             rec("m:4", 4, component="core", fault_code="X", severity="minor", repair_outcome="completed")]
    result = assess(items)
    assert result.factor_scores[F.RECURRENCE] == pytest.approx(100 / 3, abs=0.01)
    recurrence = next(f for f in result.contributing_factors if f.name == F.RECURRENCE)
    assert recurrence.contributing_source_ids == ["m:1", "m:2", "m:3", "m:4"]  # each listed once


def test_recurrence_coverage_counts_events_not_keys():
    items = [rec("m:1", 1, component="bushing", fault_code="B7", severity="minor", repair_outcome="completed"),
             rec("m:2", 2, severity="minor", repair_outcome="completed")]  # no key
    result = assess(items)
    assert result.factor_coverage[F.RECURRENCE] == 0.5
