"""PostgreSQL storage for readings, validation issues, IoT evaluations and alerts."""
import os
from contextlib import contextmanager

import psycopg2
from psycopg2.extras import Json, execute_values

from app.services.processing_service import ProcessedReading
from app.twin3d.selector import ModelSelection

# Local dev cluster on port 5434 (trust auth on localhost only); override with env var.
DATABASE_URL = os.environ.get("TWIN_DATABASE_URL", "postgresql://twin@localhost:5434/twin_iot")

SCHEMA = """
CREATE TABLE IF NOT EXISTS readings (
    id              BIGSERIAL PRIMARY KEY,
    transformer_id  TEXT NOT NULL,
    ts              TIMESTAMPTZ NOT NULL,
    raw_data        JSONB NOT NULL,
    clean_data      JSONB NOT NULL,
    issues          JSONB NOT NULL DEFAULT '[]',
    model_id        TEXT NOT NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (transformer_id, ts)
);
CREATE TABLE IF NOT EXISTS iot_evaluations (
    reading_id              BIGINT NOT NULL REFERENCES readings(id) ON DELETE CASCADE,
    iot_slot                SMALLINT NOT NULL,
    formula_name            TEXT NOT NULL,
    evaluation_status       TEXT NOT NULL,
    status                  TEXT,
    signals                 JSONB NOT NULL,
    value                   DOUBLE PRECISION,
    unit                    TEXT NOT NULL,
    time_to_critical_hours  DOUBLE PRECISION,
    details                 JSONB NOT NULL,
    PRIMARY KEY (reading_id, iot_slot)
);
CREATE TABLE IF NOT EXISTS alerts (
    id                  BIGSERIAL PRIMARY KEY,
    reading_id          BIGINT NOT NULL REFERENCES readings(id) ON DELETE CASCADE,
    transformer_id      TEXT NOT NULL,
    ts                  TIMESTAMPTZ NOT NULL,
    iot_slot            SMALLINT NOT NULL,
    signal              TEXT NOT NULL,
    value               DOUBLE PRECISION NOT NULL,
    threshold           DOUBLE PRECISION NOT NULL,
    comparison_operator TEXT NOT NULL,
    unit                TEXT NOT NULL,
    maintenance_type    TEXT NOT NULL,
    message             TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS maintenance_log (
    transformer_id      TEXT NOT NULL,
    check_no            INTEGER NOT NULL,
    opened_at           TIMESTAMPTZ NOT NULL,
    iot_slot            SMALLINT NOT NULL,
    iot_name            TEXT NOT NULL,
    signal              TEXT NOT NULL,
    previous_status     TEXT NOT NULL,
    measured_value      DOUBLE PRECISION,
    threshold           DOUBLE PRECISION,
    comparison_operator TEXT,
    unit                TEXT NOT NULL,
    maintenance_type    TEXT,
    recommended_action  TEXT NOT NULL,
    status              TEXT NOT NULL,
    resolved_at         TIMESTAMPTZ,
    duration_hours      DOUBLE PRECISION,
    reading_id          BIGINT REFERENCES readings(id) ON DELETE CASCADE,
    text                TEXT NOT NULL,
    PRIMARY KEY (transformer_id, check_no)
);
CREATE TABLE IF NOT EXISTS maintenance_events (
    id              BIGSERIAL PRIMARY KEY,
    transformer_id  TEXT NOT NULL,
    ts              TIMESTAMPTZ NOT NULL,
    iot_slot        SMALLINT NOT NULL,
    action          TEXT NOT NULL,
    source          TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS maintenance_events_transformer_ts ON maintenance_events (transformer_id, ts);
CREATE INDEX IF NOT EXISTS readings_transformer_ts ON readings (transformer_id, ts);
ALTER TABLE maintenance_log ADD COLUMN IF NOT EXISTS flag TEXT NOT NULL DEFAULT '';
CREATE INDEX IF NOT EXISTS alerts_transformer_ts ON alerts (transformer_id, ts);
"""


