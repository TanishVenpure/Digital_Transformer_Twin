"""Seed a clearly labelled SYNTHETIC demo transformer through the running API.

Uses only existing HTTP endpoints, so the normal validation, alert rules and
authorization apply. Every value below is synthetic demo data, not field telemetry.

Environment:
  DEMO_API_BASE     default http://127.0.0.1:8000
  DEMO_INGEST_KEY   an AGENT_CREDENTIALS secret scoped to the demo transformer
                    with the @ingest permission (never printed)

Usage:
  python scripts/seed_demo.py [--transformer DEMO-TX-01]

Refuses to seed unless the API's database holds no transformer or only the demo
transformer, so it cannot add synthetic data to a development database.
"""
import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone

SYNTHETIC_NOTE = "SYNTHETIC DEMO RECORD - not field data."


def call(base, key, method, path, body=None):
    request = urllib.request.Request(
        base.rstrip("/") + path, method=method,
        data=json.dumps(body).encode() if body is not None else None,
        headers={"Content-Type": "application/json", "X-Agent-API-Key": key},
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return response.status, json.loads(response.read() or b"null")
    except urllib.error.HTTPError as error:
        return error.code, json.loads(error.read() or b"null")


def readings(transformer, now):
    """Hourly synthetic electrical readings; the last one exceeds the configured 1% instrument error."""
    rows = []
    for hours_ago, phase_b in ((3, 100.3), (2, 100.6), (1, 101.4)):
        rows.append({
            "transformer_id": transformer,
            "timestamp": (now - timedelta(hours=hours_ago)).isoformat(),
            "data": {
                "source": "synthetic-demo",
                "rated_current": 100, "measured_current_r": 100.2, "measured_current_y": 99.8,
                "measured_current_b": phase_b,
                "reference_voltage": 230, "measured_voltage_r": 230.4, "measured_voltage_y": 229.6,
                "measured_voltage_b": 230.9,
            },
        })
    return rows


def maintenance(transformer, now):
    """Synthetic history using the documented details contract for the risk engine."""
    def record(days_ago, record_type, **details):
        return {
            "transformer_id": transformer, "record_type": record_type,
            "occurred_at": (now - timedelta(days=days_ago)).isoformat(),
            "details": {"source": "synthetic-demo", **details}, "notes": SYNTHETIC_NOTE,
        }
    return [
        record(400, "fault", event_id="DEMO-EV-1", component="radiator", severity="moderate"),
        record(200, "fault", event_id="DEMO-EV-2", component="bushing", severity="minor",
               repair_outcome="completed"),
        record(30, "fault", event_id="DEMO-EV-3", component="bushing", fault_code="B-OVERHEAT",
               severity="major", repair_outcome="unresolved"),
        record(10, "inspection", event_id="DEMO-EV-4", component="tank"),
    ]


def dedicated_database_problem(call_fn, base, key, transformer) -> str | None:
    """Return why seeding is unsafe, or None when the API's database is dedicated to the demo.

    /transformer/latest resolves the single stored transformer server-side: 200 with no
    transformer (empty) or the demo transformer is safe; 403 means another transformer
    (outside the demo key's scope) is stored; 400 means several are stored.
    """
    status, body = call_fn(base, key, "GET", "/api/v1/transformer/latest")
    if status == 200 and body.get("transformer_id") in (None, transformer):
        return None
    if status == 403:
        return "the API's database already holds another transformer (e.g. development data)"
    if status == 400:
        return "the API's database holds several transformers"
    return f"could not confirm the database is dedicated to the demo (HTTP {status})"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--transformer", default="DEMO-TX-01")
    args = parser.parse_args()
    if not args.transformer.startswith("DEMO-"):
        print("Refusing: demo transformer IDs must start with 'DEMO-' so synthetic data stays separate.")
        return 2
    base = os.environ.get("DEMO_API_BASE", "http://127.0.0.1:8000")
    key = os.environ.get("DEMO_INGEST_KEY", "")
    if not key:
        print("Set DEMO_INGEST_KEY to an @ingest key scoped to the demo transformer.")
        return 2

    problem = dedicated_database_problem(call, base, key, args.transformer)
    if problem:
        print(f"Refusing to seed: {problem}. Start the API on a dedicated demo database "
              "(scripts/run_demo_api.py --database <name>_demo).")
        return 2

    status, existing = call(base, key, "GET", f"/api/v1/maintenance/records?transformer_id={args.transformer}")
    if status != 200:
        print(f"Cannot read maintenance history (HTTP {status}): {existing}")
        return 1
    if existing:
        print(f"{args.transformer} already has {len(existing)} maintenance record(s); not seeding twice.")
        return 0

    now = datetime.now(timezone.utc).replace(microsecond=0)
    for row in readings(args.transformer, now):
        status, body = call(base, key, "POST", "/api/v1/data/reading", row)
        if status != 201:
            print(f"Reading rejected (HTTP {status}): {body}")
            return 1
        flagged = [name for name, result in body["evaluations"].items() if result.get("status") == "FLAGGED"]
        print(f"reading {row['timestamp']} stored; flagged rules: {flagged or 'none'}")
    for record in maintenance(args.transformer, now):
        status, body = call(base, key, "POST", "/api/v1/maintenance/records", record)
        if status != 201:
            print(f"Maintenance record rejected (HTTP {status}): {body}")
            return 1
        print(f"maintenance:{body['id']} {record['record_type']} {record['details'].get('component')} stored")
    print(f"Seeded synthetic demo transformer {args.transformer}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
