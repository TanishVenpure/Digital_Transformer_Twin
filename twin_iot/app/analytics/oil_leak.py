"""Accelerating oil leak detection from the oil-level readings alone.

A healthy transformer loses oil slowly and steadily, so each top-up lasts a long time.
When gaskets and seals deteriorate the leak speeds up: every refill lasts less time
than the one before. This module

  1. splits the oil-level series into refill cycles (a refill = a jump up in level),
  2. measures each cycle's leak rate (least-squares slope, % of gauge per day) and,
     for cycles bounded by two refills, the refill interval,
  3. flags ACCELERATING_OIL_LEAK when the leak rate has risen by at least
     `min_increase` over each of the last `consecutive` cycle-to-cycle steps.

Only data up to each refill is used for the decision made at that refill, so the
flags are what an online system would have raised at the time.
"""
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml

from app.alerts.configuration import PROJECT_ROOT

FLAG = "ACCELERATING_OIL_LEAK"


@dataclass(frozen=True)
class LeakConfig:
    refill_jump: float = 10.0        # level rise between consecutive readings that counts as a refill
    settle_hours: int = 2            # ignore readings right after a refill when fitting the slope
    min_points: int = 24             # a cycle needs at least this many readings to be rated
    min_increase: float = 0.15       # leak rate must grow >= 15 % per cycle ...
    consecutive: int = 2             # ... over this many consecutive cycle steps


@dataclass
class Cycle:
    start: datetime
    end: datetime | None             # refill that ended it (None = still running)
    leak_rate_per_day: float | None
    interval_days: float | None      # refill-to-refill duration (None when it started before the data)
    points: int
    bounded_start: bool = False      # True when the cycle began with an observed refill

    def as_dict(self) -> dict[str, Any]:
        return {
            "start": self.start.isoformat(), "end": self.end.isoformat() if self.end else None,
            "leak_rate_per_day": self.leak_rate_per_day, "interval_days": self.interval_days,
            "points": self.points, "complete": self.end is not None,
        }


@dataclass
class LeakAnalysis:
    cycles: list[Cycle] = field(default_factory=list)
    flags: list[dict[str, Any]] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {"cycles": [c.as_dict() for c in self.cycles], "flags": self.flags}


def load_config(path: Path | None = None) -> LeakConfig:
    path = path or PROJECT_ROOT / "config" / "analytics.yaml"
    if not path.exists():
        return LeakConfig()
    data = (yaml.safe_load(path.read_text(encoding="utf-8")) or {}).get("oil_leak") or {}
    return LeakConfig(**data)


def _slope_per_day(points: list[tuple[datetime, float]]) -> float | None:
    if len(points) < 2:
        return None
    t0 = points[0][0]
    xs = [(t - t0).total_seconds() / 86400 for t, _ in points]
    ys = [v for _, v in points]
    mx, my = sum(xs) / len(xs), sum(ys) / len(ys)
    sxx = sum((x - mx) ** 2 for x in xs)
    if sxx == 0:
        return None
    return sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / sxx


def _rate(points: list[tuple[datetime, float]], config: LeakConfig) -> float | None:
    fit = points[config.settle_hours:]
    if len(fit) < config.min_points:
        return None
    slope = _slope_per_day(fit)
    return None if slope is None else max(0.0, -slope)


def analyse(series: list[tuple[datetime, float | None]], config: LeakConfig | None = None) -> LeakAnalysis:
    """series: (timestamp, oil level) in time order; None values (invalid inputs) are skipped."""
    config = config or LeakConfig()
    valid = [(t, v) for t, v in series if v is not None]
    result = LeakAnalysis()
    if not valid:
        return result

    # split into cycles at refills
    segments: list[tuple[list[tuple[datetime, float]], datetime | None, bool]] = []
    current, bounded = [valid[0]], False
    for prev, point in zip(valid, valid[1:]):
        if point[1] - prev[1] >= config.refill_jump:
            segments.append((current, point[0], bounded))
            current, bounded = [point], True
        else:
            current.append(point)
    segments.append((current, None, bounded))

    for points, refill, started_with_refill in segments:
        start = points[0][0]
        interval = (refill - start).total_seconds() / 86400 if refill and started_with_refill else None
        result.cycles.append(Cycle(
            start=start, end=refill, leak_rate_per_day=_rate(points, config),
            interval_days=round(interval, 2) if interval is not None else None,
            points=len(points), bounded_start=started_with_refill,
        ))

    # decide at each refill, using only completed cycles so far
    completed: list[Cycle] = []
    flagged_since: int | None = None
    for cycle in result.cycles:
        if cycle.end is None:
            break
        completed.append(cycle)
        rated = [c for c in completed if c.leak_rate_per_day]
        if len(rated) < config.consecutive + 1:
            continue
        recent = rated[-(config.consecutive + 1):]
        steps = [b.leak_rate_per_day / a.leak_rate_per_day - 1 for a, b in zip(recent, recent[1:])]
        if all(step >= config.min_increase for step in steps):
            intervals = [c.interval_days for c in recent if c.interval_days is not None]
            rates = [round(c.leak_rate_per_day, 2) for c in recent]
            growth = (rates[-1] / rates[0]) ** (1 / (len(rates) - 1)) - 1
            first_flag = flagged_since is None
            flagged_since = flagged_since if flagged_since is not None else len(completed)
            message = (
                f"Accelerating oil leak: each top-up is lasting less time. Leak rate rose "
                f"{' -> '.join(f'{r:g}' for r in rates)} %/day over the last {len(rates)} refill cycles "
                f"(+{growth * 100:.0f}% per cycle)"
                + (f"; refill interval shortened {' -> '.join(f'{i:g}' for i in intervals)} days" if len(intervals) >= 2 else "")
                + ". This points to progressive seal / gasket degradation."
            )
            result.flags.append({
                "flag": FLAG, "detected_at": cycle.end.isoformat(), "first": first_flag,
                "leak_rates_per_day": rates, "refill_intervals_days": intervals,
                "growth_per_cycle": round(growth, 3), "message": message,
                "value": rates[-1], "unit": "percent_of_gauge/day", "trigger": "at an oil top-up",
            })
        else:
            flagged_since = None
    return result
