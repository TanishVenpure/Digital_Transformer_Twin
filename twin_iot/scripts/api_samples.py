"""Capture real JSON request/response samples from the running API for threshold hits.

Posts three live readings for transformer SAMPLE (all normal -> several thresholds hit ->
invalid inputs), then calls the GET endpoints, writes every exchange to docs/api_samples/,
and finally deletes the SAMPLE rows so the dashboard data is unchanged.

Usage: python scripts/api_samples.py [--base http://localhost:8020]
"""
import argparse
import json
import sys
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.database import db  # noqa: E402

OUT = Path(__file__).resolve().parents[1] / "docs" / "api_samples"
TRANSFORMER = "SAMPLE"


def call(base: str, method: str, path: str, body: dict | None = None) -> dict:
    request = urllib.request.Request(base + path, method=method, headers={"Content-Type": "application/json"},
                                     data=json.dumps(body).encode() if body is not None else None)
    with urllib.request.urlopen(request) as response:
        return json.loads(response.read())


def reading(**overrides) -> dict:
    data = {
        "current_r": 600.0, "current_y": 610.0, "current_b": 605.0,
        "reference_current_r": 600.0, "reference_current_y": 610.0, "reference_current_b": 605.0,
        "voltage_r": 250.0, "voltage_y": 251.0, "voltage_b": 249.0,
        "reference_voltage_r": 250.0, "reference_voltage_y": 251.0, "reference_voltage_b": 249.0,
        "oil_level": 70.0, "top_oil_temperature": 70.0, "ambient_temperature": 32.0,
        "winding_temperature_r": 66.0, "winding_temperature_y": 67.0, "winding_temperature_b": 65.0,
    }
    data.update(overrides)
    return data


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default="http://localhost:8020")
    args = parser.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    hour = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
    stamp = lambda h: (hour + timedelta(hours=h)).isoformat().replace("+00:00", "Z")  # noqa: E731
    exchanges = []

    def save(name: str, method: str, path: str, body: dict | None = None) -> dict:
        response = call(args.base, method, path, body)
        exchange = {"request": {"method": method, "path": path, **({"body": body} if body is not None else {})},
                    "response": response}
        (OUT / f"{name}.json").write_text(json.dumps(exchange, indent=2), encoding="utf-8")
        exchanges.append(name)
        return response

    try:
        save("01_post_reading_all_normal", "POST", "/api/readings",
             {"transformer_id": TRANSFORMER, "timestamp": stamp(0), "data": reading()})
        hit = save("02_post_reading_thresholds_hit", "POST", "/api/readings", {
            "transformer_id": TRANSFORMER, "timestamp": stamp(1), "data": reading(
                current_r=860.0, reference_current_r=842.0,       # IOT1 2.1 % sensor error, IOT3 102 % load
                oil_level=55.0,                                    # IOT5 falling 15 %/h -> predicted breach
                top_oil_temperature=107.0,                         # IOT6 above 105 degC trip
                winding_temperature_y=79.0,                        # IOT7 rise 47 K over 32 degC ambient
            )})
        save("03_post_reading_invalid_inputs", "POST", "/api/readings", {
            "transformer_id": TRANSFORMER, "timestamp": stamp(2), "data": reading(
                oil_level=140.0, top_oil_temperature="ERR", voltage_b=None)})
        save("04_get_alerts", "GET", f"/api/alerts/{TRANSFORMER}?limit=10")
        save("05_get_maintenance_sheet", "GET", f"/api/maintenance/{TRANSFORMER}")
        save("06_get_twin3d_state", "GET", "/api/twin3d/state?calls=3")
        if hit.get("reading_id"):
            save("07_get_reading_detail", "GET", f"/api/readings/{hit['reading_id']}")
            save("08_post_twin3d_signal", "POST", "/api/twin3d/signal", {"reading_id": hit["reading_id"], "source": "timeline"})
        save("09_get_timeline", "GET", f"/api/timeline/{TRANSFORMER}")
    finally:
        with db.connect() as connection:
            db.clear_transformer(connection, TRANSFORMER)
    print("Wrote", ", ".join(exchanges), "to", OUT)


if __name__ == "__main__":
    main()
