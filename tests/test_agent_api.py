from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import patch

from app.database.repository import Repository

HEADERS = {"X-Agent-API-Key": "test-agent-key"}


def _reading(identifier=1, transformer_id="TX-001"):
    return SimpleNamespace(
        id=identifier, transformer_id=transformer_id,
        timestamp=datetime(2026, 10, 9, 10, tzinfo=timezone.utc), raw_data={"I_A": 10},
    )


def _alert(identifier=2, transformer_id="TX-001"):
    return SimpleNamespace(
        id=identifier, transformer_id=transformer_id,
        timestamp=datetime(2026, 10, 9, 10, tzinfo=timezone.utc), parameter="current",
        formula_name="current_rule", formula_value=2.0, threshold=1.0,
        comparison_operator=">", severity="WARNING", message="Current warning",
        maintenance_type=None, details={}, formula_inputs={"I_A": 10},
        acknowledged=False, resolved=False,
    )


def test_agent_event_is_wired_with_strict_contract_and_transformer_scope(client):
    with patch.object(Repository, "readings", return_value=[_reading()]) as readings, \
         patch.object(Repository, "alerts", return_value=[_alert()]) as alerts:
        response = client.post("/api/v1/agent/events", headers=HEADERS, json={
            "event_id": "evt-1", "transformer_id": "TX-001",
            "timestamp": "2026-10-09T10:00:00Z", "payload": {},
        })

    assert response.status_code == 200
    assert response.json()["transformer_id"] == "TX-001"
    assert response.json()["status"] == "review_required"
    readings.assert_called_once_with("TX-001", limit=51, offset=0)
    assert alerts.call_args.kwargs["transformer_id"] == "TX-001"


def test_agent_chat_discloses_backend_limitations_and_does_not_claim_synthetic_data(client):
    with patch.object(Repository, "readings", return_value=[]), \
         patch.object(Repository, "alerts", return_value=[]):
        response = client.post("/api/v1/agent/chat", headers=HEADERS, json={
            "transformer_id": "TX-UNKNOWN", "question": "What is available?",
        })

    assert response.status_code == 200
    body = response.json()
    assert body["synthetic_evidence"] is False
    assert "no transformer-scoped telemetry" in body["answer"]
    assert any("maintenance-history" in warning for warning in body["warnings"])


def test_agent_rejects_extra_request_fields(client):
    response = client.post("/api/v1/agent/chat", headers=HEADERS, json={
        "transformer_id": "TX-001", "question": "status", "authorized": True,
    })
    assert response.status_code == 422


def test_agent_returns_service_error_for_malformed_backend_record(client):
    with patch.object(Repository, "readings", return_value=[_reading(identifier=None)]), \
         patch.object(Repository, "alerts", return_value=[]):
        response = client.post("/api/v1/agent/events", headers=HEADERS, json={
            "event_id": "evt-1", "transformer_id": "TX-001",
            "timestamp": "2026-10-09T10:00:00Z",
        })

    assert response.status_code == 503
    assert response.json() == {"detail": "Backend evidence is unavailable"}


def test_agent_requires_credentials(client):
    client.headers.pop("X-Agent-API-Key", None)
    response = client.post("/api/v1/agent/chat", json={
        "transformer_id": "TX-001", "question": "status",
    })
    assert response.status_code == 401


def test_agent_rejects_transformer_outside_server_allowlist(client):
    response = client.post("/api/v1/agent/chat", headers=HEADERS, json={
        "transformer_id": "TX-OTHER", "question": "status",
    })
    assert response.status_code == 403


def test_agent_credential_is_scoped_to_its_transformer(client):
    response = client.post("/api/v1/agent/chat", headers=HEADERS, json={
        "transformer_id": "TX-002", "question": "status",
    })
    assert response.status_code == 403
