"""Timebar 2: one year of daily readings - oil leakage and the three precision devices.

Oil (once a day): the oil temperature sets the volume a healthy tank should hold,
V_exp,noleak = V_ref * (1 + beta * (T_oil - T_ref)). A leak removes L_cum = sum(q_leak * 1 day), so
R = V_meas - V_exp,noleak ~ -L_cum. Two leaks in the year:
  leak 1 (gasket weep) -> OIL_LEAK alarm on the tolerance band, gasket replaced and topped up;
  leak 2 (faster, repair delayed for parts) -> OIL_LEAK alarm, then MINIMUM_OIL_LEVEL, then repaired.
Precision devices (once a day): per-phase deviation from a reference instrument. Each device drifts after
calibration until a phase passes 1 % (every ~4-6 months), is flagged for maintenance and recalibrated a few
days later.

Insulation (once a day, from an hourly profile inside each day):
  hot spot T_H(h) = top oil(h) + H g_r K(h)^y, the top oil swinging about the day's mean oil temperature
  paper ageing    1/DP += A(WCP) exp(-E / (R T_H)) dt   -> estimated DP; IEC V gives the day's equivalent ageing
  moisture        paper water WCP grows with ageing (scission releases water) and while the second gasket leak
                  is open (humid air); the oil sensor reads RS and ppm = RS x W_sat(T_oil), so ppm swings with
                  the season even when the paper is not getting wetter
  DGA             CO + CO2 generated in proportion to d(1/DP); hotter paper makes relatively more CO, so the
                  weekly CO2 / CO increment ratio falls below 3 after the overheating day
  maintenance     moisture warning -> oil reconditioning (hot-oil vacuum drying + degassing) 15 days later:
                  paper water down, CO / CO2 back to degassed values
The timebar-1 day (2026-07-15) is one day of this year: its per-minute hot spot replaces the profile on that
day, so the overheating, OTI trip and WTI trip day shows up in the paper ageing and the gases.
"""
import math
import random
from datetime import datetime, timedelta, timezone
from typing import Any

from app.twin2 import formulas as f

DEVICES = ("current", "voltage", "temperature")


def _hourly_hotspot(oil_mean: float, day_k: list[float], th: dict[str, Any]) -> list[float]:
    """24 hourly hot spots: the top oil swings about the day's mean with the load, plus the winding gradient."""
    rises = [f.ultimate_top_oil_rise(k, th) for k in day_k]
    mean_rise = sum(rises) / len(rises)
    return [oil_mean + 0.6 * (r - mean_rise) + f.ultimate_hotspot_gradient(k, th) for r, k in zip(rises, day_k)]


def _day_loading(yday: int, rng: random.Random) -> list[float]:
    """Hourly load factor K: night trough, midday plateau and evening peak, a little higher in summer."""
    season = 1 + 0.06 * math.cos(2 * math.pi * (yday - 170) / 365)
    weather = rng.gauss(1.0, 0.04)
    out = []
    for h in range(24):
        night = 0.46 + 0.04 * math.cos(2 * math.pi * (h - 3) / 24)
        day = 0.30 * math.exp(-((h - 12.5) / 4.2) ** 2) + 0.20 * math.exp(-((h - 19.5) / 1.6) ** 2)
        out.append((night + day) * season * weather)
    return out


