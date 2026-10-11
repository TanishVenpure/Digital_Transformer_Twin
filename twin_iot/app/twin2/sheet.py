"""Maintenance check sheet built from the two-timebar events (data/twin2/timebar1.json, timebar2.json).

Every warning, alarm, trip, flag and maintenance demand becomes one check with the fields the assistant
uses (check_no, iot, iot_name, signal, flag, opened_at, resolved_at, recommended_action, ...). Nothing is
invented: the recommended action is the recorded event message, and a check only resolves when the data
says so -
  Timebar 1 (one day, fast signals): the recorded condition ended (`end`) before the end of the day.
  Timebar 2 (one year, daily): the matching follow-up action for the same monitor was recorded
                               (`<TYPE>_RECALIBRATED`, or `OIL_LEAK_REPAIRED` for oil leaks and low oil).
Follow-up actions are attached to the check they close, not listed as checks of their own.

Each check lives on its own timebar, so a view of the sheet takes one cursor per timebar: the minute of
timebar 1 and the day of timebar 2 (the same indices the dashboard players use). `resolved_index` is the
first cursor index at which the check shows as resolved: on timebar 1 the minute after the condition ended
(cursor minute m stands for m:00, so an end at m:20 is only known from minute m + 1), on timebar 2 the day
of the follow-up action.
"""
from datetime import date, datetime, timedelta, timezone
from typing import Any

from app.twin2.build import load_payload

TRANSFORMER_ID = "CPRI-630"
TRANSFORMER_NAME = "CPRI 630 kVA, 11 / 0.433 kV, ONAN"
SIGNAL = {"WARNING": "WARNING", "CRITICAL": "CRITICAL", "MAINTENANCE_REQUIRED": "MAINTENANCE_REQUIRED",
          "MAINTENANCE": "MAINTENANCE_REQUIRED"}
MONITOR_NAME = {
    (1, "loading"): "Loading (overload)", (1, "voltage"): "Voltage (primary + secondary)",
    (1, "oti"): "Top oil (OTI)", (1, "wti"): "Winding (WTI)",
    (1, "pressure"): "Tank pressure", (1, "prec_current"): "Current device (CT)",
    (1, "prec_voltage"): "Voltage device (PT)", (1, "prec_temperature"): "Temperature device",
    (2, "oil"): "Oil volume (leak)", (2, "prec_current"): "Current device (CT), daily",
    (2, "prec_voltage"): "Voltage device (PT), daily", (2, "prec_temperature"): "Temperature device, daily",
    (2, "insulation"): "Paper insulation (ageing)", (2, "moisture"): "Moisture (oil sensor)",
    (2, "dga"): "Dissolved gases (CO / CO2)",
}
FOLLOW_UP = ("_RECALIBRATED", "_REPAIRED", "_RECONDITIONED")


def _resolves(follow: dict[str, Any], check: dict[str, Any]) -> bool:
    if follow["monitor"] != check["monitor"]:
        return False
    if follow["type"] == "OIL_LEAK_REPAIRED":
        return check["type"] in ("OIL_LEAK", "MINIMUM_OIL_LEVEL")
    if follow["type"] == "OIL_RECONDITIONED":       # hot-oil vacuum drying + degassing closes the moisture check
        return check["type"] == "PAPER_MOISTURE"
    return follow["type"] == check["type"] + "_RECALIBRATED"


def _iso(ts: datetime) -> str:
    return ts.astimezone(timezone.utc).isoformat()


