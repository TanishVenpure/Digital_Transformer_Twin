"""Write permissions: transformer scope alone (a chat/read key) never grants writes."""
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from app.config.settings import Settings
from app.database.db import get_db
from app.main import create_app
from app.models import Alert

CHAT_ONLY = {"X-Agent-API-Key": "chat-only-key"}
READING = {"transformer_id": "TX-001", "timestamp": "2026-10-09T10:00:00Z", "data": {"value": 1.0}}
MAINTENANCE = {
    "transformer_id": "TX-001", "record_type": "inspection",
    "occurred_at": "2026-10-09T10:00:00Z", "details": {}, "notes": None,
}

INGEST_WRITES = [
    ("post", "/api/v1/data/reading", {"json": READING}),
    ("post", "/api/v1/data/batch", {"json": [READING]}),
    ("post", "/api/v1/data/upload", {"files": {"file": ("input.csv", b"a,b\n1,2\n", "text/csv")}}),
    ("post", "/api/v1/maintenance/records", {"json": MAINTENANCE}),
]
ALERT_WRITES = [
    ("patch", "/api/v1/alerts/1/acknowledge", {}),
    ("patch", "/api/v1/alerts/1/resolve", {}),
]


def alert(transformer_id="TX-001"):
    now = datetime(2026, 10, 9, tzinfo=timezone.utc)
    # Transport fixture only; not a formula or threshold.
    return Alert(
        id=1, transformer_id=transformer_id, timestamp=now, parameter="fixture",
        formula_name="fixture", formula_value=0.0, threshold=0.0, comparison_operator=">",
        severity="fixture", message="fixture", formula_inputs={}, details={},
        maintenance_type=None, acknowledged=False, resolved=False, resolved_at=None, created_at=now,
    )


def app_client(db, credentials):
    application = create_app(Settings(database_url=None, agent_credentials=credentials))
    application.dependency_overrides[get_db] = lambda: db
    return TestClient(application)


def assert_no_writes(db):
    db.add.assert_not_called()
    db.add_all.assert_not_called()
    db.execute.assert_not_called()
    db.commit.assert_not_called()


@pytest.mark.parametrize("method,path,kwargs", INGEST_WRITES)
def test_chat_only_key_cannot_ingest(client, db, method, path, kwargs):
    response = getattr(client, method)(path, headers=CHAT_ONLY, **kwargs)
    assert response.status_code == 403
    assert response.json() == {"detail": "Ingestion access is not authorized"}
    assert_no_writes(db)


@pytest.mark.parametrize("method,path,kwargs", ALERT_WRITES)
def test_chat_only_key_cannot_mutate_alerts(client, db, method, path, kwargs):
    record = alert()
    db.get.return_value = record
    response = getattr(client, method)(path, headers=CHAT_ONLY, **kwargs)
    assert response.status_code == 403
    assert response.json() == {"detail": "Alert management is not authorized"}
    assert record.acknowledged is False and record.resolved is False and record.resolved_at is None
    assert_no_writes(db)


def test_chat_only_key_keeps_read_access(client, db):
    db.get.return_value = alert()
    assert client.get("/api/v1/alerts", headers=CHAT_ONLY).status_code == 200
    assert client.get("/api/v1/alerts/1", headers=CHAT_ONLY).status_code == 200
    assert client.get(
        "/api/v1/maintenance/records", params={"transformer_id": "TX-001"}, headers=CHAT_ONLY
    ).status_code == 200
    assert client.post(
        "/api/v1/agent/chat", headers=CHAT_ONLY, json={"transformer_id": "TX-001", "question": "Status?"}
    ).status_code == 200
    assert_no_writes(db)


def test_every_write_route_rejects_chat_only_key(client, db):
    """Census: any POST/PUT/PATCH/DELETE route added later must not accept a read key."""
    read_only_posts = {"/api/v1/agent/events", "/api/v1/agent/chat"}  # evidence lookups, no writes
    checked = []
    for path, operations in client.app.openapi()["paths"].items():
        for method in operations:
            if method.upper() not in {"POST", "PUT", "PATCH", "DELETE"} or path in read_only_posts:
                continue
            response = client.request(method.upper(), path.replace("{alert_id}", "1"), headers=CHAT_ONLY)
            assert response.status_code == 403, (method, path, response.status_code)
            checked.append(f"{method.upper()} {path}")
    # Exact census: a new write route must be added here deliberately.
    assert sorted(checked) == sorted([
        "POST /api/v1/data/reading", "POST /api/v1/data/batch", "POST /api/v1/data/upload",
        "POST /api/v1/maintenance/records",
        "PATCH /api/v1/alerts/{alert_id}/acknowledge", "PATCH /api/v1/alerts/{alert_id}/resolve",
        "POST /api/v1/simulation/start", "POST /api/v1/simulation/pause",
        "POST /api/v1/simulation/reset", "POST /api/v1/simulation/step",
    ])
    assert_no_writes(db)


