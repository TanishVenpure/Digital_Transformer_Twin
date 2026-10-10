"""FastAPI: timeline for the dashboard, live ingestion, CSV upload and 3D-model signalling.

Run: python -m uvicorn app.api.main:app --port 8000
"""
import os
import tempfile
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import PlainTextResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from app.alerts.configuration import PROJECT_ROOT
from app.database import db
from app.analytics import load_growth, winding
from app.analytics.oil_leak import analyse as analyse_oil_leak, load_config as load_leak_config
from app.formulas.registry import formula_registry
from app.maintenance.sheet import SHEET_FIELDS, load_actions
from app.processing.ingestion import read_csv
from app.services.processing_service import ProcessingService
from app.twin3d.blender_bridge import BlenderBridge
from app.twin3d.selector import load_models, select_model

DASHBOARD_DIR = Path(os.environ.get("TWIN_DASHBOARD_DIR", PROJECT_ROOT.parent / "dashboard"))
HOURS_PER_SECOND = 24  # timeline tick: one day of hourly data per second at 1x

app = FastAPI(title="Transformer Twin IoT API", version="0.1.0")
live_service = ProcessingService(history_loader=db.load_previous_valid_reading, timestamp_loader=db.load_last_timestamp)
models = load_models()
bridge = BlenderBridge(models)
actions = load_actions()


@app.on_event("startup")
def startup() -> None:
    with db.connect() as connection:
        db.create_schema(connection)
    bridge.load_base_model()


@app.get("/api/health")
def health() -> dict[str, Any]:
    with db.connect() as connection, connection.cursor() as cursor:
        cursor.execute("SELECT 1")
    return {"status": "ok", "database": "connected"}


@app.get("/api/transformers")
def list_transformers() -> list[dict]:
    with db.connect() as connection:
        return db.transformers(connection)


@app.get("/api/iots")
def list_iots() -> list[dict]:
    return [
        {"slot": rule.iot_slot, "name": name, "display_name": formula_registry.get(name).display_name,
         "unit": rule.unit or "unconfigured", "operator": rule.operator, "warning": rule.warning, "critical": rule.critical}
        for name, rule in sorted(live_service.thresholds.items(), key=lambda item: item[1].iot_slot)
    ]


@app.get("/api/timeline/{transformer_id}")
def timeline(transformer_id: str) -> dict[str, Any]:
    """Every stored hour for one transformer, compact enough to play back client-side."""
    with db.connect() as connection:
        rows = db.timeline(connection, transformer_id)
        events = db.maintenance_events(connection, transformer_id)
        thermal = winding_analysis(connection, transformer_id)
        cooling = db.top_oil_analysis(connection, transformer_id, live_service.thresholds)
    if not rows:
        raise HTTPException(404, f"No readings for {transformer_id}")
    frames = []
    slots = [iot["slot"] for iot in list_iots()]
    for reading_id, ts, model_id, issues, evaluations in rows:
        by_slot = {e[0]: e for e in evaluations}
        frames.append({
            "id": reading_id, "t": ts.isoformat(), "m": model_id,
            "s": [by_slot[s][1] if s in by_slot else None for s in slots],
            "v": [by_slot[s][2] if s in by_slot else None for s in slots],
            "ttc": [by_slot[s][3] if s in by_slot else None for s in slots],
            "d": {str(e[0]): e[4] for e in evaluations if len(e) > 4 and e[4]},
            "q": [[i["feature"], i["code"]] for i in issues],
        })
    return {
        "transformer_id": transformer_id, "start": frames[0]["t"], "end": frames[-1]["t"],
        "hours_per_second": HOURS_PER_SECOND, "iots": list_iots(),
        "models": {name: spec.label for name, spec in models.models.items()}, "frames": frames,
        "events": events,
        "analytics": {"oil_leak": oil_leak_analysis(rows), "load_growth": load_growth_analysis(rows), "winding": thermal,
                      "top_oil": cooling},
    }


def oil_leak_analysis(rows) -> dict[str, Any]:
    """Refill cycles, leak rate per cycle and accelerating-leak flags, from the oil-level readings."""
    slot = live_service.thresholds["oil_level"].iot_slot
    series = [(ts, next((e[2] for e in evaluations if e[0] == slot), None)) for _, ts, _, _, evaluations in rows]
    return {"slot": slot, **analyse_oil_leak(series, load_leak_config()).as_dict()}


def load_growth_analysis(rows) -> dict[str, Any]:
    """Weekly load reviews: overload hours/events, daily-peak trend, rated-current forecast, flags."""
    rule = live_service.thresholds["current_loading"]
    series = [(ts, next((e[2] for e in evaluations if e[0] == rule.iot_slot), None)) for _, ts, _, _, evaluations in rows]
    return {"slot": rule.iot_slot,
            **load_growth.analyse(series, load_growth.load_config(warning=rule.warning, critical=rule.critical))}


def winding_analysis(connection, transformer_id: str) -> dict[str, Any]:
    """Weekly thermal reviews: excess over the thermal model, insulation ageing, flags (+ hourly expected rise)."""
    rules = live_service.thresholds
    slot = rules["winding_temperature"].iot_slot
    inputs = db.winding_inputs(connection, transformer_id, slot, rules["current_loading"].iot_slot)
    return {"slot": slot, **winding.analyse(inputs, winding.load_config())}


