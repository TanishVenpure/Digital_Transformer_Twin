# Digital Transformer Twin: features

A digital twin of the CPRI 630 kVA, 11 / 0.433 kV distribution transformer. The dashboard plays
**two timebars**: one day of fast signals, with voltage and current every 20 ms, thermal every
1 s, and pressure and ambient every 1 min, and one year of daily readings for oil leakage and
the precision devices. A 3D model shows which part needs attention, every signal is listed with
its time, and every variable has a graph.

The project has two parts:

| Part | Folder | What it is |
|---|---|---|
| Dashboard | `dashboard/` | The web page: 3D model, monitor cards, graphs, signal list, two timebars (`timebars.js`) |
| Backend | `twin_iot/` | FastAPI: the two-timebar model (`app/twin2/`) and the earlier hourly 8-monitor pipeline on PostgreSQL; serves the dashboard at `/dashboard/` |

---

## 1. The dashboard at a glance

The 3D transformer fills the screen on a black background. Everything else floats around it
as frosted-glass panels:

| Where | Panel |
|---|---|
| Top | Header: transformer rating, voltage class and rated currents, timebar 1 clock and timebar 2 date, overall status |
| Top centre | Alarm card: which monitor the 3D view is focused on, and its status |
| Left | Monitor cards, grouped by timebar (9 fast-signal monitors, 4 daily monitors) |
| Right | Assistant (dropdown), Signals & maintenance, Data (dropdown with the datasets) |
| Bottom centre | Model controls, then the two timebars |
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

## 3. Two timebars

The dashboard plays two independent timelines of the **CPRI transformer** (630 kVA, 11 / 0.433 kV,
ONAN). Each has its own player at the bottom:

| Control | What it does |
|---|---|
| ▶ / ❚❚ | Play or pause. The player ticks every **20 ms** |
| ⏮ / ⏭ | Back or forward one hour (timebar 1) or one week (timebar 2) |
| **1 minute every [0.25] s** / **1 day every [0.5] s** | Base tick rate: seconds of playback per unit of data |
| **1× / 2× / 4× / 0.5×** | Fast-forward or slow down from the base rate |
| Bar and colour strip | Click or drag to jump; the strip shows the worst status at each point |

All the constants are in `twin_iot/config/twin2.yaml`. Values marked **ASSUMED** there aren't in
the spec and must be replaced with the transformer's own data.

### Timebar 1: one day of fast signals (`twin_iot/app/twin2/timebar1.py`)

| Stream | Sampling |
|---|---|
| Primary and secondary voltage and current (3 phases) | Every **20 ms** (per-cycle RMS). Full rate in a window around each event, plus a 1 s stream of each second's min and max |
| Top oil, WTI, measured winding, IEC model | Every **1 s** |
| Ambient temperature | Every **1 min** |
| Tank pressure | Every **1 min** |
| Precision devices (current CT, voltage PT, temperature probe) | Every **1 min** |

**Rules:**

| Monitor | Formula | Signal |
|---|---|---|
| Loading (overload) | I_rated = S / (√3 · V_LL), which is 33.07 A primary and 840 A secondary. Loading = I / I_rated × 100 | **> 100 % warning**, **> 120 % trip** (the breaker opens after 2 s) |
| Voltage | Deviation = (V − V_nominal) / V_nominal × 100 | **MV / LV class:** primary winding, ±6 %. **HV class:** secondary winding, +6 % / −9 %. Set by `voltage_class` |
| Top oil (OTI) | Absolute T_O | **95 °C alarm**, **105 °C trip** |
| Winding (WTI) | WTI = T_O + thermal image (its own gradient and time constant) | **110 °C alarm**, **120 °C trip** |
| Thermal trip | TRIP = OTI trip **OR** WTI trip | OTI and WTI are never averaged |
| Precision devices | Per phase, \|measured − reference\| / reference × 100 | **> 1 %** on any phase gives maintenance for that device |
| Tank pressure | dV_oil = β·V_oil0·(T1 − T0), V_gas1 = V_gas0 − dV_oil (invalid if ≤ 0), P1 = P0·(T_gas1 / T_gas0)·(V_gas0 / V_gas1) in kelvin, gauge = P_abs − P_atm | **185 kPa warning**, or the 30-minute trend reaching the limit. **230 kPa limit** (both ASSUMED) |

