"""FastAPI: timeline for the dashboard, live ingestion, CSV upload and 3D-model signalling.

Run: python -m uvicorn app.api.main:app --port 8000
"""
import csv
import os
import tempfile
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import PlainTextResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator
from starlette.middleware.trustedhost import TrustedHostMiddleware

from app.alerts.configuration import PROJECT_ROOT
from fastapi import Header
from app.api.security import (
    API_KEY_HEADER, MAX_READING_FIELDS, MAX_UPLOAD_BYTES, allowed_hosts, authorize_transformer,
    check_not_future, check_transformer_id, require_write_access,
)
from app.database import db
from app.analytics import load_growth, winding
from app.analytics.oil_leak import analyse as analyse_oil_leak, load_config as load_leak_config
from app.formulas.registry import formula_registry
from app.maintenance.sheet import SHEET_FIELDS, load_actions
from app.processing.ingestion import read_csv
from app.services.processing_service import ProcessingService
from app.api.assistant import router as assistant_router
from app.twin3d.blender_bridge import BlenderBridge
from app.twin3d.selector import load_models, select_model

DASHBOARD_DIR = Path(os.environ.get("TWIN_DASHBOARD_DIR", PROJECT_ROOT.parent / "dashboard"))
HOURS_PER_SECOND = 24  # timeline tick: one day of hourly data per second at 1x

app = FastAPI(title="Transformer Twin IoT API", version="0.1.0")
app.include_router(assistant_router)
# Rejects DNS-rebinding requests whose Host is not this machine (TWIN_ALLOWED_HOSTS overrides).
app.add_middleware(TrustedHostMiddleware, allowed_hosts=allowed_hosts())
live_service = ProcessingService()
models = load_models()
bridge = BlenderBridge(models)
actions = load_actions()


@app.on_event("startup")
def startup() -> None:
    with db.connect() as connection:
        db.refuse_managed_database(connection)
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
         "unit": rule.unit, "operator": rule.operator, "warning": rule.warning, "critical": rule.critical}
        for name, rule in sorted(live_service.thresholds.items(), key=lambda item: item[1].iot_slot)
    ]


@app.get("/api/timeline/{transformer_id}")
def timeline(transformer_id: str) -> dict[str, Any]:
    """Every stored hour for one transformer, compact enough to play back client-side."""
    with db.connect() as connection:
        rows = db.timeline(connection, transformer_id)
        events = db.maintenance_events(connection, transformer_id)
        thermal = _winding_analysis(connection, transformer_id)
        cooling = db.top_oil_analysis(connection, transformer_id, live_service.thresholds)
    if not rows:
        raise HTTPException(404, f"No readings for {transformer_id}")
    frames = []
    for reading_id, ts, model_id, issues, evaluations in rows:
        frames.append({
            "id": reading_id, "t": ts.isoformat(), "m": model_id,
            "s": [e[1] for e in evaluations], "v": [e[2] for e in evaluations], "ttc": [e[3] for e in evaluations],
            "q": [[i["feature"], i["code"]] for i in issues],
        })
    return {
        "transformer_id": transformer_id, "start": frames[0]["t"], "end": frames[-1]["t"],
        "hours_per_second": HOURS_PER_SECOND, "iots": list_iots(),
        "models": {name: spec.label for name, spec in models.models.items()}, "frames": frames,
        "events": events,
        "analytics": {"oil_leak": _oil_leak_analysis(rows), "load_growth": _load_growth_analysis(rows),
                      "winding": thermal, "top_oil": cooling},
    }


def _oil_leak_analysis(rows) -> dict[str, Any]:
    """Refill cycles, leak rate per cycle and accelerating-leak flags, from the oil-level readings."""
    slot = live_service.thresholds["oil_level"].iot_slot
    series = [(ts, next((e[2] for e in evaluations if e[0] == slot), None)) for _, ts, _, _, evaluations in rows]
    return {"slot": slot, **analyse_oil_leak(series, load_leak_config()).as_dict()}


def _load_growth_analysis(rows) -> dict[str, Any]:
    """Weekly load reviews: overload hours/events, daily-peak trend, rated-current forecast, flags."""
    rule = live_service.thresholds["current_loading"]
    series = [(ts, next((e[2] for e in evaluations if e[0] == rule.iot_slot), None)) for _, ts, _, _, evaluations in rows]
    return {"slot": rule.iot_slot,
            **load_growth.analyse(series, load_growth.load_config(warning=rule.warning, critical=rule.critical))}


def _winding_analysis(connection, transformer_id: str) -> dict[str, Any]:
    """Weekly thermal reviews: excess over the thermal model, insulation ageing, flags (+ hourly expected rise)."""
    rules = live_service.thresholds
    slot = rules["winding_temperature"].iot_slot
    inputs = db.winding_inputs(connection, transformer_id, slot, rules["current_loading"].iot_slot)
    return {"slot": slot, **winding.analyse(inputs, winding.load_config())}


