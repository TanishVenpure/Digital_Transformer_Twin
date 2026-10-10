# Digital Transformer Twin — Project Status

**Date:** 2026-10-10
**Repository:** `Digital_Transformer_Twin`
**Test results:** 639 passed, 27 skipped (main backend) | 125 passed (twin_iot)

---

## Architecture Overview

Two independent services share one repository:

| | Main Backend (port 8000) | twin_iot (port 8020) |
|---|---|---|
| **Role** | Risk engine, chatbot, maintenance records | Sensor pipeline, alerts, 3D dashboard |
| **Framework** | FastAPI + SQLAlchemy + Alembic | FastAPI + psycopg2 (raw SQL) |
| **Database** | PostgreSQL :5432, `digital_transformer` | PostgreSQL :5432 or :5434, `twin_iot` |
| **API prefix** | `/api/v1/...` | `/api/...` |
| **Auth header** | `X-Agent-API-Key` | `X-Twin-API-Key` |
| **Credentials** | `AGENT_CREDENTIALS` env var | `TWIN_API_CREDENTIALS` env var |
| **Dashboard** | Mounted at `/dashboard/` (static) | Mounted at `/dashboard/` (with live API) |

---

## Completed Work

### 1. Evidence-Bound Maintenance Chatbot

- **`POST /api/v1/agent/chat`** — answers questions from transformer-scoped evidence only
- **`GET /api/v1/agent/risk`** — read-only deterministic risk assessment
- Evidence retrieval: sensor readings, alerts, maintenance records
- Citation validation: every LLM-generated fact is checked field-by-field against cited evidence
- Prompt-injection protection: instruction-like text in stored evidence is withheld
- Deterministic fallback when Gemini is unavailable, times out, or produces invalid output
- **Files:** `app/agent/orchestration.py`, `app/agent/prompting.py`, `app/agent/guardrails.py`

### 2. Deterministic Maintenance Risk Scoring

- Risk index (0–100) from maintenance history, no ML, no LLM
- Five weighted factors: recurrence (30%), severity (25%), recency (20%), repair_outcome (15%), frequency_trend (10%)
- Multi-key recurrence grouping: fault_code OR component, largest group only (no double-counting)
- Category escalation for unresolved serious issues
- Policy versioned as `maintenance-risk-0.1.0-unapproved` with `approved: false`
- **File:** `app/agent/maintenance_risk.py`
- **Tests:** 53 tests in `tests/test_maintenance_risk.py` including 7 recurrence regression tests

### 3. Google Gemini Integration

- Provider-neutral LLM boundary with `GeminiClient` (`app/agent/llm.py`)
- Model: `gemini-3.8-flash` with `thinking_level=low`, temperature 0, JSON mode
- Single attempt, no retries, 30s timeout
- Sanitized diagnostics (`ProviderDiagnostic`, `CallMetrics`) — never exposes keys or prompts
- Error classification: authentication, permission, model_not_found, rate_limited, timeout, etc.
- **Tests:** ~60 mocked tests in `tests/test_gemini_client.py`
- **Live test:** `tests/test_gemini_live.py` (opt-in with `RUN_GEMINI_LIVE=1`)

### 4. Fail-Closed Demo Tooling

Created after an unintended live Gemini call caused by PowerShell `$env:GEMINI_API_KEY = ""` deleting the variable.

- **`scripts/run_demo_api.py`** — launcher with three LLM modes:
  - `--llm none`: Gemini disabled entirely
  - `--llm gemini-offline`: blank key, deterministic fallback shown
  - `--llm gemini-live --allow-live-gemini`: explicit opt-in for live calls
- Database must end in `_demo`/`_test`, must not equal dev DATABASE_URL
- `AGENT_CREDENTIALS` must contain `DEMO-` and come from the session (not `.env`)
- Defense-in-depth: re-resolves Settings after patching environ and verifies constraints
- **`scripts/seed_demo.py`** — seeds synthetic demo data via HTTP API
  - Preflight check: queries `/transformer/latest` before any writes
  - Refuses non-`DEMO-` transformer IDs
  - All records carry `source: synthetic-demo`
- **Tests:** 27 tests in `tests/test_demo_tooling.py`

### 5. API Authentication & Swagger

