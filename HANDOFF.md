# Handoff: Digital Transformer Twin — Feature Merge

**Date:** 2026-10-10
**Author:** Claude (pair session with daksh0302)
**Branch:** main (uncommitted)

---

## What was done

Merged features from a second repository (`Desktop/new twin/Digital_Transformer_Twin`) into this working repo, combining:

- **This repo's strengths:** Security layer (TrustedHostMiddleware, API key auth, transformer scoping), assistant chatbot proxy, maintenance risk assessment proxy, Swagger auth description.
- **New twin's strengths:** Analytics modules, physics-based simulation, redesigned full-screen 3D dashboard with frosted glass UI, procedural textures (metal.js), per-metric health graphs (metric-chart.js).

## Files changed

### New files (untracked)

| File | Source | Purpose |
|------|--------|---------|
| `twin_iot/app/analytics/__init__.py` | new twin | Package init |
| `twin_iot/app/analytics/load_growth.py` | new twin | Weekly load reviews, overload detection, LOAD_GROWTH flag |
| `twin_iot/app/analytics/oil_leak.py` | new twin | Refill cycle analysis, accelerating leak detection |
| `twin_iot/app/analytics/top_oil.py` | new twin | Cooling health, oil ageing, COOLING_DEGRADED flag |
| `twin_iot/app/analytics/winding.py` | new twin | Thermal review (IEC 60076-7), insulation ageing |
| `twin_iot/app/simulation/degradation_source.py` | new twin | Physics-based degradation (LoadModel, WindingThermal, CoolingSystem, GridVoltage) |
| `dashboard/metal.js` | new twin | Procedural brushed steel + aged copper textures (triplanar mapping) |
| `dashboard/metric-chart.js` | new twin | Per-metric health graph (SVG), analytics overlays |
| `twin_iot/app/api/assistant_proxy.py` | this repo | Server-side proxy for chatbot + risk (3 endpoints) |
| `twin_iot/tests/test_assistant_proxy.py` | this repo | 15 tests, all outbound HTTP mocked |

### Modified files

| File | What changed |
|------|-------------|
| `app/api/agent.py` | APIKeyHeader description for Swagger |
| `dashboard/index.html` | Replaced with new twin's full-screen 3D dashboard + integrated chatbot panel, risk panel, auth JS, chat JS, risk JS |
| `twin_iot/app/api/main.py` | Added analytics imports, helper functions (`_oil_leak_analysis`, `_load_growth_analysis`, `_winding_analysis`), 5 new endpoints (`/api/analytics/*`, `/api/maintenance-events/*`), wired analytics into timeline response |
| `twin_iot/app/database/db.py` | Added `maintenance_events` table, `flag` column on `maintenance_log`, new functions (`oil_series`, `winding_inputs`, `top_oil_inputs`, `top_oil_analysis`, `store_events`, `maintenance_events`), updated `rebuild_maintenance` for trend flags |
| `twin_iot/app/maintenance/sheet.py` | Added `"flag"` to SHEET_FIELDS, `describe()` handles trend flags, new `add_trend_flags()` function |

## Security preserved

All existing security remains intact:

- **TrustedHostMiddleware** — rejects DNS-rebinding (line 41 of main.py)
- **`require_write_access`** — X-Twin-API-Key on all write endpoints
- **`authorize_transformer`** — scopes write ops to authorized transformer IDs
- **`refuse_managed_database`** — prevents twin_iot from touching the main backend's DB
- **Input validation** — ReadingIn with field validators, SignalIn with Field constraints, MAX_UPLOAD_BYTES
- **No hardcoded keys** — dashboard uses sessionStorage KEY_STORE pattern, `writeHeaders()` on all authenticated fetches
- **Server-side proxy** — chatbot/risk calls go through `assistant_proxy.py`, API keys stay server-side

## Architecture

