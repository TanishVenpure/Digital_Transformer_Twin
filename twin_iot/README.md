# Transformer twin backend

FastAPI app that builds and serves the two-timebar model of the CPRI 630 kVA transformer and the
dashboard. Full description of every monitor, formula and signal: [../FEATURES.md](../FEATURES.md).

## Run

```
python -m venv .venv
.venv\Scripts\activate            # macOS / Linux: source .venv/bin/activate
pip install -r requirements.txt
python scripts/simulate_twin2.py      # optional: writes data/twin2/ (also built on the first API request)
python -m uvicorn app.api.main:app --port 8020
```

Open http://localhost:8020/dashboard/. No database is needed. `GEMINI_API_KEY` (optional) lets the
assistant phrase answers with Gemini; without it the assistant answers from the recorded data.

Docker: `docker compose up -d --build` (port with `TWIN_API_PORT`).

## Layout

| Path | What it is |
|---|---|
| `app/twin2/formulas.py` | Pure formulas: loading, voltage bands (primary +-6 %, secondary +6 / -9 %), IEC 60076-7 thermal model, pressure, oil balance |
| `app/twin2/timebar1.py` | One simulated day of fast signals (V / I every 20 ms, thermal every 1 s, pressure / ambient / precision every 1 min) |
| `app/twin2/timebar2.py` | One simulated year of daily readings (oil leak, precision devices) |
| `app/twin2/build.py` | Events, flags, playback payloads and CSV datasets in `data/twin2/` |
| `app/twin2/sheet.py` | Maintenance checks from the events, viewed at the two timebar cursors |
| `app/agent/`, `app/api/assistant.py` | Assistant: recorded-data answers, optional Gemini explanation |
| `app/api/main.py` | The API and the dashboard mount |
| `config/twin2.yaml` | Every constant of the model (values marked ASSUMED need the real transformer's data) |

## Tests

```
python -m pytest -q
```
