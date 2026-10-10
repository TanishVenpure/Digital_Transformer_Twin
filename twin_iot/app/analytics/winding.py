"""Winding thermal review: is the winding running hotter than its load explains? and how fast
is the insulation ageing?

Winding (hot-spot) temperature is driven by load and ambient. A thermal model (IEC 60076-7
exponential form for an ONAN distribution transformer: top-oil rise + hot-spot gradient,
exponents x and y, time constants tau_oil / tau_winding) predicts the hot-spot rise for the
measured load. The weekly review compares measurement with that expectation:

  excess = measured rise - expected rise (K)

A healthy winding tracks the model (excess ~ 0). Restricted oil flow (blocked cooling ducts,
sludge) or a developing winding fault makes the winding run hotter at the same load, so the
weekly mean excess grows. WINDING_EXCESS_HEATING is flagged when it is >= `min_excess` K and
has risen for `consecutive` weeks in a row.

When the top-oil temperature is measured too, the expected hot-spot rise uses the measured
top-oil rise + the modelled gradient (IEC 60076-7 measured-top-oil method). A cooling / radiator
problem then shows up on the top-oil review (app/analytics/top_oil.py), and the winding excess is
only what happens inside the winding (blocked ducts, winding fault).

Insulation ageing (IEC 60076-7, non-thermally-upgraded paper): relative ageing rate
V = 2 ** ((hot_spot - 98) / 6); loss of life = sum(V * hours) / 24 equivalent days.
The model parameters are generic defaults: calibrate them against the transformer's heat-run
test before relying on absolute values.
"""
import math
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import yaml

from app.alerts.configuration import PROJECT_ROOT

FLAG = "WINDING_EXCESS_HEATING"


@dataclass(frozen=True)
class ThermalConfig:
    top_oil_rise_rated: float = 30.0
    gradient_rated: float = 15.0
    loss_ratio: float = 5.0
    oil_exponent: float = 0.8
    winding_exponent: float = 1.6
    tau_oil_min: float = 180.0
    tau_winding_min: float = 4.0
    ageing_reference_c: float = 98.0
    ageing_doubling_k: float = 6.0
    review_days: int = 7
    min_excess: float = 1.5        # K, weekly mean excess over the model
    consecutive: int = 2           # weeks in a row with a larger excess than the week before


def load_config(path: Path | None = None) -> ThermalConfig:
    path = path or PROJECT_ROOT / "config" / "analytics.yaml"
    if not path.exists():
        return ThermalConfig()
    data = (yaml.safe_load(path.read_text(encoding="utf-8")) or {}).get("winding_thermal") or {}
    return ThermalConfig(**data)


def expected_rises(loadings: list[float | None], config: ThermalConfig) -> list[float | None]:
    """Hourly expected hot-spot rise over ambient (K) for the hottest phase, from its loading (% of rated)."""
    out: list[float | None] = []
    oil = grad = None
    a_oil = 1 - math.exp(-60 / config.tau_oil_min)
    a_w = 1 - math.exp(-60 / config.tau_winding_min)
    for loading in loadings:
        if loading is None:
            out.append(None)
            continue
        k = max(loading, 0.0) / 100
        oil_ss = config.top_oil_rise_rated * ((1 + config.loss_ratio * k * k) / (1 + config.loss_ratio)) ** config.oil_exponent
        grad_ss = config.gradient_rated * k ** config.winding_exponent
        oil = oil_ss if oil is None else oil + (oil_ss - oil) * a_oil
        grad = grad_ss if grad is None else grad + (grad_ss - grad) * a_w
        out.append(round(oil + grad, 2))
    return out


def ageing_rate(hot_spot_c: float, config: ThermalConfig) -> float:
    return 2 ** ((hot_spot_c - config.ageing_reference_c) / config.ageing_doubling_k)