@app.get("/api/analytics/winding/{transformer_id}")
def winding_endpoint(transformer_id: str) -> dict[str, Any]:
    with db.connect() as connection:
        result = _winding_analysis(connection, transformer_id)
    if not result["expected"]:
        raise HTTPException(404, f"No readings for {transformer_id}")
    return {k: v for k, v in result.items() if k != "expected"}


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
    return _load_growth_analysis(rows)


@app.get("/api/analytics/oil-leak/{transformer_id}")
def oil_leak_endpoint(transformer_id: str) -> dict[str, Any]:
    with db.connect() as connection:
        rows = db.timeline(connection, transformer_id)
    if not rows:
        raise HTTPException(404, f"No readings for {transformer_id}")
    return _oil_leak_analysis(rows)


@app.get("/api/maintenance-events/{transformer_id}")
def maintenance_events_endpoint(transformer_id: str) -> list[dict]:
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
    data: dict[str, Any] = Field(max_length=MAX_READING_FIELDS)

    _transformer = field_validator("transformer_id")(check_transformer_id)
    _timestamp = field_validator("timestamp")(check_not_future)


@app.post("/api/readings")
def ingest(reading: ReadingIn, authorized: set[str] = Depends(require_write_access)) -> dict[str, Any]:
    """Live path: validate -> evaluate -> store -> signal the 3D model if thresholds are met.

    Requires X-Twin-API-Key authorized for the reading's transformer.
    """
    authorize_transformer(reading.transformer_id, authorized)
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
        "iots": [{"slot": e.iot_slot, "status": e.status, "value": e.value, "signals": e.signals}
                 for e in sorted(processed.evaluations.values(), key=lambda e: e.iot_slot)],
        "model": selection.model_dump(mode="json"), "model_call": call,
    }


def _checked_rows(readings: list[tuple[str, datetime, dict[str, Any]]], authorized: set[str]) -> list:
    """Validate and authorize every row before anything is deleted or stored."""
    checked = []
    for transformer, timestamp, data in readings:
        try:
            transformer = check_transformer_id(transformer)
            check_not_future(timestamp)
        except ValueError as exc:
            raise HTTPException(400, f"Invalid CSV row: {exc}") from None
        if len(data) > MAX_READING_FIELDS:
            raise HTTPException(400, f"Invalid CSV row: more than {MAX_READING_FIELDS} columns")
        authorize_transformer(transformer, authorized)
        checked.append((transformer, timestamp, data))
    return checked


@app.post("/api/upload")
async def upload(
    file: UploadFile = File(...), transformer_id: str = Form("T1"), replace: bool = Form(True),
    authorized: set[str] = Depends(require_write_access),
) -> dict[str, Any]:
    """CSV drop zone: every row is validated and evaluated exactly like live readings.

    Requires X-Twin-API-Key authorized for every transformer named in the file; with
    replace=true those transformers' stored readings are deleted first.
    """
    if not (file.filename or "").lower().endswith(".csv"):
        raise HTTPException(400, "Only .csv files are accepted")
    try:
        default_transformer = check_transformer_id(transformer_id)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from None
    content = await file.read(MAX_UPLOAD_BYTES + 1)
    if len(content) > MAX_UPLOAD_BYTES:
        raise HTTPException(413, f"CSV exceeds {MAX_UPLOAD_BYTES} bytes")
    with tempfile.NamedTemporaryFile("wb", suffix=".csv", delete=False) as handle:
        handle.write(content)
        path = Path(handle.name)
    try:
        try:
            readings = sorted(read_csv(path, default_transformer=default_transformer), key=lambda r: (r[0], r[1]))
        except (KeyError, ValueError, TypeError, AttributeError, csv.Error):
            # UnicodeDecodeError is a ValueError. Do not echo uploaded content.
            raise HTTPException(
                400, "Could not read CSV (needs UTF-8, a header row and an ISO 'timestamp' column)"
            ) from None
    finally:
        path.unlink(missing_ok=True)
    readings = _checked_rows(readings, authorized)
    service, issues, stored, rejected = ProcessingService(), Counter(), 0, 0
    with db.connect() as connection:
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
    source: str = Field(default="timeline", min_length=1, max_length=32, pattern=r"^[a-z_]+$")


@app.post("/api/twin3d/signal")
def twin3d_signal(signal: SignalIn, x_twin_api_key: str | None = Header(default=None)) -> dict[str, Any]:
    """Timeline playback: re-select the model for a stored hour and switch only if it changed.

    Read-only model switching for the dashboard.  When an API key is provided,
    readings are scoped to the key's authorized transformers; without a key,
    any stored reading is allowed (dashboard default).
    """
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


@app.get("/api/twin2/sheet")
def twin2_sheet() -> dict[str, Any]:
    """Every maintenance check of both timebars (no status: the dashboard evaluates it at its cursors)."""
    from app.twin2.sheet import TRANSFORMER_ID, load_sheet
    checks, _, _ = load_sheet()
    return {"transformer_id": TRANSFORMER_ID, "checks": checks}


@app.post("/api/twin2/rebuild")
def twin2_rebuild(authorized: set[str] = Depends(require_write_access)) -> dict[str, Any]:
    """Re-run both simulations from config/twin2.yaml and rewrite the datasets (needs a write key)."""
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
