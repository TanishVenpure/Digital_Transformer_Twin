"""Health index of the CPRI transformer: every second on timebar 1, every day on timebar 2.

Each part of the transformer gets a sub-score 0-100 from its own measurement through piecewise-linear points
(config/twin2.yaml health): 100 = as designed, ~60 at its alarm / warning limit, ~20-25 at its trip limit. The
health index is the weighted mean of the sub-scores that have data, capped at (worst sub-score + cap_margin) so
one failing part is never averaged away by healthy ones:

  HI = min( sum(w_i * s_i) / sum(w_i),  min(s_i) + cap_margin )

Bands: Good >= 85, Fair >= 70, Poor >= 50, Critical below. The precision devices (CT, PT, temperature probe) do
not enter the index: a drifting instrument does not make the transformer less healthy, it makes the readings
less trustworthy, so it is reported separately as data confidence.

Timebar 1 (per second): loading, voltage (worst winding against its own band), top oil (OTI), WTI, insulation
ageing (log2 of the IEC rate V), winding excess over the IEC model and cooling (measured / expected top-oil rise,
both 10-minute averages), tank pressure, and protection (a short circuit costs force % x factor, recovering over
fault_recovery_min; a breaker failure holds it down until the breaker is overhauled). Out of service, loading
and voltage have no data and drop out; the thermal parts still describe the transformer.

Timebar 2 (per day): oil (leak residual and margin above the minimum volume), mechanical (severe and minor
through-faults since the last winding test), thermal (daily mean oil temperature; on the timebar-1 day the mean of
each hour's worst per-second health index on that day, so its trips and faults show in the year).
"""
import math
from bisect import bisect_left
from typing import Any

SUB1 = ("loading", "voltage", "top_oil", "wti", "ageing", "winding_excess", "cooling", "pressure", "protection")
SUB2 = ("oil", "mechanical", "thermal")
THERMAL1 = ("top_oil", "wti", "ageing", "winding_excess", "cooling")


def score(value: float | None, points: list[list[float]]) -> float | None:
    """Piecewise-linear score through [value, score] points (either direction), clamped at the ends."""
    if value is None or not math.isfinite(value):
        return None
    pts = sorted((float(x), float(y)) for x, y in points)
    xs = [p[0] for p in pts]
    if value <= xs[0]:
        return pts[0][1]
    if value >= xs[-1]:
        return pts[-1][1]
    k = bisect_left(xs, value)
    (x0, y0), (x1, y1) = pts[k - 1], pts[k]
    return y0 + (y1 - y0) * (value - x0) / (x1 - x0)


def combine(subs: dict[str, float | None], weights: dict[str, float], cap_margin: float) -> float | None:
    have = {k: v for k, v in subs.items() if v is not None and weights.get(k)}
    if not have:
        return None
    mean = sum(weights[k] * v for k, v in have.items()) / sum(weights[k] for k in have)
    return min(mean, min(have.values()) + cap_margin)


def band(hi: float | None, h: dict[str, Any]) -> str:
    if hi is None:
        return "NO_DATA"
    if hi >= h["good"]:
        return "GOOD"
    if hi >= h["fair"]:
        return "FAIR"
    return "POOR" if hi >= h["poor"] else "CRITICAL"


BAND_STATUS = {"GOOD": "NORMAL", "FAIR": "WARNING", "POOR": "MAINTENANCE_REQUIRED", "CRITICAL": "CRITICAL",
               "NO_DATA": "NO_DATA"}


def _ema(values: list[float | None], tau_s: float) -> list[float | None]:
    a = 1 - math.exp(-1 / tau_s)
    out, m = [], None
    for v in values:
        if v is not None:
            m = v if m is None else m + (v - m) * a
        out.append(m)
    return out


