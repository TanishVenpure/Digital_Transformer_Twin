from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Session

from app.alerts.engine import AlertEngine
from app.database.repository import Repository
from app.models import Alert


def alert_record():
    now = datetime(2026, 10, 9, tzinfo=timezone.utc)
    # Transport fixture only; these values are not a transformer formula or threshold.
    return Alert(
        id=1, transformer_id="T01", timestamp=now, parameter="fixture",
        formula_name="fixture", formula_value=0.0, threshold=0.0,
        comparison_operator=">", severity="fixture", message="Stored alert fixture",
        formula_inputs={}, details={}, maintenance_type=None, acknowledged=False, resolved=False,
        resolved_at=None, created_at=now,
    )


def test_empty_alert_list(client):
    assert client.get("/api/v1/alerts").json() == []


@pytest.mark.parametrize("path,method", [
    ("/api/v1/alerts/999", "get"),
    ("/api/v1/alerts/999/acknowledge", "patch"),
    ("/api/v1/alerts/999/resolve", "patch"),
])
def test_missing_alert(client, path, method):
    assert getattr(client, method)(path).status_code == 404


def test_acknowledge_and_resolve(client, db):
    record = alert_record()
    db.get.return_value = record
    response = client.patch("/api/v1/alerts/1/acknowledge")
    assert response.status_code == 200
    assert response.json()["acknowledged"] is True
    response = client.patch("/api/v1/alerts/1/resolve")
    assert response.json()["resolved"] is True
    assert record.resolved_at.tzinfo == timezone.utc
    first_resolution = record.resolved_at
    client.patch("/api/v1/alerts/1/resolve")
    assert record.resolved_at == first_resolution


@pytest.mark.parametrize("query", ["limit=0", "limit=1001", "offset=-1", "resolved=invalid"])
def test_invalid_filters(client, query):
    assert client.get("/api/v1/alerts?" + query).status_code == 422


def test_reversed_date_range(client):
    response = client.get("/api/v1/alerts", params={
        "start_date": "2026-10-10T00:00:00Z", "end_date": "2026-10-09T00:00:00Z"
    })
    assert response.status_code == 400


def test_repository_applies_all_alert_filters():
    session = MagicMock(spec=Session)
    session.scalars.return_value.all.return_value = []
    now = datetime(2026, 10, 9, tzinfo=timezone.utc)
    Repository(session).alerts(
        transformer_id="T01", parameter="fixture", severity="fixture",
        start_date=now, end_date=now, acknowledged=False, resolved=False, limit=7, offset=3,
    )
    query = session.scalars.call_args.args[0].compile(dialect=postgresql.dialect())
    sql = str(query)
    for column in ("transformer_id", "parameter", "severity", "timestamp", "acknowledged", "resolved"):
        assert "alerts." + column in sql
    assert "alerts.acknowledged = false" in sql
    assert "alerts.resolved = false" in sql
    assert 7 in query.params.values()
    assert 3 in query.params.values()


def test_alert_transformer_filter_is_applied_in_database_query():
    session = MagicMock(spec=Session)
    session.scalars.return_value.all.return_value = []
    Repository(session).alerts(transformer_id="T-A", limit=10, offset=0)
    query = session.scalars.call_args.args[0].compile(dialect=postgresql.dialect())
    assert "alerts.transformer_id" in str(query)
    assert "T-A" in query.params.values()


@pytest.mark.parametrize("operator,value,expected", [
    (">", 2, True), (">", 1, False), (">=", 1, True),
    ("<", 0, True), ("<", 1, False), ("<=", 1, True),
])
def test_generic_comparison(operator, value, expected):
    assert AlertEngine.compare(value, 1, operator) is expected


def test_unsupported_comparison():
    with pytest.raises(ValueError):
        AlertEngine.compare(1, 1, "=")


def test_empty_alert_history_uses_fixed_route(client):
    response = client.get("/api/v1/alerts/history")
    assert response.status_code == 200
    assert response.json() == {"items": [], "limit": 50, "offset": 0, "next_offset": None}