@contextmanager
def connect(url: str = DATABASE_URL):
    connection = psycopg2.connect(url, options="-c timezone=UTC")
    try:
        yield connection
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def refuse_managed_database(connection) -> None:
    """Never create twin_iot tables inside an Alembic-managed (main backend) database."""
    with connection.cursor() as cursor:
        cursor.execute("SELECT to_regclass('alembic_version') IS NOT NULL")
        if cursor.fetchone()[0]:
            raise RuntimeError(
                "TWIN_DATABASE_URL points at an Alembic-managed database; use a separate twin_iot database"
            )


def create_schema(connection) -> None:
    with connection.cursor() as cursor:
        cursor.execute(SCHEMA)


def clear_transformer(connection, transformer_id: str) -> None:
    with connection.cursor() as cursor:
        cursor.execute("DELETE FROM readings WHERE transformer_id = %s", (transformer_id,))
        cursor.execute("DELETE FROM maintenance_events WHERE transformer_id = %s", (transformer_id,))


def store(connection, processed: ProcessedReading, selection: ModelSelection) -> int | None:
    """Store one accepted reading with its evaluations and alerts. Returns the reading id."""
    if not processed.accepted:
        return None
    with connection.cursor() as cursor:
        cursor.execute(
            """INSERT INTO readings (transformer_id, ts, raw_data, clean_data, issues, model_id)
               VALUES (%s, %s, %s, %s, %s, %s)
               ON CONFLICT (transformer_id, ts) DO NOTHING RETURNING id""",
            (processed.transformer_id, processed.timestamp, Json(_jsonable(processed.raw_data)),
             Json(processed.clean_data), Json([i.model_dump(mode="json") for i in processed.issues]),
             selection.model_id),
        )
        row = cursor.fetchone()
        if row is None:
            return None
        reading_id = row[0]
        evaluations = sorted(processed.evaluations.values(), key=lambda e: e.iot_slot)
        execute_values(cursor, """INSERT INTO iot_evaluations (reading_id, iot_slot, formula_name,
            evaluation_status, status, signals, value, unit, time_to_critical_hours, details) VALUES %s""", [
            (reading_id, e.iot_slot, e.formula_name, e.evaluation_status.value,
             e.status.value if e.status else None, Json([s.value for s in e.signals]), e.value, e.unit,
             e.time_to_critical_hours, Json(e.model_dump(mode="json")["details"]))
            for e in evaluations
        ])
        alerts = [a for e in evaluations for a in e.alerts]
        if alerts:
            execute_values(cursor, """INSERT INTO alerts (reading_id, transformer_id, ts, iot_slot, signal,
                value, threshold, comparison_operator, unit, maintenance_type, message) VALUES %s""", [
                (reading_id, a.transformer_id, a.timestamp, a.iot_slot, a.signal.value, a.value, a.threshold,
                 a.comparison_operator, a.unit, a.maintenance_type.value, a.message)
                for a in alerts
            ])
    return reading_id


def _jsonable(data: dict) -> dict:
    # Raw values may include non-finite floats that JSON cannot carry; keep them as text.
    return {k: (v if not isinstance(v, float) or v == v and abs(v) != float("inf") else str(v)) for k, v in data.items()}


def transformers(connection) -> list[dict]:
    with connection.cursor() as cursor:
        cursor.execute("""SELECT transformer_id, count(*), min(ts), max(ts)
                          FROM readings GROUP BY transformer_id ORDER BY transformer_id""")
        return [{"transformer_id": t, "readings": n, "start": s.isoformat(), "end": e.isoformat()}
                for t, n, s, e in cursor.fetchall()]


def timeline(connection, transformer_id: str) -> list[tuple]:
    """One row per reading: (id, ts, model_id, issues, [(slot, status, value, ttc), ...])."""
    with connection.cursor() as cursor:
        cursor.execute("""
            SELECT r.id, r.ts, r.model_id, r.issues,
                   json_agg(json_build_array(e.iot_slot, e.status, e.value, e.time_to_critical_hours)
                            ORDER BY e.iot_slot)
            FROM readings r JOIN iot_evaluations e ON e.reading_id = r.id
            WHERE r.transformer_id = %s
            GROUP BY r.id ORDER BY r.ts""", (transformer_id,))
        return cursor.fetchall()


