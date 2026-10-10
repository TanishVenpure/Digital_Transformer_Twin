from datetime import date
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from app.config.settings import Settings
from app.database.db import get_db
from app.database.repository import Repository
from app.main import create_app


def test_transformer_selection_endpoints_removed(client):
    for path in (
        "/api/v1/transformers", "/api/v1/transformers/T01",
        "/api/v1/transformers/T01/latest", "/api/v1/transformers/T01/history",
        "/api/v1/transformers/T01/alerts", "/api/v1/transformer",
    ):
        assert client.get(path).status_code == 404


def test_no_data_returns_empty_single_transformer_state(client):
    assert client.get("/api/v1/transformer/latest").json() == {
        "transformer_id": None, "latest_reading": None, "latest_daily_metric": None
    }
    assert client.get("/api/v1/transformer/history").json() == {
        "transformer_id": None, "recent_readings": [], "daily_metrics": [], "alerts": []
    }


def test_history_uses_utc_dates_and_per_collection_pagination(client):
    with (
        patch.object(Repository, "transformer_ids", return_value=["source-ID"]),
        patch.object(Repository, "readings", return_value=[]) as readings,
        patch.object(Repository, "daily_metrics", return_value=[]) as metrics,
        patch.object(Repository, "alerts", return_value=[]) as alerts,
    ):
        response = client.get("/api/v1/transformer/history", params={
            "start_date": "2026-10-09T01:00:00+05:30",
            "end_date": "2026-10-09T12:00:00+05:30", "limit": 7, "offset": 3,
        })
    assert response.status_code == 200
    assert response.json()["transformer_id"] == "source-ID"
    assert metrics.call_args.kwargs == {
        "start_date": date(2026, 10, 8), "end_date": date(2026, 10, 9), "limit": 7, "offset": 3
    }
    assert readings.call_args.args == ("source-ID",)
    assert readings.call_args.kwargs["offset"] == 3
    assert alerts.call_args.kwargs["transformer_id"] == "source-ID"


def test_latest_uses_stored_source_identifier(client):
    with (
        patch.object(Repository, "transformer_ids", return_value=["actual-source-ID"]),
        patch.object(Repository, "readings", return_value=[]) as readings,
        patch.object(Repository, "daily_metrics", return_value=[]),
    ):
        response = client.get("/api/v1/transformer/latest")
    assert response.json()["transformer_id"] == "actual-source-ID"
    readings.assert_called_once_with("actual-source-ID", limit=1)


def test_multiple_transformers_are_not_silently_combined(client):
    with patch.object(Repository, "transformer_ids", return_value=["T01", "T02"]):
        assert client.get("/api/v1/transformer/history").status_code == 400


def test_invalid_history_pagination_and_dates(client):
    assert client.get("/api/v1/transformer/history?offset=-1").status_code == 422
    response = client.get("/api/v1/transformer/history", params={
        "start_date": "2026-10-10T00:00:00Z", "end_date": "2026-10-09T00:00:00Z"
    })
    assert response.status_code == 400


TRANSFORMER_ROUTES = ("/api/v1/transformer/latest", "/api/v1/transformer/history")


@pytest.mark.parametrize("path", TRANSFORMER_ROUTES)
@pytest.mark.parametrize("key", [None, "", "wrong-key"])
def test_transformer_routes_require_valid_credentials(client, path, key):
    if key is None:
        client.headers.pop("X-Agent-API-Key")
    else:
        client.headers["X-Agent-API-Key"] = key
    with (
        patch.object(Repository, "transformer_ids", return_value=["T01"]) as identifiers,
        patch.object(Repository, "readings", return_value=[]) as readings,
    ):
        response = client.get(path)
    assert response.status_code == 401
    identifiers.assert_not_called()
    readings.assert_not_called()


@pytest.mark.parametrize("path", TRANSFORMER_ROUTES)
def test_unconfigured_credentials_fail_closed(db, path):
    application = create_app(Settings(agent_credentials=""))
    application.dependency_overrides[get_db] = lambda: db
    with TestClient(application) as unconfigured:
        response = unconfigured.get(path, headers={"X-Agent-API-Key": "test-agent-key"})
    assert response.status_code == 503


@pytest.mark.parametrize("path", TRANSFORMER_ROUTES)
def test_stored_transformer_outside_credential_scope_is_forbidden(client, path):
    with (
        patch.object(Repository, "transformer_ids", return_value=["T-OTHER"]),
        patch.object(Repository, "readings", return_value=[]) as readings,
        patch.object(Repository, "daily_metrics", return_value=[]) as metrics,
        patch.object(Repository, "alerts", return_value=[]) as alerts,
    ):
        response = client.get(path)
    assert response.status_code == 403
    readings.assert_not_called()
    metrics.assert_not_called()
    alerts.assert_not_called()


@pytest.mark.parametrize("path", TRANSFORMER_ROUTES)
def test_client_transformer_selector_cannot_bypass_server_resolution(client, path):
    # TX-001 is within the caller's scope, but the stored transformer is not.
    with (
        patch.object(Repository, "transformer_ids", return_value=["T-OTHER"]),
        patch.object(Repository, "readings", return_value=[]) as readings,
    ):
        response = client.get(path, params={"transformer_id": "TX-001"})
    assert response.status_code == 403
    readings.assert_not_called()


@pytest.mark.parametrize("path", TRANSFORMER_ROUTES)
def test_scoped_credential_reads_its_own_transformer(client, path):
    with (
        patch.object(Repository, "transformer_ids", return_value=["T-OTHER"]),
        patch.object(Repository, "readings", return_value=[]) as readings,
        patch.object(Repository, "daily_metrics", return_value=[]),
        patch.object(Repository, "alerts", return_value=[]),
    ):
        response = client.get(path, headers={"X-Agent-API-Key": "other-scope-key"})
    assert response.status_code == 200
    assert response.json()["transformer_id"] == "T-OTHER"
    assert readings.call_args.args == ("T-OTHER",)


def test_history_collections_are_all_queried_for_the_authorized_transformer_only(client):
    with (
        patch.object(Repository, "transformer_ids", return_value=["T01"]),
        patch.object(Repository, "readings", return_value=[]) as readings,
        patch.object(Repository, "daily_metrics", return_value=[]) as metrics,
        patch.object(Repository, "alerts", return_value=[]) as alerts,
    ):
        response = client.get("/api/v1/transformer/history", params={"transformer_id": "T-OTHER"})
    assert response.status_code == 200
    assert response.json()["transformer_id"] == "T01"
    assert readings.call_args.args == ("T01",)
    assert metrics.call_args.args == ("T01",)
    assert alerts.call_args.kwargs["transformer_id"] == "T01"


@pytest.mark.parametrize("path", TRANSFORMER_ROUTES)
def test_empty_database_still_requires_credentials(client, path):
    client.headers.pop("X-Agent-API-Key")
    assert client.get(path).status_code == 401
