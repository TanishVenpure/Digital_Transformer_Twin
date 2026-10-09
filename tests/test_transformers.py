from datetime import date
from unittest.mock import patch

from app.database.repository import Repository


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
