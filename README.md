# Digital Transformer Twin backend

A Python 3.11+ FastAPI foundation using SQLAlchemy 2.x, PostgreSQL, psycopg 3,
Alembic, and Pydantic v2. Dashboard and future AI agent clients use REST APIs;
they must not access PostgreSQL directly.

Only basic reading ingestion/storage, database-backed history queries, alert
acknowledge/resolve actions, CSV preview, and in-memory simulation controls are
implemented. There are no transformer formulas, thresholds, assumed parameter
names, synthetic sensor values, health scores, frontend, or AI agent.

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
     [future Formula Registry -> real formula results]
                 |
     [future configured Threshold / Alert Engine]
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

Once DATABASE_URL points at your PostgreSQL database:

```powershell
.\.venv\Scripts\python.exe -m alembic revision --autogenerate -m "initial tables"
# Review the generated migration under alembic/versions/ before applying it.
.\.venv\Scripts\python.exe -m alembic upgrade head
.\.venv\Scripts\python.exe -m alembic current
```

Equivalent activated-environment commands:

```powershell
alembic revision --autogenerate -m "initial tables"
alembic upgrade head
```

Alembic imports all three models and uses the same settings as the application.
No database URL/password is stored in alembic.ini. No initial revision is
included until a configured PostgreSQL database is available. Running
`upgrade head` before generating the initial revision creates no model tables.

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
| GET | /api/v1/alerts | parameter, severity, start_date, end_date, acknowledged, resolved, limit, offset |
| GET | /api/v1/alerts/history | Same filters; paginated items, limit, offset, next_offset; includes resolved alerts by default |
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
insert; database failures roll back the transaction.

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
`created_at`. No inferred or modeled fields are added.

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
  acknowledged, resolved, resolved_at, created_at.

Transformer/timestamp/date/severity/resolved indexes support the history and
alert APIs. No Transformer table or specific sensor columns are assumed.
DailyMetric and Alert persistence hooks are available, but ingestion does not
generate either yet. An empty formula_results dictionary means no results;
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
No API calls these unfinished stages. The formula registry starts empty.
Threshold configuration contains an empty formulas mapping and commented future
format; comparison primitives support >, >=, <, <= without hard-coded values.

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
upload validation, simulation controls, empty formula/threshold configuration,
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
- 76 tests passed; 1 opt-in PostgreSQL integration test skipped after the single-transformer API update.
- All 40 app modules imported; application/OpenAPI creation passed.
- PostgreSQL DDL compiled for all 3 models; configuration/structure checked.
- Alembic heads/configuration and revision-template generation passed.
  The template test used a temporary directory, leaving project migrations empty.
- Python syntax compilation and pip dependency checks passed.
- The installed Starlette TestClient emitted one deprecation warning about
  httpx; the requested httpx-based tests passed.
- PostgreSQL 18 service was running, but no DATABASE_URL or .env was configured.
  Connectivity, real storage, initial migration generation, and migration
  application remain unverified until credentials are supplied.

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
