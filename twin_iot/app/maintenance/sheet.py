"""Maintenance sheet: one sequential check per threshold being met.

A check opens when an IoT moves into a threshold status (WARNING, PREDICTED_MAINTENANCE,
MAINTENANCE_REQUIRED, CRITICAL) or into a different one while already signalling.
It is resolved when that IoT returns to NORMAL. Hours where the IoT could not be
evaluated (invalid/missing inputs) neither open nor resolve a check.

Every record carries a self-contained `text` sentence so a future RAG can index
each check on its own.
"""
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml

from app.alerts.configuration import PROJECT_ROOT
from app.formulas.registry import formula_registry

TRIGGER_STATUSES = {"WARNING", "PREDICTED_MAINTENANCE", "MAINTENANCE_REQUIRED", "CRITICAL"}
SHEET_FIELDS = (
    "check_no", "transformer_id", "date", "time_utc", "opened_at", "iot", "iot_name", "signal", "flag",
    "previous_status", "measured_value", "threshold", "comparison", "unit", "maintenance_type",
    "recommended_action", "status", "resolved_at", "duration_hours", "reading_id", "text",
)


def load_actions(path: Path | None = None) -> dict[str, dict[str, str]]:
    with (path or PROJECT_ROOT / "config" / "maintenance_actions.yaml").open(encoding="utf-8") as stream:
        return yaml.safe_load(stream)["actions"]


def _fmt(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.4g}"


def describe(record: dict[str, Any]) -> str:
    when = record["opened_at"].strftime("%Y-%m-%d %H:%M UTC")
    if record.get("flag"):
        return (f"Maintenance check #{record['check_no']} for transformer {record['transformer_id']} on {when}: "
                f"IOT{record['iot']} {record['iot_name']} trend flag {record['flag']} raised {record.get('trigger', '')}. "
                f"{record['detail']} Recommended action: {record['recommended_action']}")
    sentence = (
        f"Maintenance check #{record['check_no']} for transformer {record['transformer_id']} on {when}: "
        f"IOT{record['iot']} {record['iot_name']} signalled {record['signal']}"
    )
    if record["threshold"] is not None:
        sentence += (f" (measured {_fmt(record['measured_value'])} {record['unit']} "
                     f"{record['comparison']} threshold {_fmt(record['threshold'])} {record['unit']})")
    sentence += f", previous status {record['previous_status']}. Recommended action: {record['recommended_action']}"
    if record["resolved_at"] is not None:
        sentence += (f" Resolved on {record['resolved_at'].strftime('%Y-%m-%d %H:%M UTC')} "
                     f"after {record['duration_hours']:g} hours.")
    else:
        sentence += " Status: OPEN."
    return sentence


def build_checks(
    rows: list[tuple], alerts: dict[tuple[int, int], list[dict]], *, transformer_id: str,
    actions: dict[str, dict[str, str]], names: dict[int, tuple[str, str, str]],
) -> list[dict[str, Any]]:
    """rows: (reading_id, ts, [(slot, status), ...]) ordered by ts.
    alerts: (reading_id, slot) -> alert rows. names: slot -> (formula_name, display_name, unit)."""
    last_status: dict[int, str] = {}
    open_check: dict[int, dict[str, Any]] = {}
    checks: list[dict[str, Any]] = []
    for reading_id, ts, evaluations in rows:
        for slot, status in evaluations:
            if status is None:
                continue
            previous = last_status.get(slot, "NORMAL")
            last_status[slot] = status
            if status == "NORMAL":
                if slot in open_check:
                    check = open_check.pop(slot)
                    check["resolved_at"] = ts
                    check["duration_hours"] = (ts - check["opened_at"]).total_seconds() / 3600
                continue
            if status == previous:
                continue
            if slot in open_check:  # changed while signalling: close the old check here
                check = open_check.pop(slot)
                check["resolved_at"] = ts
                check["duration_hours"] = (ts - check["opened_at"]).total_seconds() / 3600
            formula_name, display_name, unit = names[slot]
            matching = [a for a in alerts.get((reading_id, slot), []) if a["signal"] == status]
            alert = matching[0] if matching else next(iter(alerts.get((reading_id, slot), [])), None)
            check = {
                "transformer_id": transformer_id, "opened_at": ts, "iot": slot, "iot_name": display_name,
                "formula_name": formula_name, "signal": status, "previous_status": previous,
                "measured_value": alert["value"] if alert else None,
                "threshold": alert["threshold"] if alert else None,
                "comparison": alert["comparison_operator"] if alert else None,
                "unit": alert["unit"] if alert else unit,
                "maintenance_type": alert["maintenance_type"] if alert else None,
                "recommended_action": actions.get(formula_name, {}).get(
                    status, actions.get(formula_name, {}).get("CRITICAL", "Inspect transformer.")),
                "resolved_at": None, "duration_hours": None, "reading_id": reading_id,
            }
            open_check[slot] = check
            checks.append(check)
    for number, check in enumerate(checks, start=1):
        check["check_no"] = number
        check["status"] = "OPEN" if check["resolved_at"] is None else "RESOLVED"
        check["date"] = check["opened_at"].strftime("%Y-%m-%d")
        check["time_utc"] = check["opened_at"].strftime("%H:%M")
        check["text"] = describe(check)
    return checks


