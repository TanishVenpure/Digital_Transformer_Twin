"""Run the supplied rule examples through configured PostgreSQL and FastAPI.

The verification runner invokes this only against its disposable test cluster.
Run through verify_rule_postgres.py only; direct execution is blocked.
"""
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from fastapi.testclient import TestClient
from app.main import create_app

RULE = "3_phase_current_instrument_error"


VOLTAGE_RULE = "3_phase_voltage_instrument_error"


def verify_voltage_examples(client):
    examples = {}
    for label, r, y, b, expected in (
        ("PASS", 100.4, 99.7, 100.8, "PASS"),
        ("EXACT_THRESHOLD_PASS", 101, 100, 100, "PASS"),
        ("FLAGGED", 100.4, 99.7, 101.2, "FLAGGED"),
    ):
        response = client.post("/api/v1/data/reading", json={
            "transformer_id": "rule-example", "timestamp": "2026-10-09T10:00:00Z",
            "data": {
                "reference_voltage": 100, "measured_voltage_r": r,
                "measured_voltage_y": y, "measured_voltage_b": b,
            },
        })
        assert response.status_code == 201, response.text
        result = response.json()["evaluations"][VOLTAGE_RULE]
        assert result["status"] == expected
        assert response.json()["evaluations"][RULE]["status"] == "SKIPPED"
        examples[label] = result
        print("VOLTAGE_" + label + " " + json.dumps(result))
    response = client.get("/api/v1/alerts", params={
        "maintenance_type": "SYSTEM_MAINTENANCE", "parameter": "voltage_measurement",
    })
    assert response.status_code == 200 and len(response.json()) == 1
    alert = response.json()[0]
    assert alert["formula_name"] == VOLTAGE_RULE
    assert alert["details"]["max_deviation_phase"] == "B"
    examples["stored_alert"] = alert
    print("STORED_VOLTAGE_ALERT " + json.dumps(alert))
    assert client.get(f"/api/v1/alerts/{alert['id']}").json() == alert
    response = client.get("/api/v1/alerts/history?maintenance_type=SYSTEM_MAINTENANCE")
    assert response.status_code == 200
    history = response.json()["items"]
    assert {item["formula_name"] for item in history} == {RULE, VOLTAGE_RULE}
    assert len(history) == 2
    examples["common_maintenance_history"] = history
    response = client.get("/api/v1/transformer/history")
    assert response.status_code == 200
    assert {item["formula_name"] for item in response.json()["alerts"]} == {RULE, VOLTAGE_RULE}
    print("VERIFIED_SHARED_HISTORY " + json.dumps([item["formula_name"] for item in history]))
    examples["verification_environment"] = "Disposable PostgreSQL cluster; no production database modified"
    output = Path(__file__).resolve().parents[1] / "docs" / "voltage_instrument_error_examples.json"
    output.write_text(json.dumps(examples, indent=2) + "\n", encoding="utf-8")


def main():
    if os.environ.get("TRANSFORMER_RULE_VERIFICATION") != "1":
        raise SystemExit("Run verify_rule_postgres.py; examples require its disposable test cluster")
    examples = {}
    with TestClient(create_app()) as client:
        for label, r, y, b in (
            ("PASS", 100.4, 99.7, 100.8),
            ("EXACT_THRESHOLD_PASS", 101, 100, 100),
            ("FLAGGED", 100.4, 99.7, 101.2),
        ):
            response = client.post("/api/v1/data/reading", json={
                "transformer_id": "rule-example", "timestamp": "2026-10-09T10:00:00Z",
                "data": {
                    "rated_current": 100, "measured_current_r": r,
                    "measured_current_y": y, "measured_current_b": b,
                },
            })
            assert response.status_code == 201, response.text
            examples[label] = response.json()["evaluations"][RULE]
            print(label + " " + json.dumps(examples[label]))
        response = client.get("/api/v1/alerts", params={"maintenance_type": "SYSTEM_MAINTENANCE"})
        assert response.status_code == 200
        assert len(response.json()) == 1
        alert = response.json()[0]
        assert alert["details"]["max_deviation_phase"] == "B"
        assert alert["maintenance_type"] == "SYSTEM_MAINTENANCE"
        examples["stored_alert"] = alert
        print("STORED_ALERT " + json.dumps(alert))
        for path in (
            "/api/v1/alerts/history?maintenance_type=SYSTEM_MAINTENANCE",
            "/api/v1/transformer/history",
            f"/api/v1/alerts/{alert['id']}",
        ):
            assert client.get(path).status_code == 200
            print("RETRIEVED " + path)
        verify_voltage_examples(client)

    examples["verification_environment"] = "Disposable PostgreSQL cluster; no production database modified"
    output = Path(__file__).resolve().parents[1] / "docs" / "current_instrument_error_examples.json"
    output.parent.mkdir(exist_ok=True)
    if not output.exists():
        output.write_text(json.dumps(examples, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
