"""Hourly readings where each monitored quantity degrades, breaches its limits, and is
restored by maintenance - so per-metric graphs show decline -> threshold -> repair cycles.

Each of the seven monitors carries a degradation level d (0 = as new, 1 = at its critical
limit). d grows at a random rate per cycle (failure in roughly 6-30 days). Once d stays
past critical for a random repair delay (6-18 h), a maintenance crew restores it (d -> ~0)
and a new cycle starts. Daily load / ambient cycles and sensor noise are layered on top,
and a small share of values is corrupted so input validation is still exercised.

Every monitor has its own independent profile (PROFILES). Oil level models an
accelerating leak: each refill cycle is shorter than the previous one (seal / gasket
degradation), which app/analytics/oil_leak.py detects from the readings alone.

Supply voltage is external (the upstream grid): it is never "repaired" and has no decline;
all three phases move together with a daily swing, slow drift and occasional dips / rises
(GridVoltage). It is monitored for supply quality only.

Winding temperature follows the load through the IEC 60076-7 thermal model (WindingThermal):
top-oil rise + hot-spot gradient, both driven by per-phase loading. From `fault_onset_day`
restricted oil flow (blocked ducts / sludge) makes the gradient grow, so the winding runs
hotter than its load explains (app/analytics/winding.py). Not repaired within the window.

Load current is NOT equipment wear and is never "repaired": it follows customer demand
(LoadModel) - a daily curve, weekday/weekend, a gentle seasonal swing and slow demand
growth - so overloads arrive more and more often (app/analytics/load_growth.py).

The returned repairs list records when each maintenance action happened (ground truth).
"""
import math
import random
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from app.simulation.random_source import corrupt

RATED_CURRENT = 841.0
NOMINAL_VOLTAGE = 250.0


@dataclass(frozen=True)
class Profile:
    """How one monitored quantity degrades, independently of the others."""
    days_to_failure: tuple[float, float] = (6.0, 30.0)   # first cycle (and every cycle if cycle_factor is None)
    cycle_factor: float | None = None   # each new cycle lasts this fraction of the previous one; None = independent
    start_level: tuple[float, float] = (0.0, 0.5)
    repair_delay_hours: tuple[int, int] = (6, 18)
    jitter: float = 0.4                 # hour-to-hour variation of the degradation rate (+/-)


PROFILES = {
    # Measurement equipment: constant drift, and every repair (recalibration / replacement) restores it
    # to as-new, so every cycle is the same length (a renewal process, unlike the ageing oil seals).
    "current_sensor": Profile(days_to_failure=(20.0, 20.0), start_level=(0.0, 0.3), repair_delay_hours=(2, 6), jitter=0.1),
    "voltage_sensor": Profile(days_to_failure=(16.0, 16.0), start_level=(0.0, 0.3), repair_delay_hours=(2, 6), jitter=0.1),
    "current": Profile(),
    "voltage": Profile(),
    # Accelerating leak: refill interval shrinks ~23 % per cycle (about 26 -> 20 -> 15 -> 12 -> 9 days).
    "oil": Profile(days_to_failure=(25.0, 27.0), cycle_factor=0.77, start_level=(0.1, 0.15),
                   repair_delay_hours=(4, 10), jitter=0.15),
    "top_oil": Profile(),
    "winding": Profile(),
}


@dataclass
class Degradation:
    rng: random.Random
    profile: Profile = field(default_factory=Profile)
    d: float = 0.0
    rate: float = 0.0
    cycle_days: float = 0.0
    hours_past_critical: int = 0
    repair_delay: int = 12
    repairs: list[int] = field(default_factory=list)

    def __post_init__(self):
        self.d = self.rng.uniform(*self.profile.start_level)
        self.new_cycle()

    def new_cycle(self) -> None:
        p = self.profile
        if p.cycle_factor is None or not self.cycle_days:
            self.cycle_days = self.rng.uniform(*p.days_to_failure)
        else:
            self.cycle_days *= p.cycle_factor * self.rng.uniform(0.97, 1.03)
        self.rate = 1 / (24 * self.cycle_days)
        self.repair_delay = self.rng.randint(*p.repair_delay_hours)
        self.hours_past_critical = 0

    def step(self, hour: int) -> float:
        j = self.profile.jitter
        self.d += self.rate * self.rng.uniform(1 - j, 1 + j)
        if self.d >= 1.0:
            self.hours_past_critical += 1
            if self.hours_past_critical >= self.repair_delay:
                self.repairs.append(hour)
                self.d = self.rng.uniform(0.0, 0.08)
                self.new_cycle()
        return self.d


@dataclass(frozen=True)
class LoadProfile:
    """Customer demand on the transformer, as % of rated current (time compressed for the demo)."""
    start_peak: float = 82.0          # evening-peak loading on day 0
    growth_per_day: float = 0.0035    # compound demand growth (~ +37 % over 90 days; real feeders: a few %/year)
    weekend_factor: float = 0.93
    seasonal_amplitude: float = 0.04  # +/- 4 %, peaking mid-July (doy 196) - a confounder for the trend
    weather_sd: float = 0.03          # day-to-day variation (heat, rain)
    noise_sd: float = 1.0             # hour-to-hour variation, % of rated