```
 Main backend (:8000)          twin_iot (:8020)
 ┌──────────────────┐          ┌─────────────────────────┐
 │ Chatbot (Gemini) │◄─httpx───│ /api/assistant/chat      │
 │ Risk assessment  │◄─httpx───│ /api/assistant/risk       │
 │ Agent status     │◄─httpx───│ /api/assistant/status     │
 └──────────────────┘          │                           │
                               │ /api/timeline/:id         │ ← analytics dict
                               │ /api/analytics/winding/:id│
                               │ /api/analytics/top-oil/:id│
                               │ /api/analytics/load-growth│
                               │ /api/analytics/oil-leak   │
                               │ /api/maintenance-events   │
                               │ /api/maintenance/:id      │ ← trend flags
                               │ /api/upload               │
                               │ /api/readings             │
                               │ /api/twin3d/signal        │
                               └─────────────────────────┘
                                         │
                               ┌─────────┴─────────┐
                               │  Dashboard (:8020) │
                               │  index.html        │
                               │  metal.js          │
                               │  metric-chart.js   │
                               │  twin3d.js         │
                               └───────────────────┘
```

## Environment variables needed

| Variable | Purpose | Default |
|----------|---------|---------|
| `MAIN_BACKEND_URL` | Where twin_iot proxies chatbot/risk requests | `http://localhost:8000` |
| `MAIN_BACKEND_API_KEY` | API key for main backend auth | (none — proxy returns 502) |
| `TWIN_API_CREDENTIALS` | JSON: `{"KEY": ["TX-001","TX-002"]}` | (none — writes blocked) |
| `TWIN_ALLOWED_HOSTS` | Comma-separated hosts for TrustedHostMiddleware | `localhost,127.0.0.1` |
| `TWIN_DASHBOARD_DIR` | Path to dashboard static files | `../dashboard` relative to project root |

## Trend flags (new analytics)

| Flag | Module | Meaning |
|------|--------|---------|
| `ACCELERATING_OIL_LEAK` | oil_leak.py | Each oil top-up lasts less time |
| `LOAD_GROWTH` | load_growth.py | Overloads becoming more frequent |
| `WINDING_EXCESS_HEATING` | winding.py | Winding hotter than its load explains |
| `OIL_AGEING` | top_oil.py | Radiator cleaning recovers less each time |
| `COOLING_DEGRADED` | top_oil.py | Cooling system performance declining |

## Known considerations

1. **Transformer ID mismatch:** twin_iot uses `T1` as default; main backend authorizes `TX-001`/`TX-002`. The dashboard sends whatever transformer_id the API returns. The proxy endpoints skip transformer-level auth since the main backend handles its own scoping.

2. **Database schema changes are idempotent:** Uses `CREATE TABLE IF NOT EXISTS` and `ALTER TABLE ... ADD COLUMN IF NOT EXISTS`. No migration files needed; safe to run on existing databases.

3. **Dashboard size:** ~53KB HTML. The new twin's full-screen 3D layout with frosted glass panels replaces the previous simpler layout. Chat and risk panels are integrated as collapsible `<details>` elements in the right column.

4. **No commits made:** All changes are uncommitted per user instructions. Run `git diff` and `git status` to review before committing.

## How to test

```bash
# Start main backend (chatbot + risk)
cd app && uvicorn api.agent:app --port 8000

# Start twin_iot (sensors + dashboard + proxy)
cd twin_iot && python -m uvicorn app.api.main:app --port 8020

# Open dashboard
# http://localhost:8020/dashboard/

# Run proxy tests
cd twin_iot && python -m pytest tests/test_assistant_proxy.py -v
```

## What's NOT included

- No Blender integration changes (out of scope)
- No `.env` file modifications
- No database migrations (schema changes are inline and idempotent)
- No live Gemini API calls (proxy returns 502 when main backend is down)
- `random_source.py` from new twin was not copied (degradation_source.py covers simulation needs)
