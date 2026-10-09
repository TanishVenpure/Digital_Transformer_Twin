from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.database.db import get_db
from app.main import create_app


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
    application = create_app()
    application.dependency_overrides[get_db] = lambda: db
    with TestClient(application) as test_client:
        yield test_client
