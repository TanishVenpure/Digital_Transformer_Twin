"""Top-oil temperature insights: is the cooling system doing its job, why is the oil hot,
when is the next radiator cleaning due, and is the oil itself ageing?

The same IEC 60076-7 oil model as the winding review (app/analytics/winding.py) gives the top-oil
rise the load *should* produce:

  expected rise = Delta_theta_or * ((1 + R K^2) / (1 + R)) ** x, smoothed with tau_oil
  expected top-oil = ambient + expected rise

Comparing it with the measured rise gives six insights:

  I1 cooling health   = expected rise / measured rise (100 % = cooling as designed). Hours with
                        low oil level are left out (they are attributed separately, see I3).
  I2 cleaning cycles  = radiator cleanings (maintenance events, or a jump in cooling health) split
                        the data into cycles. Per cycle: interval, cooling health recovered after the
                        cleaning, health when cleaned. Recovered health falling cycle after cycle =
                        leftover excess that cleaning cannot remove -> OIL_AGEING (sludge / oxidation).
  I3 why it is hot    = measured top-oil = ambient + load rise + cooling fault + low-oil part, hourly.
  I4 time to limit    = cooling-health trend in the current cycle -> cleaning due date; daily-peak
                        top-oil and rise trends -> days to the alarm temperature and the rise limit.
  I5 winding check    = winding gradient excess (winding rise - measured top-oil rise - expected
                        gradient) vs top-oil excess, weekly -> cooling / oil problem vs internal ducts.
  I6 weather-adjusted = top-oil the transformer would reach at rated load on a reference day
                        (30 degC), weekly - independent of season and load.

COOLING_DEGRADED is raised once per cycle when the rolling 24 h cooling health drops below
`clean_below` (recommend radiator cleaning). Model constants are generic defaults: calibrate on the
first healthy days of real data (or the heat-run report) before trusting absolute values.
"""
import math
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import yaml

from app.alerts.configuration import PROJECT_ROOT
from app.analytics.winding import ThermalConfig, load_config as load_thermal_config

FLAG = "OIL_AGEING"
COOLING_FLAG = "COOLING_DEGRADED"


@dataclass(frozen=True)
class TopOilConfig:
    min_expected_rise: float = 8.0   # K: ignore hours where the load explains too little rise to compare
    low_oil_level: float = 25.0      # % of gauge: conservator nearly empty, radiator tops run dry (low oil heats the oil)
    clean_below: float = 80.0        # % cooling health -> COOLING_DEGRADED (recommend radiator cleaning)
    rolling_hours: int = 24
    recovery_skip_hours: int = 6     # oil settles after a cleaning (tau_oil ~ 3 h)
    recovery_hours: int = 48         # cooling health "recovered" = mean over this window
    jump_detect: float = 8.0         # % points day-on-day health rise = cleaning (when no maintenance log)
    min_recovery_drop: float = 1.5   # OIL_AGEING: recovered health falls >= 1.5 points per cycle ...
    consecutive: int = 2             # ... for 2 cycle-to-cycle steps in a row
    reference_ambient: float = 30.0  # I6: top-oil at rated load on a 30 degC day
    trend_days: int = 14             # I4: daily-peak trend window
    horizon_days: int = 60           # I4: forecasts beyond this are reported as "not within horizon"
    alarm_c: float = 95.0            # from thresholds.yaml (top_oil_temperature warning)
    trip_c: float = 105.0
    rise_limit: float = 40.0
    min_gradient_excess: float = 1.5  # I5: K of weekly excess counted as a problem


def load_config(path: Path | None = None, **overrides) -> TopOilConfig:
    path = path or PROJECT_ROOT / "config" / "analytics.yaml"
    data = {}
    if path.exists():
        data = (yaml.safe_load(path.read_text(encoding="utf-8")) or {}).get("top_oil") or {}
    return TopOilConfig(**{**data, **overrides})


