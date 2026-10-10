"""Timebar 1: one simulated day of fast signals for the CPRI transformer, evaluated with app/twin2/formulas.

Sampling: V / I as per-cycle RMS every 20 ms (stored at full rate in windows around each event, plus a
1 s decimated stream with the min / max of every second), top oil and winding every 1 s, ambient every
1 min, tank pressure every 1 min, precision devices every 1 min.

The day (UTC), chosen so that every condition appears at least once:
  03:20      over-voltage at light load (primary ~+8 %, secondary ~+6.7 % for 90 s) -> OVERVOLTAGE on both windings
  05:12      winding temperature probe drop-out for 20 s                           -> data error, no false trip
  07:45:10   2 s voltage dip (motor start upstream: primary ~-8.5 %, secondary ~-10 %)
                                                                                    -> UNDERVOLTAGE on both windings
             Both windings are judged: primary +-6 %, secondary +6 % / -9 %. The secondary follows the
             primary minus the load-dependent regulation drop (~2.5 % at rated load).
  10:05      a neighbouring feeder is transferred on: loading 112 %, a 126 % surge
             at 10:21 -> overload trip, transformer out until 10:40                 -> OVERLOAD warning, trip
  11:00      ONAN radiator cooling fails (two radiator banks valved off / blocked): top-oil rise up to
             x3.4 over 30 min; operators hold the load at 70 %
  12:00      blocked cooling duct in the winding: the winding gradient grows up to +40 % over 4 h (the excess
             scales with the load - no current, no excess)
             -> COOLING_DEGRADATION, WINDING_EXCESS_HEATING, top-oil rise > 40 K, ACCELERATED_THERMAL_AGEING
             -> OTI alarm, PRESSURE warning (near failure, caught), OTI trip; out of service 90 min
  then       one radiator bank reopened, one still isolated (rise x1.7); the duct is still blocked
  17:30      evening peak with the neighbouring feeder back on: loading ~117 % (warning, below trip)
             -> WTI alarm, then WTI trip; out of service 90 min, all cooling and the duct repaired
  18:00-22:00 the current device's Y phase drifts past 1 % accuracy                -> precision maintenance
             (CT / PT deviations need current / voltage: no data while the transformer is out of service)
The IEC model ("expected") runs on load and ambient; it is paused while the transformer is out of service and
restarts from the measured top oil when it is re-energised (IEC measured-top-oil method).
"""
import math
import random
from datetime import datetime, timedelta, timezone
from typing import Any

from app.twin2 import formulas as f

DAY = 86400
CYCLE = 0.02


def _t(h: float, m: float = 0, s: float = 0) -> float:
    return h * 3600 + m * 60 + s


def _smooth(t: float, start: float, end: float, ramp: float) -> float:
    """0 -> 1 -> 0 envelope with linear ramps of `ramp` seconds at each end."""
    if t < start or t > end:
        return 0.0
    if ramp <= 0:
        return 1.0
    return max(0.0, min(1.0, (t - start) / ramp, (end - t) / ramp))


# ---------------------------------------------------------------- scenario (true physical values)