@app.get("/api/analytics/winding/{transformer_id}")
def winding_endpoint(transformer_id: str) -> dict[str, Any]:
    with db.connect() as connection:
        result = winding_analysis(connection, transformer_id)
    if not result["expected"]:
        raise HTTPException(404, f"No readings for {transformer_id}")
    return {k: v for k, v in result.items() if k not in ("expected", "expected_wti")}


@app.get("/api/analytics/top-oil/{transformer_id}")
def top_oil_endpoint(transformer_id: str) -> dict[str, Any]:
    """Cooling health, cleaning cycles, oil ageing, forecasts and the winding cross-check (no hourly arrays)."""
    with db.connect() as connection:
        result = db.top_oil_analysis(connection, transformer_id, live_service.thresholds)
    if not result["cycles"]:
        raise HTTPException(404, f"No readings for {transformer_id}")
    return {k: v for k, v in result.items() if k != "hourly"}


@app.get("/api/analytics/load-growth/{transformer_id}")
def load_growth_endpoint(transformer_id: str) -> dict[str, Any]:
    with db.connect() as connection:
        rows = db.timeline(connection, transformer_id)
    if not rows:
        raise HTTPException(404, f"No readings for {transformer_id}")
    return load_growth_analysis(rows)


@app.get("/api/analytics/oil-leak/{transformer_id}")
def oil_leak(transformer_id: str) -> dict[str, Any]:
    with db.connect() as connection:
        rows = db.timeline(connection, transformer_id)
    if not rows:
        raise HTTPException(404, f"No readings for {transformer_id}")
    return oil_leak_analysis(rows)


@app.get("/api/maintenance-events/{transformer_id}")
def maintenance_events(transformer_id: str) -> list[dict]:
    """Maintenance actually performed (repairs), as opposed to checks raised by thresholds."""
    with db.connect() as connection:
        return db.maintenance_events(connection, transformer_id)


@app.get("/api/readings/{reading_id}")
def reading(reading_id: int) -> dict[str, Any]:
    with db.connect() as connection:
        detail = db.reading_detail(connection, reading_id)
    if detail is None:
        raise HTTPException(404, "Reading not found")
    return detail


@app.get("/api/alerts/{transformer_id}")
def alerts(transformer_id: str, limit: int = 50) -> list[dict]:
    with db.connect() as connection:
        return db.recent_alerts(connection, transformer_id, min(limit, 500))


class ReadingIn(BaseModel):
    transformer_id: str
    timestamp: datetime
    data: dict[str, Any]


@app.post("/api/readings")
def ingest(reading: ReadingIn) -> dict[str, Any]:
    """Live path: validate -> evaluate -> store -> signal the 3D model if thresholds are met."""
    processed = live_service.process(reading.transformer_id, reading.timestamp, reading.data)
    selection = select_model(processed.evaluations, models)
    with db.connect() as connection:
        reading_id = db.store(connection, processed, selection)
        if reading_id is not None:
            db.rebuild_maintenance(connection, processed.transformer_id, live_service.thresholds, actions)
    call = bridge.signal(selection, source="live", reading_time=processed.timestamp.isoformat()) if processed.accepted else None
    return {
        "reading_id": reading_id, "accepted": processed.accepted,
        "issues": [i.model_dump(mode="json") for i in processed.issues],
        "iots": [{"slot": e.iot_slot, "status": e.status, "value": e.value, "signals": e.signals,
                  "evaluation_status": e.evaluation_status, "unit": e.unit, "details": e.details}
                 for e in sorted(processed.evaluations.values(), key=lambda e: e.iot_slot)],
        "model": selection.model_dump(mode="json"), "model_call": call,
    }


@app.post("/api/upload")
async def upload(file: UploadFile = File(...), transformer_id: str = Form("T1"), replace: bool = Form(True)) -> dict[str, Any]:
    """CSV drop zone: every row is validated and evaluated exactly like live readings."""
    if not (file.filename or "").lower().endswith(".csv"):
        raise HTTPException(400, "Only .csv files are accepted")
    content = await file.read()
    with tempfile.NamedTemporaryFile("wb", suffix=".csv", delete=False) as handle:
        handle.write(content)
        path = Path(handle.name)
    try:
        try:
            readings = sorted(read_csv(path, default_transformer=transformer_id), key=lambda r: (r[0], r[1]))
        except (KeyError, ValueError) as exc:
            raise HTTPException(400, f"Could not read CSV (needs a 'timestamp' column in ISO format): {exc}")
    finally:
        path.unlink(missing_ok=True)
    service, issues, stored, rejected = ProcessingService(history_loader=db.load_previous_valid_reading), Counter(), 0, 0
    with db.connect() as connection:
        # Share this transaction so replacement/append history sees the same stored rows.
        service.history_loader = lambda source, name, before: db.previous_valid_reading(connection, source, name, before)
        if replace:
            for transformer in {r[0] for r in readings}:
                db.clear_transformer(connection, transformer)
        for transformer, timestamp, data in readings:
            processed = service.process(transformer, timestamp, data)
            issues.update(i.code.value for i in processed.issues)
            if db.store(connection, processed, select_model(processed.evaluations, models)) is None:
                rejected += 1
            else:
                stored += 1
        for transformer in {r[0] for r in readings}:
            db.rebuild_maintenance(connection, transformer, service.thresholds, actions)
    return {"filename": file.filename, "rows": len(readings), "stored": stored, "rejected": rejected,
            "transformers": sorted({r[0] for r in readings}), "issues": dict(issues)}