def _daily_shape(hour: float) -> float:
    """Mixed residential/commercial curve: night low, morning bump, afternoon plateau, evening peak."""
    g = lambda mu, sd: math.exp(-((hour - mu) ** 2) / (2 * sd * sd))  # noqa: E731
    return 0.50 + 0.28 * g(9, 2.0) + 0.20 * g(14, 3.0) + 0.50 * g(20.5, 2.2)


_SHAPE_MAX = max(_daily_shape(h / 10) for h in range(240))


class LoadModel:
    def __init__(self, start: datetime, rng: random.Random, profile: LoadProfile = LoadProfile()):
        self.start, self.rng, self.p = start, rng, profile
        self.weather: dict[int, float] = {}

    def loading(self, hour: int) -> float:
        t = self.start + timedelta(hours=hour)
        day = hour // 24
        if day not in self.weather:
            self.weather[day] = self.rng.gauss(1.0, self.p.weather_sd)
        seasonal = 1 + self.p.seasonal_amplitude * math.cos(2 * math.pi * (t.timetuple().tm_yday - 196) / 365)
        weekend = self.p.weekend_factor if t.weekday() >= 5 else 1.0
        level = (self.p.start_peak * _daily_shape(t.hour) / _SHAPE_MAX * weekend * seasonal
                 * (1 + self.p.growth_per_day) ** (hour / 24) * self.weather[day])
        return level + self.rng.gauss(0, self.p.noise_sd)


@dataclass(frozen=True)
class WindingProfile:
    """IEC 60076-7 style thermal behaviour (distribution transformer, ONAN) + a cooling-duct fault."""
    top_oil_rise_rated: float = 30.0   # K at rated load
    gradient_rated: float = 15.0       # hot-spot to top-oil gradient at rated load, K (rise 45 K = spec limit)
    loss_ratio: float = 5.0            # R: load losses / no-load losses at rated
    oil_exponent: float = 0.8          # x
    winding_exponent: float = 1.6      # y
    tau_oil_min: float = 180.0
    tau_winding_min: float = 4.0
    fault_onset_day: float = 45.0      # restricted oil flow begins
    fault_growth_per_day: float = 0.01   # gradient grows 1 %/day after onset (~ +6.7 K at rated by day 90)


class WindingThermal:
    def __init__(self, profile: WindingProfile = WindingProfile()):
        self.p = profile
        self.oil_rise: float | None = None
        self.gradients: list[float] | None = None

    def step(self, hour: int, k_phases: list[float]) -> list[float]:
        """Hourly step; returns the hot-spot rise over ambient for each phase (K)."""
        p = self.p
        k2 = sum(k * k for k in k_phases) / len(k_phases)
        oil_ss = p.top_oil_rise_rated * ((1 + p.loss_ratio * k2) / (1 + p.loss_ratio)) ** p.oil_exponent
        a_oil = 1 - math.exp(-60 / p.tau_oil_min)
        self.oil_rise = oil_ss if self.oil_rise is None else self.oil_rise + (oil_ss - self.oil_rise) * a_oil
        fault = 1 + max(0.0, hour / 24 - p.fault_onset_day) * p.fault_growth_per_day
        grad_ss = [p.gradient_rated * fault * max(k, 0.0) ** p.winding_exponent for k in k_phases]
        a_w = 1 - math.exp(-60 / p.tau_winding_min)
        self.gradients = grad_ss if self.gradients is None else [g + (ss - g) * a_w for g, ss in zip(self.gradients, grad_ss)]
        return [self.oil_rise + g for g in self.gradients]


class GridVoltage:
    """Upstream supply voltage as % deviation from nominal (signed). External: no wear, no repair."""
    def __init__(self, rng: random.Random):
        self.rng = rng
        self.drift = 0.0
        self.event: tuple[int, float] | None = None   # (hours left, depth %)

    def deviation(self, hour: int) -> float:
        day = 2 * math.pi * (hour % 24) / 24
        # light load at night -> slightly high; evening peak -> lower
        daily = 1.6 * math.cos(day - 2 * math.pi * 3 / 24) - 1.2 * math.exp(-((hour % 24 - 20) ** 2) / 6)
        self.drift = 0.97 * self.drift + self.rng.gauss(0, 0.25)          # slow random walk around 0
        if self.event is None and self.rng.random() < 1 / (24 * 6):       # ~ one supply event per 6 days
            dip = self.rng.random() < 0.8
            self.event = (self.rng.randint(1, 5), -self.rng.uniform(5.5, 12.5) if dip else self.rng.uniform(5.0, 8.0))
        extra = 0.0
        if self.event:
            left, depth = self.event
            extra = depth
            self.event = (left - 1, depth) if left > 1 else None
        return daily + self.drift + extra


