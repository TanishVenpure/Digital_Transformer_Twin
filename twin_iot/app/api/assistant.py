"""Self-contained chatbot and maintenance-risk assessment for twin_iot.

Answers are grounded in the local twin_iot database (readings, alerts,
maintenance sheet).  No dependency on the main backend (port 8000).

The chatbot builds a deterministic answer from the maintenance sheet, alerts,
and analytics.  The risk assessment computes a weighted index from maintenance
check severity, recurrence, recency, repair status, and frequency trend —
using the maintenance_log checks that twin_iot already derives from sensor
evaluations and analytics (not the main backend's separate maintenance_records
table).

The risk index is a relative 0–100 summary of documented maintenance history
under a provisional, versioned policy.  It is NOT a failure probability.
"""
import hashlib
import logging
import math
from collections import Counter
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from app.database import db

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/assistant", tags=["assistant"])

POLICY_VERSION = "maintenance-risk-0.1.0-unapproved"
POLICY_APPROVED = False
METHODOLOGY_DISCLAIMER = (
    "Maintenance Risk Index: a relative 0–100 summary of documented maintenance "
    "history under a provisional, versioned policy.  It is not a failure probability, "
    "is not a validated engineering standard, and does not authorize operation, shutdown, "
    "repair or return to service.  Missing records are not evidence of good condition."
)
SEVERITY_RANK = {"NORMAL": 0, "WARNING": 1, "PREDICTED_MAINTENANCE": 2, "MAINTENANCE_REQUIRED": 3, "CRITICAL": 4}
SEVERITY_SCORE = {"WARNING": 25, "PREDICTED_MAINTENANCE": 50, "MAINTENANCE_REQUIRED": 75, "CRITICAL": 100}
CATEGORY_HIGH = 70
CATEGORY_MODERATE = 40
DECAY_HALF_LIFE_DAYS = 365
WEIGHTS = {"severity": 30, "recurrence": 25, "recency": 20, "resolution": 15, "frequency_trend": 10}


# ---------------------------------------------------------------------------
# Chat
# ---------------------------------------------------------------------------


class ChatIn(BaseModel):
    transformer_id: str = Field(min_length=1, max_length=200)
    question: str = Field(min_length=1, max_length=2000)


def _chat_context(transformer_id: str) -> dict[str, Any]:
    """Gather evidence from twin_iot's own database."""
    with db.connect() as conn:
        sheet = db.maintenance_sheet(conn, transformer_id, limit=50)
        alerts_list = db.recent_alerts(conn, transformer_id, limit=50)
        events = db.maintenance_events(conn, transformer_id)
        transformers = db.transformers(conn)

    transformer_info = next((t for t in transformers if t["transformer_id"] == transformer_id), None)
    return {
        "transformer": transformer_info,
        "maintenance_checks": sheet,
        "alerts": alerts_list,
        "events": events,
    }


