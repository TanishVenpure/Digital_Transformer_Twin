"""Explicit test-only examples; invoked only by the disposable PostgreSQL verifier."""
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from fastapi.testclient import TestClient
from app.main import create_app
from app.alerts.configuration import OilThresholdRule
import app.services.processing_service as processing

RULE = "oil_level_predictive_maintenance"


def main():
    if os.environ.get("TRANSFORMER_RULE_VERIFICATION") != "1":
        raise SystemExit("Run verify_rule_postgres.py; a disposable database is required")
    original = processing.load_thresholds
    def test_configuration():
        rules = original()
        rules[RULE] = OilThresholdRule(minimum_oil_level=20, warning_oil_level=40, maintenance_lead_time=5)
        return rules
    processing.load_thresholds = test_configuration
    examples = {"verification_environment": "Disposable PostgreSQL; explicit test-only thresholds; no production values configured"}
    start = datetime(2026, 10, 10, tzinfo=timezone.utc)
    try:
        with TestClient(create_app()) as client:
            for name, level, hours in (
                ("NORMAL_FIRST_READING",100,0), ("NORMAL_WITH_TREND",90,10),
                ("LOW_OIL_WARNING",35,110), ("OIL_LEVEL_CRITICAL",10,210),
                ("PREDICTION_BASELINE",80,230), ("PREDICTED_MINIMUM_LEVEL_BREACH",60,231),
            ):
                response = client.post("/api/v1/data/reading", json={
                    "transformer_id":"rule-example", "timestamp":(start+timedelta(hours=hours)).isoformat(),
                    "data":{"oil_level":level},
                })
                assert response.status_code == 201, response.text
                examples[name] = response.json()["evaluations"][RULE]
            assert examples["NORMAL_WITH_TREND"]["details"]["oil_level_rate"] == -1
            assert examples["NORMAL_WITH_TREND"]["details"]["estimated_t_min"] == 70
            assert examples["PREDICTED_MINIMUM_LEVEL_BREACH"]["details"]["oil_level_rate"] == -20
            assert examples["PREDICTED_MINIMUM_LEVEL_BREACH"]["details"]["estimated_t_min"] == 2
            records = client.get("/api/v1/alerts?maintenance_type=TRANSFORMER_MAINTENANCE").json()
            assert len(records) == 4
            assert all(a["formula_name"] == RULE for a in records)
            for alert in records:
                assert client.get(f"/api/v1/alerts/{alert['id']}").json() == alert
            examples["stored_alerts"] = records
            history = client.get("/api/v1/alerts/history").json()["items"]
            assert {a["formula_name"] for a in history} == {
                RULE,"3_phase_current_instrument_error","3_phase_voltage_instrument_error"}
            examples["common_maintenance_history"] = history
    finally:
        processing.load_thresholds = original
    output = Path(__file__).resolve().parents[1]/"docs"/"oil_level_maintenance_examples.json"
    output.write_text(json.dumps(examples, indent=2)+"\n", encoding="utf-8")
    print("VERIFIED OIL EXAMPLES: normal, warning, critical, prediction, persisted API retrieval, three-rule common history")


if __name__ == "__main__":
    main()
