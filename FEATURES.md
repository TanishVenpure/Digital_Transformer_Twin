# Digital Transformer Twin: features

A digital twin of a 630 kVA, 11 / 0.433 kV distribution transformer. Seven monitors watch the
transformer's sensor readings hour by hour. A 3D model shows which part needs attention. A
maintenance sheet records every check the monitors raise, and trend analytics spot slow
problems that single readings miss.

The project has two parts:

| Part | Folder | What it is |
|---|---|---|
| Dashboard | `dashboard/` | The web page: 3D model, monitor cards, graphs, maintenance sheet, timeline |
| Backend | `twin_iot/` | FastAPI + PostgreSQL: validates readings, runs the 8 monitors and the analytics, serves the dashboard at `/dashboard/` |

---

## 1. The dashboard at a glance

The 3D transformer fills the screen on a black background. Everything else floats around it
as frosted-glass panels:

| Where | Panel |
|---|---|
| Top | Header: transformer rating, number of readings and date range, playback clock, overall status |
| Top centre | Alarm card: which monitor the 3D view is focused on, and its status |
| Left | Monitor cards, in two groups: Precision measurement (2) and Transformer (5) |
| Right | Assistant (dropdown), Maintenance sheet, Data (dropdown) |
| Bottom centre | Model controls, then the timeline |
| Over the model | Camera movement pad |

On screens narrower than 1100 px the layout stacks: the model on top, the panels below it.

---

## 2. The 3D model

**File:** `dashboard/transformer.glb`, built in Blender and drawn with three.js
(`dashboard/twin3d.js`).

### Materials (`dashboard/metal.js`)
- **Outside** (tank, cover, conservator, radiators, boxes): brushed, scratched grey steel with
  plate seams, rows of rivets and a light touch of rust. The texture is drawn in code, so it
  needs no image files and works offline.
- **Coils, windings and every other copper part:** aged, brushed copper.

### What the model shows
- **Sensor chips:** each sensor device has a label (for example TOP_OIL or AMB) and a
  glowing dot coloured by its status. A critical sensor's glow slowly pulses.
- **Automatic focus:** while any monitor is not Normal, the camera flies to that monitor's
  sensors and the rest of the model dims. The alarm card at the top names the view, for
  example "Top-Oil Temperature · Top-oil overheating". When all monitors are Normal, it shows
  the whole transformer.
- The backend picks the view: the monitor with the worst status wins, then the lowest
  monitor number (`twin_iot/config/models.yaml`).

### Model controls (bottom bar)
| Control | What it does |
|---|---|
| Solid / Cutaway / X-ray | Tank view: solid steel, a cut-out window into the tank, or a see-through tank |
| Reset view | Gives the camera back to the automatic focus |
| Explode / Assemble | Animates the parts apart, or back together |
| Oil | Shows or hides the insulating oil (in Cutaway and X-ray) |
| Labels | Shows or hides the sensor labels |

### Camera movement pad
| Button | Key | What it does |
|---|---|---|
| ▲ ▼ ◀ ▶ | Arrow keys, or W A S D / Q E | Move the camera up, down, left, right |
| Fwd / Back | W / S | Move forward into the model, or back out |
| + / − | + / − (or the mouse wheel) | Zoom in or out |
| ◉ (centre) | R | Reset view |
| Spin | | Turn slow auto-rotation on or off |

One click moves one step. Holding a button keeps moving. While you are moving the camera, a
"Free navigation" note appears and alerts no longer move it, until you press Reset view.
You can also drag to rotate. Right-drag, or Shift + drag, pans.

Link options: `?view=solid|cut|xray`, `?explode=0..100` and `?intro=0` (skip the opening
explode-and-assemble animation).

---

## 3. The monitors

Each card shows the monitor's name, what it measures, the current value, a health bar with
its health %, and its status. Click a card, or press Enter on it, to open its graph.