def _deterministic_answer(question: str, ctx: dict[str, Any], transformer_id: str) -> dict[str, Any]:
    """Build a factual answer grounded entirely in available evidence."""
    checks = ctx["maintenance_checks"]
    alerts_list = ctx["alerts"]
    events = ctx["events"]
    info = ctx["transformer"]
    q = question.lower()

    facts = []
    answer_parts = []

    if info:
        answer_parts.append(
            f"Transformer {transformer_id}: {info['readings']} hourly readings "
            f"from {info['start'][:10]} to {info['end'][:10]}."
        )

    # Maintenance summary
    open_checks = [c for c in checks if c.get("status") == "OPEN"]
    resolved_checks = [c for c in checks if c.get("status") == "RESOLVED"]
    flagged = [c for c in checks if c.get("flag")]

    if "maintenance" in q or "check" in q or "status" in q or "risk" in q or "health" in q:
        answer_parts.append(
            f"Maintenance sheet: {len(checks)} checks total, "
            f"{len(open_checks)} open, {len(resolved_checks)} resolved."
        )
        if open_checks:
            summary = ", ".join(
                f"#{c['check_no']} {c['iot_name']} {c['signal']}" for c in open_checks[:5]
            )
            answer_parts.append(f"Open checks: {summary}.")
            for c in open_checks[:3]:
                facts.append({"statement": f"Check #{c['check_no']}: {c['iot_name']} signalled {c['signal']} on {c['date']} {c['time_utc']} UTC. Action: {c['recommended_action']}"})

    if "flag" in q or "trend" in q or "leak" in q or "growth" in q or "ageing" in q or "winding" in q:
        if flagged:
            flag_summary = ", ".join(f"#{c['check_no']} {c['flag']}" for c in flagged[:5])
            answer_parts.append(f"Trend flags raised: {flag_summary}.")
            for c in flagged[:3]:
                facts.append({"statement": f"Flag {c['flag']} raised on {c['date']}: {c['recommended_action']}"})
        else:
            answer_parts.append("No trend flags have been raised.")

    if "alert" in q or "alarm" in q:
        if alerts_list:
            answer_parts.append(f"{len(alerts_list)} recent alerts.")
            by_signal = Counter(a["signal"] for a in alerts_list)
            answer_parts.append("Breakdown: " + ", ".join(f"{v} {k}" for k, v in by_signal.most_common()))
            for a in alerts_list[:3]:
                facts.append({"statement": f"Alert at {a['ts']}: IoT{a['iot_slot']} {a['signal']} — {a['message']}"})
        else:
            answer_parts.append("No recent alerts.")

    if "oil" in q:
        oil_checks = [c for c in checks if "oil" in (c.get("iot_name") or "").lower()]
        if oil_checks:
            answer_parts.append(f"{len(oil_checks)} oil-related maintenance checks found.")
            for c in oil_checks[:3]:
                facts.append({"statement": f"#{c['check_no']} {c['iot_name']} {c['signal']} on {c['date']}: {c['recommended_action']}"})

    if "temperature" in q or "thermal" in q or "winding" in q:
        temp_checks = [c for c in checks if "temperature" in (c.get("iot_name") or "").lower() or "winding" in (c.get("iot_name") or "").lower()]
        if temp_checks:
            answer_parts.append(f"{len(temp_checks)} temperature/winding maintenance checks found.")

    if "current" in q or "load" in q or "overload" in q:
        load_checks = [c for c in checks if "current" in (c.get("iot_name") or "").lower()]
        if load_checks:
            answer_parts.append(f"{len(load_checks)} load/current maintenance checks found.")

    if "voltage" in q or "supply" in q or "grid" in q:
        volt_checks = [c for c in checks if "voltage" in (c.get("iot_name") or "").lower()]
        if volt_checks:
            answer_parts.append(f"{len(volt_checks)} voltage-related maintenance checks found.")

    if "event" in q or "repair" in q:
        if events:
            answer_parts.append(f"{len(events)} maintenance events (repairs) recorded.")
        else:
            answer_parts.append("No maintenance events (repairs) recorded.")

    # Fallback: general summary
    if len(answer_parts) <= 1:
        answer_parts.append(
            f"Maintenance sheet has {len(checks)} checks ({len(open_checks)} open, "
            f"{len(resolved_checks)} resolved). {len(alerts_list)} recent alerts. "
            f"{len(flagged)} trend flags. {len(events)} repair events."
        )
        if open_checks:
            answer_parts.append(
                "Open issues: " + ", ".join(f"{c['iot_name']} {c['signal']}" for c in open_checks[:5]) + "."
            )
        answer_parts.append("Ask about specific sensors (oil, temperature, current, voltage), alerts, trends, or maintenance checks for more detail.")

    answer = " ".join(answer_parts)
    return {
        "transformer_id": transformer_id,
        "answer": answer,
        "evidence_source_ids": [],
        "warnings": [],
        "synthetic_evidence": False,
        "facts": facts[:20],
        "inferences": [],
        "answer_source": "deterministic",
        "risk_assessment": None,
    }


@router.post("/chat")
def assistant_chat(body: ChatIn) -> dict[str, Any]:
    """Answer a question about a transformer, grounded in twin_iot data.

    Read-only: queries the local database for maintenance checks, alerts and
    analytics.  No API key required — the answer is deterministic and grounded
    entirely in stored evidence.
    """
    ctx = _chat_context(body.transformer_id)
    if ctx["transformer"] is None:
        raise HTTPException(404, f"No data for transformer {body.transformer_id}")
    return _deterministic_answer(body.question, ctx, body.transformer_id)


# ---------------------------------------------------------------------------
# Risk assessment
# ---------------------------------------------------------------------------

