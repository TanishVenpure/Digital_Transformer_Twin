"""Load growth detection: overloads becoming more frequent.

Load current is driven by customer demand, not by equipment wear, so there is nothing to
repair. When overload events arrive more and more often, demand is growing and the
transformer is running out of capacity. This module runs a weekly load review:

  1. daily peak loading (% of rated) and hourly overload flags (>= warning limit),
  2. per 7-day block: overload hours, hours above rated, number of overload events,
  3. a least-squares trend on the last `window_days` daily peaks, and a forecast of the
     date the trend reaches rated current (when peaks will exceed rating regularly),
  4. LOAD_GROWTH is flagged when overload hours have risen for `consecutive` weeks in a
     row (latest week >= `min_overload_hours`) AND the daily-peak trend is rising by at
     least `min_growth_per_day`.

Requiring both a rising trend over weeks and rising overload hours keeps one hot week
from raising the flag. Seasonal swings can still mimic growth, so the message asks for a
same-season comparison before capacity decisions. Each review uses only data up to its
own date.
"""
from collections import OrderedDict
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import yaml

from app.alerts.configuration import PROJECT_ROOT

FLAG = "LOAD_GROWTH"


@dataclass(frozen=True)
class LoadConfig:
    warning: float = 90.0             # % of rated, from the current_loading thresholds
    critical: float = 100.0
    window_days: int = 28             # daily peaks used for the trend
    review_days: int = 7
    min_growth_per_day: float = 0.1   # % of rated per day on the daily-peak trend
    min_overload_hours: float = 2.0   # in the latest week
    consecutive: int = 2              # weeks in a row with more overload hours than the week before


def load_config(path: Path | None = None, *, warning: float | None = None, critical: float | None = None) -> LoadConfig:
    path = path or PROJECT_ROOT / "config" / "analytics.yaml"
    data = {}
    if path.exists():
        data = (yaml.safe_load(path.read_text(encoding="utf-8")) or {}).get("load_growth") or {}
    if warning is not None:
        data["warning"] = warning
    if critical is not None:
        data["critical"] = critical
    return LoadConfig(**data)


def _fit(xs: list[float], ys: list[float]) -> tuple[float, float, float] | None:
    n = len(xs)
    if n < 3:
        return None
    mx, my = sum(xs) / n, sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    if sxx == 0:
        return None
    slope = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / sxx
    intercept = my - slope * mx
    ss_tot = sum((y - my) ** 2 for y in ys)
    ss_res = sum((y - (intercept + slope * x)) ** 2 for x, y in zip(xs, ys))
    return slope, intercept, (1 - ss_res / ss_tot) if ss_tot else 0.0


def analyse(series: list[tuple[datetime, float | None]], config: LoadConfig | None = None) -> dict[str, Any]:
    """series: (timestamp, loading % of rated) in time order; None values are skipped."""
    config = config or LoadConfig()
    valid = [(t, v) for t, v in series if v is not None]
    out: dict[str, Any] = {"daily_peaks": [], "weeks": [], "reviews": [], "flags": [],
                           "warning": config.warning, "critical": config.critical}
    if not valid:
        return out

    peaks: "OrderedDict[date, float]" = OrderedDict()
    for t, v in valid:
        d = t.date()
        peaks[d] = max(peaks.get(d, v), v)
    out["daily_peaks"] = [{"date": d.isoformat(), "peak": round(p, 2)} for d, p in peaks.items()]

    start = valid[0][0].replace(hour=0, minute=0, second=0, microsecond=0)
    weeks: list[dict[str, Any]] = []
    prev_over_t = None
    for t, v in valid:
        k = int((t - start).total_seconds() // (config.review_days * 86400))
        while len(weeks) <= k:
            ws = start + timedelta(days=config.review_days * len(weeks))
            weeks.append({"start": ws, "end": ws + timedelta(days=config.review_days),
                          "overload_hours": 0, "critical_hours": 0, "events": 0, "peak": 0.0, "hours": 0})
        w = weeks[k]
        w["hours"] += 1
        w["peak"] = max(w["peak"], v)
        if v >= config.warning:
            w["overload_hours"] += 1
            if prev_over_t is None or (t - prev_over_t) > timedelta(hours=1):
                w["events"] += 1
            prev_over_t = t
        if v >= config.critical:
            w["critical_hours"] += 1
    out["weeks"] = [{**w, "start": w["start"].isoformat(), "end": w["end"].isoformat(), "peak": round(w["peak"], 2)}
                    for w in weeks]

    # weekly reviews at the end of each complete week
    days = list(peaks.items())
    day0 = days[0][0]
    flagged_before = False
    for k, w in enumerate(weeks):
        if w["hours"] < config.review_days * 24 * 0.8:   # incomplete week (end of data)
            continue
        review_at: datetime = w["end"]
        window = [(d, p) for d, p in days if review_at.date() - timedelta(days=config.window_days) <= d < review_at.date()]
        fit = _fit([(d - day0).days for d, _ in window], [p for _, p in window])
        if fit is None:
            continue
        slope, intercept, r2 = fit
        last_x = (review_at.date() - day0).days - 1
        trend_now = intercept + slope * last_x
        forecast = None
        if slope > 0 and trend_now < config.critical:
            forecast = day0 + timedelta(days=(config.critical - intercept) / slope)
        hours = [x["overload_hours"] for x in weeks[: k + 1]]
        recent = hours[-(config.consecutive + 1):]
        rising = len(recent) == config.consecutive + 1 and all(b > a for a, b in zip(recent, recent[1:]))
        review = {
            "at": review_at.isoformat(), "slope_per_day": round(slope, 3), "r2": round(r2, 2),
            "trend_from": {"date": window[0][0].isoformat(), "value": round(intercept + slope * (window[0][0] - day0).days, 2)},
            "trend_to": {"date": (review_at.date() - timedelta(days=1)).isoformat(), "value": round(trend_now, 2)},
            "forecast_exceeds_rated": forecast.isoformat() if forecast else None,
            "already_at_rated": trend_now >= config.critical,
            "overload_hours_by_week": hours[-4:], "rising": rising,
        }
        out["reviews"].append(review)
        if rising and recent[-1] >= config.min_overload_hours and slope >= config.min_growth_per_day:
            when = (f"At this rate the daily peak will exceed rated current regularly from about {forecast.isoformat()} "
                    f"({(forecast - review_at.date()).days} days)." if forecast else
                    "The daily-peak trend is already at or above rated current.")
            message = (
                f"Load growth: overloads are becoming more frequent. Overload hours per week "
                f"{' -> '.join(str(h) for h in recent)}; daily peak rising {slope:.2f} % of rated per day "
                f"(+{slope * 7:.1f} % per week). {when} Compare with the same season last year before acting."
            )
            out["flags"].append({
                "flag": FLAG, "detected_at": review_at.isoformat(), "first": not flagged_before,
                "value": round(slope, 3), "unit": "percent_of_rated/day", "trigger": "in the weekly load review",
                "overload_hours_by_week": recent, "forecast_exceeds_rated": review["forecast_exceeds_rated"],
                "message": message,
            })
            flagged_before = True
    return out
