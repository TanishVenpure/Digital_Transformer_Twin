# Transformer Twin: IoT monitoring layer

The eight registered physical-monitoring IoTs. Every IoT turns sensor readings into one standard
output: `NORMAL`, `WARNING`, `CRITICAL`, `MAINTENANCE_REQUIRED` or `PREDICTED_MAINTENANCE`.
These outputs feed the future Health Engine and the dashboard monitor cards IOT1–IOT8.

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
python -m pytest -q               # unit tests; PostgreSQL tests opt in through TWIN_TEST_DATABASE_URL

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


## Rule 8: measured transformer pressure

`transformer_pressure_monitoring` (IOT8) consumes `oil_pressure`, with optional
`top_oil_temperature` and `ambient_temperature` from the same reading. It does
not infer pressure from temperature or loading.

Pressure units and all three production thresholds are intentionally unconfigured.
Until configured, measurements and signed rates are recorded, but no pressure
threshold alert is raised. Rate uses actual elapsed hours; there is no implicit
unit conversion. There is no pressure health-score normalization or synthetic
pressure generation. Existing 90-day demo readings do not contain pressure.

PostgreSQL API tests run in disposable schemas, leaving the live demo untouched:

```powershell
$env:TWIN_TEST_DATABASE_URL = '<explicit test PostgreSQL connection URL>'
..\.venv\Scripts\python.exe -m pytest -q tests
```


### Pressure configuration and comparison behavior

The entry below is the exact checked-in Rule 8 configuration. Warning/critical
are the configured absolute pressure limits; the secondary `pressure_rate`
threshold represents `pressure_rise_rate_critical`. All three remain null.

```yaml
transformer_pressure_monitoring:
    iot_slot: 8
    operator: ">="
    unit: null                 # physical pressure unit must be supplied before comparisons
    warning: null              # pressure_warning: not supplied
    critical: null             # pressure_critical: not supplied
    lead_time_hours: null       # no pressure forecasting or scheduling
    maintenance_type: TRANSFORMER_MAINTENANCE
    maintenance_sheet: true
    breach_signal: CRITICAL
    warning_when_critical: false
    warning_trigger_condition: HIGH_PRESSURE_WARNING
    critical_trigger_condition: PRESSURE_CRITICAL
    condition_detail: absolute_pressure_condition
    warning_detail: pressure_warning
    critical_detail: pressure_critical
    warning_message: "Transformer pressure reached or exceeded the configured warning threshold."
    critical_message: "Transformer pressure reached or exceeded the configured critical threshold."
    extra_checks:
      - detail: pressure_rate
        operator: ">="
        threshold: null        # pressure_rise_rate_critical: not supplied
        unit: null             # must equal configured pressure unit/hour; no implicit conversion
        unit_detail: pressure_rate_unit
        signal: CRITICAL
        trigger_condition: RAPID_PRESSURE_RISE
        condition_detail: rapid_pressure_condition
        threshold_detail: pressure_rise_rate_critical
        message: "Transformer pressure rise rate reached or exceeded the configured critical rate."
```

Once authoritative settings are supplied, set `unit` to the actual sensor pressure
unit and the rate check unit to that same unit followed by `/hour`. The internal
time basis is elapsed hours from actual timestamps. This is a software time basis,
not an assumption of kPa/bar/psi. There is no automatic unit conversion. Rate
comparison is disabled if its configured unit does not match the calculated unit.
A change of measurement unit also makes an incompatible previous observation
unusable for rate calculation. Missing optional temperatures do not prevent evaluation.

The formula returns `CALCULATED`, `value = oil_pressure`, and diagnostics including
current/previous pressure and timestamps, elapsed time/unit, signed pressure rate,
pressure/rate units, unavailable-trend reason and any same-reading temperatures.
The alert engine adds the configured threshold values, condition classifications,
all trigger conditions and per-alert comparison receipts. No pressure calculation
or decision is inferred from temperature or loading.

TEST-ONLY examples (these settings are not production engineering values):
warning = 10, critical = 20, rate threshold = 3, unit = `test_pressure`,
rate unit = `test_pressure/hour`.

| Observations | Result |
| --- | --- |
| First pressure 9 | NORMAL; no rate or alert |
| First pressure 10 | HIGH_PRESSURE_WARNING; one WARNING alert; rate unavailable |
| First pressure 20 | PRESSURE_CRITICAL; CRITICAL plus MAINTENANCE_REQUIRED; warning suppressed |
| Pressure 10 then 16, two hours later | rate = (16 - 10) / 2 = 3; warning and RAPID_PRESSURE_RISE |
| Pressure 0 then 6, two hours later | rate = 3; RAPID_PRESSURE_RISE despite pressure below warning |
| Pressure 10 then 20, two hours later | rate = 5; PRESSURE_CRITICAL and RAPID_PRESSURE_RISE; two alerts |
| Pressure 20 then 15, two hours later | rate = -2.5; no rapid-rise trigger |
| Zero/negative elapsed interval | rate unavailable; static formula/engine checks remain possible |

All three comparisons use `>=`; calculations are not rounded before comparison.
The processing pipeline continues to reject duplicate/out-of-order incoming readings
according to its existing policy. Low-level formula handling additionally protects
against unusable historical intervals. Missing pressure yields SKIPPED; non-finite,
non-numeric or boolean pressure is rejected without a maintenance alert. No physical
pressure validation range has been invented: only finite numeric values are required.

