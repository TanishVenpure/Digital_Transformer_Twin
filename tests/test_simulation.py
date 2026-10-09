import pytest


def test_simulation_lifecycle(client):
    base = "/api/v1/simulation"
    initial = {"status": "STOPPED", "current_step": 0, "speed": 1.0}
    assert client.get(base + "/status").json() == initial
    assert client.post(base + "/pause").json() == initial
    started = client.post(base + "/start", json={"speed": 2.5})
    assert started.status_code == 200
    assert started.json()["status"] == "RUNNING"
    assert started.json()["speed"] == 2.5
    assert client.post(base + "/step").json()["current_step"] == 1
    assert client.post(base + "/pause").json()["status"] == "PAUSED"
    assert client.post(base + "/reset").json() == initial


@pytest.mark.parametrize("speed", [0, -1, "invalid"])
def test_invalid_speed(client, speed):
    assert client.post("/api/v1/simulation/start", json={"speed": speed}).status_code == 422


def test_start_without_body(client):
    assert client.post("/api/v1/simulation/start").json()["status"] == "RUNNING"


def test_simulation_does_not_store_readings(client, db):
    client.post("/api/v1/simulation/start")
    client.post("/api/v1/simulation/step")
    db.add.assert_not_called()
    db.execute.assert_not_called()


def test_app_instances_are_isolated(client):
    from fastapi.testclient import TestClient
    from app.main import create_app
    client.post("/api/v1/simulation/start")
    with TestClient(create_app()) as second:
        assert second.get("/api/v1/simulation/status").json()["status"] == "STOPPED"
