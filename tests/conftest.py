from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.database.db import get_db
from app.config.settings import Settings
from app.main import create_app


@pytest.fixture(autouse=True)
def no_live_llm(request, monkeypatch):
    """Never reach a live LLM from a developer .env; only gemini_live tests opt in."""
    if request.node.get_closest_marker("gemini_live") is None:
        monkeypatch.setenv("LLM_PROVIDER", "none")


@pytest.fixture
def db():
    """A dependency double, never an alternate SQL database."""
    session = MagicMock(spec=Session)
    session.get.return_value = None
    session.scalar.return_value = None
    session.scalars.return_value.all.return_value = []
    return session


@pytest.fixture
def client(db):
    application = create_app(Settings(
        agent_credentials=(
            "TX-001:test-agent-key;TX-UNKNOWN:test-agent-key;T01:test-agent-key;"
            "T-A:test-agent-key;T-UNKNOWN:test-agent-key;"
            "source-ID,actual-source-ID:test-agent-key;"
            # The default fixture key may also write; chat-only-key below may not.
            "@ingest,@alerts:test-agent-key;"
            "T-OTHER,@alerts:other-scope-key;"
            "@simulation:simulation-test-key;"
            "TX-001,T01:chat-only-key"
        ),
    ))
    application.dependency_overrides[get_db] = lambda: db
    with TestClient(application) as test_client:
        test_client.headers.update({"X-Agent-API-Key": "test-agent-key"})
        yield test_client
