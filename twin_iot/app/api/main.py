"""FastAPI: the two-timebar transformer twin (app/twin2), its assistant, and the dashboard.

Run: python -m uvicorn app.api.main:app --port 8020   ->  http://localhost:8020/dashboard/
"""
import os
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from app.api.assistant import router as assistant_router
from app.paths import PROJECT_ROOT
from app.twin2.build import OUT_DIR, build, load_payload
from app.twin2 import sheet as twin_sheet

DASHBOARD_DIR = Path(os.environ.get("TWIN_DASHBOARD_DIR", PROJECT_ROOT.parent / "dashboard"))

app = FastAPI(title="Transformer Twin API", version="0.2.0")
app.include_router(assistant_router)


@app.get("/api/health")
def health() -> dict[str, Any]:
    return {"status": "ok", "datasets": sorted(p.name for p in OUT_DIR.glob("timebar*.json")) if OUT_DIR.exists() else []}


# --- two-timebar model (app/twin2): timebar 1 = one day of fast signals, timebar 2 = one year daily ----------

@app.get("/api/twin2/timebar/{n}")
def twin2_timebar(n: int) -> dict[str, Any]:
    """Playback payload for timebar 1 (1-minute resolution + 20 ms event windows) or timebar 2 (daily)."""
    if n not in (1, 2):
        raise HTTPException(404, "Timebar must be 1 or 2")
    return load_payload(n)


@app.get("/api/twin2/sheet")
def twin2_sheet() -> dict[str, Any]:
    """Recorded checks from existing playback events; the browser applies each timebar cursor."""
    checks, _, _ = twin_sheet.load_sheet()
    return {"transformer_id": twin_sheet.TRANSFORMER_ID, "checks": checks}


@app.post("/api/twin2/rebuild")
def twin2_rebuild() -> dict[str, Any]:
    """Re-run both simulations from config/twin2.yaml and rewrite the datasets."""
    out = build()
    return {n: len(out[n]["events"]) for n in ("timebar1", "timebar2")}


@app.get("/api/twin2/files/{name}")
def twin2_file(name: str):
    """Download one of the generated datasets (CSV)."""
    path = (OUT_DIR / name).resolve()
    if path.parent != OUT_DIR.resolve() or path.suffix != ".csv" or not path.exists():
        raise HTTPException(404, "Unknown dataset")
    return FileResponse(path, media_type="text/csv", filename=name)


@app.get("/", include_in_schema=False)
def root() -> RedirectResponse:
    return RedirectResponse("/dashboard/")


if DASHBOARD_DIR.is_dir():
    app.mount("/dashboard", StaticFiles(directory=DASHBOARD_DIR, html=True), name="dashboard")
