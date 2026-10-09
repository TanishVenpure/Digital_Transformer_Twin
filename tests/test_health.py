from fastapi.testclient import TestClient

from app.main import create_app


def test_root_and_health():
    with TestClient(create_app()) as client:
        assert client.get("/").json() == {
            "service": "Digital Transformer Twin API", "status": "running"
        }
        assert client.get("/api/v1/health").json() == {
            "status": "healthy", "service": "digital-transformer-twin"
        }


def test_database_health(client, db):
    assert client.get("/api/v1/health/db").status_code == 200
    assert str(db.execute.call_args.args[0]) == "SELECT 1"


def test_missing_database_is_sanitized(monkeypatch):
    from app.database import db as database
    monkeypatch.setattr(database, "get_session_factory", lambda: fail_database())
    with TestClient(create_app()) as client:
        response = client.get("/api/v1/health/db")
    assert response.status_code == 503
    assert response.json() == {"detail": "Database unavailable or not configured"}


def fail_database():
    from app.database.db import DatabaseNotConfigured
    raise DatabaseNotConfigured("secret connection details")


def test_database_failure_is_sanitized(client, db):
    from sqlalchemy.exc import OperationalError
    db.execute.side_effect = OperationalError("private query", {}, Exception("private password"))
    response = client.get("/api/v1/health/db")
    assert response.status_code == 503
    assert "private" not in response.text