def reading_detail(connection, reading_id: int) -> dict | None:
    with connection.cursor() as cursor:
        cursor.execute("SELECT transformer_id, ts, raw_data, clean_data, issues, model_id FROM readings WHERE id = %s",
                       (reading_id,))
        row = cursor.fetchone()
        if row is None:
            return None
        cursor.execute("""SELECT iot_slot, formula_name, evaluation_status, status, signals, value, unit,
                                 time_to_critical_hours, details
                          FROM iot_evaluations WHERE reading_id = %s ORDER BY iot_slot""", (reading_id,))
        columns = [c.name for c in cursor.description]
        evaluations = [dict(zip(columns, r)) for r in cursor.fetchall()]
    transformer, ts, raw, clean, issues, model_id = row
    return {"id": reading_id, "transformer_id": transformer, "timestamp": ts.isoformat(), "raw_data": raw,
            "clean_data": clean, "issues": issues, "model_id": model_id, "evaluations": evaluations}


def rebuild_maintenance(connection, transformer_id: str, thresholds, actions) -> list[dict]:
    """Re-derive the sequential maintenance sheet from stored evaluations and alerts."""
    from app.analytics import load_growth, winding
    from app.analytics.oil_leak import analyse, load_config
    from app.maintenance.sheet import add_trend_flags, build_checks, iot_names
    with connection.cursor() as cursor:
        cursor.execute("""
            SELECT r.id, r.ts, json_agg(json_build_array(e.iot_slot, e.status) ORDER BY e.iot_slot)
            FROM readings r JOIN iot_evaluations e ON e.reading_id = r.id
            WHERE r.transformer_id = %s GROUP BY r.id ORDER BY r.ts""", (transformer_id,))
        rows = cursor.fetchall()
        cursor.execute("""SELECT reading_id, iot_slot, signal, value, threshold, comparison_operator, unit,
                                 maintenance_type FROM alerts WHERE transformer_id = %s ORDER BY id""", (transformer_id,))
        alerts: dict[tuple[int, int], list[dict]] = {}
        for reading_id, slot, signal, value, threshold, comparison, unit, maintenance_type in cursor.fetchall():
            alerts.setdefault((reading_id, slot), []).append({
                "signal": signal, "value": value, "threshold": threshold, "comparison_operator": comparison,
                "unit": unit, "maintenance_type": maintenance_type})
        names = iot_names(thresholds)
        quality_only = {rule.iot_slot for rule in thresholds.values() if not rule.maintenance_sheet}
        rows = [(rid, ts, [e for e in evs if e[0] not in quality_only]) for rid, ts, evs in rows]
        checks = build_checks(rows, alerts, transformer_id=transformer_id, actions=actions, names=names)
        oil_slot = thresholds["oil_level"].iot_slot
        leak = analyse(oil_series(connection, transformer_id, oil_slot), load_config())
        checks = add_trend_flags(
            checks, leak.flags, transformer_id=transformer_id, slot=oil_slot, names=names,
            action=actions.get("oil_level", {}).get("ACCELERATING_OIL_LEAK", "Inspect seals and gaskets."),
            reading_at={ts: rid for rid, ts, _ in rows},
        )
        load_rule = thresholds["current_loading"]
        growth = load_growth.analyse(
            oil_series(connection, transformer_id, load_rule.iot_slot),
            load_growth.load_config(warning=load_rule.warning, critical=load_rule.critical),
        )
        checks = add_trend_flags(
            checks, growth["flags"], transformer_id=transformer_id, slot=load_rule.iot_slot, names=names,
            action=actions.get("current_loading", {}).get("LOAD_GROWTH", "Plan load transfer or capacity upgrade."),
            reading_at={ts: rid for rid, ts, _ in rows},
        )
        wind_rule = thresholds["winding_temperature"]
        thermal = winding.analyse(winding_inputs(connection, transformer_id, wind_rule.iot_slot, load_rule.iot_slot),
                                  winding.load_config())
        checks = add_trend_flags(
            checks, thermal["flags"], transformer_id=transformer_id, slot=wind_rule.iot_slot, names=names,
            action=actions.get("winding_temperature", {}).get("WINDING_EXCESS_HEATING", "Inspect winding cooling."),
            reading_at={ts: rid for rid, ts, _ in rows},
        )
        oil_rule = thresholds["top_oil_temperature"]
        cooling = top_oil_analysis(connection, transformer_id, thresholds)
        for flags, key, fallback in ((cooling["cooling_checks"], "COOLING_DEGRADED", "Clean the radiators."),
                                     (cooling["flags"], "OIL_AGEING", "Test the oil.")):
            checks = add_trend_flags(
                checks, flags, transformer_id=transformer_id, slot=oil_rule.iot_slot, names=names,
                action=actions.get("top_oil_temperature", {}).get(key, fallback),
                reading_at={ts: rid for rid, ts, _ in rows},
            )
        cursor.execute("DELETE FROM maintenance_log WHERE transformer_id = %s", (transformer_id,))
        if checks:
            execute_values(cursor, """INSERT INTO maintenance_log (transformer_id, check_no, opened_at, iot_slot,
                iot_name, signal, previous_status, measured_value, threshold, comparison_operator, unit,
                maintenance_type, recommended_action, status, resolved_at, duration_hours, reading_id, text, flag)
                VALUES %s""", [
                (c["transformer_id"], c["check_no"], c["opened_at"], c["iot"], c["iot_name"], c["signal"],
                 c["previous_status"], c["measured_value"], c["threshold"], c["comparison"], c["unit"],
                 c["maintenance_type"], c["recommended_action"], c["status"], c["resolved_at"],
                 c["duration_hours"], c["reading_id"], c["text"], c.get("flag", "")) for c in checks])
    return checks


