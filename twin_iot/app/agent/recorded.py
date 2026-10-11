"""Source-branch recorded-data chat behavior; no rule evaluation or risk scoring."""
from collections import Counter
from typing import Any
from app.twin2 import sheet as twin_sheet

SEVERITY_RANK = {"NORMAL": 0, "WARNING": 1, "PREDICTED_MAINTENANCE": 2, "MAINTENANCE_REQUIRED": 3, "CRITICAL": 4}

MONITORS = {
    1: [("loading", "loading_max", "%", 1), ("voltage", None, "%", 2), ("oti", "top_oil", "°C", 1),
        ("wti", "wti", "°C", 1), ("pressure", "pressure_abs", "kPa abs", 1),
        ("prec_current", "prec_current", "% max deviation", 2), ("prec_voltage", "prec_voltage", "% max deviation", 2),
        ("prec_temperature", "prec_temperature", "% max deviation", 2)],
    2: [("oil", "residual", "L vs no-leak expectation", 1),
        ("prec_current", "prec_current", "% max deviation", 2), ("prec_voltage", "prec_voltage", "% max deviation", 2),
        ("prec_temperature", "prec_temperature", "% max deviation", 2),
        ("insulation", "dp", "estimated paper DP", 0), ("moisture", "wcp", "% paper water", 2),
        ("dga", "co", "ppm CO", 0)],
}
SIGNED = {"voltage", "oil"}
# Question keywords -> monitors (timebar, monitor) the answer covers. Keywords of 3 letters or fewer
# must match a whole word ("ct", "pt", "oti", "wti").
TOPICS = [
    (("overload", "loading", "load", "trip", "breaker"), [(1, "loading")]),
    (("voltage", "supply", "grid"), [(1, "voltage")]),
    (("oil", "leak", "top-up", "topped"), [(1, "oti"), (2, "oil")]),
    (("temperature", "thermal", "hot", "heat", "oti", "cooling", "radiator"), [(1, "oti"), (1, "wti")]),
    (("winding", "wti", "hotspot", "hot spot"), [(1, "wti")]),
    (("insulation", "paper", "ageing", "aging", "dp", "polymeris", "polymeriz", "furan", "life"),
     [(2, "insulation"), (2, "moisture"), (2, "dga")]),
    (("moisture", "water", "wet", "humid", "drying", "dried", "recondition"), [(2, "moisture")]),
    (("gas", "gases", "dga", "co", "co2", "carbon"), [(2, "dga")]),
    (("pressure", "tank", "headspace"), [(1, "pressure")]),
    (("precision", "calibrat", "accuracy", "instrument", "device", "ct", "pt", "probe"),
     [(1, "prec_current"), (1, "prec_voltage"), (1, "prec_temperature"),
      (2, "prec_current"), (2, "prec_voltage"), (2, "prec_temperature")]),
    (("current",), [(1, "loading"), (1, "prec_current"), (2, "prec_current")]),
]
_GREETINGS = {"hi", "hello", "hey", "yo", "sup", "hola", "greetings", "good morning",
              "good afternoon", "good evening", "good day", "thanks", "thank you", "bye"}
_SUMMARY = {"maintenance", "check", "checks", "open", "status", "risk", "health", "summary", "overview", "now"}
_SIGNALS = {"alert", "alerts", "alarm", "alarms", "signal", "signals", "event", "events", "flag", "flags", "trips"}
_REPAIRS = {"repair", "repairs", "repaired", "recalibrated", "recalibration", "fixed", "resolved", "actions"}
_DOMAIN = {"transformer", "sensor", "reading", "iot", "monitor", "threshold", "critical", "warning", "normal",
           "report", "problem", "issue", "fault", "fail", "error", "wrong", "safe", "danger", "condition", "state",
           "history", "record", "data", "value", "measure", "rate", "score", "index", "rated", "kva", "power",
           "timebar", "today", "year", "day"} | _SUMMARY | _SIGNALS | _REPAIRS


def _value(tb: dict[str, Any], key: str | None, i: int):
    if key is None:   # voltage: the deviation farthest from nominal on either winding
        vals = [tb["series"][k][i] for k in ("v_pri_min", "v_pri_max", "v_sec_min", "v_sec_max")]
        vals = [v for v in vals if v is not None]
        return max(vals, key=abs) if vals else None
    values = tb["series"].get(key, [])
    v = values[i] if i < len(values) else None
    if isinstance(v, list):   # per-phase precision deviations
        v = [x for x in v if x is not None]
        return max(v) if v else None
    return v


def _monitor_states(tb1: dict[str, Any], tb2: dict[str, Any], cur: dict[str, Any]) -> list[dict[str, Any]]:
    out = []
    for n, tb, i in ((1, tb1, cur["minute"]), (2, tb2, cur["day"])):
        for mon, key, unit, digits in MONITORS[n]:
            if mon not in tb["status"]:
                continue
            v = _value(tb, key, i)
            out.append({"timebar": n, "monitor": mon, "name": twin_sheet.MONITOR_NAME[(n, mon)],
                        "status": tb["status"][mon][i], "value": None if v is None else round(v, digits), "unit": unit})
    return out


def _when(c: dict[str, Any]) -> str:
    return f"{c['date']} {c['time_utc']} UTC" if c["timebar"] == 1 else c["date"]


def _what(c: dict[str, Any]) -> str:
    return c["flag"].replace("_", " ").lower()


def _check_line(c: dict[str, Any]) -> str:
    return f"#{c['check_no']} (timebar {c['timebar']}, {_when(c)}) {c['iot_name']}: {_what(c)}, {c['signal']}, {c['status']}"


