"""Authentication, scope and input bounds for the twin_iot HTTP API.

The database layer is replaced with an in-memory fake, so these tests never connect
to PostgreSQL. psycopg2 is only stubbed when it is not installed; the stub refuses
to connect.
"""
import sys
import types
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

try:
    import psycopg2  # noqa: F401
except ImportError:
    def _refuse(*_args, **_kwargs):
        raise RuntimeError("tests must not connect to a database")

    _extras = types.ModuleType("psycopg2.extras")
    _extras.Json = lambda value: value
    _extras.execute_values = _refuse
    _stub = types.ModuleType("psycopg2")
    _stub.connect = _refuse
    _stub.extras = _extras
    sys.modules["psycopg2"] = _stub
    sys.modules["psycopg2.extras"] = _extras

from app.api import main  # noqa: E402
from app.database import db as db_module  # noqa: E402

KEY_T1 = {"X-Twin-API-Key": "key-for-t1"}
NOW_HOUR = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
HEADER = "timestamp,transformer_id,oil_level"


class FakeDB:
    def __init__(self):
        self.stored, self.cleared, self.rebuilt = [], [], []
        self.details = {}

    @contextmanager
    def connect(self):
        yield object()

    def store(self, _connection, processed, _selection):
        if not processed.accepted:
            return None
        self.stored.append(processed.transformer_id)
        return len(self.stored)

    def clear_transformer(self, _connection, transformer_id):
        self.cleared.append(transformer_id)

    def rebuild_maintenance(self, _connection, transformer_id, *_args):
        self.rebuilt.append(transformer_id)

    def reading_detail(self, _connection, reading_id):
        return self.details.get(reading_id)

    @property
    def writes(self):
        return self.stored + self.cleared + self.rebuilt


@pytest.fixture
def fake_db(monkeypatch):
    fake = FakeDB()
    for name in ("connect", "store", "clear_transformer", "rebuild_maintenance", "reading_detail"):
        monkeypatch.setattr(db_module, name, getattr(fake, name))
    main.live_service.reset()
    return fake


@pytest.fixture
def client(fake_db, monkeypatch):
    monkeypatch.setenv("TWIN_API_CREDENTIALS", "T1:key-for-t1;T2,T3:key-for-t2-t3")
    # No context manager: startup would connect to the real twin_iot database.
    return TestClient(main.app, base_url="http://localhost")


def stamp(hours_ago=0):
    return (NOW_HOUR - timedelta(hours=hours_ago)).isoformat()


def reading(transformer="T1", hours_ago=1, **data):
    return {"transformer_id": transformer, "timestamp": stamp(hours_ago), "data": data or {"oil_level": 70}}


def csv_file(*rows, header=HEADER):
    return {"file": ("data.csv", "\n".join([header, *rows]).encode(), "text/csv")}


def write_requests():
    return {
        "readings": lambda c, h: c.post("/api/readings", json=reading(), headers=h),
        "upload": lambda c, h: c.post("/api/upload", files=csv_file(f"{stamp(1)},T1,70"), headers=h),
        "signal": lambda c, h: c.post("/api/twin3d/signal", json={"reading_id": 1}, headers=h),
    }


@pytest.mark.parametrize("route", sorted(write_requests()))
@pytest.mark.parametrize("headers", [
    pytest.param({}, id="missing"),
    pytest.param({"X-Twin-API-Key": ""}, id="empty"),
    pytest.param({"X-Twin-API-Key": "wrong"}, id="invalid"),
    pytest.param({"X-Twin-API-Key": "key-for-t1 "}, id="near-miss"),
    pytest.param({"X-Twin-API-Key": "clé".encode("latin-1")}, id="non-ascii"),
    # A main-backend header is not a credential for this server.
    pytest.param({"X-Agent-API-Key": "key-for-t1"}, id="main-app-header"),
])
def test_write_routes_reject_bad_credentials_without_writing(client, fake_db, route, headers):
    fake_db.details[1] = {"transformer_id": "T1", "timestamp": stamp(1), "evaluations": []}
    calls_before = list(main.bridge.calls)
    response = write_requests()[route](client, headers)
    assert response.status_code == 401
    assert "key-for-t1" not in response.text
    assert fake_db.writes == []
    assert main.bridge.calls == calls_before


