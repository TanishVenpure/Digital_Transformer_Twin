import pytest
from fastapi.testclient import TestClient

from app.config.settings import Settings
from app.main import create_app

SIMULATION_KEY = {"X-Agent-API-Key": "simulation-test-key"}
BASE = "/api/v1/simulation"
STATE_CHANGING = [("post", "/start"), ("post", "/pause"), ("post", "/reset"), ("post", "/step")]
ALL_ROUTES = [("get", "/status"), *STATE_CHANGING]
SCHEMA_KEYS = {"status", "current_step", "speed"}


@pytest.fixture
def sim(client):
    """Client holding the explicit @simulation permission (and no transformer scope)."""
    client.headers.update(SIMULATION_KEY)
    return client


def simulator_state(client):
    snapshot = client.app.state.simulator.status()
    return snapshot.status, snapshot.current_step, snapshot.speed


def test_simulation_lifecycle(sim):
    client = sim
    base = BASE
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
def test_invalid_speed(sim, speed):
    assert sim.post("/api/v1/simulation/start", json={"speed": speed}).status_code == 422


def test_start_without_body(sim):
    assert sim.post("/api/v1/simulation/start").json()["status"] == "RUNNING"


def test_simulation_does_not_store_readings(sim, db):
    assert sim.post("/api/v1/simulation/start").status_code == 200
    assert sim.post("/api/v1/simulation/step").status_code == 200
    db.add.assert_not_called()
    db.execute.assert_not_called()


def test_app_instances_are_isolated(sim):
    sim.post("/api/v1/simulation/start")
    second_app = create_app(Settings(
        database_url=None, agent_credentials="@simulation:simulation-test-key",
    ))
    with TestClient(second_app) as second:
        assert second.get(
            "/api/v1/simulation/status", headers=SIMULATION_KEY
        ).json()["status"] == "STOPPED"


@pytest.mark.parametrize("method,path", ALL_ROUTES)
def test_authorized_simulation_routes_return_status_schema(sim, method, path):
    response = getattr(sim, method)(BASE + path)
    assert response.status_code == 200
    assert set(response.json()) == SCHEMA_KEYS


def _advance(sim):
    """Put the shared simulator in a non-default state so any mutation is visible."""
    assert sim.post(BASE + "/start", json={"speed": 2.5}).status_code == 200
    assert sim.post(BASE + "/step").status_code == 200
    return simulator_state(sim)


REJECTIONS = [
    pytest.param(None, 401, id="missing-key"),
    pytest.param("", 401, id="empty-key"),
    pytest.param("wrong-key", 401, id="invalid-key"),
    pytest.param("simulation-test-key ", 401, id="near-miss-key"),
    # Raw latin-1 header bytes; str comparison of non-ASCII keys previously raised (HTTP 500).
    pytest.param("clé".encode("latin-1"), 401, id="non-ascii-key"),
    # Valid credentials with transformer scope only: shared simulator is out of scope.
    pytest.param("test-agent-key", 403, id="transformer-scoped-key"),
    pytest.param("other-scope-key", 403, id="other-transformer-key"),
]


@pytest.mark.parametrize("method,path", ALL_ROUTES)
@pytest.mark.parametrize("key,expected", REJECTIONS)
def test_rejected_requests_do_not_change_simulator_state(sim, method, path, key, expected):
    before = _advance(sim)
    headers = {} if key is None else {"X-Agent-API-Key": key}
    if key is None:
        sim.headers.pop("X-Agent-API-Key")
    request = getattr(sim, method)
    kwargs = {"json": {"speed": 9.0}} if path == "/start" else {}
    response = request(BASE + path, headers=headers, **kwargs)
    assert response.status_code == expected
    assert set(response.json()) == {"detail"}
    assert simulator_state(sim) == before


@pytest.mark.parametrize("method,path", STATE_CHANGING)
def test_transformer_id_cannot_grant_shared_simulator_access(sim, method, path):
    before = _advance(sim)
    headers = {"X-Agent-API-Key": "test-agent-key"}
    # TX-001 genuinely belongs to this caller, but that is not simulator permission.
    response = getattr(sim, method)(
        BASE + path, headers=headers, params={"transformer_id": "TX-001"},
        json={"speed": 9.0, "transformer_id": "TX-001"} if path == "/start" else None,
    )
    assert response.status_code == 403
    assert simulator_state(sim) == before


@pytest.mark.parametrize("method,path", STATE_CHANGING)
def test_authentication_runs_before_body_validation(client, method, path):
    client.headers.pop("X-Agent-API-Key")
    before = simulator_state(client)
    response = getattr(client, method)(BASE + path, json={"speed": -1})
    assert response.status_code == 401
    assert simulator_state(client) == before


@pytest.mark.parametrize("method,path", ALL_ROUTES)
def test_missing_authentication_configuration_fails_closed(db, method, path):
    application = create_app(Settings(database_url=None, agent_credentials=""))
    with TestClient(application) as unconfigured:
        before = unconfigured.app.state.simulator.status()
        response = getattr(unconfigured, method)(BASE + path, headers=SIMULATION_KEY)
        assert response.status_code == 503
        assert response.json() == {"detail": "Agent authentication is not configured"}
        assert unconfigured.app.state.simulator.status() == before


@pytest.mark.parametrize("credentials", [
    "@simulator:near-miss-key",       # misspelled permission grants nothing
    "@SIMULATION:near-miss-key",      # permissions are case-sensitive
    "simulation:near-miss-key",       # a transformer named "simulation" is not the permission
])
def test_only_the_exact_permission_grants_simulator_access(db, credentials):
    application = create_app(Settings(
        database_url=None, agent_credentials=credentials + ";TX-001:unrelated-key",
    ))
    with TestClient(application) as client:
        response = client.post(BASE + "/start", headers={"X-Agent-API-Key": "near-miss-key"})
        assert response.status_code in (401, 403)
        assert client.app.state.simulator.status().status.value == "STOPPED"


def test_simulation_permission_grants_no_transformer_access(sim):
    assert sim.get("/api/v1/alerts").status_code == 403
    assert sim.get("/api/v1/transformer/latest").status_code == 403
    response = sim.post("/api/v1/data/reading", json={
        "transformer_id": "@simulation", "timestamp": "2026-10-09T10:00:00Z", "data": {"external_field": 1.2},
    })
    assert response.status_code == 403


def test_permission_names_are_never_transformer_ids(client):
    # Even a credential listing "@simulation" cannot ingest as transformer "@simulation".
    response = client.post(
        "/api/v1/data/reading",
        headers={"X-Agent-API-Key": "test-agent-key"},
        json={"transformer_id": "@simulation", "timestamp": "2026-10-09T10:00:00Z", "data": {"external_field": 1.2}},
    )
    assert response.status_code == 403


def test_rejection_does_not_echo_supplied_key(client):
    response = client.post(BASE + "/start", headers={"X-Agent-API-Key": "leak-check-secret"})
    assert response.status_code == 401
    assert "leak-check-secret" not in response.text
