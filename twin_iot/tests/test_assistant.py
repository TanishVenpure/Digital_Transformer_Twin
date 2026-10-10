"""Tests for the self-contained assistant endpoints (chatbot + risk).

These tests mock the database layer so no PostgreSQL connection is needed.
"""
import os

import pytest
from fastapi.testclient import TestClient

os.environ.setdefault("TWIN_API_CREDENTIALS", "T1:test-secret")

from app.api.main import app  # noqa: E402

HEADERS = {"X-Twin-API-Key": "test-secret"}
BASE_URL = "http://localhost"

# Realistic maintenance check rows
_SAMPLE_CHECKS = [
    {
        "check_no": 1, "transformer_id": "T1",
        "date": "2026-09-01", "time_utc": "08:00",
        "opened_at": "2026-09-01T08:00:00+00:00",
        "iot": 5, "iot_name": "Oil Level",
        "signal": "WARNING", "flag": "",
        "previous_status": "NORMAL",
        "measured_value": 22.5, "threshold": 25.0,
        "comparison": "<=", "unit": "%",
        "maintenance_type": "TRANSFORMER_MAINTENANCE",
        "recommended_action": "Check conservator and top up oil.",
        "status": "RESOLVED",
        "resolved_at": "2026-09-02T08:00:00+00:00",
        "duration_hours": 24.0, "reading_id": 100,
        "text": "Check #1 ...",
    },
    {
        "check_no": 2, "transformer_id": "T1",
        "date": "2026-09-15", "time_utc": "12:00",
        "opened_at": "2026-09-15T12:00:00+00:00",
        "iot": 5, "iot_name": "Oil Level",
        "signal": "MAINTENANCE_REQUIRED", "flag": "",
        "previous_status": "WARNING",
        "measured_value": 18.0, "threshold": 20.0,
        "comparison": "<=", "unit": "%",
        "maintenance_type": "TRANSFORMER_MAINTENANCE",
        "recommended_action": "Inspect seals and gaskets.",
        "status": "OPEN",
        "resolved_at": None,
        "duration_hours": None, "reading_id": 200,
        "text": "Check #2 ...",
    },
    {
        "check_no": 3, "transformer_id": "T1",
        "date": "2026-10-01", "time_utc": "06:00",
        "opened_at": "2026-10-01T06:00:00+00:00",
        "iot": 7, "iot_name": "Winding Temperature",
        "signal": "CRITICAL", "flag": "",
        "previous_status": "MAINTENANCE_REQUIRED",
        "measured_value": 85.0, "threshold": 80.0,
        "comparison": ">=", "unit": "K",
        "maintenance_type": "TRANSFORMER_MAINTENANCE",
        "recommended_action": "Emergency shutdown.",
        "status": "OPEN",
        "resolved_at": None,
        "duration_hours": None, "reading_id": 300,
        "text": "Check #3 ...",
    },
    {
        "check_no": 4, "transformer_id": "T1",
        "date": "2026-10-05", "time_utc": "14:00",
        "opened_at": "2026-10-05T14:00:00+00:00",
        "iot": 5, "iot_name": "Oil Level",
        "signal": "MAINTENANCE_REQUIRED", "flag": "ACCELERATING_OIL_LEAK",
        "previous_status": "TREND",
        "measured_value": None, "threshold": None,
        "comparison": None, "unit": "%",
        "maintenance_type": "TRANSFORMER_MAINTENANCE",
        "recommended_action": "Inspect seals and gaskets.",
        "status": "OPEN",
        "resolved_at": None,
        "duration_hours": None, "reading_id": 350,
        "text": "Check #4 ...",
    },
]

_SAMPLE_ALERTS = [
    {"ts": "2026-10-01T06:00:00+00:00", "iot_slot": 7, "signal": "CRITICAL",
     "value": 85.0, "threshold": 80.0, "comparison_operator": ">=",
     "unit": "K", "message": "Winding Temperature exceeded critical threshold"},
]

_TRANSFORMER_INFO = [{"transformer_id": "T1", "readings": 200, "start": "2026-09-01T00:00:00+00:00", "end": "2026-10-09T23:00:00+00:00"}]


class _FakeConn:
    def __enter__(self):
        return self

    def __exit__(self, *a):
        pass

    def cursor(self):
        return type(
            "C", (),
            {"execute": lambda s, q, *a: None,
             "__enter__": lambda s: s,
             "__exit__": lambda s, *a: None},
        )()


@pytest.fixture(autouse=True)
def _mock_db(monkeypatch):
    """Mock all database calls used by the assistant."""
    monkeypatch.setattr("app.api.main.db.refuse_managed_database", lambda conn: None)
    monkeypatch.setattr("app.api.main.db.create_schema", lambda conn: None)
    monkeypatch.setattr("app.api.main.db.connect", lambda: _FakeConn())
    monkeypatch.setattr("app.api.assistant.db.connect", lambda: _FakeConn())
    monkeypatch.setattr("app.api.assistant.db.maintenance_sheet", lambda conn, tid, **kw: list(_SAMPLE_CHECKS))
    monkeypatch.setattr("app.api.assistant.db.recent_alerts", lambda conn, tid, **kw: list(_SAMPLE_ALERTS))
    monkeypatch.setattr("app.api.assistant.db.maintenance_events", lambda conn, tid: [])
    monkeypatch.setattr("app.api.assistant.db.transformers", lambda conn: list(_TRANSFORMER_INFO))