class Scenario:
    OV = (_t(3, 20), _t(3, 21, 30), 2.0, 6.3)         # start, end, ramp s, extra deviation %
    UV = (_t(7, 45, 10), _t(7, 45, 12), 0.06, -9.5)
    OVERLOAD = (_t(10, 5), _t(10, 21, 15))
    SURGE = (_t(10, 21), _t(10, 21, 40))
    COOLING_FAULT = _t(11, 0)
    DUCT_FAULT = _t(12, 0)
    PROBE_DROPOUT = (_t(5, 12), _t(5, 12, 20))
    DRIFT = (_t(18), _t(22))
    RESTORE_DELAY = 5400                   # s out of service after a thermal trip (inspection, repair)
    EVENING_OVERLOAD = _t(17, 30)

    def __init__(self, seed: int):
        self.rng = random.Random(seed)

    def base_loading(self, t: float, phase: int = 0) -> float:
        """Demand as a fraction of rated (K). `phase` = number of thermal trips so far."""
        h = t / 3600
        night = 0.46 + 0.04 * math.cos(2 * math.pi * (h - 3) / 24)
        day = 0.40 * math.exp(-((h - 12.5) / 4.2) ** 2) + 0.22 * math.exp(-((h - 19.5) / 1.6) ** 2)
        k = night + day
        if phase >= 2:                              # after the 2nd trip: light load for the night
            return 0.6
        if t >= self.EVENING_OVERLOAD and phase == 1:   # evening peak + transferred feeder, below the trip
            return 1.155 + 0.008 * math.sin(2 * math.pi * t / 900)
        if phase == 1:
            return 0.75
        if self.COOLING_FAULT <= t:                 # afternoon: operators hold the load at 70 %
            k = min(k, 0.70)
        if self.OVERLOAD[0] <= t <= self.OVERLOAD[1]:
            k = 0.78 + 0.34 * min(1.0, (t - self.OVERLOAD[0]) / 300)        # feeder transfer, up to 1.12
        if self.SURGE[0] <= t <= self.SURGE[1]:
            k = 1.26
        return k

    def voltage_extra_pct(self, t: float) -> float:
        start, end, ramp, depth = self.OV
        extra = depth * _smooth(t, start, end, ramp)
        start, end, ramp, depth = self.UV
        extra += depth * _smooth(t, start, end, ramp)
        return extra

    def voltage_base_pct(self, t: float, phase: int = 0) -> float:
        """Supply deviation follows load: high at night (light load), lower at peak."""
        return 1.4 - 2.2 * (self.base_loading(t, phase) - 0.46) / 0.5

    COOLING_LOSS = 3.4                     # x the IEC top-oil rise with two radiator banks out (ONAN: no fans)
    COOLING_PARTIAL = 1.7                  # one bank reopened, one still isolated
    DUCT_GROWTH = 0.40                     # blocked winding duct: gradient grows up to +40 %

    def cooling_factor(self, t: float, phase: int) -> float:
        if phase == 1:
            return self.COOLING_PARTIAL
        if phase >= 2 or t < self.COOLING_FAULT:
            return 1.0
        return 1.0 + (self.COOLING_LOSS - 1.0) * min(1.0, (t - self.COOLING_FAULT) / 1800)

    def duct_factor(self, t: float, phase: int) -> float:
        """Extra fraction of the winding gradient caused by the blocked duct (0 = healthy)."""
        if phase >= 2 or t < self.DUCT_FAULT:
            return 0.0
        return self.DUCT_GROWTH * min(1.0, (t - self.DUCT_FAULT) / (4 * 3600))

    def current_drift_pct(self, t: float) -> float:
        start, end = self.DRIFT
        if t < start:
            return 0.3
        return 0.3 + 0.95 * min(1.0, (t - start) / (end - start))

    def ambient(self, minute: int) -> float:
        h = minute / 60
        return 31.5 + 7.0 * math.sin(2 * math.pi * (h - 9) / 24) + self.rng.gauss(0, 0.15)


# ---------------------------------------------------------------- simulation

