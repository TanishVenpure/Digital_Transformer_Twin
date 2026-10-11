"""Formulas for the two-timebar model of the CPRI transformer. Pure functions, no I/O.

Electrical (timebar 1, per 20 ms cycle):
  I_rated   = S_rated / (sqrt(3) * V_LL)
  loading % = I / I_rated x 100                 > 100 % warning, > 120 % trip
  deviation % = (V - V_nominal) / V_nominal x 100
     primary winding: +-6 %;  secondary winding: +6 % (high side) / -9 % (low side); both are judged
Precision devices (both timebars): per phase |measured - reference| / reference x 100 > 1 % -> maintenance.
Thermal (timebar 1, every second; IEC 60076-7 exponential model):
  K = I / I_rated
  dT_Ou = dT_Or * ((1 + R K^2) / (1 + R)) ** x            ultimate top-oil rise, first-order lag tau_O
  dT_HO,u = H * g_r * K ** y                               hot-spot gradient, lag tau_W
  T_H = T_O + dT_HO          (estimated hot spot - not the WTI, not the measured winding)
  WTI = T_O + dT_image       (thermal image device with its own constants)
  T_O is already absolute: dT_O = T_O - T_A, ambient is never added again.
  V = 2 ** ((T_H - 98) / 6);  L_EQ = sum V dt;  A_ratio = L_EQ / t    (ageing, never "remaining life")
Pressure (timebar 1, every minute; sealed tank, ideal gas):
  dV_oil = beta * V_oil0 * (T1 - T0);  V_gas1 = V_gas0 - dV_oil (invalid if <= 0)
  P1 = P0 * (T_gas1 / T_gas0) * (V_gas0 / V_gas1)   (kelvin);  gauge = P_abs - P_atm
Oil leak (timebar 2, daily):
  V_exp,noleak = V_ref * (1 + beta * (T_oil - T_ref));  L_cum = integral of q_leak dt
  R = V_meas - V_exp,noleak  (~ -L_cum);  alarm when R < -tolerance;  minimum level when V_meas < V_min
"""
import math
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from app.paths import PROJECT_ROOT

CONFIG_PATH = PROJECT_ROOT / "config" / "twin2.yaml"


@lru_cache
def load_config(path: Path | None = None) -> dict[str, Any]:
    return yaml.safe_load((path or CONFIG_PATH).read_text(encoding="utf-8"))


# ---------------------------------------------------------------- electrical

def rated_current(rated_kva: float, line_kv: float) -> float:
    """I_rated = S / (sqrt(3) * V_LL), amperes."""
    return rated_kva * 1000 / (math.sqrt(3) * line_kv * 1000)


def loading_pct(current_a: float, rated_a: float) -> float:
    return current_a / rated_a * 100


def overload_status(loading: float, cfg: dict[str, Any]) -> str:
    e = cfg["electrical"]
    if loading > e["overload_trip_pct"]:
        return "CRITICAL"
    if loading > e["overload_warning_pct"]:
        return "WARNING"
    return "NORMAL"


def voltage_deviation_pct(voltage: float, nominal: float) -> float:
    return (voltage - nominal) / nominal * 100


WINDINGS = ("primary", "secondary")


def voltage_rule(cfg: dict[str, Any], winding: str = "primary") -> dict[str, Any]:
    """The over / under-voltage band of one winding: {'winding', 'high_pct', 'low_pct'}."""
    return {"winding": winding, **cfg["electrical"]["voltage_limits"][winding]}


def voltage_rules(cfg: dict[str, Any]) -> list[dict[str, Any]]:
    return [voltage_rule(cfg, w) for w in WINDINGS]


def voltage_condition(deviation: float, rule: dict[str, Any]) -> str:
    """OVERVOLTAGE / UNDERVOLTAGE / NORMAL for one winding's deviation against its band."""
    if deviation > rule["high_pct"]:
        return "OVERVOLTAGE"
    if deviation < -rule["low_pct"]:
        return "UNDERVOLTAGE"
    return "NORMAL"


# ---------------------------------------------------------------- precision devices

def precision_deviation_pct(measured: float, reference: float) -> float:
    return abs(measured - reference) / abs(reference) * 100


def precision_status(deviations: list[float | None], cfg: dict[str, Any]) -> str:
    """MAINTENANCE_REQUIRED when any phase deviates more than the permitted accuracy; NO_DATA if none."""
    valid = [d for d in deviations if d is not None and math.isfinite(d)]
    if not valid:
        return "NO_DATA"
    return "MAINTENANCE_REQUIRED" if max(valid) > cfg["precision"]["max_deviation_pct"] else "NORMAL"


# ---------------------------------------------------------------- thermal (IEC 60076-7)

@dataclass
class ThermalState:
    top_oil_rise: float | None = None   # modelled dT_O
    hotspot_gradient: float | None = None   # modelled dT_HO
    image_gradient: float | None = None     # WTI thermal image


def ultimate_top_oil_rise(k: float, t: dict[str, Any]) -> float:
    r = t["loss_ratio"]
    return t["top_oil_rise_rated"] * ((1 + r * k * k) / (1 + r)) ** t["oil_exponent"]


