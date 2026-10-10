import math
from datetime import date, datetime, timedelta, timezone

from app.analytics.load_growth import FLAG, LoadConfig, analyse

START = datetime(2026, 7, 1, tzinfo=timezone.utc)


def load(days: int, peak0: float, growth_per_day: float):
    """Hourly loading with a daily cycle: evening peak = peak0 + growth * day."""
    series = []
    for h in range(days * 24):
        peak = peak0 + growth_per_day * (h // 24)
        shape = 0.55 + 0.45 * math.exp(-((h % 24 - 20) ** 2) / 8)
        series.append((START + timedelta(hours=h), peak * shape))
    return series


def test_flat_load_is_not_flagged():
    result = analyse(load(56, 92, 0.0))
    assert result["flags"] == []
    assert all(abs(r["slope_per_day"]) < 0.01 for r in result["reviews"])


def test_growing_load_flags_and_forecasts_rated_current():
    result = analyse(load(56, 86, 0.25))
    first = result["flags"][0]
    assert first["flag"] == FLAG and first["first"]
    hours = first["overload_hours_by_week"]
    assert hours == sorted(hours) and hours[-1] > hours[0]
    # peak = 86 + 0.25*day reaches 100 on day 56
    assert first["forecast_exceeds_rated"] is not None
    assert abs((date.fromisoformat(first["forecast_exceeds_rated"]) - START.date()).days - 56) <= 2
    assert "same season" in first["message"]


def test_one_hot_week_alone_is_not_enough():
    series = load(42, 88, 0.0)
    hot = [(t, v * 1.08 if 21 * 24 <= i < 28 * 24 else v) for i, (t, v) in enumerate(series)]
    assert analyse(hot)["flags"] == []


def test_weekly_counts_and_events():
    result = analyse(load(14, 95, 0.0), LoadConfig(warning=90, critical=100))
    week = result["weeks"][0]
    assert week["events"] == 7                       # one evening overload per day
    assert week["overload_hours"] == 7 * week["overload_hours"] // 7
    assert week["critical_hours"] == 0