- API-key authentication via `X-Agent-API-Key` header
- Per-transformer scoping with permission system (`@ingest`, `@alerts`, `@simulation`)
- Swagger/OpenAPI shows `APIKeyHeader` security scheme with usage instructions
- **Fix applied:** Added description to Swagger Authorize dialog explaining to enter only the secret portion (not the full `scope:secret` string)
- **File:** `app/api/agent.py:26–32`

### 6. Maintenance Record Endpoints

- `POST /api/v1/maintenance/records` — create records (requires `@ingest`)
- `GET /api/v1/maintenance/records` — list records (transformer-scoped)
- Database migration: `alembic/versions/0003_maintenance_records.py`
- **File:** `app/api/maintenance.py`, `app/models/maintenance.py`, `app/schemas/maintenance.py`

### 7. twin_iot 3D Pipeline Fix

- **Root cause:** `BlenderBridge` hardcoded `status: "PENDING_BLENDER"` on every call — a stub that was never updated
- **Fix:** Changed default status to `"DONE"` since the Three.js dashboard handles all 3D rendering client-side
- Added `_apply_load()` and `_apply_switch()` extension points for future Blender (`bpy`) subclassing
- **File:** `twin_iot/app/twin3d/blender_bridge.py`
- **Tests:** 17 new end-to-end pipeline tests in `twin_iot/tests/test_twin3d_pipeline.py`
  - Each IoT slot (1–7) selects the correct model on WARNING/CRITICAL
  - Worst signal wins; ties go to lowest slot
  - Bridge reports DONE, gates redundant switches, tracks history
  - Full demo CSV pipeline: normal → critical escalation
  - Subclass extension point verification

### 8. Environment Setup

- Installed `psycopg2-binary 2.9.13` into `.venv` (required by twin_iot)
- Verified all dependencies available: FastAPI, Uvicorn, PyYAML, Pydantic, psycopg2
- PostgreSQL running on port 5432; port 5434 not active (twin_iot can use 5432 with `TWIN_DATABASE_URL` override)

### 9. Dashboard Integration

- `dashboard/index.html` mounted by both services at `/dashboard/`
- Fixed error handling: shows readable "API unavailable" message instead of JSON parse error
- 3D viewer (`twin3d.js`): Three.js loads `transformer.glb`, colours sensors by IoT status, camera follows signalled IoT
- Dashboard is wired to twin_iot's API shape (`/api/timeline`, `/api/twin3d/signal`)

---

## Commits

| Hash | Description |
|---|---|
| `dd923a0` | Add 7-IoT monitoring layer, PostgreSQL pipeline and 3D dashboard |
| `92a8b22` | Implement current, voltage and oil maintenance rules with shared alert history |
| `eb22833` | Initialize modular transformer backend and agent-facing APIs |
| `52b0e97` | Complete chatbot and maintenance risk features |
| `0abc2dc` | Serve dashboard from backend |
| `a1374f3` | Expose API key authorization in Swagger |

---

## Running Services

| Service | Port | Status | Command |
|---|---|---|---|
| Main backend | 8000 | Running | `python -m uvicorn app.main:app --port 8000` |
| twin_iot | 8020 | Running | `cd twin_iot && python -m uvicorn app.api.main:app --port 8020` |
| PostgreSQL | 5432 | Running | System service |

---

## Key Endpoints

### Main Backend (port 8000)

| Method | Path | Auth | Description |
|---|---|---|---|
| `POST` | `/api/v1/agent/chat` | `X-Agent-API-Key` | Evidence-bound chatbot |
| `GET` | `/api/v1/agent/risk?transformer_id=X` | `X-Agent-API-Key` | Maintenance risk assessment |
| `POST` | `/api/v1/data/reading` | `X-Agent-API-Key` + `@ingest` | Ingest sensor reading |
| `GET` | `/api/v1/alerts` | `X-Agent-API-Key` | List alerts |
| `POST` | `/api/v1/maintenance/records` | `X-Agent-API-Key` + `@ingest` | Create maintenance record |
| `GET` | `/api/v1/maintenance/records` | `X-Agent-API-Key` | List maintenance records |
| `GET` | `/api/v1/health/db` | None | Database health check |

### twin_iot (port 8020)