def ultimate_hotspot_gradient(k: float, t: dict[str, Any]) -> float:
    return t["hotspot_factor"] * t["winding_gradient_rated"] * max(k, 0.0) ** t["winding_exponent"]


def lag(previous: float | None, target: float, dt_s: float, tau_min: float) -> float:
    """First-order lag toward `target` over dt seconds with time constant tau (minutes)."""
    if previous is None:
        return target
    return previous + (target - previous) * (1 - math.exp(-dt_s / (tau_min * 60)))


def thermal_step(state: ThermalState, k: float, dt_s: float, t: dict[str, Any], energized: bool = True) -> ThermalState:
    """Advance the modelled top-oil rise, hot-spot gradient and WTI image by one step.
    De-energised there are no losses at all, so every rise decays toward zero."""
    image_target = t["wti_image_gradient_rated"] * max(k, 0.0) ** t["winding_exponent"]
    oil_target = ultimate_top_oil_rise(k, t) if energized else 0.0
    return ThermalState(
        top_oil_rise=lag(state.top_oil_rise, oil_target, dt_s, t["tau_oil_min"]),
        hotspot_gradient=lag(state.hotspot_gradient, ultimate_hotspot_gradient(k, t), dt_s, t["tau_winding_min"]),
        image_gradient=lag(state.image_gradient, image_target, dt_s, t["wti_image_tau_min"]),
    )


def relative_ageing_rate(hotspot_c: float, t: dict[str, Any]) -> float:
    return 2 ** ((hotspot_c - t["ageing_reference_c"]) / t["ageing_doubling_k"])


def oti_condition(top_oil_c: float, t: dict[str, Any]) -> str:
    if top_oil_c >= t["oti_trip_c"]:
        return "TRIP"
    return "ALARM" if top_oil_c >= t["oti_alarm_c"] else "NORMAL"


def wti_condition(wti_c: float, t: dict[str, Any]) -> str:
    if wti_c >= t["wti_trip_c"]:
        return "TRIP"
    return "ALARM" if wti_c >= t["wti_alarm_c"] else "NORMAL"


def thermal_trip(oti: str, wti: str) -> bool:
    """TRIP = OTI trip OR WTI trip. The two are never averaged."""
    return oti == "TRIP" or wti == "TRIP"


# ---------------------------------------------------------------- pressure

def tank_pressure(top_oil_c: float, p: dict[str, Any]) -> dict[str, Any]:
    """Sealed-tank headspace pressure for an oil / gas temperature (gas assumed at the top-oil temperature)."""
    t0 = p["fill_temperature_c"] + 273.15
    t1 = top_oil_c + 273.15
    expansion = p["expansion_coeff_per_c"] * p["oil_volume_l"] * (top_oil_c - p["fill_temperature_c"])
    headspace = p["gas_volume_l"] - expansion
    if headspace <= 0:
        return {"oil_expansion_volume": expansion, "headspace_volume_final": headspace,
                "pressure_final_abs": None, "pressure_rise": None, "pressure_gauge": None, "validity_flag": False}
    p1 = p["fill_pressure_kpa_abs"] * (t1 / t0) * (p["gas_volume_l"] / headspace)
    return {"oil_expansion_volume": expansion, "headspace_volume_final": headspace, "pressure_final_abs": p1,
            "pressure_rise": p1 - p["fill_pressure_kpa_abs"], "pressure_gauge": p1 - p["atmospheric_kpa"],
            "validity_flag": True}


def pressure_status(p_abs: float | None, minutes_to_limit: float | None, p: dict[str, Any]) -> str:
    if p_abs is None:
        return "NO_DATA"
    if p_abs >= p["limit_kpa_abs"]:
        return "CRITICAL"
    if p_abs >= p["warning_kpa_abs"] or (minutes_to_limit is not None and minutes_to_limit <= p["forecast_minutes"]):
        return "WARNING"
    return "NORMAL"


# ---------------------------------------------------------------- oil leak

def expected_oil_volume(oil_temp_c: float, o: dict[str, Any]) -> float:
    """V_exp,noleak = V_ref * (1 + beta * (T_oil - T_ref))."""
    return o["reference_volume_l"] * (1 + o["expansion_coeff_per_c"] * (oil_temp_c - o["reference_temperature_c"]))


def oil_leak_status(measured_l: float, expected_l: float, o: dict[str, Any]) -> str:
    if measured_l < o["minimum_volume_l"]:
        return "CRITICAL"            # MINIMUM_OIL_LEVEL
    if measured_l - expected_l < -o["tolerance_l"]:
        return "WARNING"             # OIL_LEAK
    return "NORMAL"