def test_history_keeps_resolved_alerts_and_inputs(client):
    from unittest.mock import patch
    record = alert_record()
    record.resolved = True
    record.resolved_at = record.timestamp
    record.formula_inputs = {"source_payload": {"preserved": True}}
    with patch.object(Repository, "alerts", return_value=[record]) as query:
        response = client.get("/api/v1/alerts/history")
    assert response.status_code == 200
    item = response.json()["items"][0]
    assert item["resolved"] is True
    assert item["formula_inputs"] == record.formula_inputs
    assert query.call_args.kwargs["resolved"] is None
    assert "timestamp" in item and "resolved_at" in item


def test_alert_history_pagination_and_filters(client):
    from unittest.mock import patch
    records = [alert_record(), alert_record(), alert_record()]
    for identifier, record in enumerate(records, start=1):
        record.id = identifier
    with patch.object(Repository, "alerts", return_value=records) as query:
        response = client.get("/api/v1/alerts/history", params={
            "limit": 2, "offset": 4, "resolved": "false", "acknowledged": "false",
            "severity": "fixture", "parameter": "fixture",
            "transformer_id": "T01",
        })
    assert response.status_code == 200
    body = response.json()
    assert [item["id"] for item in body["items"]] == [1, 2]
    assert body["next_offset"] == 6
    assert query.call_args.kwargs["limit"] == 3
    assert query.call_args.kwargs["offset"] == 4
    assert query.call_args.kwargs["resolved"] is False
    assert query.call_args.kwargs["acknowledged"] is False
    assert query.call_args.kwargs["transformer_id"] == "T01"


def test_alert_and_history_transformer_filter_isolates_records_and_supports_unknown_id(client):
    from unittest.mock import patch

    records = []
    for identifier, transformer_id in enumerate(("T-A", "T-B", "T-A"), start=1):
        record = alert_record()
        record.id = identifier
        record.transformer_id = transformer_id
        records.append(record)

    def filtered_alerts(**filters):
        transformer_id = filters.get("transformer_id")
        selected = [record for record in records if transformer_id is None or record.transformer_id == transformer_id]
        return selected[filters.get("offset", 0):filters.get("offset", 0) + filters.get("limit", 50)]

    with patch.object(Repository, "alerts", side_effect=filtered_alerts) as query:
        list_response = client.get("/api/v1/alerts", params={"transformer_id": "T-A"})
        history_response = client.get("/api/v1/alerts/history", params={
            "transformer_id": "T-A", "limit": 1, "offset": 1,
        })
        unknown_response = client.get("/api/v1/alerts", params={"transformer_id": "T-UNKNOWN"})

    assert [item["transformer_id"] for item in list_response.json()] == ["T-A", "T-A"]
    assert [item["transformer_id"] for item in history_response.json()["items"]] == ["T-A"]
    assert history_response.json()["next_offset"] is None
    assert unknown_response.json() == []
    assert query.call_count == 3


def test_omitting_transformer_filter_preserves_all_alerts(client):
    from unittest.mock import patch

    records = []
    for identifier, transformer_id in enumerate(("T-A", "T-B"), start=1):
        record = alert_record()
        record.id = identifier
        record.transformer_id = transformer_id
        records.append(record)

    with patch.object(Repository, "alerts", return_value=records) as query:
        response = client.get("/api/v1/alerts")

    assert [item["transformer_id"] for item in response.json()] == ["T-A", "T-B"]
    assert query.call_args.kwargs["transformer_id"] is None
    # Omitting the filter is limited to the credential's scope, never all transformers.
    assert query.call_args.kwargs["transformer_ids"] == [
        "T-A", "T-UNKNOWN", "T01", "TX-001", "TX-UNKNOWN", "actual-source-ID", "source-ID",
    ]
    assert "T-OTHER" not in query.call_args.kwargs["transformer_ids"]


@pytest.mark.parametrize("path,method", [
    ("/api/v1/alerts", "get"),
    ("/api/v1/alerts/history", "get"),
    ("/api/v1/alerts/1", "get"),
    ("/api/v1/alerts/1/acknowledge", "patch"),
    ("/api/v1/alerts/1/resolve", "patch"),
])
@pytest.mark.parametrize("headers", [{"X-Agent-API-Key": ""}, {"X-Agent-API-Key": "wrong-key"}])
def test_alert_routes_require_valid_credentials(client, db, path, method, headers):
    record = alert_record()
    db.get.return_value = record
    response = getattr(client, method)(path, headers=headers)
    assert response.status_code == 401
    assert record.acknowledged is False and record.resolved is False


