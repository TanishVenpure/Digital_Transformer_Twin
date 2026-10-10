"""Tests for the assistant proxy endpoints (chatbot + risk).

These tests mock the outbound httpx calls so they never contact a real backend.
"""
import json
import os
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from fastapi.testclient import TestClient

# Patch env BEFORE importing the app so credentials are available.
os.environ.setdefault("TWIN_API_CREDENTIALS", "T1:test-secret")
os.environ.setdefault("MAIN_BACKEND_API_KEY", "backend-secret")
os.environ.setdefault("MAIN_BACKEND_URL", "http://mock-backend:8000")

import app.api.assistant_proxy as proxy_mod  # noqa: E402

# Reset cached globals so env vars take effect.
proxy_mod._BACKEND_URL = None
proxy_mod._API_KEY = None

from app.api.main import app  # noqa: E402


class _FakeConn:
    def __enter__(self):
        return self

    def __exit__(self, *a):
        pass

    def cursor(self):
        return type(
            "C", (),
            {"execute": lambda s, q: None,
             "__enter__": lambda s: s,
             "__exit__": lambda s, *a: None},
        )()


@pytest.fixture(autouse=True)
def _skip_db(monkeypatch):
    """Skip database startup for proxy-only tests."""
    monkeypatch.setattr("app.api.main.db.refuse_managed_database", lambda conn: None)
    monkeypatch.setattr("app.api.main.db.create_schema", lambda conn: None)
    monkeypatch.setattr("app.api.main.db.connect", lambda: _FakeConn())


HEADERS = {"X-Twin-API-Key": "test-secret"}
BASE_URL = "http://localhost"


def _mock_response(status=200, body=None):
    content = json.dumps(body or {}).encode()
    return httpx.Response(
        status_code=status, content=content,
        headers={"content-type": "application/json"},
    )


def _patch_client(method="post", response=None, side_effect=None):
    """Context manager that patches httpx.AsyncClient for one proxy call."""
    p = patch("app.api.assistant_proxy.httpx.AsyncClient")

    class _Ctx:
        def __enter__(self):
            MockClient = p.__enter__()
            instance = AsyncMock()
            if side_effect:
                getattr(instance, method).side_effect = side_effect
            else:
                getattr(instance, method).return_value = response
            instance.__aenter__ = AsyncMock(return_value=instance)
            instance.__aexit__ = AsyncMock(return_value=False)
            MockClient.return_value = instance
            return instance

        def __exit__(self, *a):
            p.__exit__(*a)

    return _Ctx()


# --- chat proxy tests ---


class TestChatProxy:
    def test_chat_success(self):
        mock_body = {
            "transformer_id": "T1", "answer": "Oil level is normal.",
            "evidence_source_ids": ["s1"], "warnings": [], "synthetic_evidence": False,
            "facts": [], "inferences": [], "answer_source": "deterministic",
            "risk_assessment": None,
        }
        with _patch_client("post", _mock_response(200, mock_body)):
            client = TestClient(app, base_url=BASE_URL)
            resp = client.post(
                "/api/assistant/chat",
                json={"transformer_id": "T1", "question": "What is the oil level?"},
                headers=HEADERS,
            )
        assert resp.status_code == 200
        data = resp.json()
        assert data["answer"] == "Oil level is normal."
        assert data["answer_source"] == "deterministic"

    def test_chat_no_auth(self):
        client = TestClient(app, base_url=BASE_URL)
        resp = client.post("/api/assistant/chat",
                           json={"transformer_id": "T1", "question": "Test"})
        assert resp.status_code == 401

    def test_chat_backend_403(self):
        with _patch_client("post", _mock_response(403, {"detail": "Transformer access is not authorized"})):
            client = TestClient(app, base_url=BASE_URL)
            resp = client.post(
                "/api/assistant/chat",
                json={"transformer_id": "TX-WRONG", "question": "Test"},
                headers=HEADERS,
            )
        assert resp.status_code == 403

    def test_chat_backend_unreachable(self):
        with _patch_client("post", side_effect=httpx.ConnectError("Connection refused")):
            client = TestClient(app, base_url=BASE_URL)
            resp = client.post(
                "/api/assistant/chat",
                json={"transformer_id": "T1", "question": "Test"},
                headers=HEADERS,
            )
        assert resp.status_code == 502
        assert "not reachable" in resp.json()["detail"]

    def test_chat_backend_timeout(self):
        with _patch_client("post", side_effect=httpx.TimeoutException("timeout")):
            client = TestClient(app, base_url=BASE_URL)
            resp = client.post(
                "/api/assistant/chat",
                json={"transformer_id": "T1", "question": "Test"},
                headers=HEADERS,
            )
        assert resp.status_code == 504

    def test_chat_backend_401(self):
        with _patch_client("post", _mock_response(401, {"detail": "Invalid credentials"})):
            client = TestClient(app, base_url=BASE_URL)
            resp = client.post(
                "/api/assistant/chat",
                json={"transformer_id": "T1", "question": "Test"},
                headers=HEADERS,
            )
        assert resp.status_code == 502
        assert "rejected" in resp.json()["detail"]

    def test_chat_empty_question(self):
        client = TestClient(app, base_url=BASE_URL)
        resp = client.post(
            "/api/assistant/chat",
            json={"transformer_id": "T1", "question": ""},
            headers=HEADERS,
        )
        assert resp.status_code == 422

    def test_chat_deterministic_fallback(self):
        mock_body = {
            "transformer_id": "T1", "answer": "Fallback answer.",
            "evidence_source_ids": [], "warnings": ["Gemini unavailable"],
            "synthetic_evidence": False, "facts": [], "inferences": [],
            "answer_source": "deterministic_fallback", "risk_assessment": None,
        }
        with _patch_client("post", _mock_response(200, mock_body)):
            client = TestClient(app, base_url=BASE_URL)
            resp = client.post(
                "/api/assistant/chat",
                json={"transformer_id": "T1", "question": "What is the status?"},
                headers=HEADERS,
            )
        assert resp.status_code == 200
        assert resp.json()["answer_source"] == "deterministic_fallback"


