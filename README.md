# Digital Transformer Twin backend

A Python 3.11+ FastAPI foundation using SQLAlchemy 2.x, PostgreSQL, psycopg 3,
Alembic, and Pydantic v2. Dashboard and future AI agent clients use REST APIs;
they must not access PostgreSQL directly.

Reading ingestion/storage, database-backed history, alert acknowledgement/resolution,
CSV preview, and in-memory simulation controls are implemented. The approved per-reading rules are **3-Phase Current Instrument Error** and
**3-Phase Voltage Instrument Error**, producing configurable SYSTEM_MAINTENANCE
alerts through the same pipeline and common history. No other physical rules, synthetic sensor values,
health scores, frontend, AI agent, or maintenance scheduling are implemented.

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
allowed to create/drop schemas. The tests fail before connecting unless the
database name ends in `_test` and differs from the `DATABASE_URL` application
database. API calls in these tests use a random per-run key scoped only to the
`integration` fixture transformer; they never read `AGENT_CREDENTIALS` from `.env`.

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

The agent routes (`POST /api/v1/agent/events` and `POST /api/v1/agent/chat`) require
the `X-Agent-API-Key` header. Configure `AGENT_CREDENTIALS` as semicolon-separated
`transformer_id:secret` entries. Each credential is authorized only for its
server-side transformer mapping; the request `transformer_id` is not itself a
credential. Missing configuration fails closed with 503, invalid credentials
return 401, and out-of-scope transformer IDs return 403. Do not place secrets in
source control or client-visible URLs.

Data ingestion routes also require `X-Agent-API-Key`; readings and batches must
use a transformer ID authorized by that credential. CSV upload preview requires
the key even though the upload does not yet persist data. This protects local and
deployed ingestion from unauthenticated callers, but a shared API key remains a
coarse-grained control until device or tenant identity is added.

Write permissions. A key whose scope lists only transformer IDs is read-only: it can
read alerts, transformer history and maintenance records and call `/api/v1/agent/chat`
and `/api/v1/agent/events`, but cannot write. Writes need an explicit permission in the
same key's scope, plus the transformer:

| Permission | Routes |
|---|---|
| `@ingest` | `POST /api/v1/data/reading`, `/data/batch`, `/data/upload`, `POST /api/v1/maintenance/records` |
| `@alerts` | `PATCH /api/v1/alerts/{alert_id}/acknowledge`, `/resolve` |

For example: `TX-001:<chat-secret>;TX-001,@ingest:<ingest-secret>;TX-001,@alerts:<alerts-secret>`.
A key without the permission gets 403, and so does a key with a permission but no
transformer scope. Misspelled permissions grant nothing. Give the agent caller a
chat-only key, so data it writes can never come back to it as backend evidence.

Future timestamps. Sensor-reading `timestamp` and maintenance-record `occurred_at`
values are normalized to UTC (naive values are treated as UTC). They are rejected with
422 before any database write if they are more than 5 minutes later than the server's
current UTC time (`MAX_FUTURE_SKEW` in `app/schemas/sensor.py`; this is a clock-skew
tolerance, not an engineering threshold). A batch with one future row stores nothing.

### Maintenance Risk Index (agent)

`GET /api/v1/agent/risk?transformer_id=TX-001` returns a deterministic assessment of
the transformer's stored maintenance records (read-only key scoped to the transformer;
403 out of scope, 503 if the database or policy is unavailable). `/api/v1/agent/chat`
attaches the same assessment as `risk_assessment` and cites it by `assessment_id`.

The index is a relative 0-100 summary under `config/maintenance_risk_policy.yaml`,
which is **versioned and NOT approved**. It is not a failure probability and
authorizes nothing. Scoring reads only these validated `details` keys of maintenance
records (free text is never interpreted): `event_id`, `component`, `fault_code`,
`severity` (`minor|moderate|major|critical`), `repair_outcome`
(`completed|partial|unresolved|unknown`). Records whose `record_type` is `fault` or
`failure` are fault events; other records only count toward history coverage.

**Maintenance-record fields needed for a useful assessment.** Records written through
`POST /api/v1/maintenance/records` (requires `@ingest`) should use:

| Field | Required for | Values |
|---|---|---|
| `record_type` | identifying fault events | `fault` or `failure` (others are history only) |
| `occurred_at` | recency, trend, decay | ISO 8601; not more than 5 minutes in the future |
| `details.severity` | severity factor, escalation | `minor`, `moderate`, `major`, `critical` |
| `details.repair_outcome` | repair-outcome factor, escalation | `completed`, `partial`, `unresolved`, `unknown` |
| `details.component` and/or `details.fault_code` | recurrence | letters, digits, space, `_ / -` (no `.`), up to 64 characters |
| `details.event_id` (recommended) | deduplication, linking repair updates to a fault | letters, digits, `_ : -`, up to 128 characters |

Example:

```json
{"transformer_id": "TX-001", "record_type": "fault", "occurred_at": "2026-09-09T12:00:00Z",
 "details": {"event_id": "EV-1042", "component": "bushing", "fault_code": "E7",
             "severity": "major", "repair_outcome": "unresolved"}}
```

Records without these keys are stored and shown as history but cannot be scored; a
history of such records returns `INSUFFICIENT_EVIDENCE`. Free text in `notes` or
`details.description` is never interpreted. Field names, allowed values and the
policy numbers are provisional and need agreement with whoever records maintenance.

| Factor (weight) | 0-100 rule |
|---|---|
| Recurrence (30) | faults in 1825 days grouped separately by fault_code and by component; the single largest group is scored (never a sum, so an event with both keys is not double-counted): min(100, 100 x (largest group - 1) / 3) |
| Severity (25) | max of severity score x 0.5^(age/365 d); unresolved major/critical events never decay |
| Recency (20) | 100 x 0.5^(age of latest fault / 365 d) |
| Repair outcome (15) | mean of completed 0 / partial 50 / unresolved 100 / unknown 50 |
| Frequency trend (10) | faults in last 365 d (R) vs previous 365 d (P): P=0 -> 100 if R>0 else 0, else min(100, 50 x R / P); needs >= 4 faults, 730 d of history and complete retrieval |

A factor needs at least 50% of its inputs documented; the index is the weighted
mean of available factors and requires 60% of total weight, otherwise
`INSUFFICIENT_EVIDENCE` with a null index. Categories: >= 70 HIGH, >= 40 MODERATE,
otherwise `LOW_RECORDED_MAINTENANCE_RISK` (few documented serious events; never
"safe"). An unresolved or conflicting fault of major severity or above raises a
review flag and floors the category at HIGH (`category_escalated`). Records sharing
`event_id`, or identical in every documented field and timestamp, count once; the
latest dated outcome of an event wins, and disagreeing outcomes at the same time are
treated as unresolved and flagged. Synthetic, untrusted (instruction-like text),
undated, future-dated and other-transformer records are excluded and reported.

The chatbot sends the question, assessment and evidence to an injected LLM client
(none is configured by default) and accepts only JSON whose facts match cited fields
exactly; any failure returns the deterministic summary with the assessment unchanged.

### Gemini chat explanations (optional)

Install dependencies (`google-genai`, the official Google Gen AI SDK, is in
`requirements.txt`), then set in `.env`:

```text
LLM_PROVIDER=gemini
GEMINI_API_KEY=<your key>
GEMINI_MODEL=gemini-3.8-flash
GEMINI_TIMEOUT_SECONDS=30
GEMINI_THINKING_LEVEL=low
```

`GEMINI_TIMEOUT_SECONDS` (default 30) is also sent to Google as the server deadline
(`X-Server-Timeout`), so a slow generation returns 504 `DEADLINE_EXCEEDED` and the chat
falls back. Successful live calls took about 10-11 s, while two calls hit a 20 s
deadline; one attempt is made and there are no retries. A value set in `.env` overrides
the default. `GEMINI_THINKING_LEVEL` (default `low`) bounds the model's thinking effort;
Google recommends lowering it to reduce latency. `default` sends no thinking setting.

