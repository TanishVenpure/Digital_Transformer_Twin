# Maintenance Risk Index

Policy version: `maintenance-risk-0.1.0-unapproved`
Implementation: `_assess_risk()` in [twin_iot/app/api/assistant.py](twin_iot/app/api/assistant.py)
Endpoint: `GET /api/assistant/risk?transformer_id=CPRI-630&until=<ISO timestamp>&until2=<ISO date>`
Data: the twin2 simulated datasets only (`twin_iot/data/twin2`, built by `scripts/simulate_twin2.py`)

> **Status: prototype, not approved by engineering review.** The index summarises recorded
> maintenance checks. It is not a validated engineering standard and does not authorise
> operation, shutdown, repair or return to service. Missing records are not evidence of
> good condition.

## What it is

A single 0–100 score for transformer `CPRI-630`, computed from the **maintenance check sheet**
built from the twin2 events (`app/twin2/sheet.py`), not from raw readings. The dashboard has two
timebars and sends both cursors:

- `until`: the timebar 1 minute (one day of fast signals, 2026-07-15, 1 440 minutes).
- `until2`: the timebar 2 day (one year of daily data, 2026-07-01 to 2027-06-30).

Each check belongs to one timebar and is judged at that timebar's cursor: it counts only once
its own cursor has reached it, it is still open if its resolution comes after the cursor, and
its `age_days` is measured from that cursor. Omitted cursors default to the end of the timebar.

How a check is opened and resolved:

| | Timebar 1 (one day) | Timebar 2 (one year) |
|---|---|---|
| Opened | the recorded event start | the event day |
| Resolved | the recorded condition ended before the end of the day (from the next minute) | the matching follow-up action: `<TYPE>_RECALIBRATED`, or `OIL_LEAK_REPAIRED` for `OIL_LEAK` and `MINIMUM_OIL_LEVEL` |
| Recommended action | the recorded event message | the recorded event message |

Follow-up actions are attached to the check they close, not counted as checks. With the current
data there are 37 checks: 26 on timebar 1 and 11 on timebar 2 (plus 10 follow-up actions). The event
level `MAINTENANCE` is recorded as `MAINTENANCE_REQUIRED`.

"Faults" are all checks whose signal is not `NORMAL`:

| Signal | Severity score |
|---|---|
| `WARNING` | 25 |
| `PREDICTED_MAINTENANCE` | 50 |
| `MAINTENANCE_REQUIRED` | 75 |
| `CRITICAL` | 100 |

## Formula

```
Risk Index = Σ (weight × factor_score) / Σ (weights of available factors)
```

Each factor scores 0 (no concern) to 100 (maximum concern). `age_days` is the age of a check
at the playback time. Half-life `H = 365` days.

| # | Factor | Weight | Score |
|---|---|---|---|
| 1 | Severity | 30 | `max over faults of severity × 0.5^(age_days / H)` — no decay for an **unresolved** `MAINTENANCE_REQUIRED` or `CRITICAL` check |
| 2 | Recurrence | 25 | `min(100, 100 × (n − 1) / 3)`, `n` = most fault checks on a single monitor of one timebar (saturates at 4+) |
| 3 | Recency | 20 | `100 × 0.5^(age_days of newest fault / H)` (today = 100, one year old = 50) |
| 4 | Resolution | 15 | `100 × open faults / all faults` |
| 5 | Frequency trend | 10 | `min(100, 50 × recent / prior)`, where recent = faults in last 365 days, prior = faults 365–730 days ago. If prior = 0 → 100. Unavailable if fewer than 4 faults. |

If the available weights sum to **less than 60**, no score is produced and the result is
`INSUFFICIENT_EVIDENCE`.

## Category

| Condition | Category |
|---|---|
| Index ≥ 70 | `HIGH_MAINTENANCE_RISK` |
| 40 ≤ Index < 70 | `MODERATE_MAINTENANCE_RISK` |
| Index < 40 | `LOW_RECORDED_MAINTENANCE_RISK` |
| Any unresolved `MAINTENANCE_REQUIRED` / `CRITICAL` check | Forced to `HIGH_MAINTENANCE_RISK` (flag `UNRESOLVED_SERIOUS_ISSUE`) |

The recommended action shown is the `recommended_action` of the highest-severity unresolved check.
Every result also carries the review flag `POLICY_NOT_APPROVED`.

## Worked example (CPRI-630, timebar 1 at 15:00 UTC, timebar 2 at 2027-02-25)

23 fault checks are visible at these cursors, and 9 of them are open. Check #34 is the most severe open one: `CRITICAL`, Oil volume (leak), flag `MINIMUM_OIL_LEVEL`, raised on 2027-02-21.

| Factor | Score | Reason | Weight × score |
|---|---|---|---|
| Severity | 100.0 | #34 is unresolved `CRITICAL`, so no decay | 30 × 100 = 3000 |
| Recurrence | 100.0 | One monitor has 5 checks | 25 × 100 = 2500 |
| Recency | 100.0 | The newest timebar 1 check is 0 days old | 20 × 100 = 2000 |
| Resolution | 39.1 | 9 of 23 faults open | 15 × 39.1 = 586.5 |
| Frequency trend | 100.0 | All checks fall within the last year, with no prior-year window | 10 × 100 = 1000 |

```
Index = (3000 + 2500 + 2000 + 586.5 + 1000) / 100 = 90.9  →  HIGH_MAINTENANCE_RISK
```

The recommended action comes from #34: *Measured oil volume 286.6 L is below the minimum 288 L.*

## Known limitations

- **Two time scales, one formula.** Timebar 1 spans one day and timebar 2 one year, but the half-life
  and the trend window are both one year. Timebar 1 checks never decay, and Frequency trend is always
  100 because there is no prior year. About 30 % of the weight barely moves, so the index sits high.
- **"Resolved" on timebar 1 means only that the recorded condition ended.** No repair is recorded on
  that timebar.
- **Synthetic data.** Every result carries `synthetic: true`. The datasets are simulated.
- **Recurrence saturates quickly.** Four checks on one monitor give the maximum score,
  regardless of how far apart they are or whether they were resolved.
- **Record-based only.** The index reflects what was recorded as a check; it does not look at
  raw readings, and gaps in records lower the score rather than raise uncertainty.
- **Unapproved policy.** Weights, severity scores, thresholds and half-life are prototype
  values pending engineering review.
