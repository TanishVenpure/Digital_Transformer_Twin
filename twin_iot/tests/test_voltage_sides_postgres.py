from datetime import timedelta

from app.database import db
from app.services.processing_service import ProcessingService
from tests.conftest import NOW
from tests.test_pressure_postgres import api_db
from tests.test_voltage_sides import configured


def test_20ms_voltage_api_persistence_and_restart_deduplication(api_db):
    client, main, connect = api_db
    def restart():
        main.live_service = ProcessingService(configured(), history_loader=db.load_previous_valid_reading,
                                              timestamp_loader=db.load_last_timestamp)
    restart()
    for i, voltage in enumerate((458.98, 458.98, 433, 458.98)):
        if i == 1:
            restart()
        response = client.post("/api/readings", json={"transformer_id": "T1",
            "timestamp": (NOW + timedelta(milliseconds=20*i)).isoformat(),
            "data": {f"lv_voltage_{p}": voltage for p in "ryb"}})
        assert response.status_code == 200 and response.json()["accepted"]
    alerts = [a for a in client.get("/api/alerts/T1").json() if a["iot_slot"] == 4]
    assert len(alerts) == 2
    assert all(a["trigger_condition"] == "OVERVOLTAGE_TRIP_ALERT" for a in alerts)
    assert all(a["maintenance_type"] == "TRANSFORMER_MAINTENANCE" for a in alerts)
    assert all(a["details"]["measurement_side"] == "LV" for a in alerts)
    with connect() as connection, connection.cursor() as cursor:
        cursor.execute("SELECT ts FROM readings ORDER BY ts")
        times = [r[0] for r in cursor.fetchall()]
        assert times == [NOW + timedelta(milliseconds=20*i) for i in range(4)]
