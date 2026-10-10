"""Generate hourly readings, validate + evaluate them, and store them in PostgreSQL.

--mode degradation (default): each monitor degrades, breaches its limits and is restored by
maintenance; the repairs are stored as maintenance events. --mode random: uniform random values.

Usage: python scripts/simulate.py [--mode degradation|random] [--days 90] [--seed 7] [--transformer T1]
"""
import argparse
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.database import db  # noqa: E402
from app.maintenance.sheet import load_actions, write_exports  # noqa: E402
from app.services.processing_service import ProcessingService  # noqa: E402
from app.simulation import degradation_source, random_source  # noqa: E402
from app.twin3d.selector import load_models, select_model  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=90)
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--transformer", default="T1")
    parser.add_argument("--start", default="2026-07-01")
    parser.add_argument("--mode", choices=("degradation", "random"), default="degradation")
    parser.add_argument("--invalid-rate", type=float, default=None)
    parser.add_argument("--keep", action="store_true", help="do not delete this transformer's existing readings")
    args = parser.parse_args()

    start = datetime.fromisoformat(args.start).replace(tzinfo=timezone.utc)
    service, models = ProcessingService(), load_models()
    statuses, issues, stored = Counter(), Counter(), 0
    with db.connect() as connection:
        db.create_schema(connection)
        if not args.keep:
            db.clear_transformer(connection, args.transformer)
        if args.mode == "degradation":
            readings, repairs = degradation_source.generate(
                start=start, days=args.days, seed=args.seed,
                **({"invalid_rate": args.invalid_rate} if args.invalid_rate is not None else {}))
        else:
            readings, repairs = random_source.generate(
                start=start, days=args.days, seed=args.seed, invalid_rate=args.invalid_rate or 0.01), {}
        for timestamp, data in readings:
            processed = service.process(args.transformer, timestamp, data)
            selection = select_model(processed.evaluations, models)
            if db.store(connection, processed, selection) is not None:
                stored += 1
            issues.update(i.code.value for i in processed.issues)
            statuses.update(e.status.value if e.status else "NOT_EVALUATED" for e in processed.evaluations.values())
        actions = load_actions()
        slot_of = dict(zip(degradation_source.MONITORS, range(1, 8)))
        formula_of = {rule.iot_slot: name for name, rule in service.thresholds.items()}
        performed = {"top_oil": "Radiator fins cleaned; cooling restored (oil ageing is not removed by cleaning)."}
        events = [
            (args.transformer, start + timedelta(hours=hour), slot_of[monitor],
             performed.get(monitor) or (lambda a: a.get("CRITICAL") or a.get("MAINTENANCE_REQUIRED") or "Maintenance performed.")(
                 actions.get(formula_of[slot_of[monitor]], {})), "simulated")
            for monitor, hours in repairs.items() for hour in hours
        ]
        db.store_events(connection, events)
        checks = db.rebuild_maintenance(connection, args.transformer, service.thresholds, actions)
        sheet = db.maintenance_sheet(connection, args.transformer)
    paths = write_exports(sheet, Path(__file__).resolve().parents[1] / "data" / "maintenance", args.transformer)

    print(f"Stored {stored} hourly readings for {args.transformer} from {start:%Y-%m-%d} ({args.days} days)")
    print("IoT statuses :", dict(statuses.most_common()))
    print("Validation   :", dict(issues.most_common()) or "no issues")
    print(f"Repairs      : {len(events)} maintenance events stored")
    print(f"Maintenance  : {len(checks)} checks ({sum(c['status'] == 'OPEN' for c in checks)} open) ->",
          ", ".join(str(p.name) for p in paths.values()))


if __name__ == "__main__":
    main()