def _assess_risk(transformer_id: str) -> dict[str, Any]:
    """Compute maintenance risk from twin_iot's own maintenance_log.

    twin_iot's maintenance sheet contains checks derived from sensor threshold
    evaluations and analytics trend flags — different source data than the main
    backend's maintenance_records table, but structurally richer for IoT-monitored
    transformers since every threshold crossing is recorded.
    """
    now = datetime.now(timezone.utc)
    with db.connect() as conn:
        checks = db.maintenance_sheet(conn, transformer_id)

    if not checks:
        return _empty_assessment(transformer_id, now)

    # Only fault-level checks (not NORMAL)
    faults = [c for c in checks if c.get("signal") in SEVERITY_SCORE]
    if not faults:
        return _empty_assessment(transformer_id, now)

    # Parse timestamps
    for c in faults:
        c["_opened"] = datetime.fromisoformat(c["opened_at"]) if c.get("opened_at") else now
        c["_age_days"] = max(0, (now - c["_opened"]).total_seconds() / 86400)
        c["_resolved"] = c.get("status") == "RESOLVED"

    contributing_factors = []
    factor_scores = {}

    # 1. Severity (weight 30%): max severity with time decay
    max_severity = 0.0
    for c in faults:
        score = SEVERITY_SCORE.get(c["signal"], 0)
        decay = 0.5 ** (c["_age_days"] / DECAY_HALF_LIFE_DAYS)
        # No decay for unresolved MAINTENANCE_REQUIRED or CRITICAL
        if not c["_resolved"] and score >= SEVERITY_SCORE["MAINTENANCE_REQUIRED"]:
            decay = 1.0
        max_severity = max(max_severity, score * decay)
    factor_scores["severity"] = round(max_severity, 1)
    contributing_factors.append({
        "name": "severity", "weight": WEIGHTS["severity"], "available": True,
        "score": factor_scores["severity"],
        "method": "Max signal severity with time decay (no decay for unresolved serious issues)",
    })

    # 2. Recurrence (weight 25%): repeated checks on the same IoT
    by_iot = Counter(c["iot"] for c in faults)
    largest_group = max(by_iot.values()) if by_iot else 0
    recurrence_score = min(100.0, 100.0 * max(0, largest_group - 1) / 3)
    factor_scores["recurrence"] = round(recurrence_score, 1)
    contributing_factors.append({
        "name": "recurrence", "weight": WEIGHTS["recurrence"], "available": True,
        "score": factor_scores["recurrence"],
        "method": "Repeated threshold checks on the same IoT sensor (saturation at 3 repeats)",
    })

    # 3. Recency (weight 20%): how recent is the latest fault
    most_recent_age = min(c["_age_days"] for c in faults)
    recency_score = round(100.0 * (0.5 ** (most_recent_age / DECAY_HALF_LIFE_DAYS)), 1)
    factor_scores["recency"] = recency_score
    contributing_factors.append({
        "name": "recency", "weight": WEIGHTS["recency"], "available": True,
        "score": factor_scores["recency"],
        "method": "Time decay from most recent fault check (half-life 365 days)",
    })

    # 4. Resolution (weight 15%): unresolved checks score higher
    open_faults = [c for c in faults if not c["_resolved"]]
    if faults:
        resolution_score = round(100.0 * len(open_faults) / len(faults), 1)
    else:
        resolution_score = 0.0
    factor_scores["resolution"] = resolution_score
    contributing_factors.append({
        "name": "resolution", "weight": WEIGHTS["resolution"], "available": True,
        "score": factor_scores["resolution"],
        "method": "Fraction of fault checks still unresolved",
    })

    # 5. Frequency trend (weight 10%): compare recent vs prior window
    trend_available = False
    trend_score = 0.0
    if len(faults) >= 4:
        recent = [c for c in faults if c["_age_days"] <= 365]
        prior = [c for c in faults if 365 < c["_age_days"] <= 730]
        if prior:
            trend_score = min(100.0, 50.0 * len(recent) / len(prior))
            trend_available = True
        elif recent:
            trend_score = 100.0
            trend_available = True
    factor_scores["frequency_trend"] = round(trend_score, 1) if trend_available else None
    contributing_factors.append({
        "name": "frequency_trend", "weight": WEIGHTS["frequency_trend"],
        "available": trend_available,
        "score": factor_scores["frequency_trend"],
        "method": "Fault check count in last 365 days vs prior 365 days (requires >= 4 checks)",
        "unavailable_reason": None if trend_available else "Fewer than 4 fault checks or insufficient history span",
    })

    # Weighted index
    total_weight = sum(WEIGHTS[f["name"]] for f in contributing_factors if f["available"])
    if total_weight < 60:
        return _empty_assessment(transformer_id, now, checks_considered=len(checks), faults_considered=len(faults))

    index = sum(
        WEIGHTS[f["name"]] * (f["score"] or 0)
        for f in contributing_factors if f["available"]
    ) / total_weight
    index = round(index, 1)

    # Categorize
    if index >= CATEGORY_HIGH:
        category = "HIGH_MAINTENANCE_RISK"
    elif index >= CATEGORY_MODERATE:
        category = "MODERATE_MAINTENANCE_RISK"
    else:
        category = "LOW_RECORDED_MAINTENANCE_RISK"

    # Escalation: unresolved serious issues
    escalated = False
    review_flags = []
    review_flags.append({"code": "POLICY_NOT_APPROVED", "detail": "Risk policy has not been approved by engineering review."})
    unresolved_serious = [c for c in faults if not c["_resolved"] and SEVERITY_SCORE.get(c["signal"], 0) >= 75]
    if unresolved_serious and category != "HIGH_MAINTENANCE_RISK":
        category = "HIGH_MAINTENANCE_RISK"
        escalated = True
        review_flags.append({
            "code": "UNRESOLVED_SERIOUS_ISSUE",
            "detail": f"{len(unresolved_serious)} unresolved check(s) at MAINTENANCE_REQUIRED or CRITICAL severity.",
        })

    # Trend flags as review flags
    flagged = [c for c in checks if c.get("flag")]
    for c in flagged:
        review_flags.append({
            "code": "TREND_FLAG",
            "detail": f"Trend flag {c['flag']} raised on {c.get('date', 'unknown date')}.",
        })

    assessment_id = hashlib.sha256(f"{transformer_id}:{now.isoformat()}".encode()).hexdigest()[:16]

    return {
        "transformer_id": transformer_id,
        "assessment_id": assessment_id,
        "assessed_at": now.isoformat(),
        "policy_version": POLICY_VERSION,
        "policy_approved": POLICY_APPROVED,
        "maintenance_risk_index": index,
        "risk_category": category,
        "completeness": "COMPLETE",
        "category_escalated": escalated,
        "weight_coverage": round(total_weight, 1),
        "contributing_factors": contributing_factors,
        "review_flags": review_flags,
        "records_considered": len(checks),
        "fault_events_considered": len(faults),
        "history_complete": True,
        "synthetic": False,
        "engineering_review_required": True,
        "methodology_disclaimer": METHODOLOGY_DISCLAIMER,
    }