def oil_series(connection, transformer_id: str, slot: int) -> list[tuple]:
    """(timestamp, monitored value) of one IoT slot for every stored hour; None where the input was invalid."""
    with connection.cursor() as cursor:
        cursor.execute("""SELECT r.ts, e.value FROM readings r JOIN iot_evaluations e ON e.reading_id = r.id
                          WHERE r.transformer_id = %s AND e.iot_slot = %s ORDER BY r.ts""", (transformer_id, slot))
        return cursor.fetchall()


def winding_inputs(connection, transformer_id: str, winding_slot: int, load_slot: int) -> list[tuple]:
    """(timestamp, winding hot-spot rise K, loading % of rated, ambient degC, measured top-oil rise K)
    for every stored hour."""
    with connection.cursor() as cursor:
        cursor.execute("""
            SELECT r.ts, ew.value, el.value, (r.clean_data->>'ambient_temperature')::float,
                   (r.clean_data->>'top_oil_temperature')::float - (r.clean_data->>'ambient_temperature')::float
            FROM readings r
            LEFT JOIN iot_evaluations ew ON ew.reading_id = r.id AND ew.iot_slot = %s
            LEFT JOIN iot_evaluations el ON el.reading_id = r.id AND el.iot_slot = %s
            WHERE r.transformer_id = %s ORDER BY r.ts""", (winding_slot, load_slot, transformer_id))
        return cursor.fetchall()


def top_oil_inputs(connection, transformer_id: str, load_slot: int, winding_slot: int) -> list[tuple]:
    """(timestamp, top-oil degC, ambient degC, loading % hottest phase, RMS loading % of the three phases,
    oil level %, winding hot-spot rise K) for every stored hour."""
    phase = lambda p: f"(r.clean_data->>'current_{p}')::float"  # noqa: E731
    with connection.cursor() as cursor:
        cursor.execute(f"""
            SELECT r.ts, (r.clean_data->>'top_oil_temperature')::float, (r.clean_data->>'ambient_temperature')::float,
                   el.value,
                   el.value * sqrt((power({phase('r')}, 2) + power({phase('y')}, 2) + power({phase('b')}, 2)) / 3)
                     / NULLIF(greatest({phase('r')}, {phase('y')}, {phase('b')}), 0),
                   (r.clean_data->>'oil_level')::float, ew.value
            FROM readings r
            LEFT JOIN iot_evaluations el ON el.reading_id = r.id AND el.iot_slot = %s
            LEFT JOIN iot_evaluations ew ON ew.reading_id = r.id AND ew.iot_slot = %s
            WHERE r.transformer_id = %s ORDER BY r.ts""", (load_slot, winding_slot, transformer_id))
        return cursor.fetchall()