@pytest.mark.parametrize("route", sorted(write_requests()))
def test_write_routes_fail_closed_without_configuration(client, fake_db, monkeypatch, route):
    monkeypatch.delenv("TWIN_API_CREDENTIALS")
    response = write_requests()[route](client, KEY_T1)
    assert response.status_code == 503
    assert fake_db.writes == []


def test_main_backend_credentials_are_not_read(client, fake_db, monkeypatch):
    monkeypatch.delenv("TWIN_API_CREDENTIALS")
    monkeypatch.setenv("AGENT_CREDENTIALS", "T1:key-for-t1")
    assert client.post("/api/readings", json=reading(), headers=KEY_T1).status_code == 503


def test_authorized_reading_is_stored(client, fake_db):
    response = client.post("/api/readings", json=reading(), headers=KEY_T1)
    assert response.status_code == 200
    assert response.json()["accepted"] is True
    assert fake_db.stored == ["T1"] and fake_db.rebuilt == ["T1"]


def test_reading_for_another_transformer_is_forbidden(client, fake_db):
    response = client.post("/api/readings", json=reading("T2"), headers=KEY_T1)
    assert response.status_code == 403
    assert fake_db.writes == []


@pytest.mark.parametrize("payload", [
    pytest.param({**reading(), "transformer_id": "   "}, id="blank-transformer"),
    pytest.param({**reading(), "transformer_id": "T" * 256}, id="long-transformer"),
    pytest.param({**reading(), "timestamp": "9999-01-01T00:00:00Z"}, id="far-future"),
    pytest.param({**reading(), "timestamp": (NOW_HOUR + timedelta(hours=2)).isoformat()}, id="future"),
    pytest.param({**reading(), "data": {f"f{i}": 1 for i in range(257)}}, id="too-many-fields"),
])
def test_invalid_readings_are_rejected_before_processing(client, fake_db, payload):
    response = client.post("/api/readings", json=payload, headers=KEY_T1)
    assert response.status_code == 422
    assert fake_db.writes == []


def test_future_reading_cannot_block_later_genuine_readings(client, fake_db):
    poisoned = {**reading(), "timestamp": "9999-01-01T00:00:00Z"}
    assert client.post("/api/readings", json=poisoned, headers=KEY_T1).status_code == 422
    response = client.post("/api/readings", json=reading(hours_ago=1), headers=KEY_T1)
    assert response.json()["accepted"] is True


def test_naive_timestamp_within_skew_is_accepted(client, fake_db):
    payload = {**reading(), "timestamp": NOW_HOUR.replace(tzinfo=None).isoformat()}
    assert client.post("/api/readings", json=payload, headers=KEY_T1).status_code == 200


def test_authorized_upload_replaces_only_its_transformer(client, fake_db):
    response = client.post("/api/upload", files=csv_file(f"{stamp(2)},T1,70", f"{stamp(1)},T1,69"),
                           headers=KEY_T1)
    assert response.status_code == 200
    assert response.json()["stored"] == 2
    assert fake_db.cleared == ["T1"]
    assert fake_db.stored == ["T1", "T1"]


def test_upload_naming_out_of_scope_transformer_deletes_and_stores_nothing(client, fake_db):
    files = csv_file(f"{stamp(2)},T1,70", f"{stamp(1)},T2,10")
    response = client.post("/api/upload", files=files, headers=KEY_T1)
    assert response.status_code == 403
    assert fake_db.writes == []


def test_upload_default_transformer_must_be_in_scope(client, fake_db):
    files = csv_file(f"{stamp(1)},70", header="timestamp,oil_level")
    response = client.post("/api/upload", files=files, data={"transformer_id": "T2"}, headers=KEY_T1)
    assert response.status_code == 403
    assert fake_db.writes == []


def test_multi_transformer_key_can_upload_its_transformers(client, fake_db):
    files = csv_file(f"{stamp(1)},T2,70", f"{stamp(1)},T3,70")
    response = client.post("/api/upload", files=files, headers={"X-Twin-API-Key": "key-for-t2-t3"})
    assert response.status_code == 200
    assert sorted(fake_db.cleared) == ["T2", "T3"]