# --- maintenance sheet --------------------------------------------------------------------------------

@app.get("/api/maintenance/{transformer_id}")
def maintenance(transformer_id: str, until: datetime | None = None, iot: int | None = None,
                status: str | None = None, limit: int | None = None, format: str = "json"):
    """Sequential maintenance checks. format: json | csv | jsonl | md (jsonl/md for the future RAG)."""
    with db.connect() as connection:
        rows = db.maintenance_sheet(connection, transformer_id, until=until, iot=iot, status=status, limit=limit)
    if format == "json":
        return {"transformer_id": transformer_id, "checks": len(rows), "fields": list(SHEET_FIELDS), "rows": rows}
    if format == "csv":
        import csv
        import io
        buffer = io.StringIO()
        writer = csv.DictWriter(buffer, fieldnames=SHEET_FIELDS)
        writer.writeheader()
        writer.writerows(rows)
        return PlainTextResponse(buffer.getvalue(), media_type="text/csv", headers={
            "Content-Disposition": f'attachment; filename="maintenance_sheet_{transformer_id}.csv"'})
    if format == "jsonl":
        import json
        return PlainTextResponse("".join(json.dumps(r) + "\n" for r in rows), media_type="application/x-ndjson")
    if format == "md":
        return PlainTextResponse("\n\n".join(r["text"] for r in rows), media_type="text/markdown")
    raise HTTPException(400, "format must be json, csv, jsonl or md")


# --- 3D model signalling ---------------------------------------------------------------------------

@app.get("/api/twin3d/models")
def twin3d_models() -> dict[str, Any]:
    return {"trigger_signals": models.trigger_signals,
            "models": {name: spec.model_dump() for name, spec in models.models.items()}}


@app.get("/api/twin3d/state")
def twin3d_state(calls: int = 20) -> dict[str, Any]:
    return {"current_model": bridge.current_model, "recent_calls": bridge.calls[-calls:][::-1]}


class SignalIn(BaseModel):
    reading_id: int
    source: str = "timeline"


@app.post("/api/twin3d/signal")
def twin3d_signal(signal: SignalIn) -> dict[str, Any]:
    """Timeline playback: re-select the model for a stored hour and switch only if it changed."""
    with db.connect() as connection:
        detail = db.reading_detail(connection, signal.reading_id)
    if detail is None:
        raise HTTPException(404, "Reading not found")
    from app.alerts.engine import IoTEvaluation  # local: only needed to rebuild statuses
    evaluations = [
        IoTEvaluation(formula_name=e["formula_name"], display_name=e["formula_name"], iot_slot=e["iot_slot"],
                      evaluation_status=e["evaluation_status"], status=e["status"], unit=e["unit"])
        for e in detail["evaluations"]
    ]
    selection = select_model(evaluations, models)
    call = bridge.signal(selection, source=signal.source, reading_time=detail["timestamp"])
    return {"model": selection.model_dump(mode="json"), "switched": call is not None, "model_call": call}


# --- two-timebar model (app/twin2): timebar 1 = one day of fast signals, timebar 2 = one year daily ----------

@app.get("/api/twin2/timebar/{n}")
def twin2_timebar(n: int) -> dict[str, Any]:
    """Playback payload for timebar 1 (1-minute resolution + 20 ms event windows) or timebar 2 (daily)."""
    from app.twin2.build import load_payload
    if n not in (1, 2):
        raise HTTPException(404, "Timebar must be 1 or 2")
    return load_payload(n)


@app.post("/api/twin2/rebuild")
def twin2_rebuild() -> dict[str, Any]:
    """Re-run both simulations from config/twin2.yaml and rewrite the datasets."""
    from app.twin2.build import build
    out = build()
    return {n: len(out[n]["events"]) for n in ("timebar1", "timebar2")}


@app.get("/api/twin2/files/{name}")
def twin2_file(name: str):
    """Download one of the generated datasets (CSV)."""
    from fastapi.responses import FileResponse
    from app.twin2.build import OUT_DIR
    path = (OUT_DIR / name).resolve()
    if path.parent != OUT_DIR.resolve() or path.suffix != ".csv" or not path.exists():
        raise HTTPException(404, "Unknown dataset")
    return FileResponse(path, media_type="text/csv", filename=name)


@app.get("/", include_in_schema=False)
def root() -> RedirectResponse:
    return RedirectResponse("/dashboard/")


if DASHBOARD_DIR.is_dir():
    app.mount("/dashboard", StaticFiles(directory=DASHBOARD_DIR, html=True), name="dashboard")