**Thermal analytics (IEC 60076-7). These never trip:**
- K = I / I_rated. Ultimate top-oil rise ΔT_Ou = ΔT_Or·((1 + R·K²) / (1 + R))^x, with a first-order
  lag τ_O. T_O is already absolute, so ΔT_O = T_O − T_A and ambient is never added twice.
- Hot spot T_H = T_O + ΔT_HO, where ΔT_HO,u = H·g_r·K^y with the winding lag. T_H isn't the WTI and
  isn't the measured winding.
- Winding thermal residual R_W = measured winding − expected winding.
  **WINDING_EXCESS_HEATING** is flagged when R_W is ≥ 1.5 K and has grown 2 windows in a row. The
  windows are **30 minutes, scaled down** from the weekly review because this timebar is one day.
- Ageing V = 2^((T_H − 98) / 6), L_EQ = Σ V·dt (equivalent hours) and ageing ratio = L_EQ / t.
  **ACCELERATED_THERMAL_AGEING** is flagged when the mean V over 60 min is above 1. This is never
  shown as "remaining life".
- **COOLING_DEGRADATION** is flagged when the measured top-oil rise is more than 1.15 × the model
  over 30 min. **Top-oil rise > 40 K** is an analytics warning.
- After an outage, the model restarts from the measured oil state (the IEC measured-top-oil
  method).

**The simulated day (2026-07-15, UTC).** Every condition appears at least once:

| Time | What happens | Signals |
|---|---|---|
| 03:20 | Light-load over-voltage: primary +7.3 % for 88 s | OVERVOLTAGE |
| 05:12 | Winding probe drop-out for 20 s | Data error; the residual shows no data and nothing trips |
| 07:45:10 | 2 s voltage dip, primary −8.8 % | UNDERVOLTAGE |
| 10:08 to 10:21 | A neighbouring feeder is transferred on (112 %), then a 128 % surge | OVERLOAD warning, then overload trip. The breaker opens; out until 10:40 |
| 11:00 to 13:43 | Radiator fans fail with a valve part-closed, and a winding cooling duct blocks | COOLING_DEGRADATION, top-oil rise > 40 K, WINDING_EXCESS_HEATING, ACCELERATED_THERMAL_AGEING, OTI alarm, WTI alarm, PRESSURE warning, then the **OTI trip** at 105 °C |
| 13:43 | Tank pressure peaks at 201.8 kPa: a **near failure caught** 28 kPa below the limit | PRESSURE_WARNING |
| 17:30 to 19:02 | Evening peak at about 117 % with one radiator bank still out | OVERLOAD warning (below the trip), WTI alarm, then the **WTI trip** at 120 °C |
| 20:42 | The current device's Y phase drifts past 1 % | PRECISION_CURRENT (maintained on timebar 2) |

### Timebar 2: one year of daily readings (`twin_iot/app/twin2/timebar2.py`)

| Monitor | Formula | Signal |
|---|---|---|
| Oil volume (leak) | V_exp,noleak = V_ref·[1 + β(T_oil − T_ref)], L_cum = Σ q_leak·dt, R = V_meas − V_exp,noleak ≈ −L_cum | **OIL_LEAK** when R < −3 L. **MINIMUM_OIL_LEVEL** when V_meas < 288 L |
| Precision devices (CT, PT, temperature) | Daily per-phase deviation from the reference | **> 1 %**, then maintenance (recalibration) a few days later |

The simulated year (2026-07-01 to 2027-06-30) has two leaks. The first is caught by the
tolerance alarm and repaired. The second reaches the **minimum oil level** before it's repaired.
Each precision device fails 2 or 3 times, about every 4 months, and is recalibrated each time.

---

## 4. Monitor cards and graphs

The left column has one card per monitor, grouped by timebar. Each card shows the value at its
timebar's cursor and its status: Normal, Warning, Maintenance, Critical or **Out of service**
(while the breaker is open).

Click a card, or a signal in the right-hand list, to open its graphs. **Every variable has a
graph:**
- **Loading:** max and mean per minute, with the 100 / 120 % lines. A **20 ms detail** chart
  for the overload onset and the overload trip.
