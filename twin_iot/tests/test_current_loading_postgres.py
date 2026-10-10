"""Current overload persistence through the existing API in an isolated schema."""
import math
from datetime import timedelta

import pytest

from tests.conftest import NOW
from tests.test_pressure_postgres import api_db


def test_current_warning_and_trip_persist_in_common_history(api_db):
    client, main, connect = api_db
    rated = 633000 / (math.sqrt(3) * 433)
    for hour, loading in enumerate((109.99, 110, 120)):
        data = {"current_r": rated, "current_y": rated * loading / 100, "current_b": rated}
        receipt = client.post("/api/readings", json={"transformer_id": "T1",
            "timestamp": (NOW + timedelta(hours=hour)).isoformat(), "data": data})
        assert receipt.status_code == 200 and receipt.json()["accepted"]
    alerts = [a for a in client.get("/api/alerts/T1").json() if a["iot_slot"] == 3]
    assert len(alerts) == 2
    assert {a["trigger_condition"] for a in alerts} == {"OVERLOAD_WARNING", "TRIP_ALERT"}
    for alert in alerts:
        assert alert["maintenance_type"] == "TRANSFORMER_MAINTENANCE"
        assert alert["details"]["rated_current"] == pytest.approx(rated)
        assert alert["details"]["max_current_phase"] == "Y"
        assert set(alert["details"]["phase_loading_percent"]) == {"R", "Y", "B"}
        assert "current_y" in alert["formula_inputs"]