### Pressure persistence and agent access

No new table, dedicated pressure column or schema migration is required. Formula
inputs live in readings JSONB and diagnostics in `iot_evaluations.details`. Individual
threshold receipts are retained there, allowing joined alert responses to identify
each trigger even after threshold configuration changes. The common maintenance
response also exposes formula identity, source inputs, diagnostics and trigger conditions.

- `POST /api/readings`: accepts actual measured `oil_pressure`; returns slot-8 evaluation details.
- `GET /api/readings/{reading_id}`: full stored reading and evaluations.
- `GET /api/alerts/T1`: alerts with formula name, parameter, maintenance category,
  trigger identity, source inputs and diagnostic details.
- `GET /api/maintenance/T1?iot=8`: common pressure maintenance checks and context.
- `GET /api/timeline/T1`: eight-slot arrays plus per-slot diagnostics for actually
  stored evaluations. Pre-Rule-8 rows get response-only null padding; no backfill.

Restart-safe history uses the generic `ProcessingService.history_loader`. When
its in-memory prior observation is unavailable, the database adapter retrieves the
latest successful prior evaluation for the same transformer and formula, with a
strictly earlier timestamp. Invalid/skipped observations are ignored. The live
API also restores the latest persisted timestamp for duplicate/out-of-order checks.
CSV append/replacement history is read through the upload's own database transaction.

The dashboard has a pressure card, measured-pressure graph, and pressure-rate detail.
Pressure health normalization is intentionally unavailable. Existing demo pressure
history is empty and is displayed as such. Slot 8 uses the existing transformer asset
with empty focus_nodes: the asset has pressure-relief/Buchholz components but no
verified measured-pressure sensor correspondence, so no sensor node/position was invented.
The whole transformer remains visible when a pressure condition selects this view.

Synthetic sources were not changed. The system still models one intended transformer.
Nothing was committed or pushed as part of this implementation.


Verification for this change: **102 tests passed**, including the original 70 and
32 new pressure tests, with all 8 PostgreSQL/API integration tests enabled. Existing
FastAPI lifecycle/TestClient deprecation warnings remain. Live dashboard/API checks
passed; the browser reported no JavaScript exceptions. The pre-existing missing
`/favicon.ico` returns 404 and does not affect the application.

Before/after SHA-256 snapshots of all five live demo tables matched exactly:
2,160 readings, 15,120 evaluations (seven per historical reading), 4,279 alerts,
1,321 maintenance checks, and 20 performed-maintenance events. No historical
pressure values or evaluations were inserted. PostgreSQL tests used disposable
schemas that were removed after each test.

## Revised LV current overload rule

`current_loading` uses supplied 633 kVA / 433 V line-to-line LV ratings:
`I_rated = 633000 / (sqrt(3) * 433) = approximately 844.024758 A`.
Each R/Y/B phase loading is `abs(current) / I_rated * 100`; the largest phase decides.
At 110% loading raise `OVERLOAD_WARNING`; at 120% raise `TRIP_ALERT` instead of a
warning. Both comparisons are inclusive and use unrounded calculations. Existing
severity values remain WARNING / CRITICAL; trigger identities are in stored details.
Both belong to TRANSFORMER_MAINTENANCE. Trip is an alert only, with no breaker control.
The old prediction lead time is disabled. All phase loadings, source currents and
nameplate inputs are retained in diagnostics. Existing demo data and past evaluations
are unchanged; incoming readings use the revised configuration.

## HV/LV RMS voltage monitors

LV remains slot 4 (`voltage_deviation`); HV is slot 9 (`hv_voltage_deviation`).
Use explicit `lv_voltage_r/y/b` and `hv_voltage_r/y/b` RMS channels in volts.
Legacy `voltage_r/y/b` data is not silently assigned to a transformer side.
Supplied line-to-line nameplate ratings: LV 433 V, HV 11000 V.
Set each rule's `parameters.measurement_type` to `LINE_TO_LINE` or
`PHASE_TO_NEUTRAL` once the sensor output is confirmed. Until then evaluations
are SKIPPED, not normal or flagged. Phase-to-neutral reference is rated LL / sqrt(3).
Signed deviation is (measured - nominal) / nominal * 100 for every R/Y/B channel.
HV: over warning >=3%, trip >=6%; under warning >=5%, trip >=9% in magnitude.
LV: over/under warning >=3%, trip >=6% in magnitude. Thresholds are inclusive.
Trips suppress the warning for that direction. Opposite-direction breaches remain
independent. Details preserve per-channel voltages, signed deviations, direction,
side and exact trigger receipts. Existing severity WARNING/CRITICAL is retained.
All actual alerts use TRANSFORMER_MAINTENANCE and the common alert history.
There is no timer, breaker control, averaging, or forecast for these voltage rules.
Current and voltage rules evaluate each reading with millisecond timestamps;
unchanged breaches keep evaluations but do not repeatedly emit alerts. Recovery
allows a new breach alert; persisted prior readings restore this state after restart.
A 20 ms cadence needs 50 RMS updates/second; sustained database throughput has
not been benchmarked. Existing hourly demo data and old stored history are unchanged.