def phase_loadings(loading: float) -> list[float]:
    """Per-phase loading as a fraction of rated (R, Y, B): small, fixed imbalance."""
    return [loading / 100 * (1 - 0.02 * i) for i in range(3)]


def reading_from_levels(levels: dict[str, float], hour: int, rng: random.Random, loading: float | None = None,
                        grid_deviation: float | None = None, winding_rises: list[float] | None = None) -> dict[str, Any]:
    day = 2 * math.pi * (hour % 24) / 24
    noise = lambda s: rng.gauss(0, s)  # noqa: E731
    ambient = 30 + 6 * math.sin(day - math.pi / 2) + noise(0.5)
    data: dict[str, Any] = {"ambient_temperature": round(ambient, 1)}

    # Current: customer demand (LoadModel) -> loading % of 841 A.
    if loading is None:
        loading = 58 + 9 * math.sin(day - math.pi / 2) + 38 * levels["current"] + noise(1.0)
    # Current sensor: instrument error on one phase grows with its degradation.
    sensor_error = 0.15 + 0.95 * levels["current_sensor"] + abs(noise(0.04))
    for i, phase in enumerate("ryb"):
        true_current = RATED_CURRENT * loading / 100 * (1 - 0.02 * i) + noise(3)
        error = sensor_error if phase == "y" else 0.1 + abs(noise(0.05))
        data[f"reference_current_{phase}"] = round(true_current, 1)
        data[f"current_{phase}"] = round(true_current * (1 + error / 100), 1)

    # Voltage: external grid supply (all phases together) unless an old-style level is given;
    # the voltage sensor error drifts independently.
    v_error = 0.15 + 0.95 * levels["voltage_sensor"] + abs(noise(0.04))
    for i, phase in enumerate("ryb"):
        if grid_deviation is not None:
            true_voltage = NOMINAL_VOLTAGE * (1 + grid_deviation / 100) + noise(0.4)
        else:
            deviation = 1.0 + 0.6 * math.sin(day) + 9.2 * levels["voltage"] + abs(noise(0.25))
            true_voltage = NOMINAL_VOLTAGE * (1 - (deviation if phase == "r" else deviation * 0.5) / 100) + noise(0.3)
        error = v_error if phase == "b" else 0.1 + abs(noise(0.05))
        data[f"reference_voltage_{phase}"] = round(true_voltage, 1)
        data[f"voltage_{phase}"] = round(true_voltage * (1 - error / 100), 1)

    # Oil level: leak drains from ~86 % toward the 20 % minimum.
    data["oil_level"] = round(86 - 66.5 * levels["oil"] + noise(0.4), 1)

    # Top oil: ambient + rise; cooling degradation raises the rise until the 105 degC trip.
    rise = 24 + 4 * math.sin(day - math.pi / 2) + 50 * levels["top_oil"] + noise(0.6)
    data["top_oil_temperature"] = round(ambient + rise, 1)

    # Winding: hottest-phase rise over ambient reaches 45 K at critical.
    if winding_rises is not None:   # thermal model driven by load (fibre-optic probe = hot-spot)
        for phase, rise in zip("ryb", winding_rises):
            data[f"winding_temperature_{phase}"] = round(ambient + rise + noise(0.3), 1)
    else:
        w_rise = 25 + 3 * math.sin(day - math.pi / 2) + 20.5 * levels["winding"] + noise(0.5)
        for i, phase in enumerate("ryb"):
            data[f"winding_temperature_{phase}"] = round(ambient + w_rise - 1.5 * i + noise(0.3), 1)
    return data


MONITORS = ("current_sensor", "voltage_sensor", "current", "voltage", "oil", "top_oil", "winding")


def generate(
    *, start: datetime, days: int, seed: int | None = None, invalid_rate: float = 0.003,
) -> tuple[Iterator[tuple[datetime, dict[str, Any]]], dict[str, list[int]]]:
    """Returns (readings iterator, repairs) - repairs fill in as the iterator is consumed."""
    rng = random.Random(seed)
    start = start.replace(minute=0, second=0, microsecond=0, tzinfo=start.tzinfo or timezone.utc)
    states = {name: Degradation(random.Random(rng.random()), PROFILES[name]) for name in MONITORS}
    repairs = {name: state.repairs for name, state in states.items()}
    repairs["current"] = []   # load is managed, not repaired
    repairs["voltage"] = []   # supply voltage is external, nothing to repair
    repairs["winding"] = []   # duct blockage needs an internal inspection; not repaired in the window
    winding = WindingThermal()
    load = LoadModel(start, random.Random((seed or 0) + 1009))
    grid = GridVoltage(random.Random((seed or 0) + 2027))

    def readings():
        for hour in range(days * 24):
            levels = {name: state.step(hour) for name, state in states.items()}
            loading = load.loading(hour)
            data = reading_from_levels(levels, hour, rng, loading=loading, grid_deviation=grid.deviation(hour),
                                       winding_rises=winding.step(hour, phase_loadings(loading)))
            yield start + timedelta(hours=hour), corrupt(data, rng, invalid_rate)

    return readings(), repairs
