# Digital Transformer Twin backend

A Python 3.11+ FastAPI foundation using SQLAlchemy 2.x, PostgreSQL, psycopg 3,
Alembic, and Pydantic v2. Dashboard and future AI agent clients use REST APIs;
they must not access PostgreSQL directly.

Reading ingestion/storage, database-backed history, alert acknowledgement/resolution,
CSV preview, and in-memory simulation controls are implemented. The approved per-reading rules are **3-Phase Current Instrument Error** and
**3-Phase Voltage Instrument Error**, producing configurable SYSTEM_MAINTENANCE
alerts through the same pipeline and common history. No other physical rules, synthetic sensor values,
health scores, AI agent, or maintenance scheduling are implemented in the backend;
the 3D frontend is described below.

## 3D digital twin (frontend)

`frontend/` holds the 3D transformer page. The API serves it at
[http://127.0.0.1:8000/twin/](http://127.0.0.1:8000/twin/), so no second server is needed.

- The model is a 500 kVA oil-immersed distribution transformer (`frontend/transformer.glb`)
  with internals, explode/assemble, cutaway and X-ray views, and 13 IoT sensor markers.
- The page simulates readings (Auto demo, Normal, Overload, Oil leak, Voltage sag, CT drift, VT fault).
  Every 2 s it posts the latest reading to `POST /api/v1/data/reading` through the normal
  ProcessingService pipeline, so readings and alerts land in PostgreSQL like real data.
- The API's rule results colour the matching modules and sensors, marked "checked by API":
  `3_phase_current_instrument_error` (module 1), `3_phase_voltage_instrument_error` (module 2) and
  `oil_level_predictive_maintenance` (module 5). The other four modules (loading, voltage deviation,
  top-oil and winding temperature) still run in `frontend/engine.js` until rules exist for them here.
- Each phase is sent as a percentage of its own reference meter (`rated_current` / `reference_voltage`
  = 100), so the single-reference formula gives the per-phase error. Raw readings (IA, VA_REF, OIL,
  ...) are stored alongside in the same JSON.
- Readings are time-stamped on a demo clock where one simulated second is one minute, so the oil
  trend develops over hours.
- three.js 0.147.0 is vendored in `frontend/vendor/three/` (MIT), so the page works offline.

The production `config/thresholds.yaml` leaves the oil thresholds null, so the oil rule is SKIPPED.
For the demo, point `THRESHOLDS_FILE` at `config/thresholds.demo.yaml` (minimum 20 %, warning 35 %,
lead time 0.5 h, from the twin's IoT design, not a nameplate). Rules 1 and 2 are unchanged there.

```powershell
# in .env (or the environment)
THRESHOLDS_FILE=config/thresholds.demo.yaml
.\.venv\Scripts\python.exe -m alembic upgrade head
.\.venv\Scripts\python.exe -m uvicorn app.main:app --reload
# open http://127.0.0.1:8000/twin/
```

Without `DATABASE_URL` the page still runs and says "database not configured, page rules only".
Opened from another server, add `?dtt=http://127.0.0.1:8000` to the page URL (DEBUG=true allows the CORS).
`tests/test_frontend.py` checks the page, model and threshold switch.

## Architecture

```text
Real API data / future synthetic data
                 |
       ProcessingService
       receive -> validate
                 |
        Repository -> PostgreSQL raw SensorReading
                 |
     [future normalization / day completeness policy]
                 |
     [future Daily Aggregation -> DailyMetric]
                 |
     Formula Registry (PER_READING rule; DAILY reserved)
                 |
     Configured Threshold / Alert Engine (current rule)
                 |
        PostgreSQL Alert History
                 |
             FastAPI
            /       \
       Dashboard    Agent
```

API -> Service -> Repository -> SQLAlchemy -> PostgreSQL.
Both real and future synthetic readings enter ProcessingService. No source may
bypass the shared processing/formula/alert pipeline. Raw JSON payloads are
preserved unchanged; no units or sensor names are inferred. Formula inputs are
stored with future alerts to preserve provenance.

## Windows setup

Run in PowerShell:

```powershell
Set-Location C:\Users\HP\Digital_Transformer_Twin
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
Copy-Item .env.example .env
```

Python must be 3.11 or newer. If `python` is unavailable, use `py -3.12`
when the Python launcher is installed. On this machine the bundled Python used
to create and verify the project is:

```powershell
& 'C:\Users\HP\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' -m venv .venv
```

The project virtual environment is already created. Activation is optional;
you can always run `.\.venv\Scripts\python.exe` explicitly.

Open `.env` and set your own database credentials:

```dotenv
APP_NAME=Digital Transformer Twin API
APP_VERSION=0.1.0
API_V1_PREFIX=/api/v1
DEBUG=true
DATABASE_URL=postgresql+psycopg://postgres:<PASSWORD>@localhost:5432/digital_transformer
```

Replace `<PASSWORD>` with your actual PostgreSQL password. URL-encode special
characters in the password. Never commit `.env` or real credentials.
Configuration is loaded from the project-root `.env` and environment variables
(environment variables take precedence). DATABASE_URL only accepts
`postgresql+psycopg`; SQLite is not supported.

### PostgreSQL

Install/start PostgreSQL and create the database using your PostgreSQL role.
The installed PostgreSQL 18 client on this machine can be invoked as follows:

```powershell
& 'C:\Program Files\PostgreSQL\18\bin\psql.exe' -h localhost -U postgres -d postgres -W -c 'CREATE DATABASE digital_transformer;'
```

The `-W` option prompts for your password. If the database already exists,
skip creation. If using another PostgreSQL version, adjust the executable path
or use `psql` from PATH. The application does not create databases or tables
at startup.

### Alembic migrations

Once DATABASE_URL points at your PostgreSQL database, review the supplied migrations and apply them:

```powershell
.\.venv\Scripts\python.exe -m alembic upgrade head
.\.venv\Scripts\python.exe -m alembic current
.\.venv\Scripts\python.exe -m alembic check
```

The repository now includes 0001 (the original three-table baseline) followed
by 0002 (maintenance_type and details additions). New databases can upgrade
straight to head. If tables were created manually before migrations existed,
compare their complete schema with 0001 before considering `alembic stamp 0001`;
do not blindly stamp an unverified database. Existing versioned databases follow
the normal upgrade chain. Future changes can use:

```powershell
alembic revision --autogenerate -m "describe the schema change"
alembic upgrade head
```

Alembic imports all models and uses the application's DATABASE_URL. No real
password is stored in alembic.ini. This implementation was tested using a
disposable PostgreSQL cluster; the user's configured database was not modified.

### Run

```powershell
.\.venv\Scripts\python.exe -m uvicorn app.main:app --reload
# Or, after activation:
uvicorn app.main:app --reload
```

- Swagger UI: [http://127.0.0.1:8000/docs](http://127.0.0.1:8000/docs)
- ReDoc: [http://127.0.0.1:8000/redoc](http://127.0.0.1:8000/redoc)
- OpenAPI: [http://127.0.0.1:8000/openapi.json](http://127.0.0.1:8000/openapi.json)

Root, health, CSV preview, and simulation work without DATABASE_URL.
Database-backed endpoints need credentials and migrated tables. Missing
configuration/connection failures return sanitized HTTP 503 responses.
Other database/unexpected failures return sanitized 500 responses. Debug
tracebacks, connection strings, and SQL payloads are not returned to clients.
DEBUG enables development-only wildcard CORS; production origins must be
explicitly configured before deployment.

## API contract

Default prefix: `/api/v1`. List endpoints return JSON arrays; history returns
a structured object. Lists are newest-first with an ID tie-breaker, except
the single-transformer routes, which derive the source ID from stored data.

| Method | Path | Behavior / query parameters |
| --- | --- | --- |
| GET | / | Service status |
| GET | /api/v1/health | Application liveness, independent of DB |
| GET | /api/v1/health/db | PostgreSQL SELECT 1 connectivity check |
| POST | /api/v1/data/reading | Store SensorReadingCreate; 201 |
| POST | /api/v1/data/batch | JSON array of SensorReadingCreate; atomic bulk insert; 201 |
| POST | /api/v1/data/upload | Multipart `file`; CSV preview only |
| GET | /api/v1/alerts | maintenance_type, parameter, severity, start_date, end_date, acknowledged, resolved, limit, offset |
| GET | /api/v1/alerts/history | Same filters including maintenance_type; paginated items, limit, offset, next_offset; includes resolved alerts by default |
| GET | /api/v1/alerts/{alert_id} | Stored alert; 404 if absent |
| PATCH | /api/v1/alerts/{alert_id}/acknowledge | Set acknowledged=true |
| PATCH | /api/v1/alerts/{alert_id}/resolve | Set resolved=true and UTC resolved_at; repeated calls preserve original resolution time |
| GET | /api/v1/transformer/latest | latest_reading and latest_daily_metric (nullable); sole source ID derived automatically |
| GET | /api/v1/transformer/history | start_date, end_date, limit, offset; recent_readings, daily_metrics, alerts |
| POST | /api/v1/simulation/start | Optional JSON `{"speed": 1.0}` |
| POST | /api/v1/simulation/pause | Pause if RUNNING; otherwise preserve state |
| POST | /api/v1/simulation/reset | STOPPED, current_step=0, speed=1.0 |
| POST | /api/v1/simulation/step | Increment counter only |
| GET | /api/v1/simulation/status | status, current_step, speed |
| GET | /docs | Swagger UI |
| GET | /redoc | ReDoc |
| GET | /openapi.json | Machine-readable API contract |

Pagination defaults: limit=50, offset=0; limit range 1..1000. History applies
limit and offset separately to each collection. These are transport limits, not domain
thresholds. Batch size is 1..10000. Batch validation completes before any
insert; readings and generated alerts commit atomically and roll back on failure.
Batch responses add evaluations in input-row order.

Date filters are inclusive datetime boundaries. Daily metrics use the UTC
calendar dates covered by those boundaries, including the entire boundary days.
Prefer ISO 8601 timestamps with `Z` or a UTC offset. Incoming naive timestamps
are explicitly interpreted as UTC for compatibility with the example request.
Stored timestamps are timezone-aware UTC; daily date policy/completeness rules
remain to be supplied.

Example reading:

```json
{
  "transformer_id": "T01",
  "timestamp": "2026-10-09T10:00:00Z",
  "data": {"any_parameter": 123.4}
}
```

The response contains `id`, `transformer_id`, `timestamp`, `raw_data`, and
`created_at`, and immediate `evaluations`. Raw values remain unchanged.
Evaluation receipts are not separate persisted sensor columns: passing/invalid
results appear in the ingestion response, while every flagged result is durably
stored as an alert. Later sensor-history reads do not reconstruct those receipts.

CSV preview accepts a nonempty UTF-8 (optional BOM), comma-separated `.csv`
file with a nonblank unique header and consistent data rows. One-column files
are supported. A 2 MiB upload limit bounds preview parsing. The response
reports column names and up to five preview rows counted; it contains no sensor
values and stores nothing. No mapping or processing is performed. Malformed
files return 400, oversized files 413, and missing files 422.

Simulation state is in-memory, per app/process, and is lost on restart/reload.
RUNNING changes control state only; no background clock executes. Step works in
any state and changes only current_step. Speed is retained for future scheduler
integration. Use a single worker for these skeleton controls.

## Database structure

- SensorReading: id, transformer_id, timestamp, raw_data JSONB, created_at.
- DailyMetric: id, transformer_id, date, aggregated_data JSONB,
  formula_results JSONB, created_at. One metric per transformer/UTC date.
- Alert: id, transformer_id, timestamp, parameter, formula_name, formula_value,
  threshold, comparison_operator, severity, message, formula_inputs JSONB,
  acknowledged, resolved, resolved_at, created_at, maintenance_type, details JSONB.

Transformer/timestamp/date/severity/resolved indexes support the history and
alert APIs. No Transformer table or specific sensor columns are assumed.
Ingestion generates per-reading alerts for the current and voltage instrument rules.
DailyMetric generation remains a future extension point. An empty formula_results dictionary means no results;
it is never a synthetic calculation.

## Extension points to edit later

| Component | Files |
| --- | --- |
| Synthetic readings / scenarios | app/simulation/generator.py, scenarios.py, simulator.py |
| Shared processing orchestration | app/services/processing_service.py |
| Approved formulas | new modules under app/formulas/, base.py, registry.py |
| Approved thresholds | config/thresholds.yaml |
| Daily aggregation / completeness | app/processing/aggregation.py |
| Threshold comparisons / alert construction | app/alerts/engine.py |
| Alert persistence and queries | app/database/repository.py, app/services/alert_service.py |
| Dataset column mapping | config/column_mapping.yaml, app/processing/ingestion.py |
| Validation and normalization | app/processing/validation.py, app/services/processing_service.py |
| Settings | app/config/settings.py, .env |

Placeholder methods deliberately raise NotImplementedError when called.
No API calls the unfinished daily aggregation, normalization, or synthetic stages.
The registry contains only 3_phase_current_instrument_error and
3_phase_voltage_instrument_error. Threshold configuration contains their supplied
> 1.0 percent rules; generic comparisons also support
>=, <, <= for future supplied rules.

## Tests and verification

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m pytest -m "not integration" -q
.\.venv\Scripts\python.exe scripts/verify_backend.py
.\.venv\Scripts\python.exe -m pip check
# After activation:
pytest
```

Unit tests use FastAPI dependency overrides and Session mocks, never SQLite.
They cover schema/UTC/raw-data preservation, bulk inserts, query filters,
alert acknowledgement/resolution, missing resources, error sanitization,
upload validation, simulation controls, the approved formula registration and threshold configuration,
PostgreSQL DDL compilation, Alembic setup, and explicit placeholders.

The PostgreSQL integration test is skipped unless TEST_DATABASE_URL is set.
It creates a uniquely named temporary schema with the test role, exercises
storage, then drops only that schema. Use an isolated test database and a role
allowed to create/drop schemas:

```powershell
# Set your own credentials; do not copy placeholder credentials literally.
$env:TEST_DATABASE_URL = 'postgresql+psycopg://postgres:<PASSWORD>@localhost:5432/digital_transformer_test'
.\.venv\Scripts\python.exe -m pytest -m integration -q
Remove-Item Env:TEST_DATABASE_URL
```

The verification script imports every app module, builds OpenAPI, compiles
PostgreSQL DDL, checks the Alembic directory, and prints the endpoint list.
If DATABASE_URL is configured it additionally performs SELECT 1. It neither
creates tables nor applies migrations.

## Directory tree

```text
Digital_Transformer_Twin/
â”œâ”€â”€ app/
â”‚   â”œâ”€â”€ __init__.py
â”‚   â”œâ”€â”€ main.py
â”‚   â”œâ”€â”€ api/
â”‚   â”‚   â”œâ”€â”€ __init__.py
â”‚   â”‚   â”œâ”€â”€ health.py
â”‚   â”‚   â”œâ”€â”€ transformers.py
â”‚   â”‚   â”œâ”€â”€ alerts.py
â”‚   â”‚   â”œâ”€â”€ simulation.py
â”‚   â”‚   â””â”€â”€ data.py
â”‚   â”œâ”€â”€ simulation/
â”‚   â”‚   â”œâ”€â”€ __init__.py
â”‚   â”‚   â”œâ”€â”€ generator.py
â”‚   â”‚   â”œâ”€â”€ scenarios.py
â”‚   â”‚   â””â”€â”€ simulator.py
â”‚   â”œâ”€â”€ processing/
â”‚   â”‚   â”œâ”€â”€ __init__.py
â”‚   â”‚   â”œâ”€â”€ ingestion.py
â”‚   â”‚   â”œâ”€â”€ validation.py
â”‚   â”‚   â””â”€â”€ aggregation.py
â”‚   â”œâ”€â”€ formulas/
â”‚   â”‚   â”œâ”€â”€ __init__.py
â”‚   â”‚   â”œâ”€â”€ base.py
â”‚   â”‚   â””â”€â”€ registry.py
â”‚   â”œâ”€â”€ alerts/
â”‚   â”‚   â”œâ”€â”€ __init__.py
â”‚   â”‚   â””â”€â”€ engine.py
â”‚   â”œâ”€â”€ models/
â”‚   â”‚   â”œâ”€â”€ __init__.py
â”‚   â”‚   â”œâ”€â”€ base.py
â”‚   â”‚   â”œâ”€â”€ sensor.py
â”‚   â”‚   â”œâ”€â”€ daily.py
â”‚   â”‚   â””â”€â”€ alert.py
â”‚   â”œâ”€â”€ schemas/
â”‚   â”‚   â”œâ”€â”€ __init__.py
â”‚   â”‚   â”œâ”€â”€ sensor.py
â”‚   â”‚   â”œâ”€â”€ alert.py
â”‚   â”‚   â”œâ”€â”€ transformer.py
â”‚   â”‚   â””â”€â”€ simulation.py
â”‚   â”œâ”€â”€ services/
â”‚   â”‚   â”œâ”€â”€ __init__.py
â”‚   â”‚   â”œâ”€â”€ processing_service.py
â”‚   â”‚   â”œâ”€â”€ alert_service.py
â”‚   â”‚   â”œâ”€â”€ transformer_service.py
â”‚   â”‚   â””â”€â”€ errors.py
â”‚   â”œâ”€â”€ database/
â”‚   â”‚   â”œâ”€â”€ __init__.py
â”‚   â”‚   â”œâ”€â”€ db.py
â”‚   â”‚   â””â”€â”€ repository.py
â”‚   â””â”€â”€ config/
â”‚       â”œâ”€â”€ __init__.py
â”‚       â””â”€â”€ settings.py
â”œâ”€â”€ alembic/
â”‚   â”œâ”€â”€ env.py
â”‚   â”œâ”€â”€ script.py.mako
â”‚   â””â”€â”€ versions/.gitkeep
â”œâ”€â”€ config/
â”‚   â”œâ”€â”€ thresholds.yaml
â”‚   â””â”€â”€ column_mapping.yaml
â”œâ”€â”€ data/
â”‚   â”œâ”€â”€ synthetic/.gitkeep
â”‚   â””â”€â”€ incoming/.gitkeep
â”œâ”€â”€ scripts/
â”‚   â””â”€â”€ verify_backend.py
â”œâ”€â”€ tests/
â”‚   â”œâ”€â”€ __init__.py
â”‚   â”œâ”€â”€ conftest.py
â”‚   â”œâ”€â”€ test_health.py
â”‚   â”œâ”€â”€ test_alerts.py
â”‚   â”œâ”€â”€ test_data.py
â”‚   â”œâ”€â”€ test_simulation.py
â”‚   â”œâ”€â”€ test_formulas.py
â”‚   â”œâ”€â”€ test_aggregation.py
â”‚   â”œâ”€â”€ test_transformers.py
â”‚   â”œâ”€â”€ test_database.py
â”‚   â””â”€â”€ test_postgres_integration.py
â”œâ”€â”€ alembic.ini
â”œâ”€â”€ requirements.txt
â”œâ”€â”€ pytest.ini
â”œâ”€â”€ .env.example
â”œâ”€â”€ .gitignore
â””â”€â”€ README.md
```

## References

The foundation follows [SQLAlchemy's PostgreSQL/psycopg documentation](https://docs.sqlalchemy.org/en/20/dialects/postgresql.html#module-sqlalchemy.dialects.postgresql.psycopg)
and [FastAPI's testing documentation](https://fastapi.tiangolo.com/tutorial/testing/).

## Verification on 2026-10-09

- Python 3.12.14 virtual environment created; dependencies installed.
- 116 tests passed with PostgreSQL, including migrations and API persistence.
- The 33 new rule-specific unit tests passed independently.
- All 43 app modules imported; application/OpenAPI creation passed.
- PostgreSQL DDL compiled for all 3 models; configuration/structure checked.
- Alembic heads/configuration and revision-template generation passed.
  The template test uses a temporary directory; two reviewed project migrations are now included.
- Python syntax compilation and pip dependency checks passed.
- The installed Starlette TestClient emitted one deprecation warning about
  httpx; the requested httpx-based tests passed.
- PostgreSQL 18 service was running, but no DATABASE_URL or .env was configured.
  Production connectivity and upgrade application await user credentials.
  Migration application, storage, and retrieval were verified separately against
  an isolated PostgreSQL cluster; alembic check reported no schema differences.

## Single-transformer agent integration

This project serves one transformer. Transformer-list and identity endpoints
are removed. The latest/history routes use `/api/v1/transformer` and require
no transformer ID in the URL. The sole ID is derived from stored readings,
daily metrics, and alerts, preserving the original ingestion identity.
Ingestion still includes `transformer_id` for provenance: use the same source
ID for every reading/batch. No real identifier is guessed or hard-coded.
Before any data exists, latest/history return null identity and empty data.
Multiple stored IDs cause a clear 400 on transformer latest/history rather
than silently combining separate assets.

The friend building the agent needs only these HTTP endpoints:

| Agent operation | Request |
| --- | --- |
| Current active alerts | GET /api/v1/alerts?resolved=false |
| Unacknowledged active alerts | GET /api/v1/alerts?resolved=false&acknowledged=false |
| Maintenance-alert history | GET /api/v1/alerts/history?limit=50&offset=0 |
| Individual alert evidence | GET /api/v1/alerts/{alert_id} |
| Latest sensor/daily data | GET /api/v1/transformer/latest |
| Combined historical context | GET /api/v1/transformer/history?limit=50&offset=0 |
| Acknowledge an alert, when authorized by the user | PATCH /api/v1/alerts/{alert_id}/acknowledge |
| Resolve an alert, when authorized by the user | PATCH /api/v1/alerts/{alert_id}/resolve |

Alert history preserves active, acknowledged, and resolved records; filters are
optional. Acknowledge/resolve actions update status without deleting the alert.
Each alert retains its timestamp, parameter/formula, calculated value,
threshold/operator, severity/message, formula_inputs, status, and resolved_at.
This is a history of maintenance alerts; maintenance-work logs, repair actions,
and inspections have no storage model yet and can be added later.

The alert-history envelope is:

```json
{"items": [], "limit": 50, "offset": 0, "next_offset": null}
```

For nonempty pages, `items` contains AlertResponse records. Follow `next_offset`
until it is null. Ordering is newest timestamp first, then descending ID.
Date filters use UTC and inclusive start/end boundaries. Active-alert queries
return arrays; combined history contains `transformer_id`, `recent_readings`,
`daily_metrics`, and `alerts`, with limit/offset applied separately to each list.

Minimal client example (HTTP access only, no agent implementation):

```python
import httpx

with httpx.Client(base_url="http://127.0.0.1:8000", timeout=10) as backend:
    response = backend.get("/api/v1/alerts", params={"resolved": "false"})
    response.raise_for_status()
    active_alerts = response.json()

    response = backend.get("/api/v1/transformer/history", params={"limit": 50})
    response.raise_for_status()
    context = response.json()

    offset = 0
    alert_history = []
    while True:
        response = backend.get("/api/v1/alerts/history", params={"limit": 50, "offset": offset})
        response.raise_for_status()
        page = response.json()
        alert_history.extend(page["items"])
        if page["next_offset"] is None:
            break
        offset = page["next_offset"]
```

Share the base URL and `/openapi.json` with the agent developer; `/docs` provides
interactive schemas. A separately hosted agent needs a reachable backend URL;
127.0.0.1 refers to the agent's own machine. Configure PostgreSQL and apply the
initial migration before data/history requests work. A 503 means database
configuration/connectivity is unavailable, not an empty alert history.
The agent itself, tool calling, reasoning, and other application logic remain
for the agent developer to implement.

The updated server was restarted and its live OpenAPI contract checked. Removed transformer-selection routes returned 404. Agent-facing database routes were registered and returned sanitized 503 responses because DATABASE_URL remains unconfigured.

## First rule: 3-Phase Current Instrument Error

Identifier: `3_phase_current_instrument_error`; category: `current_measurement`;
evaluation mode: `PER_READING`. The original registry is used; the Formula
abstraction now returns a structured FormulaResult so diagnostics are preserved.
EvaluationMode also supports DAILY without enabling unknown aggregation logic.

Required raw_data/input features (all phases use the same rated/reference):

- rated_current
- measured_current_r
- measured_current_y
- measured_current_b

For each Indian electrical phase R, Y, B:
`error_percent = abs(measured_current - rated_current) / abs(rated_current) * 100`.
The result value is the maximum phase error. Details retain phase_errors keyed by
R/Y/B and max_deviation_phase. Ties select the first phase in R, Y, B order.
No values are rounded before threshold comparison.

`config/thresholds.yaml` configures operator `>`, threshold `1.0`, unit `percent`,
and maintenance_type `SYSTEM_MAINTENANCE`. Severity `WARNING` is a configurable
operational label, independent of the maintenance category. The formula itself
contains no maintenance decision or threshold. There is no maintenance schedule.

| Inputs (rated=100) | Maximum error | Outcome |
| --- | --- | --- |
| R=100.4, Y=99.7, B=100.8 | approximately 0.8% | PASS; no alert |
| R=101, Y=100, B=100 | exactly 1.0% | PASS; no alert |
| R=100.4, Y=99.7, B=101.2 | approximately 1.2%, B | FLAGGED; SYSTEM_MAINTENANCE |
| Required key missing | unavailable | SKIPPED with missing_features; no alert |
| Reference zero / invalid numeric value | invalid | INVALID with reason; no alert |

Input values must be finite numbers, not booleans or numeric strings. Missing or
invalid current-test data still allows the valid generic raw reading to be
stored. JSON nonfinite numbers are rejected at the transport-schema boundary.
Negative differences and nonzero negative references use absolute magnitudes
exactly as specified. No numeric sign/rating assumptions are introduced.

Each FLAGGED reading creates its own alert in the same transaction as its raw
reading. Passing results do not create alerts or auto-resolve prior alerts.
Legacy alerts retain null maintenance_type until an explicit classification is
supplied; the migration never invents a category for historical rows.

Alert fields add only nullable maintenance_type (constrained to the two allowed
categories, indexed) and nonnull generic JSONB details (default {}). Details
contain phase_errors, max_deviation_phase, test_name, and threshold_unit.
Formula_inputs separately preserves the exact four source values. SensorReading
has no new dedicated current columns.

Example request through the existing ingestion API:

```json
{
  "transformer_id": "YOUR_EXISTING_SOURCE_ID",
  "timestamp": "2026-10-09T10:00:00Z",
  "data": {
    "rated_current": 100,
    "measured_current_r": 100.4,
    "measured_current_y": 99.7,
    "measured_current_b": 101.2
  }
}
```

POST this to `/api/v1/data/reading`, or use an array for `/api/v1/data/batch`.
The additive `evaluations` response reports PASS/FLAGGED/SKIPPED/INVALID,
unrounded value, diagnostics, reason, and missing_features for the registered rule.
Batch evaluations follow input order. Future generated readings must call this
same ProcessingService; the synthetic generator remains unimplemented.

Agent/dashboard retrieval uses the unchanged REST contract plus category filters:

```text
GET /api/v1/alerts?maintenance_type=SYSTEM_MAINTENANCE
GET /api/v1/alerts/history?maintenance_type=SYSTEM_MAINTENANCE
GET /api/v1/alerts/{alert_id}
GET /api/v1/transformer/history
```

All these expose maintenance_type, formula_name/value, configured threshold and
operator, message, source inputs, diagnostic details, timestamps, and lifecycle
status. No transformer-list or selection endpoints are introduced.

Actual API evaluation receipts and the stored alert from the disposable
PostgreSQL verification are saved in `docs/current_instrument_error_examples.json`.
That file is labeled as test evidence, not a production reading or synthetic
transformer dataset. Reproduce verification with:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_current_instrument_error.py -q
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -u scripts/verify_rule_postgres.py
```

The last command starts a uniquely named temporary loopback-only PostgreSQL
cluster using installed PostgreSQL 18 binaries, applies migrations, runs all
unit/integration tests, checks metadata consistency, runs the supplied examples,
and stops/removes its own cluster. Set POSTGRES_BIN for another installed binary
path. It never uses the user's production DATABASE_URL. The internal
rule_examples.py helper refuses direct execution outside this verification flow.
The existing Starlette httpx deprecation warning remains unrelated to this rule.

## Local rule implementation file inventory

Modified existing files:

```text
README.md
app/alerts/engine.py
app/api/alerts.py
app/api/data.py
app/database/db.py
app/database/repository.py
app/formulas/base.py
app/formulas/registry.py
app/models/alert.py
app/schemas/alert.py
app/schemas/sensor.py
app/services/processing_service.py
config/thresholds.yaml
tests/test_alerts.py
tests/test_data.py
tests/test_database.py
tests/test_formulas.py
tests/test_postgres_integration.py
```

Created files:

```text
alembic/versions/0001_initial_tables.py
alembic/versions/0002_alert_maintenance_details.py
app/alerts/configuration.py
app/alerts/types.py
app/formulas/current_instrument_error.py
docs/current_instrument_error_examples.json
scripts/rule_examples.py
scripts/verify_rule_postgres.py
tests/test_current_instrument_error.py
tests/test_migrations_integration.py
```

No commit or push was made for this rule implementation.

## Rule 2: 3-Phase Voltage Instrument Error

Rule ID: `3_phase_voltage_instrument_error`; display name:
`3-Phase Voltage Instrument Error`; parameter: `voltage_measurement`.
Mode: `PER_READING`; maintenance category: `SYSTEM_MAINTENANCE`.

The exact required features are:

```text
reference_voltage
measured_voltage_r
measured_voltage_y
measured_voltage_b
```

One common reference_voltage is used for all R/Y/B phases. Each error is
`abs(measured_voltage - reference_voltage) / abs(reference_voltage) * 100`.
The maximum is result.value; result.details preserves phase_errors and
max_deviation_phase. Zero/nonnumeric/nonfinite references are INVALID, while
missing required features are SKIPPED. Neither outcome generates an alert.
Missing voltage data does not stop the current rule, and vice versa.

The small instrument_error.py helper shares Rule 1's existing validation/math,
including its unchanged R/Y/B tie order and absolute denominator. Both formula
classes remain independently registered through the existing registry:

```python
formula_registry.register(CurrentInstrumentError())
formula_registry.register(VoltageInstrumentError())
```

Configuration added to the same config/thresholds.yaml:

```yaml
3_phase_voltage_instrument_error:
  operator: ">"
  threshold: 1.0
  unit: percent
  maintenance_type: SYSTEM_MAINTENANCE
  severity: WARNING
  message: "3-phase voltage instrument error exceeded the configured allowed deviation."
```

Formula code contains no threshold decision. Existing ProcessingService and
AlertEngine automatically discover/evaluate this PER_READING rule, store its
alert inputs and JSONB diagnostics, and commit the reading and alerts atomically.
No database models, migrations, API routes, history tables, or ingestion schemas
were changed for Rule 2. DAILY support remains available for future supplied
rules; this rule never uses daily averaging.

| Voltage inputs | Maximum / phase | Outcome |
| --- | --- | --- |
| Reference=100, R=100.4, Y=99.7, B=100.8 | approximately 0.8%, B | PASS |
| Reference=100, R=101, Y=100, B=100 | exactly 1.0%, R | PASS |
| Reference=100, R=100.4, Y=99.7, B=101.2 | approximately 1.2%, B | FLAGGED / SYSTEM_MAINTENANCE |
| Reference=200, R=201, Y=198, B=204 | R=0.5%, Y=1.0%, B=2.0%; max B | FLAGGED |

The comparison is strictly > 1.0; 1.0000001% also triggers. Stored values are
not rounded. Fixed supplied examples are saved as PostgreSQL test evidence in
`docs/voltage_instrument_error_examples.json`, including the actual stored alert
and a shared-history response containing both Rule 1 and Rule 2 failures.

The existing APIs retrieve the records without voltage-specific endpoints:

```text
GET /api/v1/alerts?maintenance_type=SYSTEM_MAINTENANCE
GET /api/v1/alerts?maintenance_type=SYSTEM_MAINTENANCE&parameter=voltage_measurement
GET /api/v1/alerts/history?maintenance_type=SYSTEM_MAINTENANCE
GET /api/v1/alerts/{alert_id}
GET /api/v1/transformer/history
```

Verification on 2026-10-09: 156 tests passed with real disposable PostgreSQL,
including all Rule 1 tests, 34 new voltage unit tests and 6 voltage PostgreSQL
integration cases. The normal suite without TEST_DATABASE_URL reports 143 passed
and 13 integration cases skipped. Alembic check reports no model/schema drift.
The existing Starlette httpx deprecation warning remains unrelated to the rules.

Created for Rule 2:

```text
app/formulas/instrument_error.py
app/formulas/voltage_instrument_error.py
tests/test_voltage_instrument_error.py
tests/test_voltage_postgres_integration.py
docs/voltage_instrument_error_examples.json
```

Modified for Rule 2:

```text
app/formulas/current_instrument_error.py
app/formulas/registry.py
config/thresholds.yaml
tests/test_formulas.py
scripts/rule_examples.py
README.md
```

No nominal-voltage/transformer-abnormality rule, separate phase reference
voltages, extra maintenance category, synthetic-data generator, scheduling, or
multiple-transformer interface was introduced. All changes remain local and
uncommitted; nothing was pushed.


## Rule 3: Oil-Level IoT Predictive Maintenance

Registered `oil_level_predictive_maintenance` uses the existing formula registry,
ProcessingService, AlertEngine and common `alerts` table. Its only required raw
feature is `oil_level`. SensorReading supplies the timestamp. Mode: PER_READING.
Maintenance category: TRANSFORMER_MAINTENANCE. Rules 1 and 2 remain
SYSTEM_MAINTENANCE with strict `> 1.0` comparisons and R/Y/B naming.

Production YAML leaves minimum_oil_level, warning_oil_level and
maintenance_lead_time null. Supply all three real values to enable decisions.
Until then evaluations are SKIPPED with a configuration reason; readings remain
stored. No production threshold values were invented. Level units match the
source sensor; maintenance_lead_time and estimated_t_min use hours. Both oil
level comparisons and lead-time comparison include equality (`<=`). Every
crossed threshold produces a record, including warning alongside critical.
MAINTENANCE_REQUIRED accompanies critical/predicted outputs, not a fourth alert.

Repository.previous_reading_with_feature retrieves the latest earlier-ingested
SensorReading containing the oil_level JSONB key at/before the current timestamp,
excluding the current record and future records. ProcessingService supplies this
via FormulaContext; calculations never query the database. History-aware batches
store/evaluate sequentially within one transaction to prevent future lookahead.
Other batches retain bulk storage. Equal timestamps make trend unavailable while
static threshold checks still run. No oil history table or sensor column was added.

Calculation: rate = (current - previous) / elapsed hours;
T_min = (current - minimum) / abs(rate). Direction remains declining, stable or
increasing. Zero rate has no finite prediction. Signed T_min is retained when
calculable. Future breach interpretation uses declining oil with T_min >= 0;
increasing oil does not predict a future fall, and below-minimum oil uses static
alerts. This interpretation is documented explicitly, with no extra numeric
threshold or formula. Missing/invalid data never creates a maintenance failure.

Test-only thresholds: minimum 20, warning 40, lead time 5 hours.
These numbers are absent from production YAML.

| Scenario | Previous/current | Hours | Rate/hour | T_min hours | Output |
| --- | --- | --- | --- | --- | --- |
| Normal | 100 / 90 | 10 | -1 | 70 | NORMAL |
| Warning, first reading | none / 35 | unavailable | unavailable | unavailable | LOW_OIL_WARNING |
| Critical, first reading | none / 20 | unavailable | unavailable | unavailable | LOW_OIL_WARNING + OIL_LEVEL_CRITICAL + MAINTENANCE_REQUIRED |
| Predicted breach | 80 / 60 | 1 | -20 | 2 | PREDICTED_MINIMUM_LEVEL_BREACH + MAINTENANCE_REQUIRED |

Actual PostgreSQL/API evidence is saved in docs/oil_level_maintenance_examples.json.
It includes all output examples, stored alerts and a history containing all three
rules. It comes from a disposable database, not live production records.
Generic details preserve trigger_condition, current/minimum/warning levels,
maintenance lead time, previous reading, rate, direction, elapsed hours, estimated
T_min, units, outputs and maintenance_required. formula_inputs separately stores
current oil_level, timestamp and previous source reading. Acknowledgement and
resolution remain the existing shared fields.

Dashboard and agent retrieval:

```text
GET /api/v1/alerts?maintenance_type=TRANSFORMER_MAINTENANCE
GET /api/v1/alerts/history?maintenance_type=TRANSFORMER_MAINTENANCE
GET /api/v1/alerts/{alert_id}
GET /api/v1/transformer/history
```

Created: app/formulas/context.py, app/formulas/oil_level.py,
tests/test_oil_level.py, tests/test_oil_postgres_integration.py,
scripts/oil_rule_examples.py, docs/oil_level_maintenance_examples.json.

Modified: app/formulas/base.py, app/formulas/registry.py,
app/alerts/configuration.py, app/alerts/engine.py, app/database/repository.py,
app/services/processing_service.py, config/thresholds.yaml, tests/test_formulas.py,
tests/test_voltage_instrument_error.py, scripts/verify_rule_postgres.py, README.md.
Verification refreshes docs/voltage_instrument_error_examples.json.

No Rule 3 schema change or migration is needed. One transformer, shared history,
and DAILY extension points are preserved. No acceleration, temperature,
nominal-voltage, health-score, synthetic-data or scheduling feature was added.
Nothing was committed or pushed.

Final Rule 3 verification: 184 tests passed against disposable PostgreSQL,
including all prior tests, 18 oil unit cases and 10 oil integration cases.
The focused three-rule unit suites passed 85 tests. Alembic check found no
schema drift; git diff --check passed. One existing Starlette deprecation
warning remains. The temporary test database was stopped and removed.