Restart the API. `/api/v1/agent/chat` then sends the question, the deterministic
risk assessment and the transformer's evidence to Gemini (JSON mode, temperature 0,
one attempt, bounded timeout). Evidence flagged as instruction-like text is withheld.
Gemini's reply is accepted only if every cited source exists, every fact matches its
cited field exactly, and the prose does not contradict the computed index, category or
policy approval or claim probability, safety or authorization. Otherwise, and on
missing credentials, timeouts, rate limits or provider errors, the response falls back
to the deterministic summary (`answer_source: "deterministic_fallback"`, with a warning
naming the reason). The risk assessment is never produced or modified by Gemini.

Google notes that access to 2.5 models is limited to projects that used them before;
a new key may need a newer model in `GEMINI_MODEL`. The code default is still
`gemini-2.5-flash`; the end-to-end live check that passed validation used
`GEMINI_MODEL=gemini-3.8-flash` with `GEMINI_THINKING_LEVEL=low`.

**Data sent to Google when `LLM_PROVIDER=gemini`.** For each chat request, only for the
transformer the API key is authorized for:

- the user's question;
- the deterministic risk assessment (index, category, factors, normalized maintenance
  fields, flags, limitations);
- bounded backend evidence: up to 50 recent sensor readings (raw measurement values),
  up to 50 alerts (formula inputs, values, thresholds, messages, details) and the
  transformer's maintenance records (record type, date, `details`, `notes`), at most 100
  evidence items in total;
- evidence warnings.

Not sent: API keys, database credentials, other transformers' data, or the text of any
record flagged as instruction-like (replaced by a withheld marker). With the default
`LLM_PROVIDER=none` nothing is sent. Check that sharing this data with Google is
acceptable before enabling it.

Tests mock the SDK. An opt-in live check sends only synthetic fixture records:
`RUN_GEMINI_LIVE=1 pytest -m gemini_live -s` (requires `GEMINI_API_KEY`).

Simulation routes (`/api/v1/simulation/start`, `/pause`, `/reset`, `/step` and
`/status`) control one process-wide simulator that has no transformer scope, so
transformer access never grants them. They require a credential whose scope
includes the `@simulation` permission, for example `@simulation:<secret>`, and return
401 for missing or invalid keys, 403 for valid keys without the permission, and 503
when no credentials are configured. Scope items starting with `@` are permissions,
never transformer IDs; unknown permissions grant nothing.

Alert routes (`/api/v1/alerts`, `/api/v1/alerts/history`, `/api/v1/alerts/{alert_id}`
and its acknowledge/resolve actions) also require `X-Agent-API-Key`. A
`transformer_id` filter outside the credential's scope returns 403; omitting the
filter returns only alerts for the credential's authorized transformers. Alert IDs
belonging to another transformer return 404 and are never acknowledged or resolved.
`/api/v1/transformer/latest` and `/history` also require `X-Agent-API-Key`. They
accept no transformer selector: the server resolves the stored transformer ID and
returns 403 before reading any data if that ID is outside the credential's scope.

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

## Hackathon demo runbook (main API: readings, alerts, maintenance, risk, AI explanation)

Verified on 2026-10-10 against a separate migrated database, over real HTTP, with
`scripts/seed_demo.py`. All demo data is **synthetic**; the risk policy is
**provisional and unapproved**; nothing here is a failure probability, an ML prediction
or a remaining-useful-life estimate (no ML model exists in this repository).

**Settings resolution.** Values come from process environment variables first, then
`.env`, then code defaults. In PowerShell, `$env:NAME = ""` *deletes* a variable
(the `.env` value then applies); use a single space to blank one for a session.

### 1. Use a database that holds only the demo transformer

`/api/v1/transformer/latest` and `/history` return 400 when more than one transformer
has readings, and the development database (`DATABASE_URL`) already holds one, so the
demo needs a separate migrated database on the same PostgreSQL server (port 5432).
Verified with `digital_transformer_test` (already at revision 0003). To use your own
`digital_transformer_demo`, create it and run `alembic upgrade head` with `DATABASE_URL`
pointing at it (same procedure as the test database; not re-run for this runbook).
Never point the demo at `twin_iot` (port 5434).

### 2. Start the API with the fail-closed launcher

```powershell
$env:AGENT_CREDENTIALS = "DEMO-TX-01,@ingest:<ingest-secret>;DEMO-TX-01:<chat-secret>"
.\.venv\Scripts\python.exe scripts\run_demo_api.py --database digital_transformer_test --llm none --port 8000
```

