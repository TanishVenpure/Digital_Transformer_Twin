# Transformer Digital Twin — Claude Code Handoff

## Project status

This repository contains a FastAPI-based Transformer Digital Twin backend using SQLAlchemy, PostgreSQL, Alembic, Pydantic, and pytest.

The current work is local development work and remains uncommitted. Preserve all existing modifications and do not reset, clean, stash, or commit them unless the owner explicitly requests it.

## What has been implemented

### Existing backend foundation

- FastAPI application and health endpoints.
- SQLAlchemy database layer with PostgreSQL/psycopg support.
- Alembic migrations.
- Sensor-reading ingestion and batch ingestion.
- Alert persistence and existing maintenance-rule processing.
- Simulation and transformer-related API functionality already present in the repository.

### Agent integration

- Added agent API routes:
  - `POST /api/v1/agent/events`
  - `POST /api/v1/agent/chat`
- Agent requests use strict Pydantic schemas.
- Agent responses include transformer scope, answer text, evidence IDs, warnings, and a synthetic-evidence indicator.
- Backend evidence retrieval is bounded and transformer-scoped.
- The agent does not fabricate telemetry, alerts, maintenance history, thresholds, scores, or health results.
- Database-unavailable conditions return a controlled `503` response.

### Provenance protection

Caller-supplied monitoring results and health-engine outputs are not treated as verified evidence merely because their transformer IDs match.

- Untrusted caller-supplied CRITICAL statuses cannot create a trusted CRITICAL result.
- Trusted evidence behavior remains available for explicitly trusted providers.
- Synthetic evidence is clearly marked and is not presented as field evidence.
- Regression tests cover fabricated monitoring and health-engine CRITICAL outputs.

### Authentication and transformer scope

- Agent and data-ingestion routes require an API key.
- Credentials are configured per transformer using `AGENT_CREDENTIALS`.
- A credential for one transformer cannot be used to access another transformer.
- Missing credentials produce an explicit configuration error.
- Invalid credentials return `401`.
- Out-of-scope transformer access returns `403`.

Authentication is currently API-key based. It is not a complete production identity, role, audit, or key-rotation system.

### Maintenance history

- Added `maintenance_records` model and PostgreSQL JSONB details storage.
- Added Alembic migration `0003_maintenance_records`.
- Added authenticated maintenance-record creation and retrieval routes:
  - `POST /api/v1/maintenance/records`
  - `GET /api/v1/maintenance/records?transformer_id=...`
- Agent backend evidence retrieval includes bounded maintenance-history records when available.
- Maintenance notes/details that look instruction-like are not automatically trusted as factual evidence.

## Verification already performed

The owner reported the following successful local verification:

- Full pytest suite: `233 passed` in the latest reported run.
- Alembic migration command completed:

  ```powershell
  .\.venv\Scripts\alembic.exe upgrade head
  ```

- PostgreSQL database `digital_transformer` was created locally.
- A maintenance record for `TX-001` was created successfully with `201 Created`.
- Maintenance history retrieval returned `200 OK` and the stored record.
- Sensor-reading ingestion returned `201 Created`.
- Agent chat returned `200 OK` with backend evidence.
- `git diff --check` completed without whitespace errors; Windows CRLF conversion notices may appear.

Do not claim a test or command passed unless it is rerun and its output is available in the current session.

## Current local setup requirements

The README states Python 3.11 or newer. The project requirements are in `requirements.txt`. The expected database configuration is PostgreSQL:

```text
postgresql+psycopg://postgres:<password>@localhost:5432/digital_transformer
```

The project-root `.env` contains local configuration and must not be printed, committed, or overwritten. `.env.example` is only a template.

After a laptop restart, verify the following from the repository root:

```powershell
python --version
Get-Service postgresql-x64-18
Test-Path .\.venv\Scripts\python.exe
```

If the virtual environment is broken and a usable Python installation exists, recreate only the generated environment:

```powershell
Remove-Item -LiteralPath .\.venv -Recurse -Force
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

Do not copy `.env.example` over an existing `.env`.

## Remaining work

### Immediate local restoration

1. Restore or install a usable Python 3.11+ installation if `python.exe` is unavailable.
2. Recreate `.venv` if its interpreter cannot start.
3. Start PostgreSQL service `postgresql-x64-18` if it is stopped.
4. Confirm `.env` still has a valid `DATABASE_URL` without displaying the password.
5. Run migrations and verify the current Alembic revision:

   ```powershell
   .\.venv\Scripts\alembic.exe upgrade head
   .\.venv\Scripts\alembic.exe current
   ```

6. Run the full tests:

   ```powershell
   .\.venv\Scripts\python.exe -m pytest -q
   ```

### Backend integration work

- Connect the backend evidence adapter to real telemetry, alert, health-engine, ML, and maintenance systems.
- Define and document trust boundaries for each external source.
- Add integration tests against representative real schemas and failure modes.
- Replace the local synthetic maintenance record with clearly labelled real records when available.
- Decide whether the agent should expose only factual evidence summaries or also separately reviewed recommendations.

### Security hardening

- Replace shared static API keys with a managed credential or identity system.
- Add key rotation and revocation.
- Add audit logging for agent and maintenance-record access.
- Add rate limiting and abuse protection.
- Configure TLS and secure deployment boundaries.
- Review authorization at every transformer-scoped endpoint.
- Ensure secrets are rotated if any were previously pasted into chat, screenshots, shell history, or logs.

### Production readiness

- Add deployment configuration and environment-specific settings.
- Add structured logging, metrics, tracing, and alerting.
- Add backup/restore procedures and database migration runbooks.
- Add CI checks for tests, formatting, dependency vulnerabilities, and migration consistency.
- Add operational documentation and an incident-response procedure.
- Review the API contract and frontend/dashboard integration.

## Important safety limitations

- A matching `transformer_id` is not authorization by itself.
- Agent output is evidence context only; it does not authorize shutdown, repair, operation, or return to service.
- Missing backend data must remain missing; do not fill it with guessed values.
- Thresholds and engineering decisions must come from reviewed configuration or domain owners.
- The current local API is suitable for development verification, not direct production exposure.

## Safe working rules for the next agent

- Inspect `git status --short` before editing.
- Preserve every existing uncommitted change.
- Do not reset, clean, stash, or commit.
- Do not overwrite `.env` or print secrets.
- Use focused tests for changed behavior, then the full suite.
- Run `git diff --check` after edits.
- Report exact commands and actual results, including failures and skipped tests.