def simulate(cfg: dict[str, Any] | None = None, day_override: dict[str, Any] | None = None) -> dict[str, Any]:
    """day_override: {"day": index, "hotspot_minutes": [...]} - the timebar-1 day's per-minute hot spot."""
    cfg = cfg or f.load_config()
    tb = cfg["timebar2"]
    o = cfg["oil_leak"]
    th, ins = cfg["thermal"], cfg["insulation"]
    irng = random.Random(tb["seed"] + 101)
    rng = random.Random(tb["seed"])
    start = datetime.fromisoformat(tb["start"]).replace(tzinfo=timezone.utc)
    days = tb["days"]

    # ---- oil leak scenario: (start day, initial rate L/day, rate growth L/day per day, repair delay after flag)
    leaks = [
        {"start": 70, "rate": 0.10, "growth": 0.006, "repair_after": "alarm", "delay": 12},
        {"start": 205, "rate": 0.30, "growth": 0.012, "repair_after": "minimum", "delay": 6},
    ]
    leaked = 0.0
    active = None
    pending_repair = None
    rows: list[dict[str, Any]] = []
    events: list[dict[str, Any]] = []
    flagged_alarm = flagged_min = False

    # ---- precision devices: per device, the phase that drifts and how fast (crosses 1 % in ~4-6 months)
    drift = {}
    for i, dev in enumerate(DEVICES):
        drift[dev] = {"phase": rng.randrange(3), "since": 0, "offset": 15 * i,
                      "rate": rng.uniform(0.75, 0.85) / rng.uniform(120, 165), "flagged": None, "recal": None}

    # ---- insulation state
    inv_dp = 1 / ins["dp_initial"]
    wcp = ins["wcp_initial_pct"]
    co, co2 = ins["co_initial_ppm"], ins["co2_initial_ppm"]
    loss_of_life_h = 0.0
    recondition_on = None
    moisture_flagged = False
    gas_hist: list[tuple[float, float]] = []
    dga_flagged = False

    for d in range(days):
        day = start + timedelta(days=d)
        yday = day.timetuple().tm_yday
        # oil temperature: daily mean, seasonal (hottest mid-year) + load noise
        oil_temp = 48 + 9 * math.cos(2 * math.pi * (yday - 190) / 365) + rng.gauss(0, 1.5)
        # --- leak dynamics
        for leak in leaks:
            if d == leak["start"]:
                active = dict(leak, day0=d)
        if active is not None:
            rate = active["rate"] + active["growth"] * (d - active["day0"])
            leaked += rate
        expected = f.expected_oil_volume(oil_temp, o)
        measured = expected - leaked + rng.gauss(0, 0.35)
        residual = measured - expected
        status = f.oil_leak_status(measured, expected, o)
        if status == "WARNING" and not flagged_alarm:
            flagged_alarm = True
            events.append({"day": d, "type": "OIL_LEAK", "level": "WARNING",
                           "message": f"Oil volume {residual:.1f} L below the no-leak expectation at {oil_temp:.0f} degC "
                                      f"(tolerance {o['tolerance_l']:.0f} L): leak suspected; cumulative loss ~{leaked:.1f} L."})
            if active and active["repair_after"] == "alarm":
                pending_repair = d + active["delay"]
        if status == "CRITICAL" and not flagged_min:
            flagged_min = True
            events.append({"day": d, "type": "MINIMUM_OIL_LEVEL", "level": "CRITICAL",
                           "message": f"Measured oil volume {measured:.1f} L is below the minimum {o['minimum_volume_l']:.0f} L."})
            if active and active["repair_after"] == "minimum":
                pending_repair = d + active["delay"]
        if pending_repair is not None and d >= pending_repair:
            events.append({"day": d, "type": "OIL_LEAK_REPAIRED", "level": "MAINTENANCE",
                           "message": f"Leak repaired and tank topped up with {leaked:.1f} L."})
            leaked, active, pending_repair = 0.0, None, None
            flagged_alarm = flagged_min = False
        leak_open = active is not None
        row = {"day": d, "date": day.date().isoformat(), "oil_temperature": round(oil_temp, 2),
               "volume_expected_noleak": round(expected, 2), "volume_measured": round(measured, 2),
               "residual": round(residual, 2), "cumulative_leak": round(leaked, 2), "oil_status": status}

        # --- insulation: hourly hot spot -> paper ageing, water and gases
        if day_override and d == day_override["day"]:
            temps, dt_h = day_override["hotspot_minutes"], 1 / 60          # the timebar-1 day, per minute
        else:
            temps, dt_h = _hourly_hotspot(oil_temp, _day_loading(yday, irng), th), 1.0
        d_inv = co_gen = co2_gen = v_hours = 0.0
        for t_h in temps:
            step = f.paper_ageing_k(t_h, wcp, ins) * dt_h
            d_inv += step
            gas = step * ins["gas_ppm_per_inv_dp"]
            r = f.co2_co_generation_ratio(t_h)
            co_gen += gas / (1 + r)
            co2_gen += gas * r / (1 + r)
            v_hours += f.relative_ageing_rate(t_h, th) * dt_h
        inv_dp += d_inv
        loss_of_life_h += v_hours
        wcp += ins["water_per_inv_dp"] * d_inv + (ins["ingress_pct_per_day"] if leak_open and d >= leaks[1]["start"] else 0.0)
        co += co_gen
        co2 += co2_gen
        if recondition_on is not None and d >= recondition_on:
            before = wcp
            wcp = max(1.0, wcp - ins["drying_pct"])
            co, co2 = ins["degassed_co_ppm"], ins["degassed_co2_ppm"]
            gas_hist.clear()
            events.append({"day": d, "type": "OIL_RECONDITIONED", "level": "MAINTENANCE",
                           "message": f"Oil reconditioned (hot-oil vacuum drying + degassing): paper water {before:.2f} -> "
                                      f"{wcp:.2f} %, CO / CO2 back to {co:.0f} / {co2:.0f} ppm."})
            recondition_on, moisture_flagged = None, False
        gas_hist.append((co, co2))
        dp = 1 / inv_dp
        mstat = f.moisture_status(wcp, ins)
        if mstat != "NORMAL" and not moisture_flagged:
            moisture_flagged = True
            recondition_on = d + ins["reconditioning_delay_days"]
            events.append({"day": d, "type": "PAPER_MOISTURE", "level": mstat,
                           "message": f"Estimated paper water content {wcp:.2f} % (warning {ins['wcp_warning_pct']} %, oil "
                                      f"{f.water_in_oil_ppm(wcp, oil_temp):.0f} ppm at {oil_temp:.0f} degC): wet paper ages faster - "
                                      f"oil reconditioning planned in {ins['reconditioning_delay_days']} days."})
        # weekly increments for the CO2 / CO ratio (IEC 60599 uses increments when the background is high)
        dco = co - gas_hist[-8][0] if len(gas_hist) >= 8 else None
        dco2 = co2 - gas_hist[-8][1] if len(gas_hist) >= 8 else None
        ratio = dco2 / dco if dco and dco > 0 else None
        paper_hot = ratio is not None and ratio < ins["ratio_limit"] and dco >= ins["ratio_min_co_increase_ppm"]
        gstat = "WARNING" if (paper_hot or co > ins["co_typical_ppm"] or co2 > ins["co2_typical_ppm"]) else "NORMAL"
        if gstat == "WARNING" and not dga_flagged:
            dga_flagged = True
            why = (f"weekly CO2 / CO increment {ratio:.1f} (< {ins['ratio_limit']:.0f}) with CO up {dco:.0f} ppm"
                   if paper_hot else f"CO {co:.0f} ppm / CO2 {co2:.0f} ppm above the IEC 60599 typical values")
            events.append({"day": d, "type": "DGA_PAPER_OVERHEATING", "level": "WARNING",
                           "message": f"Dissolved gases: {why} - the paper insulation has been overheated. Confirm with a "
                                      f"furan (2-FAL) lab test and review the thermal events of the last week."})
        elif gstat == "NORMAL":
            dga_flagged = False
        istat = f.insulation_status(dp, ins)
        if v_hours >= ins["ageing_day_warning_h"] and istat == "NORMAL":
            istat = "WARNING"
            events.append({"day": d, "type": "ACCELERATED_INSULATION_AGEING", "level": "WARNING",
                           "message": f"Paper aged {v_hours:.1f} h in one day (IEC V; hot spot up to {max(temps):.0f} degC): "
                                      f"estimated DP {dp:.0f}."})
        row.update({"hotspot_max": round(max(temps), 2), "hotspot_mean": round(sum(temps) / len(temps), 2),
                    "ageing_hours": round(v_hours, 3), "loss_of_life_days": round(loss_of_life_h / 24, 3),
                    "dp": round(dp, 1), "wcp": round(wcp, 3), "rs": round(f.relative_saturation_pct(wcp), 2),
                    "water_ppm": round(f.water_in_oil_ppm(wcp, oil_temp) + irng.gauss(0, 0.4), 2),
                    "co": round(co + irng.gauss(0, 2), 1), "co2": round(co2 + irng.gauss(0, 15), 1),
                    "co_week": round(dco, 2) if dco is not None else None,
                    "co2_week": round(dco2, 2) if dco2 is not None else None,
                    "co2_co_ratio_week": round(ratio, 2) if ratio is not None else None,
                    "insulation_status": istat, "moisture_status": mstat, "dga_status": gstat})

        # --- precision devices
        for dev in DEVICES:
            st = drift[dev]
            age = d - st["since"] + st["offset"] if st["since"] == 0 else d - st["since"]
            devs = []
            for ph in range(3):
                base = 0.18 + 0.07 * ph + abs(rng.gauss(0, 0.04))
                if ph == st["phase"]:
                    base += st["rate"] * age
                devs.append(round(base, 3))
            pstat = f.precision_status(devs, cfg)
            if pstat == "MAINTENANCE_REQUIRED" and st["flagged"] is None:
                st["flagged"] = d
                st["recal"] = d + rng.randint(4, 9)
                events.append({"day": d, "type": f"PRECISION_{dev.upper()}", "level": "MAINTENANCE",
                               "message": f"{dev.capitalize()} device phase {'RYB'[st['phase']]} deviates {max(devs):.2f} % from the "
                                          f"reference (limit {cfg['precision']['max_deviation_pct']:.0f} %): recalibrate."})
            if st["recal"] is not None and d >= st["recal"]:
                events.append({"day": d, "type": f"PRECISION_{dev.upper()}_RECALIBRATED", "level": "MAINTENANCE",
                               "message": f"{dev.capitalize()} device recalibrated."})
                st.update(since=d, flagged=None, recal=None, phase=rng.randrange(3),
                          rate=rng.uniform(0.75, 0.85) / rng.uniform(120, 165), offset=0)
            row[dev] = devs
            row[f"{dev}_status"] = pstat
        rows.append(row)
    return {"start": start, "days": days, "rows": rows, "events": events}
