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

from app.alerts.configuration import PROJECT_ROOT

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


# ---------------------------------------------------------------- insulation (timebar 2)
# Paper ageing: 1/DP(t) - 1/DP0 = sum k dt,  k = A(w) * exp(-E / (R * T_H))   (IEC 60076-7:2018 Annex A form)
# Moisture: paper water content WCP (%) -> oil relative saturation RS (%) -> water in oil (ppm) = RS * W_sat(T)
# DGA: CO + CO2 generated in proportion to the paper ageing (d(1/DP)); the CO share grows with temperature.

GAS_CONSTANT = 8.314


def arrhenius_a(wcp_pct: float, ins: dict[str, Any]) -> float:
    """Pre-exponential factor A (1/h) for the paper water content, log-interpolated between the table points."""
    pts = [(float(w), float(a)) for w, a in ins["arrhenius_a_per_h"]]   # YAML may read 4.1e10 as text
    w = min(max(wcp_pct, pts[0][0]), pts[-1][0])
    for (w0, a0), (w1, a1) in zip(pts, pts[1:]):
        if w <= w1:
            frac = (w - w0) / (w1 - w0)
            return math.exp(math.log(a0) + frac * (math.log(a1) - math.log(a0)))
    return pts[-1][1]


def paper_ageing_k(hotspot_c: float, wcp_pct: float, ins: dict[str, Any]) -> float:
    """Chain-scission rate k (1/h): d(1/DP)/dt."""
    return arrhenius_a(wcp_pct, ins) * math.exp(-float(ins["activation_energy_j_mol"]) / (GAS_CONSTANT * (hotspot_c + 273.15)))


def water_saturation_ppm(oil_c: float) -> float:
    """Water solubility of mineral oil (ppm): log10(W_s) = 7.0895 - 1567 / T(K)."""
    return 10 ** (7.0895 - 1567 / (oil_c + 273.15))


def relative_saturation_pct(wcp_pct: float) -> float:
    """Oil relative saturation in equilibrium with the paper (simplified moisture-equilibrium curve)."""
    return 100 * (max(wcp_pct, 0.0) / 8.0) ** 1.6


def water_in_oil_ppm(wcp_pct: float, oil_c: float) -> float:
    return relative_saturation_pct(wcp_pct) / 100 * water_saturation_ppm(oil_c)


def co2_co_generation_ratio(hotspot_c: float) -> float:
    """CO2 / CO produced by paper: ~10 up to 80 degC, falling to ~1.5 by 110 degC (paper overheating)."""
    return max(1.5, min(10.0, 10.0 - (hotspot_c - 80.0) * (8.5 / 30.0)))


def insulation_status(dp: float, ins: dict[str, Any]) -> str:
    if dp <= ins["dp_critical"]:
        return "CRITICAL"
    return "WARNING" if dp <= ins["dp_warning"] else "NORMAL"


def moisture_status(wcp_pct: float, ins: dict[str, Any]) -> str:
    if wcp_pct >= ins["wcp_critical_pct"]:
        return "CRITICAL"
    return "WARNING" if wcp_pct >= ins["wcp_warning_pct"] else "NORMAL"