def analyse(rows: list[tuple], config: ThermalConfig | None = None) -> dict[str, Any]:
    """rows: (timestamp, measured hot-spot rise K, loading % of rated, ambient degC[, measured top-oil
    rise K]) in time order. With the top-oil rise, expected = measured top-oil rise + model gradient."""
    from app.analytics.top_oil import expected_components
    config = config or ThermalConfig()
    out: dict[str, Any] = {"expected": [], "weeks": [], "reviews": [], "flags": [], "loss_of_life_days": 0.0}
    if not rows:
        return out
    expected = expected_rises([r[2] for r in rows], config)
    if any(len(r) > 4 and r[4] is not None for r in rows):
        comps = expected_components([r[2] for r in rows], config)
        expected = [round(r[4] + c[1], 2) if (c is not None and len(r) > 4 and r[4] is not None) else e
                    for r, c, e in zip(rows, comps, expected)]
    out["expected"] = expected

    start = rows[0][0].replace(hour=0, minute=0, second=0, microsecond=0)
    weeks: list[dict[str, Any]] = []
    cumulative = 0.0
    for (t, rise, _, ambient, *_), exp in zip(rows, expected):
        k = int((t - start).total_seconds() // (config.review_days * 86400))
        while len(weeks) <= k:
            ws = start + timedelta(days=config.review_days * len(weeks))
            weeks.append({"start": ws, "end": ws + timedelta(days=config.review_days), "hours": 0,
                          "excess_sum": 0.0, "excess_n": 0, "ageing_days": 0.0, "max_hot_spot": None})
        w = weeks[k]
        w["hours"] += 1
        if rise is not None and exp is not None:
            w["excess_sum"] += rise - exp
            w["excess_n"] += 1
        if rise is not None and ambient is not None:
            hot = ambient + rise
            v = ageing_rate(hot, config) / 24          # equivalent days consumed in this hour
            w["ageing_days"] += v
            cumulative += v
            w["max_hot_spot"] = hot if w["max_hot_spot"] is None else max(w["max_hot_spot"], hot)
    out["loss_of_life_days"] = round(cumulative, 4)

    total = 0.0
    excesses: list[float] = []
    data_end = rows[-1][0] + timedelta(hours=1)
    for k, w in enumerate(weeks):
        excess = w["excess_sum"] / w["excess_n"] if w["excess_n"] else None
        total += w["ageing_days"]
        rate = w["ageing_days"] / (w["hours"] / 24) if w["hours"] else None
        out["weeks"].append({
            "start": w["start"].isoformat(), "end": w["end"].isoformat(),
            "mean_excess_k": round(excess, 2) if excess is not None else None,
            "ageing_days": round(w["ageing_days"], 4), "ageing_rate": round(rate, 4) if rate is not None else None,
            "max_hot_spot_c": round(w["max_hot_spot"], 1) if w["max_hot_spot"] is not None else None,
        })
        if w["hours"] < config.review_days * 24 * 0.8 or excess is None:
            continue
        excesses.append(excess)
        recent = excesses[-(config.consecutive + 1):]
        rising = len(recent) == config.consecutive + 1 and all(b > a for a, b in zip(recent, recent[1:]))
        review = {
            "at": min(w["end"], data_end).isoformat(), "mean_excess_k": round(excess, 2),
            "excess_by_week": [round(x, 2) for x in excesses[-4:]], "rising": rising,
            "ageing_days_week": round(w["ageing_days"], 4), "ageing_rate": round(rate, 4),
            "loss_of_life_days": round(total, 4), "max_hot_spot_c": out["weeks"][-1]["max_hot_spot_c"],
        }
        out["reviews"].append(review)
        if rising and excess >= config.min_excess:
            message = (
                f"Winding running hotter than its load explains: at the same load and ambient the hot-spot "
                f"rise is {excess:.1f} K above the thermal model (weekly mean excess "
                f"{' -> '.join(f'{x:.1f}' for x in recent)} K). This points to restricted oil flow in the winding "
                f"(blocked cooling ducts, sludge) or a developing winding fault. Insulation ageing this week: "
                f"{w['ageing_days']:.2f} equivalent days ({rate:.2f}x the design rate at 98 C)."
            )
            out["flags"].append({
                "flag": FLAG, "detected_at": min(w["end"], data_end).isoformat(), "first": not out["flags"],
                "value": round(excess, 2), "unit": "K above model", "trigger": "in the weekly thermal review",
                "excess_by_week": [round(x, 2) for x in recent], "message": message,
            })
    return out