def test_alert_routes_without_header_are_rejected(client):
    client.headers.pop("X-Agent-API-Key")
    assert client.get("/api/v1/alerts").status_code == 401


@pytest.mark.parametrize("path", ["/api/v1/alerts", "/api/v1/alerts/history"])
def test_out_of_scope_transformer_filter_is_forbidden(client, path):
    from unittest.mock import patch

    with patch.object(Repository, "alerts", return_value=[]) as query:
        response = client.get(path, params={"transformer_id": "T-OTHER"})
    assert response.status_code == 403
    query.assert_not_called()


@pytest.mark.parametrize("path,method", [
    ("/api/v1/alerts/1", "get"),
    ("/api/v1/alerts/1/acknowledge", "patch"),
    ("/api/v1/alerts/1/resolve", "patch"),
])
def test_out_of_scope_alert_is_hidden_and_not_modified(client, db, path, method):
    record = alert_record()
    record.transformer_id = "T-OTHER"
    db.get.return_value = record
    response = getattr(client, method)(path)
    assert response.status_code == 404
    assert record.acknowledged is False
    assert record.resolved is False and record.resolved_at is None
    db.commit.assert_not_called()


def test_scoped_credential_can_manage_its_own_alert(client, db):
    record = alert_record()
    record.transformer_id = "T-OTHER"
    db.get.return_value = record
    response = client.patch(
        "/api/v1/alerts/1/acknowledge", headers={"X-Agent-API-Key": "other-scope-key"},
    )
    assert response.status_code == 200
    assert record.acknowledged is True


def test_repository_restricts_alerts_to_authorized_transformers():
    session = MagicMock(spec=Session)
    session.scalars.return_value.all.return_value = []
    Repository(session).alerts(transformer_ids=["T-A", "T-B"], limit=10, offset=0)
    query = session.scalars.call_args.args[0].compile(dialect=postgresql.dialect())
    assert "alerts.transformer_id IN" in str(query)
    assert ["T-A", "T-B"] in query.params.values()


def test_transformer_filter_preserves_severity_status_and_date_filters(client):
    from unittest.mock import patch

    record = alert_record()
    record.transformer_id = "T-A"
    record.severity = "critical"
    record.resolved = True

    with patch.object(Repository, "alerts", return_value=[record]) as query:
        response = client.get("/api/v1/alerts", params={
            "transformer_id": "T-A", "severity": "critical", "resolved": "true",
            "start_date": "2026-10-08T00:00:00Z", "end_date": "2026-10-10T00:00:00Z",
        })

    assert response.status_code == 200
    assert response.json()[0]["transformer_id"] == "T-A"
    filters = query.call_args.kwargs
    assert filters["transformer_id"] == "T-A"
    assert filters["severity"] == "critical"
    assert filters["resolved"] is True
    assert filters["start_date"].tzinfo == timezone.utc
    assert filters["end_date"].tzinfo == timezone.utc


def test_history_invalid_date_range(client):
    response = client.get("/api/v1/alerts/history", params={
        "start_date": "2026-10-10T00:00:00Z", "end_date": "2026-10-09T00:00:00Z"
    })
    assert response.status_code == 400


def test_agent_contract_exposes_alert_transformer_selector(client):
    schema = client.get("/openapi.json").json()
    assert "/api/v1/transformers" not in schema["paths"]
    assert "/api/v1/transformer/latest" in schema["paths"]
    assert "/api/v1/transformer/history" in schema["paths"]
    for path in ("/api/v1/alerts", "/api/v1/alerts/history"):
        parameters = schema["paths"][path]["get"].get("parameters", [])
        assert any(parameter["name"] == "transformer_id" for parameter in parameters)
    history_parameters = schema["paths"]["/api/v1/transformer/history"]["get"].get("parameters", [])
    assert all(parameter["name"] != "transformer_id" for parameter in history_parameters)
