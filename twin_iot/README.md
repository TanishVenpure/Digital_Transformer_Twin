# Transformer Twin: IoT monitoring layer

The seven physical-monitoring IoTs. Every IoT turns sensor readings into one standard
output: `NORMAL`, `WARNING`, `CRITICAL`, `MAINTENANCE_REQUIRED` or `PREDICTED_MAINTENANCE`.
These outputs feed the future Health Engine and the dashboard health bars IOT1–IOT7.

The structure mirrors `Digital_Transformer_Twin/app` (Formula → FormulaRegistry →
AlertEngine with thresholds from `config/thresholds.yaml`). `formulas/base.py` and
`formulas/context.py` are copied unchanged. There is no database or API here, so the
modules can be moved into the backend later.

## The seven IoTs

| Slot | IoT | Monitored value | Limits (config) | Prediction |
|---|---|---|---|---|
| IOT1 | Current Measurement Health | E_max = max \|I_meas − I_ref\| / I_ref × 100 | > 1 % → MAINTENANCE_REQUIRED | – |
| IOT2 | Voltage Measurement Health | E_max for V | > 1 % → MAINTENANCE_REQUIRED | – |
| IOT3 | Current | Loading = I_max / I_rated × 100 (I_rated = 841 A) | ≥ 90 % warn, ≥ 100 % critical | dLoading/dt |
| IOT4 | Voltage | D_max = max \|V − V_N\| / V_N × 100 | ≥ 5 % warn, ≥ 10 % critical | dD/dt |
| IOT5 | Oil Level | O(t) | ≤ 40 warn, ≤ 20 minimum | T_min = (O − O_min) / \|dO/dt\| |
| IOT6 | Top-Oil Temperature | T_oil (+ rise T_oil − T_amb) | ≥ 95 °C alarm, ≥ 105 °C trip, rise > 40 K warn | T_breach to trip |
| IOT7 | Winding Temperature | ΔT = max(T_R,T_Y,T_B) − T_amb | > 40 K warn, > 45 K critical | dΔT/dt |

Limits marked EXAMPLE in `config/thresholds.yaml` are placeholders. Replace them with
the real nameplate and manufacturer values.

**Signal rules**
- A critical breach gives `CRITICAL` + `MAINTENANCE_REQUIRED`. The measurement-health
  IoTs give `MAINTENANCE_REQUIRED` only, because a faulty sensor is not a transformer fault.
- `PREDICTED_MAINTENANCE` is raised when the trend reaches the critical limit within
  `lead_time_hours` and the limit has not been crossed yet.
- The rate comes from the previous reading of the same IoT and transformer:
  (value − previous) / hours.
- Missing inputs make the IoT `SKIPPED`, and bad inputs make it `INVALID`. Neither is
  treated as a fault.
- `status` is the worst signal: CRITICAL > MAINTENANCE_REQUIRED > PREDICTED_MAINTENANCE > WARNING > NORMAL.

## Input columns

`current_r/y/b`, `reference_current_r/y/b`, `voltage_r/y/b`, `reference_voltage_r/y/b`,
`oil_level`, `top_oil_temperature`, `ambient_temperature`, `winding_temperature_r/y/b`,
plus `timestamp` and optionally `transformer_id`. Phases R/Y/B = A/B/C. Rename dataset
columns in `config/column_mapping.yaml`.

## Full stack: PostgreSQL + FastAPI + dashboard

`../dashboard/` is the web page (served by this API at `/dashboard/`). It has the IOT1–7
health bars, the Blender transformer model (`transformer.glb` from the FRONTEND branch, with
tank views, cutaway and explode/assemble), the maintenance sheet, and a time bar that plays
hourly data at 1 day per second.

```bash
# 1. A local PostgreSQL database (any server works; set TWIN_DATABASE_URL to use another)
initdb -D <data-dir> -U twin -A trust      # once
pg_ctl start -D <data-dir> -o "-p 5434"
createdb -h localhost -p 5434 -U twin twin_iot
# default URL: postgresql://twin@localhost:5434/twin_iot

# 2. Random hourly sample data (90 days) + maintenance sheet exports in data/maintenance/
python scripts/simulate.py

# 3. API + dashboard  ->  http://localhost:8020/dashboard/
python -m uvicorn app.api.main:app --port 8020
```

**Security.** This server has its own credentials; main-backend `AGENT_CREDENTIALS` keys
grant nothing here.

- `POST /api/readings`, `POST /api/upload` and `POST /api/twin3d/signal` require an
  `X-Twin-API-Key` header. Configure `TWIN_API_CREDENTIALS` as semicolon-separated
  `transformer_id[,transformer_id]:secret` entries. Missing configuration returns 503, a
  missing or invalid key 401, and an out-of-scope transformer 403. Signals for another
  transformer's reading return 404.
- An upload is rejected before anything is deleted or stored if any row names a
  transformer outside the key's scope, or is malformed or future-dated. Uploads are limited
  to 2 MiB.
- Readings dated more than `TWIN_MAX_FUTURE_SKEW_SECONDS` (default 300) in the future are
  rejected, so one bad timestamp cannot block later genuine readings.
- Requests whose `Host` is not `localhost` or `127.0.0.1` are rejected, which blocks DNS
  rebinding. Set `TWIN_ALLOWED_HOSTS` only if you deliberately serve another host name.
- GET routes stay unauthenticated for the local dashboard. Keep the default loopback bind
  and do not use `--host 0.0.0.0`.
- Startup refuses an Alembic-managed database, so `TWIN_DATABASE_URL` cannot point at the
  main backend database.
- The dashboard asks for the key on the first upload and keeps it only for that browser tab.

- **Validation:** every input of the 7 IoTs is range- and type-checked (`config/validation.yaml`).
- **3D signalling:** when an IoT's threshold is met, FastAPI selects that IoT's view
  (`config/models.yaml`) and calls the Blender bridge (`app/twin3d/blender_bridge.py`; the calls
  return `PENDING_BLENDER` until the bpy code is added).
- **Maintenance sheet:** one check per threshold being met, in date order, for the future RAG.
  It is available from `GET /api/maintenance/{transformer}?format=json|csv|jsonl|md`.
- **API samples:** `docs/api_samples/` has real request and response JSON;
  `python scripts/api_samples.py` captures them again.

## Run (models only)

```bash
python scripts/run_csv.py                                  # demo CSV
python scripts/run_csv.py my_data.csv --json results.json  # full evaluations as JSON
python -m pytest -q
```

Needs Python 3.11+, pydantic 2 and PyYAML.