def simulate(cfg: dict[str, Any] | None = None) -> dict[str, Any]:
    cfg = cfg or f.load_config()
    tr, th, pr = cfg["transformer"], cfg["thermal"], cfg["pressure"]
    seed = cfg["timebar1"]["seed"]
    sc = Scenario(seed)
    rng = random.Random(seed + 1)
    day0 = datetime.fromisoformat(cfg["timebar1"]["date"]).replace(tzinfo=timezone.utc)
    i_rated_pri = f.rated_current(tr["rated_kva"], tr["primary_kv"])
    i_rated_sec = f.rated_current(tr["rated_kva"], tr["secondary_kv"])
    v_nom_pri = tr["primary_kv"] * 1000                       # line-to-line
    v_nom_sec = tr["secondary_kv"] * 1000 / math.sqrt(3)      # phase-to-neutral
    rule_pri, rule_sec = f.voltage_rule(cfg, "primary"), f.voltage_rule(cfg, "secondary")

    ambient_min = [sc.ambient(m) for m in range(DAY // 60)]

    energized = True
    out_until = None
    phase = 0                           # thermal trips so far
    state_true = f.ThermalState()       # actual transformer (with the faults)
    state_model = f.ThermalState()      # IEC model as configured (no faults)
    k_true_prev = 0.0
    L_eq_s = 0.0                        # equivalent ageing, seconds
    sec: dict[str, list] = {key: [] for key in (
        "energized", "loading_max", "loading_mean", "loading_pri", "v_pri_min", "v_pri_max", "v_sec_min", "v_sec_max",
        "ambient", "load_factor", "top_oil", "top_oil_expected", "top_oil_rise_measured", "top_oil_rise_expected",
        "hotspot_temperature_estimated", "wti", "winding_measured", "winding_expected", "winding_thermal_residual",
        "relative_ageing_rate", "equivalent_ageing_hours", "ageing_ratio",
        "oti_condition", "wti_condition", "overload", "voltage", "voltage_pri", "voltage_sec", "probe_valid")}
    events: list[dict[str, Any]] = []
    trip_reason = None
    surge_trip_after = 2.0              # s above the trip setting before the overload protection opens
    above_trip_s = 0.0

    for s in range(DAY):
        t = float(s)
        # --- re-energise after an outage
        if not energized and out_until is not None and t >= out_until:
            energized, out_until = True, None
            # IEC measured-top-oil method: on re-energising, the model restarts from the measured oil state
            # (otherwise heat left over from before the outage would read as a cooling fault). The expected
            # series is blank while out of service, so the restart shows as a break, not a vertical jump.
            state_model = f.ThermalState(top_oil_rise=actual_rise, hotspot_gradient=state_model.hotspot_gradient,
                                         image_gradient=state_model.image_gradient)
        k = sc.base_loading(t, phase) if energized else 0.0
        if energized and k > 0:
            k *= 1 + rng.gauss(0, 0.006)
        # ---- electrical: per-second min / max of the 50 per-cycle values
        cyc = [t + c * CYCLE for c in range(50)]
        if energized:
            if sc.OV[0] - 3 <= t <= sc.OV[1] + 3 or sc.UV[0] - 1 <= t <= sc.UV[1] + 1:
                devs = [sc.voltage_base_pct(x, phase) + sc.voltage_extra_pct(x) for x in cyc]
            else:
                devs = [sc.voltage_base_pct(t, phase) + sc.voltage_extra_pct(t)]
            noise = rng.gauss(0, 0.12)
            dmin, dmax = min(devs) + noise - 0.08, max(devs) + noise + 0.08
            loading = k * 100
            lmax = loading * (1 + 0.004 + abs(rng.gauss(0, 0.002)))
            sec["loading_max"].append(round(lmax, 2))
            sec["loading_mean"].append(round(loading, 2))
            sec["loading_pri"].append(round(loading * (1 + rng.gauss(0, 0.002)) , 2))
            # the secondary follows the primary (fixed tap ratio) minus the load-dependent regulation drop
            smin, smax = dmin - 2.5 * k, dmax - 2.5 * k
            sec["v_pri_min"].append(round(dmin, 3))
            sec["v_pri_max"].append(round(dmax, 3))
            sec["v_sec_min"].append(round(smin, 3))
            sec["v_sec_max"].append(round(smax, 3))
            ov = f.overload_status(lmax, cfg)
            # both windings are judged, each against its own band (primary +-6 %, secondary +6 / -9 %)
            vc_pri = f.voltage_condition(dmax, rule_pri)
            vc_pri = vc_pri if vc_pri != "NORMAL" else f.voltage_condition(dmin, rule_pri)
            vc_sec = f.voltage_condition(smax, rule_sec)
            vc_sec = vc_sec if vc_sec != "NORMAL" else f.voltage_condition(smin, rule_sec)
            vc = vc_pri if vc_pri != "NORMAL" else vc_sec
        else:
            for key in ("loading_max", "loading_mean", "loading_pri", "v_pri_min", "v_pri_max", "v_sec_min", "v_sec_max"):
                sec[key].append(None)
            ov, vc, vc_pri, vc_sec = "OFF", "OFF", "OFF", "OFF"
        sec["energized"].append(energized)
        sec["overload"].append(ov)
        sec["voltage"].append(vc)
        sec["voltage_pri"].append(vc_pri)
        sec["voltage_sec"].append(vc_sec)
        # overload protection
        if energized and ov == "CRITICAL":
            above_trip_s += 1
            if above_trip_s >= surge_trip_after:
                energized, out_until, trip_reason = False, _t(10, 40), "OVERLOAD"
                events.append({"t": t, "type": "OVERLOAD_TRIP_OPERATED", "level": "CRITICAL",
                               "message": "Overload protection opened the breaker; transformer out of service until 10:40."})
        else:
            above_trip_s = 0.0

        # ---- thermal (1 s)
        amb = ambient_min[s // 60]
        k_true = k
        state_true = f.thermal_step(state_true, k_true, 1.0, th, energized)
        state_model = f.thermal_step(state_model, k_true, 1.0, th, energized)
        cool = sc.cooling_factor(t, phase)
        # the actual oil carries the cooling fault: its rise is the IEC rise times the cooling factor
        if s == 0:
            actual_rise = state_true.top_oil_rise
        else:
            target = f.ultimate_top_oil_rise(k_true, th) * cool if energized else 0.0
            actual_rise = f.lag(actual_rise, target, 1.0, th["tau_oil_min"])
        top_oil = amb + actual_rise + rng.gauss(0, 0.08)
        top_oil_exp = amb + state_model.top_oil_rise
        hotspot = top_oil + state_model.hotspot_gradient                       # T_H = T_O + dT_HO (model)
        wti = top_oil + state_true.image_gradient + rng.gauss(0, 0.08)        # thermal image device
        # blocked duct: the winding gradient itself is larger, so the excess vanishes with the load
        winding_true = top_oil + state_true.hotspot_gradient * (1 + sc.duct_factor(t, phase))
        probe_ok = not (sc.PROBE_DROPOUT[0] <= t < sc.PROBE_DROPOUT[1])
        winding_meas = winding_true + rng.gauss(0, 0.1) if probe_ok else None
        winding_exp = top_oil + state_model.hotspot_gradient
        residual = winding_meas - winding_exp if winding_meas is not None else None
        v_age = f.relative_ageing_rate(hotspot, th)
        L_eq_s += v_age
        oti, wtic = f.oti_condition(top_oil, th), f.wti_condition(wti, th)
        exp_on = energized or s == 0           # model output shown only while in service
        for key, val in (("ambient", amb), ("load_factor", k_true), ("top_oil", top_oil),
                         ("top_oil_expected", top_oil_exp if exp_on else None),
                         ("top_oil_rise_measured", top_oil - amb),
                         ("top_oil_rise_expected", state_model.top_oil_rise if exp_on else None),
                         ("hotspot_temperature_estimated", hotspot), ("wti", wti), ("winding_measured", winding_meas),
                         ("winding_expected", winding_exp), ("winding_thermal_residual", residual),
                         ("relative_ageing_rate", v_age), ("equivalent_ageing_hours", L_eq_s / 3600),
                         ("ageing_ratio", L_eq_s / (s + 1))):
            sec[key].append(round(val, 4) if isinstance(val, float) else val)
        sec["oti_condition"].append(oti)
        sec["wti_condition"].append(wtic)
        sec["probe_valid"].append(probe_ok)
        if energized and f.thermal_trip(oti, wtic):
            energized, out_until, trip_reason = False, t + sc.RESTORE_DELAY, "THERMAL"
            hh, mm = divmod(int(out_until) // 60, 60)
            back = f"{hh:02d}:{mm:02d} " + ("with one radiator bank reopened (one still isolated)" if phase == 0
                                             else "with all cooling and the winding duct repaired")
            phase += 1
            on = "OTI" if oti == "TRIP" else "WTI"
            events.append({"t": t, "type": "THERMAL_TRIP_OPERATED", "level": "CRITICAL",
                           "message": f"Thermal trip on {on} (OTI {oti.lower()}, WTI {wtic.lower()}): top oil "
                                      f"{top_oil:.1f} degC, WTI {wti:.1f} degC. Breaker opened; back in service {back}."})

    data = {"day0": day0, "sec": sec, "ambient_min": ambient_min, "events": events, "scenario": sc,
            "i_rated_pri": i_rated_pri, "i_rated_sec": i_rated_sec, "v_nom_pri": v_nom_pri, "v_nom_sec": v_nom_sec}
    data["pressure"] = _pressure(data, cfg)
    data["precision"] = _precision_1min(sc, cfg, random.Random(seed + 2), sec["energized"])
    data["windows"] = _windows_20ms(data, cfg, random.Random(seed + 3))
    return data


def _pressure(data: dict[str, Any], cfg: dict[str, Any]) -> list[dict[str, Any]]:
    """Every minute: sealed-tank pressure from the top-oil temperature, with a 30 min linear forecast."""
    p = cfg["pressure"]
    top = data["sec"]["top_oil"]
    rows = []
    for m in range(DAY // 60):
        row = f.tank_pressure(top[m * 60], p)
        row["minute"] = m
        rows.append(row)
    for m, row in enumerate(rows):
        mtl = None
        if m >= 10 and row["validity_flag"] and rows[m - 10]["validity_flag"]:
            slope = (row["pressure_final_abs"] - rows[m - 10]["pressure_final_abs"]) / 10
            if slope > 0 and row["pressure_final_abs"] < p["limit_kpa_abs"]:
                mtl = (p["limit_kpa_abs"] - row["pressure_final_abs"]) / slope
        row["minutes_to_limit"] = mtl
        row["status"] = f.pressure_status(row["pressure_final_abs"], mtl, p)
    return rows


def _precision_1min(sc: Scenario, cfg: dict[str, Any], rng: random.Random,
                    energized: list[bool] | None = None) -> list[dict[str, Any]]:
    """CT / PT are compared with the reference instrument on the live current / voltage: with the
    transformer out of service there is nothing to compare (NO_DATA). The temperature probe still reads."""
    rows = []
    for m in range(DAY // 60):
        t = m * 60.0
        live = energized is None or all(energized[m * 60:(m + 1) * 60])
        row = {"minute": m}
        for device, base in (("current", 0.3), ("voltage", 0.25), ("temperature", 0.35)):
            devs = []
            for i, phase in enumerate("RYB"):
                dev = base * (0.8 + 0.2 * i) + abs(rng.gauss(0, 0.05))
                if device == "current" and phase == "Y":
                    dev = sc.current_drift_pct(t) + abs(rng.gauss(0, 0.03))
                devs.append(round(dev, 3) if live or device == "temperature" else None)
            row[device] = devs
            row[f"{device}_status"] = f.precision_status(devs, cfg)
        rows.append(row)
    return rows


def _windows_20ms(data: dict[str, Any], cfg: dict[str, Any], rng: random.Random) -> list[dict[str, Any]]:
    """Full-rate (20 ms per-cycle RMS) windows around each electrical event."""
    sc: Scenario = data["scenario"]
    first_overload = next(i for i, v in enumerate(data["sec"]["overload"]) if v == "WARNING")
    trip = next(e["t"] for e in data["events"] if e["type"] == "OVERLOAD_TRIP_OPERATED")
    specs = [
        ("overvoltage_start", "OVERVOLTAGE", sc.OV[0] - 2, sc.OV[0] + 6),
        ("undervoltage_dip", "UNDERVOLTAGE", sc.UV[0] - 2, sc.UV[1] + 2),
        ("overload_onset", "OVERLOAD", first_overload - 4, first_overload + 4),
        ("overload_trip", "OVERLOAD", sc.SURGE[0] - 2, trip + 2),
    ]
    out = []
    for wid, kind, a, b in specs:
        n = int(round((b - a) / CYCLE))
        t0 = a
        loading, vdev, vsec = [], [], []
        for c in range(n):
            x = a + c * CYCLE
            s = int(x)
            on = data["sec"]["energized"][s] if 0 <= s < DAY else True
            if not on:
                loading.append(0.0)
                vdev.append(None)
                vsec.append(None)
                continue
            k = sc.base_loading(x)
            loading.append(round(k * 100 * (1 + rng.gauss(0, 0.003)), 2))
            v = sc.voltage_base_pct(x) + sc.voltage_extra_pct(x) + rng.gauss(0, 0.06)
            vdev.append(round(v, 3))
            vsec.append(round(v - 2.5 * k, 3))
        out.append({"id": wid, "kind": kind, "t0": t0, "dt": CYCLE, "loading_pct": loading,
                    "voltage_deviation_pct": vdev, "voltage_secondary_pct": vsec})
    return out