def timebar1(sec: dict[str, list], pressure: list[dict], precision: list[dict], faults: list[dict],
             cfg: dict[str, Any]) -> dict[str, Any]:
    """Per-second sub-scores, health index and data confidence."""
    h = cfg["health"]
    c = h["timebar1"]
    pts, w = c["points"], c["weights"]
    lim = cfg["electrical"]["voltage_limits"]
    n = len(sec["top_oil"])
    ratio = [(sec["top_oil_rise_measured"][i] / sec["top_oil_rise_expected"][i])
             if sec["energized"][i] and (sec["top_oil_rise_expected"][i] or 0) >= 8 else None for i in range(n)]
    cool_avg = _ema(ratio, 600)
    excess_avg = _ema([r if sec["energized"][i] else 0.0 for i, r in enumerate(sec["winding_thermal_residual"])], 600)

    # protection: per-second score from the recorded short circuits
    protection = [100.0] * n
    for fl in faults:
        det, eff = fl.get("detection"), fl.get("effects")
        if not det:
            continue
        t0 = int(fl["t"])
        hit = eff["force_pct_of_design"] * c["fault_force_factor"]
        for i in range(t0, n):
            protection[i] = min(protection[i], 100 - hit * math.exp(-(i - t0) / (c["fault_recovery_min"] * 60)))
        if det["breaker_failure"]:
            back = next((i for i in range(t0 + 1, n) if sec["energized"][i]), n)
            for i in range(t0, n):
                protection[i] = min(protection[i], c["breaker_failure_score"] if i < back else c["breaker_overhaul_score"])

    subs = {k: [] for k in SUB1}
    hi, conf = [], []
    for i in range(n):
        on = sec["energized"][i]
        s = {}
        s["loading"] = score(sec["loading_max"][i], pts["loading"]) if on else None
        if on and sec["v_pri_min"][i] is not None:
            frac = max(max(sec["v_pri_max"][i], 0) / lim["primary"]["high_pct"], max(-sec["v_pri_min"][i], 0) / lim["primary"]["low_pct"],
                       max(sec["v_sec_max"][i], 0) / lim["secondary"]["high_pct"], max(-sec["v_sec_min"][i], 0) / lim["secondary"]["low_pct"])
            s["voltage"] = score(frac, pts["voltage"])
        else:
            s["voltage"] = None
        s["top_oil"] = score(sec["top_oil"][i], pts["top_oil"])
        s["wti"] = score(sec["wti"][i], pts["wti"])
        v = sec["relative_ageing_rate"][i]
        s["ageing"] = score(math.log2(v) if v and v > 0 else 0.0, pts["ageing"])
        s["winding_excess"] = score(excess_avg[i], pts["winding_excess"])
        s["cooling"] = score(cool_avg[i], pts["cooling"]) if on else None
        p = pressure[min(i // 60, len(pressure) - 1)]
        s["pressure"] = score(p["pressure_final_abs"], pts["pressure"]) if p["validity_flag"] else None
        s["protection"] = protection[i]
        for k in SUB1:
            subs[k].append(s[k])
        hi.append(combine(s, w, h["cap_margin"]))
        pr = precision[min(i // 60, len(precision) - 1)]
        devs = [x for dev in ("current", "voltage", "temperature") for x in pr[dev] if x is not None]
        conf.append(score(max(devs), pts["precision"]) if devs else None)
    return {"health": hi, "subs": subs, "confidence": conf}


def timebar2(rows: list[dict], cfg: dict[str, Any], tb1_day: dict[str, Any] | None = None) -> dict[str, Any]:
    """Per-day sub-scores, health index and data confidence. tb1_day: {"day", "thermal"} from timebar 1."""
    h = cfg["health"]
    c = h["timebar2"]
    pts, w = c["points"], c["weights"]
    sc = cfg["short_circuit"]
    minimum = cfg["oil_leak"]["minimum_volume_l"]
    subs = {k: [] for k in SUB2}
    hi, conf = [], []
    penalty = 0.0
    for d, r in enumerate(rows):
        oil = score(r["residual"], pts["oil_residual"])
        margin = score(r["volume_measured"] - minimum, pts["oil_above_minimum"])
        s = {"oil": min(oil, margin)}
        # mechanical: penalty per through-fault since the last winding test (reset when the severe count resets)
        if d and rows[d - 1].get("through_fault_duty_open") and not r.get("through_fault_duty_open"):
            penalty = 0.0                                   # winding tests passed: the windings are confirmed sound
        for f in r.get("through_fault_forces", []):
            penalty += f * (c["severe_fault_penalty"] if f >= sc["severe_force_pct"] else c["minor_fault_penalty"])
        mech = max(c["mechanical_floor"], 100 - penalty)
        s["mechanical"] = min(mech, c["duty_open_cap"]) if r.get("through_fault_duty_open") else mech
        s["thermal"] = (tb1_day["thermal"] if tb1_day and d == tb1_day["day"]
                        else score(r["oil_temperature"], pts["thermal"]))
        for k in SUB2:
            subs[k].append(round(s[k], 1))
        hi.append(round(combine(s, w, h["cap_margin"]), 1))
        devs = [x for dev in ("current", "voltage", "temperature") for x in r[dev] if x is not None]
        conf.append(round(score(max(devs), pts["precision"]), 1) if devs else None)
    return {"health": hi, "subs": subs, "confidence": conf}