def _fmt_value(m: dict[str, Any]) -> str:
    if m["value"] is None:
        return "no reading"
    return f"{m['value']:+g} {m['unit']}" if m["monitor"] in SIGNED else f"{m['value']:g} {m['unit']}"


def _attention(monitors: list[dict[str, Any]]) -> str:
    bad = [m for m in monitors if m["status"] not in ("NORMAL", "OFF", "NO_DATA")]
    if not bad:
        return "All monitors are within their limits at the cursors."
    return "Monitors needing attention now: " + ", ".join(f"{m['name']} (timebar {m['timebar']}) {m['status']}" for m in bad) + "."


def _reply(transformer_id: str, answer: str, facts=None, ctx=None) -> dict[str, Any]:
    return {
        "transformer_id": transformer_id, "answer": answer, "evidence_source_ids": [],
        "warnings": [], "synthetic_evidence": True, "facts": facts or [], "inferences": [],
        "answer_source": "deterministic",
        "cursor": ctx["cursor"] if ctx else None,
    }


def _deterministic_answer(question: str, ctx: dict[str, Any], transformer_id: str) -> dict[str, Any]:
    """Build a factual answer from the checks and monitor states recorded up to the two cursors."""
    q = question.lower().strip().rstrip("?!.")
    if q in _GREETINGS or (len(q) < 12 and any(q.startswith(g) for g in ("hi ", "hey ", "hello "))):
        return _reply(transformer_id, (f"Hello! I can answer questions about transformer {transformer_id} at the "
                                       "two timebar cursors: loading, voltage, oil, temperature, pressure, "
                                       "precision devices, signals, repairs, maintenance checks."), ctx=ctx)

    checks, monitors, cur = ctx["checks"], ctx["monitors"], ctx["cursor"]
    words = set(q.replace("/", " ").replace(",", " ").replace("?", " ").split())
    open_checks = sorted((c for c in checks if c["status"] == "OPEN"), key=lambda c: -SEVERITY_RANK.get(c["signal"], 0))
    resolved = [c for c in checks if c["status"] == "RESOLVED"]
    facts: list[dict[str, str]] = []
    parts = [f"At timebar 1 {cur['timebar1'][11:16]} UTC on {cur['timebar1'][:10]} and timebar 2 {cur['timebar2']}:"]

    wanted: list[tuple[int, str]] = []
    for keys, mons in TOPICS:
        if any((k in words) if len(k) <= 3 else (k in q) for k in keys):
            wanted += [m for m in mons if m not in wanted]
    for n, mon in wanted:
        m = next((x for x in monitors if x["timebar"] == n and x["monitor"] == mon), None)
        if m is None:
            parts.append(f"{twin_sheet.MONITOR_NAME.get((n, mon), mon)} has no recorded monitor in this prototype.")
            continue
        related = [c for c in checks if c["timebar"] == n and c["iot"] == mon]
        line = f"{m['name']} (timebar {n}) is {m['status']} at {_fmt_value(m)}"
        if related:
            still = [c for c in related if c["status"] == "OPEN"]
            line += f"; {len(related)} check(s) recorded so far, {len(still)} open; latest: {_what(related[0])} on {_when(related[0])}"
        else:
            line += "; no checks recorded so far"
        parts.append(line + ".")
        for c in related[:3]:
            note = f" Resolution: {c['resolution']}" if c["status"] == "RESOLVED" and c["resolution"] else ""
            facts.append({"statement": f"{_check_line(c)}. {c['recommended_action']}{note}"})

    if words & _SUMMARY:
        parts.append(f"Maintenance sheet up to the cursors: {len(checks)} checks, {len(open_checks)} open, "
                     f"{len(resolved)} resolved.")
        if open_checks:
            parts.append("Open: " + "; ".join(_check_line(c) for c in open_checks[:5]) + ".")
            for c in open_checks[:3]:
                facts.append({"statement": f"{_check_line(c)}. {c['recommended_action']}"})
        parts.append(_attention(monitors))
        if not ctx["energized"]:
            parts.append("Timebar 1: the breaker is open, so the transformer is out of service at this minute.")

    if words & _SIGNALS:
        by_signal = Counter(c["signal"] for c in checks)
        parts.append(f"{len(checks)} signals recorded up to the cursors"
                     + (": " + ", ".join(f"{v} {k}" for k, v in by_signal.most_common()) if checks else "") + ".")
        for c in checks[:3]:
            facts.append({"statement": f"{_check_line(c)}. {c['recommended_action']}"})

    if words & _REPAIRS:
        done = [c for c in resolved if c["timebar"] == 2]
        parts.append(f"{len(done)} maintenance action(s) recorded on timebar 2 up to {cur['timebar2']}"
                     + (" (latest 5)" if len(done) > 5 else "")
                     + (": " + "; ".join(f"#{c['check_no']} {_what(c)} closed on {c['resolved_at'][:10]} ({c['resolution']})"
                                         for c in done[:5]) if done else "") + ".")

    if len(parts) == 1:
        if not words & _DOMAIN:
            return _reply(transformer_id, (f"I can only answer questions about transformer {transformer_id} — try asking "
                                           "about loading, voltage, oil, temperature, pressure, precision devices, "
                                           "signals, repairs, maintenance checks."), ctx=ctx)
        parts.append(f"{len(checks)} checks recorded ({len(open_checks)} open, {len(resolved)} resolved). "
                     + _attention(monitors))
        parts.append("Ask about a monitor (loading, voltage, oil, temperature, winding, pressure, precision), "
                     "signals, repairs, maintenance checks for more detail.")

    return _reply(transformer_id, " ".join(parts), facts[:20], ctx)