| # | Monitor | Group | Measures | Warning | Critical |
|---|---|---|---|---|---|
| 1 | Current Sensor | Precision measurement | Instrument error vs a reference meter, % | | > 1 % |
| 2 | Voltage Sensor | Precision measurement | Instrument error vs a reference meter, % | | > 1 % |
| 3 | Current | Transformer | Load, % of rated current (841 A) | ≥ 90 % | ≥ 100 % |
| 4 | Voltage | Transformer | Supply deviation from 250 V nominal, % | ≥ 5 % | ≥ 10 % |
| 5 | Oil Level | Transformer | Conservator gauge, % | ≤ 40 % | ≤ 20 % |
| 6 | Top-Oil Temperature | Transformer | Top-oil temperature, °C | ≥ 95 °C (alarm) | ≥ 105 °C (trip) |
| 7 | Winding Temperature (WTI) | Transformer | Hottest phase winding temperature, absolute °C | ≥ 110 °C (alarm) | ≥ 120 °C (trip) |
| 8 | Transformer Pressure | Transformer | Measured oil / tank pressure | not set yet | not set yet |

The limits are set in `twin_iot/config/thresholds.yaml`. Several are marked EXAMPLE and
should be replaced with the real nameplate and manufacturer values.

Top-Oil Temperature has one extra check: a rise over ambient of more than 40 K gives a
Warning.

**Winding temperature (WTI), CPRI transformer:** the protection quantity is the absolute
temperature of the hottest phase, T_WTI = max(T_R, T_Y, T_B). 109.9 °C is Normal, 110.0 °C is the
alarm (Warning), 119.9 °C is still Warning and 120.0 °C is the trip (Critical). The settings live in
`thresholds.yaml`, not in the code. Missing or invalid WTI data gives No data, never a false trip,
and a missing ambient doesn't stop the WTI check. The winding rise over ambient (the old 40 / 45 K
rule) no longer sets the status. It's kept only as analytics: the rise, the thermal model's
expected value, the excess over it and the insulation loss of life (section 5).

**OTI and WTI are independent:** top-oil (95 / 105 °C) and winding (110 / 120 °C) are judged
separately and never averaged. The overall status is the worse of the two. Health % and the
assistant never affect a trip.

**Transformer Pressure** has no unit or limits configured yet, so it shows No data until its
settings are supplied.

### Statuses
| Status | Colour | Meaning |
|---|---|---|
| Normal | Green | Within limits |
| Warning | Amber | Past the warning limit |
| Predicted | Purple | Not past the limit yet, but the trend reaches the critical limit within the lead time (24 h for current, 72 h for oil, 6 h for temperatures) |
| Maintenance | Orange | Maintenance required. The sensor monitors (1 and 2) only ever raise this, because a faulty sensor is not a transformer fault |
| Critical | Red | Past the critical limit |

A monitor shows **No data** when its inputs were missing or invalid for that hour. Bad input
is never treated as a fault.

### Health %
100 % means as new and 0 % means at the critical limit. A value past the limit goes below 0.
The bar is coloured by status. Each group header shows how many of its monitors need
attention, and the header pill shows the overall worst status.

### Trend badges on the cards
When the trend analytics (section 5) raise a flag, the matching card shows a badge:

| Badge | Card | Meaning |
|---|---|---|
| Leak accelerating | Oil Level | Each oil top-up lasts less time |
| Load growing | Current | Overloads are becoming more frequent |
| Running hot | Winding Temperature | The winding is hotter than its load explains |
| Oil ageing | Top-Oil Temperature | Radiator cleaning recovers less each time |

---

## 4. Graph view (click a monitor card)

