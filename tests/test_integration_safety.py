"""Guards for the opt-in PostgreSQL tests; these run without any database."""
from unittest.mock import MagicMock

import pytest
from sqlalchemy.orm import Session

from tests.test_postgres_integration import (
    INTEGRATION_API_KEY,
    integration_client,
    require_isolated_test_database,
)

APP_DATABASE = "postgresql+psycopg://user:secret@localhost:5432/digital_transformer"


@pytest.fixture
def app_database(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", APP_DATABASE)


@pytest.mark.parametrize("url", [
    "postgresql+psycopg://user:secret@localhost:5432/digital_transformer",
    "postgresql+psycopg://user:secret@localhost/digital_transformer",
    "postgresql+psycopg://user:secret@127.0.0.1:5432/digital_transformer",
    "postgresql+psycopg://user:secret@localhost:5432/scratch",
    "postgresql+psycopg://user:secret@localhost:5432/test_digital_transformer",
    "postgresql+psycopg://user:secret@localhost:5432/",
    "postgresql+psycopg2://user:secret@localhost:5432/digital_transformer_test",
    "sqlite:///digital_transformer_test",
    "not a url",
])
def test_unsafe_test_database_urls_fail_without_revealing_credentials(app_database, url):
    with pytest.raises(pytest.fail.Exception) as failure:
        require_isolated_test_database(url)
    assert "secret" not in str(failure.value)


def test_test_database_matching_application_database_fails(monkeypatch):
    monkeypatch.setenv(
        "DATABASE_URL", "postgresql+psycopg://user:secret@localhost:5432/shared_test"
    )
    with pytest.raises(pytest.fail.Exception, match="must not point at"):
        require_isolated_test_database(
            "postgresql+psycopg://other:secret@127.0.0.1/shared_test"
        )


def test_dedicated_test_database_is_accepted(app_database):
    url = "postgresql+psycopg://user:secret@localhost:5432/digital_transformer_test"
    assert require_isolated_test_database(url) == url


def test_integration_client_uses_only_its_scoped_test_credential():
    session = MagicMock(spec=Session)
    session.scalars.return_value.all.return_value = []
    with integration_client(session) as client:
        assert client.headers["X-Agent-API-Key"] == INTEGRATION_API_KEY
        assert client.get("/api/v1/alerts", params={"transformer_id": "integration"}).status_code == 200
        assert client.get("/api/v1/alerts", params={"transformer_id": "TX-001"}).status_code == 403
        client.headers.pop("X-Agent-API-Key")
        assert client.get("/api/v1/alerts").status_code == 401