`scripts/run_demo_api.py` reuses the `DATABASE_URL` credentials in-process with the
named database (never printed) and refuses to start when: the database is the
development database or does not end in `_demo`/`_test`; `AGENT_CREDENTIALS` is not set
in the session with `DEMO-` keys (`.env` credentials are not used); `--llm gemini-live`
lacks `--allow-live-gemini`; or a real Gemini key would be used in offline mode.
Choose your own long random secrets. Check:
`Invoke-RestMethod http://127.0.0.1:8000/api/v1/health/db` returns `healthy`.
(Verified with `.venv_test`; `.venv` has the same dependencies, including `google-genai`.)

### 3. Load the synthetic demo transformer

```powershell
$env:DEMO_API_BASE = "http://127.0.0.1:8000"
$env:DEMO_INGEST_KEY = "<ingest-secret>"
.\.venv\Scripts\python.exe scripts\seed_demo.py
```

It posts 3 hourly readings (the last exceeds the configured 1% current
instrument-error threshold, so the existing rule raises a WARNING alert) and 4
maintenance records using the documented `details` fields. It refuses IDs not starting
with `DEMO-`, does not seed twice, and refuses without writing unless the API's
database holds no transformer or only the demo transformer; against an API on the
development database it stops with "already holds another transformer" (verified). Records carry `source: synthetic-demo` and a
"SYNTHETIC DEMO RECORD" note; the API has no provenance column, so it reports them like
any stored record (`synthetic_evidence: false`). Say so when presenting.

### 4. Show readings, alerts, maintenance history and the risk assessment

Use the read-only chat key (it cannot write: writes return 403):

```powershell
$H = @{ "X-Agent-API-Key" = "<chat-secret>" }; $B = "http://127.0.0.1:8000/api/v1"
Invoke-RestMethod "$B/transformer/latest" -Headers $H
Invoke-RestMethod "$B/alerts?transformer_id=DEMO-TX-01&resolved=false" -Headers $H
Invoke-RestMethod "$B/maintenance/records?transformer_id=DEMO-TX-01" -Headers $H
Invoke-RestMethod "$B/agent/risk?transformer_id=DEMO-TX-01" -Headers $H
```

Verified results: 3 readings; 1 WARNING alert (`3_phase_current_instrument_error`,
1.4% vs 1.0%); 4 maintenance records; risk index 61.3, `HIGH_MAINTENANCE_RISK`
(escalated by the unresolved major bushing fault), `PROVISIONAL`, policy
`maintenance-risk-0.1.0-unapproved`, flags `POLICY_NOT_APPROVED` and
`UNRESOLVED_SERIOUS_ISSUE`. The index depends on the current date (recency decay).

### 5. AI explanation and the deterministic fallback

```powershell
$body = '{"transformer_id":"DEMO-TX-01","question":"Which faults have recurred?"}'
Invoke-RestMethod "$B/agent/chat" -Method Post -Headers $H -ContentType "application/json" -Body $body
```

- `LLM_PROVIDER=none`: `answer_source: deterministic` with a risk summary.
- Fallback without contacting Google (verified): restart with
  `scripts\run_demo_api.py --database digital_transformer_test --llm gemini-offline`.
  Chat returns `answer_source: deterministic_fallback` and the warning "The AI explanation
  was unavailable (missing_credentials)"; the risk assessment is unchanged. Do not blank
  the key by hand: in PowerShell `$env:GEMINI_API_KEY = ""` deletes the variable and the
  `.env` key is used (this caused one unintended live call during preparation).
- Live Gemini (sends the demo evidence to Google): put `GEMINI_API_KEY`,
  `GEMINI_MODEL=gemini-3.8-flash`, `GEMINI_THINKING_LEVEL=low` and
  `GEMINI_TIMEOUT_SECONDS=30` in `.env`, then start with
  `--llm gemini-live --allow-live-gemini`. An accepted answer has `answer_source: llm`
  and `facts` whose citations were checked field by field. Google may return 503/504 at
  busy times; the chat then falls back.

### 6. 3D dashboard