@pytest.mark.parametrize("method,path,kwargs", INGEST_WRITES + ALERT_WRITES)
def test_write_routes_still_require_valid_key(client, db, method, path, kwargs):
    client.headers.pop("X-Agent-API-Key")
    response = getattr(client, method)(path, **kwargs)
    assert response.status_code == 401
    assert_no_writes(db)


def test_ingest_permission_with_scope_can_write_but_not_manage_alerts(db):
    def assign_generated_fields():
        from app.models.base import utc_now
        reading = db.add.call_args.args[0]
        reading.id = 1
        reading.created_at = utc_now()
    db.flush.side_effect = assign_generated_fields
    with app_client(db, "TX-001,@ingest:ingest-key") as client:
        headers = {"X-Agent-API-Key": "ingest-key"}
        assert client.post("/api/v1/data/reading", json=READING, headers=headers).status_code == 201
        db.get.return_value = alert()
        assert client.patch("/api/v1/alerts/1/acknowledge", headers=headers).status_code == 403
        assert db.get.return_value.acknowledged is False


def test_alerts_permission_with_scope_can_manage_but_not_ingest(db):
    record = alert()
    db.get.return_value = record
    with app_client(db, "TX-001,@alerts:alerts-key") as client:
        headers = {"X-Agent-API-Key": "alerts-key"}
        assert client.patch("/api/v1/alerts/1/acknowledge", headers=headers).status_code == 200
        assert record.acknowledged is True
        assert client.post("/api/v1/data/reading", json=READING, headers=headers).status_code == 403
        db.add.assert_not_called()


def test_permission_still_requires_transformer_scope(db):
    record = alert("TX-002")
    db.get.return_value = record
    with app_client(db, "TX-001,@ingest,@alerts:scoped-key") as client:
        headers = {"X-Agent-API-Key": "scoped-key"}
        out_of_scope = {**READING, "transformer_id": "TX-002"}
        assert client.post("/api/v1/data/reading", json=out_of_scope, headers=headers).status_code == 403
        assert client.post("/api/v1/data/batch", json=[READING, out_of_scope], headers=headers).status_code == 403
        assert client.post(
            "/api/v1/maintenance/records", json={**MAINTENANCE, "transformer_id": "TX-002"}, headers=headers
        ).status_code == 403
        # Another transformer's alert stays hidden even with @alerts.
        assert client.patch("/api/v1/alerts/1/resolve", headers=headers).status_code == 404
    assert record.resolved is False
    assert_no_writes(db)


@pytest.mark.parametrize("method,path,kwargs", INGEST_WRITES + ALERT_WRITES)
def test_permission_without_any_transformer_scope_is_forbidden(db, method, path, kwargs):
    db.get.return_value = alert()
    with app_client(db, "@ingest,@alerts:permission-only-key") as client:
        response = getattr(client, method)(path, headers={"X-Agent-API-Key": "permission-only-key"}, **kwargs)
    assert response.status_code == 403
    assert_no_writes(db)


@pytest.mark.parametrize("scope", [
    "TX-001,@Ingest", "TX-001,@ingests", "TX-001,@INGEST", "TX-001,ingest",   # misspelled / not a permission
    "TX-001,@ingest ,@alert", "TX-001,@ alerts",
])
def test_misspelled_permissions_fail_closed(db, scope):
    db.get.return_value = alert()
    with app_client(db, f"{scope}:typo-key") as client:
        headers = {"X-Agent-API-Key": "typo-key"}
        assert client.patch("/api/v1/alerts/1/acknowledge", headers=headers).status_code == 403
        if "@ingest " not in scope:
            assert client.post("/api/v1/data/reading", json=READING, headers=headers).status_code == 403
    assert db.get.return_value.acknowledged is False


def test_whitespace_around_valid_permission_is_trimmed_like_transformer_ids(db):
    # Scope items are stripped consistently; " @ingest " is the @ingest permission.
    def assign_generated_fields():
        from app.models.base import utc_now
        reading = db.add.call_args.args[0]
        reading.id = 1
        reading.created_at = utc_now()
    db.flush.side_effect = assign_generated_fields
    with app_client(db, "TX-001, @ingest :spaced-key") as client:
        response = client.post("/api/v1/data/reading", json=READING, headers={"X-Agent-API-Key": "spaced-key"})
    assert response.status_code == 201


def test_permission_names_cannot_be_used_as_transformer_ids(client, db):
    response = client.post("/api/v1/data/reading", json={**READING, "transformer_id": "@ingest"})
    assert response.status_code == 403
    assert_no_writes(db)
