# Transformer Twin: IoT monitoring layer

The seven physical-monitoring IoTs. Every IoT turns sensor readings into one standard
output: `NORMAL`, `WARNING`, `CRITICAL`, `MAINTENANCE_REQUIRED` or `PREDICTED_MAINTENANCE`.
These outputs feed the future Health Engine and the dashboard health bars IOT1–IOT7.

The structure mirrors `Digital_Transformer_Twin/app` (Formula → FormulaRegistry →
AlertEngine with thresholds from `config/thresholds.yaml`). `formulas/base.py` and
`formulas/context.py` are copied unchanged. On top of the IoTs it has a PostgreSQL store,
a FastAPI server, a degradation simulator, trend analytics and the web dashboard
(`../dashboard/`).

## Quick start (any machine)

Everything runs from this folder (`twin_iot/`). It starts with an empty database: load data
by dropping a CSV on the dashboard or by running the simulator.

### Option A: Docker (nothing else to install)

> Not yet tested end to end; Option B is the verified route.

```bash
cd twin_iot
docker compose up -d --build
```

Open <http://localhost:8020/dashboard/>. For 90 days of demo data:

```bash
docker compose exec api python scripts/simulate.py
```

Stop with `docker compose down` (add `-v` to delete the data too). If ports 8020 or 5434
are taken, set other ones: `TWIN_API_PORT=8080 TWIN_DB_PORT=5440 docker compose up -d`.

### Option B: Python + your own PostgreSQL

Needs Python 3.11+ and PostgreSQL 13+ (the `initdb`, `pg_ctl`, `createdb` tools are in its
`bin` folder).

```bash
cd twin_iot
python -m venv .venv
.venv/Scripts/activate            # Windows   (macOS / Linux: source .venv/bin/activate)
pip install -r requirements.txt
python -m pytest -q               # 70 tests, no database needed

# a database: user "twin", database "twin_iot", port 5434, no password (local only)
initdb -D twin_pg -U twin -A trust
pg_ctl start -D twin_pg -o "-p 5434" -l twin_pg.log
createdb -h localhost -p 5434 -U twin twin_iot

python scripts/simulate.py        # optional: 90 days of demo data
python -m uvicorn app.api.main:app --port 8020
```

Open <http://localhost:8020/dashboard/>.

To use an existing PostgreSQL server instead, create a database and point the app at it
(the default is `postgresql://twin@localhost:5434/twin_iot`):

```bash
export TWIN_DATABASE_URL=postgresql://USER:PASSWORD@HOST:PORT/DBNAME     # Windows PowerShell: $env:TWIN_DATABASE_URL="..."
```

### Loading your own data

- **Dashboard:** drop a CSV on "Drop CSV data here" (it uses `POST /api/upload`). An upload
  replaces the stored readings of the transformers in the file (send `replace=false` to append).
- **Script:** `python scripts/run_csv.py my_data.csv` evaluates a CSV without the database.
- Column names are listed under "Input columns" below; rename yours in `config/column_mapping.yaml`.
- `data/examples/escalation_demo.csv` is a small example file.

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

How to start it: see "Quick start" at the top. The simulator writes the maintenance sheet
exports to `data/maintenance/`.

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

Needs Python 3.11+ and `pip install -r requirements.txt`.