def add_trend_flags(
    checks: list[dict[str, Any]], flags: list[dict[str, Any]], *, transformer_id: str, slot: int,
    names: dict[int, tuple[str, str, str]], action: str, reading_at: dict[datetime, int],
) -> list[dict[str, Any]]:
    """Merge cycle-trend flags (e.g. accelerating oil leak) into the sheet, in date order, renumbered."""
    formula_name, display_name, _ = names[slot]
    for f in flags:
        opened = datetime.fromisoformat(f["detected_at"])
        checks.append({
            "transformer_id": transformer_id, "opened_at": opened, "iot": slot, "iot_name": display_name,
            "formula_name": formula_name, "signal": "MAINTENANCE_REQUIRED", "flag": f["flag"],
            "previous_status": "TREND", "measured_value": f.get("value"), "threshold": None,
            "comparison": None, "unit": f.get("unit", ""), "maintenance_type": "TRANSFORMER_MAINTENANCE",
            "trigger": f.get("trigger", ""),
            "recommended_action": action, "resolved_at": None, "duration_hours": None,
            "reading_id": reading_at.get(opened), "detail": f["message"],
        })
    checks.sort(key=lambda c: (c["opened_at"], 1 if c.get("flag") else 0))
    for number, check in enumerate(checks, start=1):
        check["check_no"] = number
        check.setdefault("flag", "")
        check["status"] = "OPEN" if check["resolved_at"] is None else "RESOLVED"
        check["date"] = check["opened_at"].strftime("%Y-%m-%d")
        check["time_utc"] = check["opened_at"].strftime("%H:%M")
        check["text"] = describe(check)
    return checks


def iot_names(thresholds) -> dict[int, tuple[str, str, str]]:
    return {rule.iot_slot: (name, formula_registry.get(name).display_name, rule.unit)
            for name, rule in thresholds.items()}


def as_row(check: dict[str, Any]) -> dict[str, Any]:
    return {field: (check[field].isoformat() if isinstance(check[field], datetime) else check[field])
            for field in SHEET_FIELDS}


def write_exports(rows: list[dict[str, Any]], out_dir: Path, transformer_id: str) -> dict[str, Path]:
    """Write the sheet as CSV (spreadsheet), JSONL (one record per line for RAG) and Markdown."""
    import csv
    import json
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = out_dir / f"maintenance_sheet_{transformer_id}"
    paths = {"csv": stem.with_suffix(".csv"), "jsonl": stem.with_suffix(".jsonl"), "md": stem.with_suffix(".md")}
    with paths["csv"].open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=SHEET_FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    with paths["jsonl"].open("w", encoding="utf-8") as stream:
        for row in rows:
            stream.write(json.dumps(row) + "\n")
    lines = [f"# Maintenance sheet: transformer {transformer_id}", "",
             f"{len(rows)} maintenance checks in date order. Each check = one IoT threshold being met.", ""]
    for row in rows:
        lines += [f"## Check {row['check_no']}: {row['date']} {row['time_utc']} UTC, IOT{row['iot']} {row['signal']}",
                  "", row["text"], ""]
    paths["md"].write_text("\n".join(lines), encoding="utf-8")
    return paths
