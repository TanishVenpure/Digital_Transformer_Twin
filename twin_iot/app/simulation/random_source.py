"""Physics-aware hourly readings with realistic degradation events.

Most of the time the transformer operates normally.  Scheduled events
introduce gradual degradation in one or two parameters, cross thresholds
for a few hours, then recover — mimicking real field behaviour like slow
oil leaks, transient overloads, or cooling fan failures.

Values use a slow random walk (mean-reverting) so consecutive hours are
highly correlated and the prediction algorithm doesn't false-alarm from
measurement noise.

A small share of values is deliberately corrupted (out of range, non-numeric,
missing) so the validation layer is exercised.
"""
import math
import random
from collections.abc import Iterator
from datetime import datetime, timedelta, timezone
from typing import Any

# Normal operating baselines (well within thresholds).
BASELINES = {
    "current": 420.0,
    "voltage": 250.0,
    "oil_level": 80.0,
    "top_oil_temperature": 55.0,
    "ambient_temperature": 28.0,
    "winding_temperature": 48.0,
}

# Per-hour innovation (σ of the random walk step, NOT the spread of the value).
# Kept tiny so hour-to-hour changes don't trigger lead-time predictions.
WALK_SIGMA = {
    "current": 3.0,
    "voltage": 0.3,
    "oil_level": 0.15,
    "top_oil_temperature": 0.3,
    "ambient_temperature": 0.3,
    "winding_temperature": 0.25,
}

# Mean-reversion strength: fraction of drift pulled back to baseline each hour.
# Higher = faster return to baseline = less wandering.
REVERSION = 0.05

REFERENCE_SPREAD = 0.005
BAD_VALUES = (-50.0, 99999.0, "ERR", "NaN", None)

# Gentle diurnal load multiplier (peak ~1.02, trough ~0.80)
_DIURNAL = [0.85, 0.82, 0.80, 0.80, 0.80, 0.82, 0.88, 0.93,
            0.97, 1.0, 1.0, 1.0, 1.0, 0.98, 0.98, 1.0,
            1.02, 1.02, 1.0, 0.97, 0.95, 0.92, 0.90, 0.87]


