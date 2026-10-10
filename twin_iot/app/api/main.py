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
from app.api.security import (
    MAX_READING_FIELDS, MAX_UPLOAD_BYTES, allowed_hosts, authorize_transformer, check_not_future,
    check_transformer_id, require_write_access,
)
from app.database import db
from app.formulas.registry import formula_registry
from app.maintenance.sheet import SHEET_FIELDS, load_actions
from app.processing.ingestion import read_csv
from app.services.processing_service import ProcessingService
from app.twin3d.blender_bridge import BlenderBridge
from app.twin3d.selector import load_models, select_model

DASHBOARD_DIR = Path(os.environ.get("TWIN_DASHBOARD_DIR", PROJECT_ROOT.parent / "dashboard"))
HOURS_PER_SECOND = 24  # timeline tick: one day of hourly data per second at 1x

app = FastAPI(title="Transformer Twin IoT API", version="0.1.0")
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
    }


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
def twin3d_signal(signal: SignalIn, authorized: set[str] = Depends(require_write_access)) -> dict[str, Any]:
    """Timeline playback: re-select the model for a stored hour and switch only if it changed.

    Requires X-Twin-API-Key; readings of other transformers are reported as not found.
    """
    with db.connect() as connection:
        detail = db.reading_detail(connection, signal.reading_id)
    if detail is None or detail["transformer_id"] not in authorized:
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


@app.get("/", include_in_schema=False)
def root() -> RedirectResponse:
    return RedirectResponse("/dashboard/")


if DASHBOARD_DIR.is_dir():
    app.mount("/dashboard", StaticFiles(directory=DASHBOARD_DIR, html=True), name="dashboard")