class TestChat:
    def test_chat_success(self):
        client = TestClient(app, base_url=BASE_URL)
        resp = client.post(
            "/api/assistant/chat",
            json={"transformer_id": "T1", "question": "What is the maintenance status?"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["answer_source"] == "deterministic"
        assert "T1" in data["answer"]
        assert "maintenance" in data["answer"].lower() or "check" in data["answer"].lower()

    def test_chat_oil_question(self):
        client = TestClient(app, base_url=BASE_URL)
        resp = client.post(
            "/api/assistant/chat",
            json={"transformer_id": "T1", "question": "Tell me about oil levels"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "oil" in data["answer"].lower()

    def test_chat_alert_question(self):
        client = TestClient(app, base_url=BASE_URL)
        resp = client.post(
            "/api/assistant/chat",
            json={"transformer_id": "T1", "question": "Show me the alerts"},
        )
        assert resp.status_code == 200
        assert "alert" in resp.json()["answer"].lower()

    def test_chat_trend_flags(self):
        client = TestClient(app, base_url=BASE_URL)
        resp = client.post(
            "/api/assistant/chat",
            json={"transformer_id": "T1", "question": "Are there any trend flags?"},
        )
        assert resp.status_code == 200
        assert "ACCELERATING_OIL_LEAK" in resp.json()["answer"]

    def test_chat_no_auth_works(self):
        client = TestClient(app, base_url=BASE_URL)
        resp = client.post("/api/assistant/chat",
                           json={"transformer_id": "T1", "question": "What is the status?"})
        assert resp.status_code == 200

    def test_chat_empty_question(self):
        client = TestClient(app, base_url=BASE_URL)
        resp = client.post(
            "/api/assistant/chat",
            json={"transformer_id": "T1", "question": ""},
        )
        assert resp.status_code == 422

    def test_chat_unknown_transformer(self, monkeypatch):
        monkeypatch.setattr("app.api.assistant.db.transformers", lambda conn: [])
        client = TestClient(app, base_url=BASE_URL)
        resp = client.post(
            "/api/assistant/chat",
            json={"transformer_id": "UNKNOWN", "question": "Test"},
        )
        assert resp.status_code == 404

    def test_chat_general_question(self):
        client = TestClient(app, base_url=BASE_URL)
        resp = client.post(
            "/api/assistant/chat",
            json={"transformer_id": "T1", "question": "How is the transformer doing?"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["answer"]) > 20
        assert data["facts"] is not None


class TestRisk:
    def test_risk_with_data(self):
        client = TestClient(app, base_url=BASE_URL)
        resp = client.get("/api/assistant/risk?transformer_id=T1")
        assert resp.status_code == 200
        data = resp.json()
        assert data["risk_category"] in (
            "HIGH_MAINTENANCE_RISK", "MODERATE_MAINTENANCE_RISK", "LOW_RECORDED_MAINTENANCE_RISK",
        )
        assert data["maintenance_risk_index"] is not None
        assert isinstance(data["maintenance_risk_index"], (int, float))
        assert 0 <= data["maintenance_risk_index"] <= 100
        assert data["policy_version"] == "maintenance-risk-0.1.0-unapproved"
        assert data["policy_approved"] is False
        assert "failure probability" not in data["methodology_disclaimer"].lower() or "not a failure probability" in data["methodology_disclaimer"].lower()
        assert len(data["contributing_factors"]) >= 4

    def test_risk_has_escalation_for_unresolved_critical(self):
        client = TestClient(app, base_url=BASE_URL)
        resp = client.get("/api/assistant/risk?transformer_id=T1", headers={})
        data = resp.json()
        # We have an unresolved CRITICAL check — should escalate to HIGH
        assert data["risk_category"] == "HIGH_MAINTENANCE_RISK"

    def test_risk_contributing_factors_weights(self):
        client = TestClient(app, base_url=BASE_URL)
        resp = client.get("/api/assistant/risk?transformer_id=T1", headers={})
        data = resp.json()
        available = [f for f in data["contributing_factors"] if f["available"]]
        total_weight = sum(f["weight"] for f in available)
        assert total_weight >= 60

    def test_risk_review_flags(self):
        client = TestClient(app, base_url=BASE_URL)
        resp = client.get("/api/assistant/risk?transformer_id=T1", headers={})
        data = resp.json()
        codes = [f["code"] for f in data["review_flags"]]
        assert "POLICY_NOT_APPROVED" in codes

    def test_risk_no_auth_works(self):
        client = TestClient(app, base_url=BASE_URL)
        resp = client.get("/api/assistant/risk?transformer_id=T1")
        assert resp.status_code == 200

    def test_risk_no_data(self, monkeypatch):
        monkeypatch.setattr("app.api.assistant.db.maintenance_sheet", lambda conn, tid, **kw: [])
        client = TestClient(app, base_url=BASE_URL)
        resp = client.get("/api/assistant/risk?transformer_id=T1", headers={})
        assert resp.status_code == 200
        data = resp.json()
        assert data["risk_category"] == "INSUFFICIENT_EVIDENCE"
        assert data["maintenance_risk_index"] is None

    def test_risk_trend_flag_in_review(self):
        client = TestClient(app, base_url=BASE_URL)
        resp = client.get("/api/assistant/risk?transformer_id=T1", headers={})
        data = resp.json()
        trend_flags = [f for f in data["review_flags"] if f["code"] == "TREND_FLAG"]
        assert len(trend_flags) >= 1
        assert "ACCELERATING_OIL_LEAK" in trend_flags[0]["detail"]


class TestStatus:
    def test_status(self):
        client = TestClient(app, base_url=BASE_URL)
        resp = client.get("/api/assistant/status")
        assert resp.status_code == 200
        data = resp.json()
        assert data["backend"] == "self-contained"
        assert data["status"] == "ok"