def expected_components(loadings: list[float | None], thermal: ThermalConfig,
                        rms_loadings: list[float | None] | None = None) -> list[tuple[float, float] | None]:
    """Hourly (expected top-oil rise, expected hot-spot gradient) in K. The oil rise uses the RMS
    loading of the three phases (total losses); the gradient uses the hottest phase."""
    rms_loadings = rms_loadings or loadings
    out: list[tuple[float, float] | None] = []
    oil = grad = None
    a_oil = 1 - math.exp(-60 / thermal.tau_oil_min)
    a_w = 1 - math.exp(-60 / thermal.tau_winding_min)
    for loading, rms in zip(loadings, rms_loadings):
        if loading is None:
            out.append(None)
            continue
        k, kr = max(loading, 0.0) / 100, max(rms if rms is not None else loading, 0.0) / 100
        oil_ss = thermal.top_oil_rise_rated * ((1 + thermal.loss_ratio * kr * kr) / (1 + thermal.loss_ratio)) ** thermal.oil_exponent
        grad_ss = thermal.gradient_rated * k ** thermal.winding_exponent
        oil = oil_ss if oil is None else oil + (oil_ss - oil) * a_oil
        grad = grad_ss if grad is None else grad + (grad_ss - grad) * a_w
        out.append((oil, grad))
    return out


def _slope(xs: list[float], ys: list[float]) -> tuple[float, float] | None:
    """Least-squares (slope, intercept)."""
    n = len(xs)
    if n < 2:
        return None
    mx, my = sum(xs) / n, sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    if sxx == 0:
        return None
    slope = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / sxx
    return slope, my - slope * mx


def _days_to(level: float, slope: float, intercept: float, now: float, horizon: float) -> float | None:
    """Days from `now` until the line reaches `level` (None if not rising toward it within the horizon)."""
    if slope <= 0:
        return None
    current = intercept + slope * now
    days = 0.0 if current >= level else (level - current) / slope
    return round(days, 1) if days <= horizon else None