`dashboard/` is served by the separate `twin_iot` API (port 8020, its own PostgreSQL
on 5434, `psycopg2`), which is not runnable in this environment. Served as static files
(`python -m http.server` in `dashboard/`), the page renders the 3D transformer and its
controls and reports "API unavailable: HTTP 404 ... (is the twin_iot API running?)".
Its Assistant panel is a disabled placeholder; it is not connected to `/api/v1/agent/chat`.
The 3D view shows `twin_iot` data, not the main API's demo transformer.

## Integration contracts and open blockers

Documented, not implemented: each needs a teammate decision or a service unavailable here.

**twin_iot service (dashboard and 3D data).** Run from inside `twin_iot/` (its package is
also called `app`): `python -m uvicorn app.api.main:app --port 8020`. It needs the Python
packages `fastapi`, `uvicorn`, `pydantic`, `PyYAML`, `python-multipart` and `psycopg2`
(`psycopg2` is not in `requirements.txt` and is installed in neither venv); a PostgreSQL
database at `TWIN_DATABASE_URL` (default `postgresql://twin@localhost:5434/twin_iot`,
trust auth, separate from the main database; startup refuses an Alembic-managed
database); `TWIN_API_CREDENTIALS` for uploads, readings and signals; and data from
`scripts/simulate.py` (random synthetic hourly readings, default transformer `T1`) or a
CSV upload. None of this was runnable in this environment. Served as static files,
`dashboard/` renders the 3D model and reports "API unavailable: HTTP 404 ... (is the
twin_iot API running?)" (verified).

**Consistent transformer IDs.** The dashboard shows twin_iot transformers (`T1` by
default) from its own database; the main API demo uses `DEMO-TX-01`. The two databases are
not linked. To present one transformer, generate the twin_iot data with the same ID
(`python scripts/simulate.py --transformer DEMO-TX-01`, synthetic; not run here) and say
that the 3D view and the risk/chat results come from separate synthetic datasets.

**Assistant panel to `POST /api/v1/agent/chat`** (the panel is a disabled placeholder):

- Request: `Content-Type: application/json`; header `X-Agent-API-Key` with a read-only key
  scoped to the transformer (never a key with `@ingest` or `@alerts`); body
  `{"transformer_id": str, "question": str}` (question 1-2000 characters, no other fields).
- Response: `transformer_id`, `answer`, `answer_source` (`deterministic`, `llm` or
  `deterministic_fallback`), `evidence_source_ids`, `warnings`, `synthetic_evidence`,
  `facts[{statement, source_ids, assertions[{source_id, field, expected_value}]}]`,
  `inferences[{statement, source_ids}]` and `risk_assessment` (same shape as
  `GET /api/v1/agent/risk`, or null). Errors: 401 bad key, 403 out-of-scope transformer,
  422 invalid body or guardrail violation, 503 database or backend unavailable.
- Rendering: insert all text with `textContent`; show `warnings`; mark `facts` as checked
  citations and `inferences` as interpretation; show `risk_assessment.policy_approved`
  (false) and the review flags; never present the index as a probability.
- Origin and CORS: the dashboard is served by twin_iot (port 8020) and the chat API by the
  main app (port 8000). The main app enables CORS only with `DEBUG=true` (all origins,
  no credentials). Recommended: a same-origin proxy route in twin_iot that holds the chat
  key server-side and forwards the two fields, so the browser never holds an API key.
  Local-demo alternative only: DEBUG CORS plus a key typed by the operator and kept in
  `sessionStorage` (the pattern the dashboard already uses for its upload key).

**Frontend ingestion (`origin/FRONTEND`, `frontend/twin-api.js`).** That page is served by
its branch's main app at `/twin/` (same origin, no CORS needed) and calls
`GET /api/v1/transformer/latest`, `GET /api/v1/alerts` and `POST /api/v1/data/reading`
without a key, so all three now return 401. It needs a read key for the GETs and an
`@ingest` key scoped to its transformer for the POST. Never embed either in browser code:
post readings from a server-side process that holds the key, or, for a local demo only,
have the operator type keys per session into `sessionStorage`. Merging that branch also
requires reconciling its `app/main.py` (static `/twin/` mount) with the current one.

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