| Control | Options |
|---|---|
| Chart | **Health %** (100 to 0) or **Measured value** (in the monitor's own unit) |
| Range | 7 days, 30 days or All |
| Close | × button or Esc |

The graph shows:
- The metric line, with dashed **warning** and **critical** limit lines.
- **▼ markers** where a maintenance check was raised, coloured by its signal.
- **Ⓜ markers** where maintenance was actually carried out.
- A status strip under the chart, one block per hour.
- A hover tooltip with the hour, value, status and any repair at that moment.
- A stats row: health now, measured value, time healthy, checks raised, maintenance done and
  time to critical.
- A banner explaining any trend flag in plain words.
- A list of the maintenance performed in the window.

Monitor-specific extras:
- **Top-Oil Temperature:** the temperature the thermal model expects for this load and
  weather, and shaded bands showing where the heat comes from: grey for load, amber for a
  cooling fault, red for low oil. It also shows radiator-cleaning cycles, cooling health,
  the weather-adjusted temperature and when the next cleaning is due.
- **Winding Temperature (WTI):** the 110 / 120 °C alarm and trip lines (in the Measured value
  view), the WTI the thermal model expects at this load (ambient + expected rise), and a weekly
  review of the rise analytics (excess over the model, insulation ageing). These are analytics
  only and never the trip.
- **Current:** the daily-peak trend line, with a dashed projection.
- **Oil Level:** refill cycles and how long each one lasted.
- In the Measured value view, monitors where higher is worse (all except Oil Level) have the
  axis inverted, so heading toward the limit always reads as a decline, as it does in the
  Health % view.

The graph follows the timeline. Its window ends at the playback hour, so playing the
timeline draws it live. Link options: `?metric=1..7` opens a graph, `?range=7d|30d|all`,
`?chart=health|value`.

---

## 5. Trend analytics (backend: `twin_iot/app/analytics/`)

These look across days and maintenance cycles rather than single readings. Settings are in
`twin_iot/config/analytics.yaml`.

| Analytic | Flag | How it's detected |
|---|---|---|
| Oil leak (`oil_leak.py`) | ACCELERATING_OIL_LEAK | Finds oil top-ups (a jump of 10 % or more on the gauge), fits the leak rate in each refill cycle, and flags when the rate grows by 15 % or more per cycle, 2 cycles in a row |
| Load growth (`load_growth.py`) | LOAD_GROWTH | Weekly review of the daily peaks over 28 days. Flags when overload hours keep rising week on week and the peak trend grows at 0.1 % of rated per day or more |
| Winding thermal (`winding.py`) | WINDING_EXCESS_HEATING | IEC 60076-7 thermal model of an ONAN transformer. Flags when the winding runs 1.5 K or more above what its load explains and the gap grows 2 weeks in a row. Also tracks insulation loss of life |
| Top-oil cooling (`top_oil.py`) | COOLING_DEGRADED, OIL_AGEING | Compares the measured top oil with the thermal model to get a cooling health %. Below 80 % it recommends radiator cleaning. If each cleaning recovers less and the interval keeps shrinking, it flags oil ageing |

Each flag is added to the maintenance sheet and **pinned** at the top of the sheet in the
dashboard.

---

## 6. Maintenance sheet (right panel)

- One row per check raised: check number, monitor, signal, group, recommended action, when it
  opened, and whether it is still open or was resolved (and after how many hours).
- It follows the playback hour, so it shows only the checks raised up to that point, newest
  first.
- **Filters:** All, Precision measurement or Transformer.
- The latest trend flag is **pinned** on top.
- **CSV** link: downloads the whole sheet.
- Voltage deviations are left out of the sheet. They are supply-quality events for the
  utility, not transformer maintenance.

The recommended actions are in `twin_iot/config/maintenance_actions.yaml`.

---

## 7. Timeline (bottom panel)

| Control | What it does |
|---|---|
| ▶ / ❚❚ | Play or pause |
| ⏮ / ⏭ | Back or forward one day |
| **1 hour every [2] s** | Base tick rate: seconds of playback per hour of data (default 2 s, so 90 days take 72 minutes). Accepts 0.01 to 60 s |
| **1× / 2× / 4× / 0.5×** | Fast-forward or slow down from the base rate. For example, 2 s at 2× plays 1 hour every second |
| Time bar | Click or drag to jump to any hour |
| Colour strip | The worst status for each hour, across all 90 days |

The header shows the playback clock and the day (for example "Day 64 of 90"). Below the bar,
the dashboard lists any invalid inputs for that hour. Link option: `#t=960` opens at hour 960.

---

## 8. Data panel (dropdown, right)

Drop a CSV of hourly readings, or click to choose one. Every row is validated and evaluated
exactly like a live reading. The result line shows how many rows were stored and rejected,
and why. The dashboard then reloads with the new data.

The CSV needs a `timestamp` column (ISO format) plus the input columns:
`current_r/y/b`, `reference_current_r/y/b`, `voltage_r/y/b`, `reference_voltage_r/y/b`,
`oil_level`, `top_oil_temperature`, `ambient_temperature`, `winding_temperature_r/y/b`, and
optionally `transformer_id`. To rename columns, edit `twin_iot/config/column_mapping.yaml`.

**Assistant (dropdown):** a placeholder for the planned LLM + RAG assistant. It is not wired
up yet.

---

## 9. Backend (`twin_iot/`)

### Processing pipeline
Reading → **validation** (`config/validation.yaml`: each input must be a finite number in a
plausible range; bad values are dropped and reported as OUT_OF_RANGE, NOT_NUMERIC,
NOT_FINITE, MISSING or GAP) → the **8 monitors** → **alerts** → **3D model selection** →
stored in PostgreSQL → **maintenance sheet** rebuilt.

### API
| Method | Path | Returns |
|---|---|---|
| GET | `/api/health` | API and database status |
| GET | `/api/transformers` | Transformers with readings and their date range |
| GET | `/api/iots` | The monitors and their limits |
| GET | `/api/timeline/{id}` | Every stored hour (statuses, values, time to critical, input issues, 3D view) plus repairs and all trend analytics. The dashboard's main data feed |
| GET | `/api/analytics/oil-leak/{id}` | Oil-leak analysis |
| GET | `/api/analytics/load-growth/{id}` | Load-growth analysis |
| GET | `/api/analytics/winding/{id}` | Winding thermal analysis |
| GET | `/api/analytics/top-oil/{id}` | Top-oil cooling analysis |
| GET | `/api/maintenance-events/{id}` | Maintenance actually performed (repairs) |
| GET | `/api/maintenance/{id}?format=json\|csv\|jsonl\|md` | The maintenance sheet. Filters: `until`, `iot`, `status`, `limit`. jsonl and md are for the planned RAG |
| GET | `/api/readings/{reading_id}` | One stored hour in full |
| GET | `/api/alerts/{id}?limit=50` | Recent alerts |
| POST | `/api/readings` | Ingest one live reading |
| POST | `/api/upload` | Upload a CSV (form fields: file, transformer_id, replace) |
| GET | `/api/twin3d/models` | The 3D views and what triggers them |
| GET | `/api/twin3d/state` | The current 3D view and recent calls |
| POST | `/api/twin3d/signal` | Asks the backend to switch the 3D view for a stored hour |
| GET | `/` | Redirects to `/dashboard/` |

The Blender bridge (`app/twin3d/blender_bridge.py`) records each view switch and answers
`PENDING_BLENDER` until the bpy code is added.

### Sample data (`scripts/simulate.py`)
```
python scripts/simulate.py [--days 90] [--seed 7] [--transformer T1] [--start 2026-07-01] [--mode degradation|random] [--keep]
```
- **degradation** (the default) produces realistic wear: each monitor degrades, crosses its
  limits and is repaired, with daily load and weather cycles. It also includes an
  accelerating oil leak, a winding cooling fault, radiator fouling with oil ageing, one
  emergency-overload episode (day 60, about 1.4 × rated load with a part-closed radiator valve,
  so the WTI passes 110 °C and then 120 °C for several hours before the load is shed), and
  supply-voltage swings. A small share of values is corrupted on purpose, so validation is
  exercised.
- **random** produces independent random values.

It also writes the maintenance sheet exports to `twin_iot/data/maintenance/`.

### Tests
```
cd twin_iot
python -m pytest -q
```
The tests cover the monitors, validation, the 3D view selection, the maintenance sheet, the
simulator and all four analytics. They need no database.

---

## 10. Running it locally

Needs Python 3.11+ and PostgreSQL 13+.

```
cd twin_iot
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt

initdb -D twin_pg -U twin -A trust
pg_ctl start -D twin_pg -o "-p 5434" -l twin_pg.log
createdb -h localhost -p 5434 -U twin twin_iot

python scripts/simulate.py
python -m uvicorn app.api.main:app --port 8020
```

Then open http://localhost:8020/dashboard/.

To use another database, set `TWIN_DATABASE_URL`, for example
`postgresql://twin@localhost:5436/postgres`. To serve another dashboard folder, set
`TWIN_DASHBOARD_DIR`. Docker is also available (`twin_iot/docker-compose.yml`); see
`twin_iot/README.md`.

After changing the dashboard's scripts, bump the `?v=` tag on their `<script>` lines in
`dashboard/index.html` so browsers load the new copies.