def analyse(rows: list[tuple], cleanings: list[datetime] | None = None,
            config: TopOilConfig | None = None, thermal: ThermalConfig | None = None) -> dict[str, Any]:
    """rows: (timestamp, top-oil degC, ambient degC, loading % hottest phase, RMS loading %, oil level %,
    winding hot-spot rise K) in time order; any value may be None. cleanings: radiator cleanings."""
    config, thermal = config or TopOilConfig(), thermal or ThermalConfig()
    out: dict[str, Any] = {"hourly": {}, "cycles": [], "weeks": [], "cooling_checks": [], "flags": [],
                           "forecast": None, "current": None, "clean_below": config.clean_below}
    if not rows:
        return out
    times = [r[0] for r in rows]
    comps = expected_components([r[3] for r in rows], thermal, [r[4] for r in rows])

    # --- I1 / I3: hourly comparison --------------------------------------------------------------
    ratio: list[float | None] = []        # measured rise / expected rise, normal-oil hours only
    expected_c, ambient_h, cooling_k, low_oil_k, health_h = [], [], [], [], []
    roll: list[tuple[int, float]] = []
    last_ratio, seen = 1.0, False   # without fresh comparable hours, keep the last estimate
    last_low: int | None = None      # last low-oil hour: the oil needs a few hours to cool after a top-up
    for i, ((t, oil_c, amb, _, _, level, _), comp) in enumerate(zip(rows, comps)):
        r = None
        if oil_c is not None and amb is not None and comp is not None:
            exp_rise, meas_rise = comp[0], oil_c - amb
            low = level is not None and level < config.low_oil_level
            if low:
                last_low = i
            settling = last_low is not None and i - last_low <= config.recovery_skip_hours
            if exp_rise >= config.min_expected_rise and not settling:
                r = meas_rise / exp_rise
            roll.append((i, r)) if r is not None else None
            while roll and roll[0][0] <= i - config.rolling_hours:
                roll.pop(0)
            if roll:
                last_ratio, seen = sum(x for _, x in roll) / len(roll), True
            # breakdown: ambient + load + cooling fault (+ low oil)
            fault = meas_rise - exp_rise
            cool = exp_rise * (last_ratio - 1) if settling else fault
            expected_c.append(round(amb + exp_rise, 2))
            ambient_h.append(round(amb, 2))
            cooling_k.append(round(cool, 2))
            low_oil_k.append(round(fault - cool, 2) if settling else 0.0)
        else:
            expected_c.append(None); ambient_h.append(None); cooling_k.append(None); low_oil_k.append(None)
        ratio.append(r)
        health_h.append(round(100 / last_ratio, 1) if seen else None)
    out["hourly"] = {"expected": expected_c, "ambient": ambient_h, "cooling_k": cooling_k,
                     "low_oil_k": low_oil_k, "cooling_health": health_h}

    # --- I2: cleaning cycles ---------------------------------------------------------------------
    if cleanings is None:   # no maintenance log: a day-on-day jump in cooling health marks a cleaning
        cleanings = []
        daily: dict[datetime, list[float]] = {}
        for t, r in zip(times, ratio):
            if r:
                daily.setdefault(t.replace(hour=0, minute=0, second=0, microsecond=0), []).append(100 / r)
        days = sorted(daily)
        for a, b in zip(days, days[1:]):
            if sum(daily[b]) / len(daily[b]) - sum(daily[a]) / len(daily[a]) >= config.jump_detect:
                cleanings.append(b)
    cleanings = sorted(c for c in cleanings if times[0] < c <= times[-1])
    bounds = [times[0], *cleanings, times[-1] + timedelta(hours=1)]
    data_end = times[-1] + timedelta(hours=1)

    def mean_health(a: datetime, b: datetime, first: int | None = None, last: int | None = None) -> float | None:
        """Mean cooling health over [a, b); optionally only the first / last N comparable hours."""
        vals = [100 / r for t, r in zip(times, ratio) if r and a <= t < b]
        vals = vals[:first] if first else vals[-last:] if last else vals
        return sum(vals) / len(vals) if len(vals) >= 6 else None

    recovered_list: list[float] = []
    for k, (a, b) in enumerate(zip(bounds, bounds[1:])):
        cleaned = k > 0
        rec_from = a + timedelta(hours=config.recovery_skip_hours if cleaned else 0)
        # first `recovery_hours` comparable hours after the cleaning (low-oil hours are skipped)
        recovered = mean_health(rec_from, b, first=config.recovery_hours)
        end_health = mean_health(a, b, last=config.rolling_hours)
        complete = b < data_end
        cycle = {
            "start": a.isoformat(), "end": b.isoformat() if complete else None,
            "started_by_cleaning": cleaned,
            "interval_days": round((b - a).total_seconds() / 86400, 1) if complete and cleaned else None,
            "recovered_health": round(recovered, 1) if recovered is not None else None,
            "health_when_cleaned": round(end_health, 1) if complete and end_health is not None else None,
            "leftover_k_at_rated": round(thermal.top_oil_rise_rated * (100 / recovered - 1), 1) if recovered else None,
        }
        out["cycles"].append(cycle)
        # COOLING_DEGRADED: first hour in this cycle with rolling health below the limit
        for t, h in zip(times, health_h):
            if a + timedelta(hours=config.rolling_hours) <= t < b and h is not None and h < config.clean_below:
                out["cooling_checks"].append({
                    "flag": COOLING_FLAG, "detected_at": t.isoformat(), "first": not out["cooling_checks"],
                    "value": h, "unit": "% cooling health", "trigger": "by the cooling-health check",
                    "message": (f"Top-oil cooling at {h:.0f} % of design (24 h mean): at this load and ambient the "
                                f"oil runs {thermal.top_oil_rise_rated * (100 / h - 1):.1f} K hotter at rated load than "
                                f"the thermal model expects. Likely fouled radiators or a cooling fault; clean the radiators."),
                })
                break
        # OIL_AGEING: recovered health keeps falling after successive cleanings
        if cleaned and recovered is not None:
            recovered_list.append(recovered)
            recent = recovered_list[-(config.consecutive + 1):]
            drops = [x - y for x, y in zip(recent, recent[1:])]
            valid = [t for t, r in zip(times, ratio) if r and rec_from <= t < b]
            at = valid[min(len(valid), config.recovery_hours) - 1] + timedelta(hours=1)
            if (len(recent) == config.consecutive + 1 and all(d >= config.min_recovery_drop for d in drops)
                    and at <= data_end):
                intervals = [c["interval_days"] for c in out["cycles"] if c["interval_days"]]
                leftover = thermal.top_oil_rise_rated * (100 / recovered - 1)
                out["flags"].append({
                    "flag": FLAG, "detected_at": min(at, data_end).isoformat(), "first": not out["flags"],
                    "value": round(recovered, 1), "unit": "% cooling health after cleaning",
                    "trigger": "in the cleaning-cycle review", "recovered_by_cycle": [round(x, 1) for x in recent],
                    "intervals_days": intervals[-4:],
                    "message": (f"Oil ageing: radiator cleaning recovers less each time (cooling health after cleaning "
                                f"{' -> '.join(f'{x:.0f}' for x in recent)} %), leaving {leftover:.1f} K at rated load "
                                f"that cleaning cannot remove"
                                + (f"; intervals between cleanings {' -> '.join(f'{x:.0f}' for x in intervals[-4:])} days"
                                   if len(intervals) >= 2 else "")
                                + ". Points to sludge / oil oxidation: test the oil (acidity, interfacial tension, "
                                  "moisture, DGA) and plan oil filtration or reclamation."),
                })

    # --- I5 / I6: weekly reviews -----------------------------------------------------------------
    start = times[0].replace(hour=0, minute=0, second=0, microsecond=0)
    weeks: dict[int, dict[str, list[float]]] = {}
    for (t, oil_c, amb, _, _, level, wind), comp, r in zip(rows, comps, ratio):
        w = weeks.setdefault(int((t - start).total_seconds() // (7 * 86400)), {"ratio": [], "oil_x": [], "grad_x": [], "hours": []})
        w["hours"].append(1)
        if r:
            w["ratio"].append(r)
        if comp and oil_c is not None and amb is not None and r:
            w["oil_x"].append(oil_c - amb - comp[0])
            if wind is not None:
                w["grad_x"].append(wind - (oil_c - amb) - comp[1])
    normalised: list[float] = []
    for k in sorted(weeks):
        w = weeks[k]
        mean = lambda v: sum(v) / len(v) if v else None  # noqa: E731
        r, ox, gx = mean(w["ratio"]), mean(w["oil_x"]), mean(w["grad_x"])
        norm = config.reference_ambient + thermal.top_oil_rise_rated * r if r else None
        oil_bad = ox is not None and ox >= config.min_gradient_excess
        duct_bad = gx is not None and gx >= config.min_gradient_excess
        diagnosis = ("both" if oil_bad and duct_bad else "cooling" if oil_bad else "ducts" if duct_bad else "healthy")
        if norm is not None and len(w["hours"]) >= 7 * 24 * 0.8:
            normalised.append(norm)
        ws = start + timedelta(days=7 * k)
        out["weeks"].append({
            "start": ws.isoformat(), "end": min(ws + timedelta(days=7), data_end).isoformat(),
            "complete": len(w["hours"]) >= 7 * 24 * 0.8,
            "cooling_health": round(100 / r, 1) if r else None,
            "normalised_top_oil_c": round(norm, 1) if norm is not None else None,
            "top_oil_excess_k": round(ox, 2) if ox is not None else None,
            "winding_gradient_excess_k": round(gx, 2) if gx is not None else None,
            "diagnosis": diagnosis,
        })

    # --- I4: forecasts from the latest data ------------------------------------------------------
    now_t = times[-1]
    now_d = (now_t - times[0]).total_seconds() / 86400
    fc: dict[str, Any] = {"at": now_t.isoformat()}
    last_clean = cleanings[-1] if cleanings else times[0]
    pts = [((t - times[0]).total_seconds() / 86400, 100 / r) for t, r in zip(times, ratio)
           if r and t >= last_clean + timedelta(hours=config.recovery_skip_hours)]
    fit = _slope([p[0] for p in pts], [p[1] for p in pts]) if len(pts) >= 72 else None
    if fit:
        slope, icpt = fit
        fc["cooling_trend_per_week"] = round(slope * 7, 2)
        fc["cleaning_due_days"] = (None if slope >= 0 else
                                   round(max(0.0, (icpt + slope * now_d - config.clean_below) / -slope), 1))
        if fc["cleaning_due_days"] is not None and fc["cleaning_due_days"] > config.horizon_days:
            fc["cleaning_due_days"] = None
    daily_peak: dict[datetime, tuple[float, float]] = {}
    for t, oil_c, amb, *_ in rows:
        if oil_c is None or amb is None or t < now_t - timedelta(days=config.trend_days):
            continue
        d = t.replace(hour=0, minute=0, second=0, microsecond=0)
        p = daily_peak.get(d, (-1e9, -1e9))
        daily_peak[d] = (max(p[0], oil_c), max(p[1], oil_c - amb))
    if len(daily_peak) >= 7:
        xs = [(d - times[0]).total_seconds() / 86400 + 0.5 for d in sorted(daily_peak)]
        for key, idx, level in (("days_to_alarm", 0, config.alarm_c), ("days_to_trip", 0, config.trip_c),
                                ("days_to_rise_limit", 1, config.rise_limit)):
            f = _slope(xs, [daily_peak[d][idx] for d in sorted(daily_peak)])
            fc[key] = _days_to(level, *f, now_d, config.horizon_days) if f else None
        f = _slope(xs, [daily_peak[d][0] for d in sorted(daily_peak)])
        fc["peak_trend_per_week"] = round(f[0] * 7, 2) if f else None
    out["forecast"] = fc
    out["current"] = {"cooling_health": health_h[-1], "recovered_by_cycle": [round(x, 1) for x in recovered_list],
                      "intervals_days": [c["interval_days"] for c in out["cycles"] if c["interval_days"]]}
    return out