class _Event:
    """A degradation event that ramps up, holds, then recovers."""
    __slots__ = ("param", "start_hour", "peak_hour", "end_hour", "peak_delta", "kind")

    def __init__(self, param, start_hour, duration, peak_delta, kind="ramp"):
        self.param = param
        self.start_hour = start_hour
        ramp = max(1, duration // 3)
        self.peak_hour = start_hour + ramp
        self.end_hour = start_hour + duration
        self.peak_delta = peak_delta
        self.kind = kind

    def delta(self, hour):
        if hour < self.start_hour or hour > self.end_hour:
            return 0.0
        if hour < self.peak_hour:
            return self.peak_delta * (hour - self.start_hour) / (self.peak_hour - self.start_hour)
        recovery_hours = self.end_hour - self.peak_hour
        if recovery_hours <= 0:
            return self.peak_delta
        return self.peak_delta * (1 - (hour - self.peak_hour) / recovery_hours)


def _schedule_events(rng, total_hours):
    """Create a handful of degradation events spread across the timeline."""
    events = []
    event_defs = [
        ("current", 250, 400, 4, 18, "overload"),
        ("oil_level", -30, -50, 24, 120, "oil leak"),
        ("top_oil_temperature", 30, 50, 6, 24, "cooling failure"),
        ("winding_temperature", 20, 40, 4, 16, "winding overheating"),
        ("voltage", 15, 30, 3, 12, "supply disturbance"),
    ]

    n_events = rng.randint(max(3, total_hours // 500), max(6, total_hours // 300))
    window = total_hours // max(1, n_events)

    for i in range(n_events):
        defn = rng.choice(event_defs)
        param, min_d, max_d, min_dur, max_dur = defn[:5]
        start = rng.randint(max(0, i * window), min(total_hours - max_dur - 1, (i + 1) * window))
        duration = rng.randint(min_dur, max_dur)
        delta = rng.uniform(min_d, max_d)
        events.append(_Event(param, start, duration, delta))

    return events


class _WalkState:
    """Tracks per-parameter random walk offsets (mean-reverting)."""

    def __init__(self):
        self.offsets: dict[str, float] = {k: 0.0 for k in BASELINES}

    def step(self, rng: random.Random):
        for param in self.offsets:
            innovation = rng.gauss(0, WALK_SIGMA[param])
            self.offsets[param] = self.offsets[param] * (1 - REVERSION) + innovation


def _reading(rng: random.Random, hour: int, walk: _WalkState, events: list) -> dict[str, Any]:
    data: dict[str, Any] = {}
    diurnal = _DIURNAL[hour % 24]

    deltas = {}
    for ev in events:
        d = ev.delta(hour)
        if d != 0.0:
            deltas[ev.param] = deltas.get(ev.param, 0.0) + d

    base_current = BASELINES["current"] * diurnal + walk.offsets["current"] + deltas.get("current", 0.0)
    base_voltage = BASELINES["voltage"] + walk.offsets["voltage"] + deltas.get("voltage", 0.0)
    base_winding = BASELINES["winding_temperature"] + walk.offsets["winding_temperature"] + deltas.get("winding_temperature", 0.0)
    current_heat = max(0, (base_current - 600) * 0.04)

    for phase in "ryb":
        # Tiny per-phase jitter (much smaller than the walk step)
        current = max(50, base_current + rng.gauss(0, 2.0))
        voltage = max(180, base_voltage + rng.gauss(0, 0.3))
        winding = max(30, base_winding + current_heat + rng.gauss(0, 0.3))
        data[f"current_{phase}"] = round(current, 1)
        data[f"reference_current_{phase}"] = round(current * (1 + rng.uniform(-REFERENCE_SPREAD, REFERENCE_SPREAD)), 1)
        data[f"voltage_{phase}"] = round(voltage, 1)
        data[f"reference_voltage_{phase}"] = round(voltage * (1 + rng.uniform(-REFERENCE_SPREAD, REFERENCE_SPREAD)), 1)
        data[f"winding_temperature_{phase}"] = round(winding, 1)

    ambient = BASELINES["ambient_temperature"] + walk.offsets["ambient_temperature"]
    ambient += 5 * math.sin(math.pi * ((hour % 24) - 6) / 12) if 6 <= (hour % 24) <= 18 else -3
    data["ambient_temperature"] = round(max(5, ambient), 1)

    oil = BASELINES["oil_level"] + walk.offsets["oil_level"] + deltas.get("oil_level", 0.0)
    data["oil_level"] = round(max(5, min(99, oil)), 1)

    top_oil = (BASELINES["top_oil_temperature"] + walk.offsets["top_oil_temperature"]
               + deltas.get("top_oil_temperature", 0.0) + current_heat * 0.8)
    data["top_oil_temperature"] = round(max(20, top_oil), 1)

    return data


def corrupt(data: dict[str, Any], rng: random.Random, rate: float) -> dict[str, Any]:
    for feature in list(data):
        if rng.random() < rate:
            bad = rng.choice(BAD_VALUES)
            if bad is None:
                del data[feature]
            else:
                data[feature] = bad
    return data


def generate(
    *, start: datetime, days: int, seed: int | None = None, invalid_rate: float = 0.01, gap_rate: float = 0.005,
) -> Iterator[tuple[datetime, dict[str, Any]]]:
    rng = random.Random(seed)
    start = start.replace(minute=0, second=0, microsecond=0, tzinfo=start.tzinfo or timezone.utc)
    total_hours = days * 24
    events = _schedule_events(rng, total_hours)
    walk = _WalkState()
    for hour in range(total_hours):
        walk.step(rng)
        if rng.random() < gap_rate:
            continue
        yield start + timedelta(hours=hour), corrupt(_reading(rng, hour, walk, events), rng, invalid_rate)
