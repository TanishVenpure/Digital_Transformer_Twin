"""Completely random hourly readings for every IoT input (no physics, no threshold tuning).

A small share of values is deliberately corrupted (out of range, non-numeric,
missing) so the validation layer is exercised.
"""
import random
from collections.abc import Iterator
from datetime import datetime, timedelta, timezone
from typing import Any

# Uniform ranges per input. Wide on purpose: thresholds are crossed freely.
RANGES = {
    "current": (200.0, 1000.0),          # A per phase
    "voltage": (215.0, 285.0),           # V phase-to-neutral
    "oil_level": (10.0, 95.0),           # percent of gauge
    "top_oil_temperature": (40.0, 115.0),
    "ambient_temperature": (15.0, 45.0),
    "winding_temperature": (45.0, 120.0),
}
REFERENCE_SPREAD = 0.015  # reference instrument within +/-1.5 % of the device reading
BAD_VALUES = (-50.0, 99999.0, "ERR", "NaN", None)


def random_reading(rng: random.Random) -> dict[str, Any]:
    data: dict[str, Any] = {}
    for phase in "ryb":
        current = rng.uniform(*RANGES["current"])
        voltage = rng.uniform(*RANGES["voltage"])
        data[f"current_{phase}"] = round(current, 1)
        data[f"reference_current_{phase}"] = round(current * (1 + rng.uniform(-REFERENCE_SPREAD, REFERENCE_SPREAD)), 1)
        data[f"voltage_{phase}"] = round(voltage, 1)
        data[f"reference_voltage_{phase}"] = round(voltage * (1 + rng.uniform(-REFERENCE_SPREAD, REFERENCE_SPREAD)), 1)
        data[f"winding_temperature_{phase}"] = round(rng.uniform(*RANGES["winding_temperature"]), 1)
    for feature in ("oil_level", "top_oil_temperature", "ambient_temperature"):
        data[feature] = round(rng.uniform(*RANGES[feature]), 1)
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
    for hour in range(days * 24):
        if rng.random() < gap_rate:
            continue  # dropped reading -> GAP issue on the next one
        yield start + timedelta(hours=hour), corrupt(random_reading(rng), rng, invalid_rate)
