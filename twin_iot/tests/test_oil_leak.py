from datetime import datetime, timedelta, timezone

import pytest

from app.analytics.oil_leak import FLAG, LeakConfig, analyse

START = datetime(2026, 7, 1, tzinfo=timezone.utc)


def sawtooth(cycle_days: list[float], top=86.0, bottom=20.0):
    """Hourly oil level: drain from top to bottom over each cycle length, then top up."""
    series, t = [], START
    for days in cycle_days:
        hours = int(days * 24)
        for h in range(hours):
            series.append((t, top - (top - bottom) * h / hours))
            t += timedelta(hours=1)
    series.append((t, top))
    return series


def test_constant_leak_is_not_flagged():
    result = analyse(sawtooth([20, 20, 20, 20]))
    assert [round(c.leak_rate_per_day, 1) for c in result.cycles if c.end] == [3.3] * 4
    assert result.flags == []


def test_each_shorter_cycle_raises_the_flag_at_the_third_refill():
    result = analyse(sawtooth([24, 18, 13.5, 10]))
    rates = [c.leak_rate_per_day for c in result.cycles if c.end]
    assert all(b > a for a, b in zip(rates, rates[1:]))
    assert [c.interval_days for c in result.cycles if c.end][1:] == pytest.approx([18, 13.5, 10], abs=0.05)
    assert result.flags[0]["flag"] == FLAG
    assert result.flags[0]["detected_at"] == result.cycles[2].end.isoformat()   # decided with 3 cycles only
    assert result.flags[0]["first"] and "seal / gasket" in result.flags[0]["message"]


def test_one_faster_cycle_alone_is_not_enough():
    assert analyse(sawtooth([20, 20, 14, 20])).flags == []


def test_invalid_readings_are_skipped():
    series = sawtooth([24, 18, 13.5])
    series[100] = (series[100][0], None)
    result = analyse(series, LeakConfig())
    assert len([c for c in result.cycles if c.end]) == 3
    assert result.flags