# ---------------------------------------------------------------- short circuit (timebar 1, 20 ms cycles)
# Rated short-circuit current: I_sc = I_rated x 100 / Z%. Detection from per-cycle RMS current and voltage:
# pickup when any phase >= pickup_multiple x rated; a short circuit when a faulted phase's voltage collapses
# below sag_pct; cleared when every phase is below cleared_pct; breaker failure when that takes longer than
# breaker_failure_ms; internal when the tank pressure jumps during the fault (sudden-pressure relay).
# Effects (IEC 60076-5): I2t against I_sc for withstand_s, force against the design first peak
# (kappa x sqrt(2) x I_sc), and the adiabatic copper winding temperature
#   theta1 = theta0 + 2 (theta0 + 235) / (106000 / (J^2 t) - 1)      J in A/mm2, t in s

PHASES = ("R", "Y", "B")


def rated_short_circuit_multiple(sc: dict[str, Any]) -> float:
    """I_sc / I_rated = 100 / Z%."""
    return 100.0 / float(sc["impedance_pct"])


def adiabatic_winding_temperature(theta0_c: float, current_density_a_mm2: float, duration_s: float) -> float:
    """IEC 60076-5 average copper winding temperature after a short circuit of duration_s."""
    j2t = current_density_a_mm2 ** 2 * duration_s
    if j2t <= 0:
        return theta0_c
    denom = 106000.0 / j2t - 1
    return theta0_c + 2 * (theta0_c + 235) / denom if denom > 0 else float("inf")


def fault_kind(phases: list[str]) -> str:
    return {1: f"single line-to-ground ({phases[0]})", 2: f"line-to-line ({'-'.join(phases)})",
            3: "three-phase"}.get(len(phases), "")


def detect_short_circuit(i_pct: list[list[float]], v_pct: list[list[float]], dt_ms: float, sc: dict[str, Any],
                         pressure_rise_kpa: float = 0.0) -> dict[str, Any] | None:
    """Per-cycle RMS current (% of rated) and voltage (% of nominal) per phase -> the protection's decision."""
    pickup = sc["pickup_multiple"] * 100
    pick = next((k for k, row in enumerate(i_pct) if max(row) >= pickup), None)
    if pick is None:
        return None
    need = int(sc["trip_cycles"])
    if any(max(i_pct[k]) < pickup for k in range(pick, min(pick + need, len(i_pct)))):
        return None                                   # did not hold: a single-cycle spike, no trip
    trip = pick + need
    window = range(pick, min(trip, len(i_pct)))
    phases = [p for n, p in enumerate(PHASES) if any(i_pct[k][n] >= pickup for k in window)]
    sagged = [p for n, p in enumerate(PHASES) if any(v_pct[k][n] < sc["sag_pct"] for k in window)]
    cleared = next((k for k in range(trip, len(i_pct)) if max(i_pct[k]) < sc["cleared_pct"]), None)
    duration_ms = (cleared - pick) * dt_ms if cleared is not None else None
    short = any(p in sagged for p in phases)
    fault_cycles = range(pick, cleared if cleared is not None else len(i_pct))
    peak = max(max(i_pct[k]) for k in fault_cycles)
    vmin = min(min(v_pct[k][n] for n, p in enumerate(PHASES) if p in phases) for k in fault_cycles)
    return {
        "kind": "SHORT_CIRCUIT" if short else "INSTANT_OVERCURRENT", "phases": phases,
        "fault": fault_kind(phases) if short else "", "pickup_ms": round(pick * dt_ms, 1),
        "trip_ms": round(trip * dt_ms, 1), "cleared_ms": round(cleared * dt_ms, 1) if cleared is not None else None,
        "duration_ms": round(duration_ms, 1) if duration_ms is not None else None,
        "breaker_failure": duration_ms is None or duration_ms > sc["breaker_failure_ms"],
        "internal": pressure_rise_kpa >= sc["pressure_surge_kpa"], "pressure_rise_kpa": round(pressure_rise_kpa, 1),
        "peak_current_pct": round(peak, 1), "min_voltage_pct": round(vmin, 1),
        "i2_dt_pu2s": sum((max(i_pct[k]) / 100) ** 2 * dt_ms / 1000 for k in fault_cycles),
    }


def fault_effects(event: dict[str, Any], winding_before_c: float, sc: dict[str, Any]) -> dict[str, Any]:
    """I2t and force against the IEC 60076-5 design withstand, and the winding temperature after the fault."""
    m = rated_short_circuit_multiple(sc)
    duration = (event["duration_ms"] or 0) / 1000
    i_pu = event["peak_current_pct"] / 100
    i2t_pct = event["i2_dt_pu2s"] / (m * m * sc["withstand_s"]) * 100
    force_pct = (i_pu / m) ** 2 * 100                      # same kappa on both sides: ratio of peaks squared
    theta1 = adiabatic_winding_temperature(winding_before_c, sc["rated_current_density_a_mm2"] * i_pu, duration)
    return {"i2t_pct_of_withstand": round(i2t_pct, 2), "force_pct_of_design": round(force_pct, 1),
            "peak_asymmetric_pu": round(sc["peak_factor_kappa"] * math.sqrt(2) * i_pu, 1),
            "winding_before_c": round(winding_before_c, 1), "winding_after_c": round(theta1, 1),
            "winding_limit_c": sc["winding_limit_c"]}