- **Voltage:** the watched winding's min and max deviation with its limits, the other
  winding's deviation for reference, and 20 ms detail for the over-voltage and the dip.
- **Top oil:** measured vs the IEC expected value, plus ambient, with the 95 / 105 °C lines.
  Also the top-oil rise measured vs expected, with the 40 K line.
- **WTI:** the WTI, the estimated hot spot and the measured winding, with the 110 / 120 °C lines.
- **Thermal analytics:** the residual R_W, the ageing rate V, L_EQ, the ageing ratio and the
  load factor K.
- **Tank pressure:** absolute and gauge pressure with the warning and limit lines, plus the
  oil expansion and remaining headspace.
- **Precision devices:** the R, Y and B deviation with the 1 % line, on both timebars.
- **Oil volume:** measured vs expected, the residual vs −L_cum with the tolerance line, the
  minimum-level line, and the oil temperature.

Each graph marks every signal raised (▼), the cursor (white line) and the out-of-service
periods (shaded). Choose the **whole timebar** or **±1 hour / ±30 days** around the cursor.

The **3D model** takes its colours from the cards: current device and loading on the CTs,
voltage device and voltage on the LV leads, oil on the gauge, OTI and pressure on the top-oil
sensor, WTI, thermal analytics and the temperature device on the winding sensors. The camera
focuses on the worst monitor.

---

## 5. Signals and maintenance (right panel)

Every warning, alarm, trip, analytics flag and maintenance action up to each cursor, newest
first. You can filter by timebar, and click a signal to open its monitor's graphs. **CSV**
downloads them all.

---

## 6. Datasets (Data dropdown)

The generated files are in `twin_iot/data/twin2/`, and the dashboard links to them:

| File | Contents |
|---|---|
| `timebar1_electrical_1s.csv` | Energised flag, loading %, primary and secondary deviation (min / max of each second's 50 cycles), conditions |
| `timebar1_electrical_20ms.csv` | Per-cycle loading and voltage deviation in each event window |
| `timebar1_thermal_1s.csv` | load_factor, top_oil_rise_measured / expected, hotspot_temperature_estimated, WTI, winding_thermal_residual, relative_ageing_rate, equivalent_ageing_hours, ageing_ratio, the oti / wti alarm and trip conditions, winding_excess_heating, accelerated_thermal_ageing, cooling_degradation |
| `timebar1_ambient_1min.csv`, `timebar1_pressure_1min.csv`, `timebar1_precision_1min.csv` | Ambient. Pressure (oil_expansion_volume, headspace_volume_final, pressure_final_abs, pressure_rise, validity_flag). Precision deviations |
| `timebar2_daily.csv` | The oil volume balance and the precision devices, one row a day |
| `events.csv` | Every signal of both timebars |

Regenerate them with `python scripts/simulate_twin2.py`, or `POST /api/twin2/rebuild`.

---

## 7. Hourly 8-monitor backend (`twin_iot/`, still available through the API)

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
| GET | `/api/twin2/timebar/1` or `/2` | Two-timebar payloads the dashboard plays (built on first request) |
| POST | `/api/twin2/rebuild` | Re-run both simulations from `config/twin2.yaml` |
| GET | `/api/twin2/files/{name}.csv` | Download a generated dataset |
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

## 8. Running it locally

Needs Python 3.11+ and PostgreSQL 13+.

```
cd twin_iot
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt

initdb -D twin_pg -U twin -A trust
pg_ctl start -D twin_pg -o "-p 5434" -l twin_pg.log
createdb -h localhost -p 5434 -U twin twin_iot

python scripts/simulate.py            # hourly 8-monitor data (PostgreSQL)
python scripts/simulate_twin2.py      # two-timebar datasets (no database needed)
python -m uvicorn app.api.main:app --port 8020
```

Then open http://localhost:8020/dashboard/.

To use another database, set `TWIN_DATABASE_URL`, for example
`postgresql://twin@localhost:5436/postgres`. To serve another dashboard folder, set
`TWIN_DASHBOARD_DIR`. Docker is also available (`twin_iot/docker-compose.yml`); see
`twin_iot/README.md`.

After changing the dashboard's scripts, bump the `?v=` tag on their `<script>` lines in
`dashboard/index.html` so browsers load the new copies.