@pytest.mark.parametrize("files,expected", [
    pytest.param({"file": ("big.csv", b"x" * (2 * 1024 * 1024 + 1), "text/csv")}, 413, id="too-large"),
    pytest.param({"file": ("data.txt", b"timestamp\n", "text/plain")}, 400, id="not-csv"),
    pytest.param({"file": ("data.csv", "timestamp,oil_level\n2026-10-09T00:00:00,\xff".encode("latin-1"),
                           "text/csv")}, 400, id="not-utf8"),
    pytest.param(csv_file("2026-10-09T00:00:00,70", header="when,oil_level"), 400, id="no-timestamp"),
    pytest.param(csv_file("SECRET-CELL,T1,70"), 400, id="bad-timestamp"),
    pytest.param(csv_file(f"{stamp(1)},T1,70,unexpected-extra"), 400, id="extra-column"),
    pytest.param(csv_file("2026-10-09T00:00:00,T1\x00,70"), 400, id="nul-byte"),
    pytest.param(csv_file(f"{stamp(1)}, ,70"), 400, id="blank-row-transformer"),
    pytest.param(csv_file("9999-01-01T00:00:00,T1,70"), 400, id="future-row"),
])
def test_malformed_uploads_are_rejected_without_writes(client, fake_db, files, expected):
    response = client.post("/api/upload", files=files, headers=KEY_T1)
    assert response.status_code == expected
    assert "SECRET-CELL" not in response.text
    assert fake_db.writes == []


def test_upload_with_blank_form_transformer_is_rejected(client, fake_db):
    files = csv_file(f"{stamp(1)},70", header="timestamp,oil_level")
    response = client.post("/api/upload", files=files, data={"transformer_id": " "}, headers=KEY_T1)
    assert response.status_code == 400
    assert fake_db.writes == []


def test_authentication_runs_before_upload_parsing(client, fake_db):
    response = client.post("/api/upload", files={"file": ("big.csv", b"x" * 10, "text/csv")})
    assert response.status_code == 401


def _detail(transformer):
    return {"transformer_id": transformer, "timestamp": stamp(1), "evaluations": [{
        "formula_name": "oil_level", "iot_slot": 5, "evaluation_status": "CALCULATED",
        "status": "CRITICAL", "unit": "percent_of_gauge",
    }]}


def test_signal_for_own_reading_switches_model(client, fake_db):
    fake_db.details[1] = _detail("T1")
    main.bridge.current_model = "base"
    response = client.post("/api/twin3d/signal", json={"reading_id": 1}, headers=KEY_T1)
    assert response.status_code == 200
    assert response.json()["switched"] is True


def test_signal_for_other_transformer_reading_is_hidden(client, fake_db):
    fake_db.details[1] = _detail("T2")
    main.bridge.current_model = "base"
    calls_before = list(main.bridge.calls)
    response = client.post("/api/twin3d/signal", json={"reading_id": 1}, headers=KEY_T1)
    assert response.status_code == 404
    assert main.bridge.current_model == "base"
    assert main.bridge.calls == calls_before


@pytest.mark.parametrize("source", ["", "x" * 33, "<script>", "Timeline"])
def test_signal_source_is_bounded(client, fake_db, source):
    fake_db.details[1] = _detail("T1")
    response = client.post("/api/twin3d/signal", json={"reading_id": 1, "source": source}, headers=KEY_T1)
    assert response.status_code == 422


@pytest.mark.parametrize("host", ["evil.example", "attacker.test:8020", "192.168.1.20"])
def test_unexpected_host_header_is_rejected(client, fake_db, host):
    # DNS rebinding: a page on another origin resolving to 127.0.0.1 still sends its own Host.
    assert client.get("/api/iots", headers={"Host": host}).status_code == 400
    assert client.post("/api/readings", json=reading(), headers={**KEY_T1, "Host": host}).status_code == 400
    assert fake_db.writes == []


@pytest.mark.parametrize("host", ["localhost", "localhost:8020", "127.0.0.1:8020"])
def test_local_host_headers_are_accepted(client, host):
    assert client.get("/api/iots", headers={"Host": host}).status_code == 200


def test_read_only_routes_remain_open_on_localhost(client):
    # Documented limitation: reads stay unauthenticated for the local dashboard.
    assert client.get("/api/iots").status_code == 200


class _Cursor:
    def __init__(self, managed):
        self.managed = managed

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def execute(self, _sql):
        pass

    def fetchone(self):
        return (self.managed,)


class _Connection:
    def __init__(self, managed):
        self.managed = managed

    def cursor(self):
        return _Cursor(self.managed)


def test_refuses_to_create_schema_in_alembic_managed_database():
    with pytest.raises(RuntimeError, match="Alembic-managed"):
        db_module.refuse_managed_database(_Connection(True))
    db_module.refuse_managed_database(_Connection(False))