def _empty_assessment(transformer_id: str, now: datetime, *, checks_considered: int = 0, faults_considered: int = 0) -> dict[str, Any]:
    return {
        "transformer_id": transformer_id,
        "assessment_id": hashlib.sha256(f"{transformer_id}:{now.isoformat()}".encode()).hexdigest()[:16],
        "assessed_at": now.isoformat(),
        "policy_version": POLICY_VERSION,
        "policy_approved": POLICY_APPROVED,
        "maintenance_risk_index": None,
        "risk_category": "INSUFFICIENT_EVIDENCE",
        "completeness": "INSUFFICIENT_EVIDENCE",
        "category_escalated": False,
        "weight_coverage": 0,
        "contributing_factors": [],
        "review_flags": [{"code": "POLICY_NOT_APPROVED", "detail": "Risk policy has not been approved by engineering review."}],
        "records_considered": checks_considered,
        "fault_events_considered": faults_considered,
        "history_complete": True,
        "synthetic": False,
        "engineering_review_required": False,
        "methodology_disclaimer": METHODOLOGY_DISCLAIMER,
    }


@router.get("/risk")
def assistant_risk(
    transformer_id: str = Query(min_length=1, max_length=200),
) -> dict[str, Any]:
    """Compute maintenance risk from twin_iot's own maintenance checks.

    Read-only: the risk index is computed deterministically from maintenance_log.
    No API key required.
    """
    return _assess_risk(transformer_id)


@router.get("/status")
def assistant_status() -> dict[str, Any]:
    """Self-contained: always available when twin_iot is running."""
    return {"backend": "self-contained", "status": "ok"}