# --- risk proxy tests ---


class TestRiskProxy:
    def test_risk_success(self):
        mock_body = {
            "transformer_id": "T1", "assessment_id": "a1",
            "assessed_at": "2026-10-10T00:00:00Z",
            "policy_version": "maintenance-risk-0.1.0-unapproved",
            "policy_approved": False,
            "maintenance_risk_index": 42.5,
            "risk_category": "MODERATE_MAINTENANCE_RISK",
            "completeness": "COMPLETE",
            "factor_scores": {}, "factor_weights": {}, "factor_coverage": {},
            "weight_coverage": 100.0,
            "contributing_factors": [],
            "limitations": [], "review_flags": [],
            "records_considered": 10, "fault_events_considered": 3,
            "history_complete": True, "synthetic": False,
            "engineering_review_required": True,
            "methodology_disclaimer": "This is a deterministic score.",
        }
        with _patch_client("get", _mock_response(200, mock_body)):
            client = TestClient(app, base_url=BASE_URL)
            resp = client.get("/api/assistant/risk?transformer_id=T1", headers=HEADERS)
        assert resp.status_code == 200
        data = resp.json()
        assert data["maintenance_risk_index"] == 42.5
        assert data["risk_category"] == "MODERATE_MAINTENANCE_RISK"

    def test_risk_no_auth(self):
        client = TestClient(app, base_url=BASE_URL)
        resp = client.get("/api/assistant/risk?transformer_id=T1")
        assert resp.status_code == 401

    def test_risk_backend_403(self):
        with _patch_client("get", _mock_response(403, {"detail": "Transformer access is not authorized"})):
            client = TestClient(app, base_url=BASE_URL)
            resp = client.get("/api/assistant/risk?transformer_id=TX-WRONG", headers=HEADERS)
        assert resp.status_code == 403

    def test_risk_backend_unreachable(self):
        with _patch_client("get", side_effect=httpx.ConnectError("Connection refused")):
            client = TestClient(app, base_url=BASE_URL)
            resp = client.get("/api/assistant/risk?transformer_id=T1", headers=HEADERS)
        assert resp.status_code == 502

    def test_risk_insufficient_evidence(self):
        mock_body = {
            "transformer_id": "T1", "assessment_id": "a2",
            "assessed_at": "2026-10-10T00:00:00Z",
            "policy_version": "maintenance-risk-0.1.0-unapproved",
            "policy_approved": False,
            "maintenance_risk_index": None,
            "risk_category": "INSUFFICIENT_EVIDENCE",
            "completeness": "INSUFFICIENT_EVIDENCE",
            "factor_scores": {}, "factor_weights": {}, "factor_coverage": {},
            "weight_coverage": 0, "contributing_factors": [],
            "limitations": [], "review_flags": [],
            "records_considered": 0, "fault_events_considered": 0,
            "history_complete": True, "synthetic": False,
            "engineering_review_required": False,
            "methodology_disclaimer": "Deterministic score.",
        }
        with _patch_client("get", _mock_response(200, mock_body)):
            client = TestClient(app, base_url=BASE_URL)
            resp = client.get("/api/assistant/risk?transformer_id=T1", headers=HEADERS)
        assert resp.status_code == 200
        assert resp.json()["risk_category"] == "INSUFFICIENT_EVIDENCE"


# --- backend status ---


class TestBackendStatus:
    def test_status_reachable(self):
        with _patch_client("get", _mock_response(200, {"service": "test"})):
            client = TestClient(app, base_url=BASE_URL)
            resp = client.get("/api/assistant/status")
        assert resp.status_code == 200
        assert resp.json()["backend"] == "reachable"

    def test_status_unreachable(self):
        with _patch_client("get", side_effect=httpx.ConnectError("refused")):
            client = TestClient(app, base_url=BASE_URL)
            resp = client.get("/api/assistant/status")
        assert resp.status_code == 200
        assert resp.json()["backend"] == "unreachable"