def cleanings(connection, transformer_id: str, slot: int) -> list | None:
    """Timestamps of maintenance performed on one IoT slot (None when there is no maintenance log at all)."""
    with connection.cursor() as cursor:
        cursor.execute("SELECT count(*) FROM maintenance_events WHERE transformer_id = %s", (transformer_id,))
        if not cursor.fetchone()[0]:
            return None
        cursor.execute("SELECT ts FROM maintenance_events WHERE transformer_id = %s AND iot_slot = %s ORDER BY ts",
                       (transformer_id, slot))
        return [r[0] for r in cursor.fetchall()]


def top_oil_limits(thresholds) -> dict:
    """Top-oil alarm / trip / rise limits, from thresholds.yaml."""
    rule = thresholds["top_oil_temperature"]
    rise = next((c.threshold for c in (rule.extra_checks or ()) if c.detail == "top_oil_rise"), 40.0)
    return {"alarm_c": rule.warning, "trip_c": rule.critical, "rise_limit": rise}


def top_oil_analysis(connection, transformer_id: str, thresholds) -> dict:
    from app.analytics import top_oil, winding
    return {"slot": thresholds["top_oil_temperature"].iot_slot, **top_oil.analyse(
        top_oil_inputs(connection, transformer_id, thresholds["current_loading"].iot_slot,
                       thresholds["winding_temperature"].iot_slot),
        cleanings(connection, transformer_id, thresholds["top_oil_temperature"].iot_slot),
        top_oil.load_config(**top_oil_limits(thresholds)), winding.load_config())}


def store_events(connection, rows: list[tuple]) -> None:
    """rows: (transformer_id, ts, iot_slot, action, source) - maintenance actually performed."""
    if rows:
        with connection.cursor() as cursor:
            execute_values(cursor, "INSERT INTO maintenance_events (transformer_id, ts, iot_slot, action, source) VALUES %s", rows)


def maintenance_events(connection, transformer_id: str) -> list[dict]:
    with connection.cursor() as cursor:
        cursor.execute("""SELECT ts, iot_slot, action, source FROM maintenance_events
                          WHERE transformer_id = %s ORDER BY ts, iot_slot""", (transformer_id,))
        return [{"ts": ts.isoformat(), "iot": slot, "action": action, "source": source}
                for ts, slot, action, source in cursor.fetchall()]


def maintenance_sheet(connection, transformer_id: str, *, until=None, iot: int | None = None,
                      status: str | None = None, limit: int | None = None) -> list[dict]:
    from app.maintenance.sheet import SHEET_FIELDS
    query = """SELECT check_no, transformer_id, to_char(opened_at AT TIME ZONE 'UTC', 'YYYY-MM-DD') AS date,
                      to_char(opened_at AT TIME ZONE 'UTC', 'HH24:MI') AS time_utc, opened_at, iot_slot AS iot,
                      iot_name, signal, flag, previous_status, measured_value, threshold,
                      comparison_operator AS comparison, unit, maintenance_type, recommended_action, status,
                      resolved_at, duration_hours, reading_id, text
               FROM maintenance_log WHERE transformer_id = %s"""
    params: list = [transformer_id]
    if until is not None:
        query += " AND opened_at <= %s"
        params.append(until)
    if iot is not None:
        query += " AND iot_slot = %s"
        params.append(iot)
    if status is not None:
        query += " AND status = %s"
        params.append(status)
    query += " ORDER BY check_no"
    if limit is not None:
        query += " LIMIT %s"
        params.append(limit)
    with connection.cursor() as cursor:
        cursor.execute(query, params)
        columns = [c.name for c in cursor.description]
        rows = [dict(zip(columns, r)) for r in cursor.fetchall()]
    for row in rows:
        for key in ("opened_at", "resolved_at"):
            row[key] = row[key].isoformat() if row[key] is not None else None
    return [{field: row[field] for field in SHEET_FIELDS} for row in rows]


def recent_alerts(connection, transformer_id: str, limit: int = 50) -> list[dict]:
    with connection.cursor() as cursor:
        cursor.execute("""SELECT ts, iot_slot, signal, value, threshold, comparison_operator, unit, message
                          FROM alerts WHERE transformer_id = %s ORDER BY ts DESC, iot_slot LIMIT %s""",
                       (transformer_id, limit))
        columns = [c.name for c in cursor.description]
        return [{**dict(zip(columns, r)), "ts": r[0].isoformat()} for r in cursor.fetchall()]
