# Digital Transformer Twin: Demo Script

**Duration:** 3–5 minutes
**Presenter:** Daksh Pareek
**Transformer:** `CPRI-630` (630 kVA, 11 / 0.433 kV, ONAN). The data is simulated twin2 data.

---

## Startup (before the demo)

### Required environment variables

| Variable | Purpose |
|----------|---------|
| `TWIN_DATABASE_URL` | PostgreSQL connection string. The server checks the database at startup. The dashboard, chat and risk read only the twin2 datasets. |
| `TWIN_API_CREDENTIALS` | API keys for the write endpoints (including `POST /api/twin2/rebuild`) |

Do not show the real values on screen.

### Start command

```powershell
$env:TWIN_DATABASE_URL = '<your-connection-string>'
$env:TWIN_API_CREDENTIALS = '<your-credentials>'
cd twin_iot
python -m uvicorn app.api.main:app --port 8020 --reload
```

Open **http://localhost:8020/dashboard/** in a browser.

The datasets live in `twin_iot/data/twin2/` (git-ignored). If they are missing, the first request
builds them from `config/twin2.yaml`. To rebuild them yourself, run `python scripts/simulate_twin2.py`.

### Pre-flight checklist

- [ ] The server starts without errors ("Application startup complete")
- [ ] The dashboard shows the 3D model, two monitor columns and two timebars
- [ ] The signal list reads "· 0 of 47" at the start (26 timebar 1 events and 21 timebar 2 events)
- [ ] `GET /api/assistant/status` returns `{"backend":"self-contained","status":"ok","transformer_id":"CPRI-630"}`

---

## Script

### 1. The problem (30 seconds)

> Distribution transformers fail expensively and with little warning. Utilities log loading,
> voltage, temperatures, oil level and instrument accuracy, but they rarely view it all in one place.
> This digital twin replays two views of one 630 kVA transformer. The first is a single day
> of fast signals; the second is a full year of daily condition data. Maintenance checks,
> risk and an assistant all follow the replay.

### 2. The 3D twin (30 seconds)

- Show the **Solid / Cutaway / X-ray** views, then **Explode** and **Assemble**, then the **Oil** toggle
- The banner above the model names the worst monitor right now, and the camera moves to that part

### 3. Two timebars (60 seconds)

- **Timebar 1:** 2026-07-15, one minute per step. It covers loading, voltage, top oil (OTI), winding (WTI), the IEC 60076-7 thermal model, tank pressure and three precision devices.
- **Timebar 2:** 2026-07-01 to 2027-06-30, one day per step. It covers oil volume (the leak) and daily drift of the precision devices.
- Press play on timebar 1 and use the speed button. Watch the cards on the left change colour. A popup appears at the top whenever a timebar crosses a warning, alarm, trip or maintenance demand.
- Click a popup or a card. The graphs open with event markers and the cursor. For voltage and loading, open a **20 ms** event window.
- Drag timebar 2 to February 2027 to show the oil leak getting worse.

### 4. Signals & maintenance (30 seconds)

- The list holds only signals up to each cursor. Filter it by timebar.
- On timebar 2, `*_RECALIBRATED` and `OIL_LEAK_REPAIRED` are recorded maintenance actions, and each one closes the matching open check.
- **CSV** downloads every event. The **Data** panel lists every dataset.

### 5. Maintenance risk (45 seconds)

Set timebar 1 to **15:00** and timebar 2 to **2027-02-25**, then open **Maintenance risk**.

- Expected: **90.9, High maintenance risk**. 23 checks are visible and 9 are open.
- Recommended action, from check #34 (`CRITICAL`, oil volume): *Measured oil volume 286.6 L is below the minimum 288 L.*
- Walk through the factors: severity 30 %, recurrence 25 %, recency 20 %, resolution 15 %, trend 10 %.
- Move either cursor. The score updates, because each check is judged at its own timebar's cursor.

> This is a relative summary of the recorded checks, not a failure probability. The policy
> `maintenance-risk-0.1.0-unapproved` has not been reviewed by engineering. See [RISK_INDEX.md](RISK_INDEX.md).

### 6. The assistant (45 seconds)

Keep the same cursors and open **Assistant**:

- **"What needs maintenance?"** lists open checks by severity (#34 first) and the monitors that need attention now.
- **"How is the oil?"** gives top oil on timebar 1 and the oil leak on timebar 2, with the related checks.
- **"Which repairs were done?"** lists the timebar 2 actions up to 2027-02-25 and what each one closed.
- Move a cursor and ask again. The answer changes, and each reply states which cursors it used.

> The assistant is deterministic. No LLM and no external API is called. Every answer is
> built from the twin2 datasets up to the two cursors and cites check numbers.

---

## If something fails

| Failure | Recovery |
|---------|----------|
| Server won't start | PostgreSQL must be reachable: `pg_isready -h localhost -p 5432`. Check that the env vars are set. |
| "API unavailable" in the header | `GET /api/twin2/timebar/1` should return JSON. If the datasets are corrupt, run `python scripts/simulate_twin2.py`. |
| No popups | Popups appear only while a timebar moves forward across a check, not on page load. Press play from the start. |
| Risk panel says "Risk unavailable" | Check `GET /api/assistant/risk?transformer_id=CPRI-630` and the server log. |
| Stale page after an update | Hard-reload (Ctrl+F5). |

### Fallback: API only

```bash
curl -X POST http://localhost:8020/api/assistant/chat -H "Content-Type: application/json" \
  -d '{"transformer_id":"CPRI-630","question":"what needs maintenance?","until":"2026-07-15T15:00:00Z","until2":"2027-02-25"}'
curl "http://localhost:8020/api/assistant/risk?transformer_id=CPRI-630&until=2026-07-15T15:00:00Z&until2=2027-02-25"
curl http://localhost:8020/api/twin2/sheet
```

---

## Real vs. simulated vs. future

**Implemented:**
- Two-timebar simulation and playback
- Per-monitor graphs with 20 ms windows
- A maintenance sheet derived from events, with recorded resolutions
- A deterministic assistant and a 5-factor risk index that both follow the two cursors
- Popups as either timebar crosses a check
- Dataset downloads

**Simulated:** all data comes from `scripts/simulate_twin2.py` using `config/twin2.yaml`. Nothing is connected to real hardware.

**Limitations:**
- On timebar 1, "resolved" means only that the recorded condition ended. No repair is recorded on that timebar.
- The risk half-life and trend window are one year, so timebar 1 checks never decay.
- There is one transformer (`CPRI-630`); other IDs return 404.

**Future:**
- Live SCADA / IEC 61850 ingestion
- An LLM-backed assistant
- A fleet view
- Engineering approval of the risk policy