def build_sheet(tb1: dict[str, Any], tb2: dict[str, Any]) -> list[dict[str, Any]]:
    """All checks of both timebars, timebar 1 first, each in recorded order. Status is not set here."""
    day0 = datetime.fromisoformat(tb1["start"])
    day_end = tb1["minutes"] * 60
    y0 = date.fromisoformat(tb2["start"])
    checks: list[dict[str, Any]] = []

    def add(e: dict[str, Any], opened: datetime, index: int, resolved: datetime | None, resolved_index, note) -> None:
        checks.append({
            "check_no": len(checks) + 1, "timebar": e["timebar"], "index": index,
            "iot": e["monitor"], "iot_name": MONITOR_NAME.get((e["timebar"], e["monitor"]), e["monitor"]),
            "signal": SIGNAL.get(e["level"], e["level"]), "level": e["level"], "flag": e["type"],
            "opened_at": _iso(opened), "date": opened.date().isoformat(),
            "time_utc": opened.strftime("%H:%M:%S") if e["timebar"] == 1 else None,
            "resolved_at": _iso(resolved) if resolved else None, "resolved_index": resolved_index,
            "resolution": note, "recommended_action": e["message"],
            "measured_value": None, "unit": None,
        })

    for e in sorted(tb1["events"], key=lambda e: e["t"]):
        ended = e.get("t_end") is not None and e["t_end"] < day_end
        add(e, day0 + timedelta(seconds=e["t"]), int(e["t"] // 60),
            day0 + timedelta(seconds=e["t_end"]) if ended else None,
            int(e["t_end"] // 60) + 1 if ended else None,
            "Recorded condition ended." if ended else None)

    events2 = sorted(tb2["events"], key=lambda e: e["day"])
    for e in events2:
        if e["type"].endswith(FOLLOW_UP):
            continue
        follow = next((f for f in events2 if f["day"] >= e["day"] and f["type"].endswith(FOLLOW_UP)
                       and _resolves(f, e)), None)
        opened = datetime.combine(y0 + timedelta(days=e["day"]), datetime.min.time(), timezone.utc)
        add(e, opened, e["day"],
            datetime.combine(y0 + timedelta(days=follow["day"]), datetime.min.time(), timezone.utc) if follow else None,
            follow["day"] if follow else None, follow["message"] if follow else None)
    return checks


def load_sheet() -> tuple[list[dict[str, Any]], dict[str, Any], dict[str, Any]]:
    tb1, tb2 = load_payload(1), load_payload(2)
    return build_sheet(tb1, tb2), tb1, tb2


def cursors(tb1: dict[str, Any], tb2: dict[str, Any], until: str | None, until2: str | None) -> dict[str, Any]:
    """Minute of timebar 1 and day of timebar 2 for the given timestamps (end of each timebar when omitted)."""
    day0 = datetime.fromisoformat(tb1["start"])
    y0 = date.fromisoformat(tb2["start"])
    m, d = tb1["minutes"] - 1, tb2["days"] - 1
    if until:
        ts = datetime.fromisoformat(until.replace("Z", "+00:00"))
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        m = max(0, min(m, int((ts - day0).total_seconds() // 60)))
    if until2:
        d = max(0, min(d, (date.fromisoformat(until2[:10]) - y0).days))
    return {
        "minute": m, "day": d,
        1: day0 + timedelta(minutes=m),
        2: datetime.combine(y0 + timedelta(days=d), datetime.min.time(), timezone.utc),
    }


def sheet_at(checks: list[dict[str, Any]], cur: dict[str, Any]) -> list[dict[str, Any]]:
    """Checks opened up to each timebar's cursor, newest first, with OPEN / RESOLVED as known at the cursor."""
    out = []
    for c in checks:
        i = cur["minute"] if c["timebar"] == 1 else cur["day"]
        if c["index"] > i or datetime.fromisoformat(c["opened_at"]) > cur[c["timebar"]]:
            continue
        done = c["resolved_index"] is not None and c["resolved_index"] <= i
        out.append({**c, "resolved_at": c["resolved_at"] if done else None,
                    "resolution": c["resolution"] if done else None,
                    "resolved_index": c["resolved_index"] if done else None,
                    "status": "RESOLVED" if done else "OPEN", "as_of": _iso(cur[c["timebar"]])})
    return sorted(out, key=lambda c: (c["timebar"], c["index"], c["check_no"]), reverse=True)
