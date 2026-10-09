"""Replay a CSV through all seven IoTs and print one status row per reading.

Usage: python scripts/run_csv.py [path/to/readings.csv] [--json out.json]
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.processing.ingestion import read_csv  # noqa: E402
from app.services.processing_service import ProcessingService  # noqa: E402

SHORT = {
    "NORMAL": "OK", "WARNING": "WARN", "CRITICAL": "CRIT",
    "MAINTENANCE_REQUIRED": "MAINT", "PREDICTED_MAINTENANCE": "PRED",
}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("csv", nargs="?", default=Path(__file__).resolve().parents[1] / "data" / "examples" / "escalation_demo.csv")
    parser.add_argument("--json", type=Path, help="write full evaluations as JSON")
    args = parser.parse_args()

    readings = list(read_csv(Path(args.csv)))
    results = ProcessingService().replay(readings)

    print(f"{'timestamp':<20}" + "".join(f"IOT{i:<6}" for i in range(1, 8)))
    for processed in results:
        row = sorted(processed.evaluations.values(), key=lambda e: e.iot_slot)
        issues = [f"{i.feature}:{i.code.value}" for i in processed.issues if i.code.value != "MISSING"]
        print(f"{processed.timestamp:%Y-%m-%d %H:%M}    " + "".join(
            f"{SHORT[e.status.value] if e.status else e.evaluation_status.value[:4]:<9}" for e in row
        ) + ("REJECTED " if not processed.accepted else "") + " ".join(issues))

    if args.json:
        payload = [processed.model_dump(mode="json") for processed in results]
        args.json.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        print(f"\nWrote {args.json}")


if __name__ == "__main__":
    main()