| Method | Path | Auth | Description |
|---|---|---|---|
| `GET` | `/api/timeline/{id}` | None | Full timeline for dashboard playback |
| `POST` | `/api/readings` | `X-Twin-API-Key` | Live sensor ingestion |
| `POST` | `/api/upload` | `X-Twin-API-Key` | CSV bulk upload (replace=true by default) |
| `GET` | `/api/alerts/{id}` | None | Alert history |
| `GET` | `/api/maintenance/{id}` | None | Maintenance sheet |
| `POST` | `/api/twin3d/signal` | `X-Twin-API-Key` | 3D model switch signal |
| `GET` | `/api/twin3d/state` | None | Current model and recent calls |

---

## Configuration

### `.env` (main backend)

```
DATABASE_URL=postgresql+psycopg://...   # never printed
AGENT_CREDENTIALS=TX-001:<secret>;TX-002:<secret>
LLM_PROVIDER=none                       # or "gemini" for live AI
GEMINI_API_KEY=...                      # only with LLM_PROVIDER=gemini
GEMINI_MODEL=gemini-3.8-flash
GEMINI_TIMEOUT_SECONDS=30
GEMINI_THINKING_LEVEL=low
```

### twin_iot environment

```
TWIN_DATABASE_URL=postgresql://twin@localhost:5432/twin_iot
TWIN_API_CREDENTIALS=T1:<secret>
```

---

## Test Summary

### Main Backend

```
639 passed, 27 skipped, 0 failures
```

Key test files:
- `tests/test_maintenance_risk.py` — 53 tests (risk engine + recurrence regression)
- `tests/test_citation_contract.py` — 31 tests (offline citation validation)
- `tests/test_gemini_client.py` — ~60 tests (mocked Gemini adapter)
- `tests/test_risk_chat.py` — 34 tests (chatbot with fake LLM)
- `tests/test_risk_api.py` — 16 tests (risk API endpoint)
- `tests/test_permissions.py` — auth and scoping tests
- `tests/test_demo_tooling.py` — 27 tests (launcher and seeder safety)

Skipped: Gemini live tests (need `RUN_GEMINI_LIVE=1`), PostgreSQL integration tests (need running database).

### twin_iot

```
125 passed, 0 failures
```

Key test files:
- `tests/test_twin3d_pipeline.py` — 17 tests (sensor → model selection → bridge)
- `tests/test_service.py` — processing service tests
- `tests/test_electrical.py`, `test_thermal.py`, `test_oil_level.py`, `test_measurement_health.py` — IoT formula tests
- `tests/test_validation_models_sheet.py` — validation, model selection, maintenance sheet
- `tests/test_api_security.py` — twin_iot auth tests

---

## Known Limitations

1. **Policy is unapproved** — `maintenance-risk-0.1.0-unapproved` is provisional; the risk index is not a failure probability.
2. **No provenance column** — synthetic demo records are indistinguishable from real records in API responses.
3. **Risk index drifts with date** — recency decay means the index changes daily.
4. **Gemini availability** — Google may return 503/504 at busy times; deterministic fallback handles this.
5. **No ML model** — the repository has no machine learning model; all scoring is deterministic from maintenance history.
6. **Blender (bpy) not integrated** — the bridge tracks state and reports DONE, but `_apply_load`/`_apply_switch` are no-ops. The Three.js dashboard handles all 3D rendering independently.
7. **Dashboard requires twin_iot** — the 3D dashboard at `/dashboard/` is wired to twin_iot's API shape and does not work against the main backend's endpoints.

---

## Remaining Work

- [ ] Approve the risk policy (change `approved: true` in `config/maintenance_risk_policy.yaml`)
- [ ] Add a `source` column to maintenance records for provenance tracking
- [ ] Integrate Blender `bpy` into the bridge (subclass `BlenderBridge`, override `_apply_load`/`_apply_switch`)
- [ ] Connect the dashboard's Assistant panel to `/api/v1/agent/chat`
- [ ] Set up PostgreSQL on port 5434 for twin_iot (or continue using 5432 with `TWIN_DATABASE_URL`)
- [ ] Load demo data into twin_iot via `/api/upload` with `escalation_demo.csv`
- [ ] Add ML model integration when the teammate's ML backend is ready
