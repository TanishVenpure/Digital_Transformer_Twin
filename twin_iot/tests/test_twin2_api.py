"""HTTP endpoints for the two-timebar datasets: read-only downloads, rebuild needs a write key."""
import pytest
from fastapi.testclient import TestClient

from app.api import main
from app.twin2 import build as twin2_build


@pytest.fixture
def client(tmp_path, monkeypatch):
    (tmp_path / "events.csv").write_bytes(b"time,type\n")
    (tmp_path / "timebar2.json").write_text('{"days": []}', encoding="utf-8")
    (tmp_path / "notes.txt").write_text("x", encoding="utf-8")
    monkeypatch.setattr(twin2_build, "OUT_DIR", tmp_path)
    monkeypatch.setattr(twin2_build, "build", lambda: pytest.fail("rebuild must not run without a valid key"))
    monkeypatch.setenv("TWIN_API_CREDENTIALS", "T1:key-for-t1")
    return TestClient(main.app, base_url="http://localhost")


def test_dataset_download(client):
    r = client.get("/api/twin2/files/events.csv")
    assert r.status_code == 200
    assert r.text == "time,type\n"


@pytest.mark.parametrize("name", ["notes.txt", "missing.csv", "..%2Fevents.csv", "timebar2.json"])
def test_only_existing_csv_datasets_are_served(client, name):
    assert client.get(f"/api/twin2/files/{name}").status_code == 404


def test_timebar_payload_and_unknown_timebar(client):
    assert client.get("/api/twin2/timebar/2").json() == {"days": []}
    assert client.get("/api/twin2/timebar/3").status_code == 404


@pytest.mark.parametrize("headers", [{}, {"X-Twin-API-Key": "wrong"}])
def test_rebuild_requires_a_write_key(client, headers):
    assert client.post("/api/twin2/rebuild", headers=headers).status_code == 401
