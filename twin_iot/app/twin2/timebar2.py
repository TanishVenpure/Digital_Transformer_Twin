"""Timebar 2: one year of daily readings - oil leakage and the three precision devices.

Oil (once a day): the oil temperature sets the volume a healthy tank should hold,
V_exp,noleak = V_ref * (1 + beta * (T_oil - T_ref)). A leak removes L_cum = sum(q_leak * 1 day), so
R = V_meas - V_exp,noleak ~ -L_cum. Two leaks in the year:
  leak 1 (gasket weep) -> OIL_LEAK alarm on the tolerance band, gasket replaced and topped up;
  leak 2 (faster, repair delayed for parts) -> OIL_LEAK alarm, then MINIMUM_OIL_LEVEL, then repaired.
Precision devices (once a day): per-phase deviation from a reference instrument. Each device drifts after
calibration until a phase passes 1 % (every ~4-6 months), is flagged for maintenance and recalibrated a few
days later.

Through-fault duty (once a day): every short circuit the transformer carries (fed from it, cleared downstream) is
counted with its I2t and peak force against the IEC 60076-5 design withstand. Repeated severe through-faults
loosen the windings: after `severe_faults_warning` severe ones (force >= severe_force_pct) THROUGH_FAULT_DUTY asks
for winding tests (SFRA, winding resistance, ratio), done a couple of weeks later. The timebar-1 day (2026-07-15)
is one day of this year: its two recorded short circuits are its through-faults.
"""
import math
import random
from datetime import datetime, timedelta, timezone
from typing import Any

from app.twin2 import formulas as f

DEVICES = ("current", "voltage", "temperature")


def _year_faults(days: int, start: datetime, rng: random.Random, skip_day: int | None) -> dict[int, list[dict]]:
    """External short circuits through the year, more often in the monsoon (lightning, wet cables, trees)."""
    out: dict[int, list[dict]] = {}
    for d in range(days):
        if d == skip_day:
            continue
        month = (start + timedelta(days=d)).month
        p = 0.035 if month in (6, 7, 8, 9) else 0.010
        if rng.random() < p:
            out.setdefault(d, []).append({"force_pct_of_design": round(rng.uniform(4, 48), 1),
                                          "i2t_pct_of_withstand": round(rng.uniform(0.2, 6), 2), "source": "simulated"})
    return out


def simulate(cfg: dict[str, Any] | None = None, day_faults: dict[str, Any] | None = None) -> dict[str, Any]:
    """day_faults: {"day": index, "faults": [fault_effects of the timebar-1 short circuits]}."""
    cfg = cfg or f.load_config()
    tb = cfg["timebar2"]
    o = cfg["oil_leak"]
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

    scfg = cfg["short_circuit"]
    tf_day = day_faults["day"] if day_faults else None
    year_faults = _year_faults(days, start, random.Random(tb["seed"] + 211), tf_day)
    if day_faults:
        year_faults[tf_day] = [dict(e, source="timebar 1") for e in day_faults["faults"]]
    tf_count = tf_severe = 0
    tf_i2t = 0.0
    tf_flagged = None
    tf_tested_on = None

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
        row = {"day": d, "date": day.date().isoformat(), "oil_temperature": round(oil_temp, 2),
               "volume_expected_noleak": round(expected, 2), "volume_measured": round(measured, 2),
               "residual": round(residual, 2), "cumulative_leak": round(leaked, 2), "oil_status": status}

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
        # --- through-fault duty
        for ft in year_faults.get(d, []):
            tf_count += 1
            tf_i2t += ft["i2t_pct_of_withstand"]
            severe = ft["force_pct_of_design"] >= scfg["severe_force_pct"]
            tf_severe += severe
            events.append({"day": d, "type": "THROUGH_FAULT", "level": "WARNING" if severe else "NORMAL",
                           "message": f"Through-fault ({ft['source']}): peak force {ft['force_pct_of_design']:.0f} % of the "
                                      f"design withstand, I2t {ft['i2t_pct_of_withstand']:.1f} % of the 2 s withstand"
                                      + (" - severe." if severe else ".")})
        if tf_severe >= scfg["severe_faults_warning"] and tf_flagged is None:
            tf_flagged = d
            tf_tested_on = d + 14
            events.append({"day": d, "type": "THROUGH_FAULT_DUTY", "level": "MAINTENANCE",
                           "message": f"{tf_severe} severe through-faults since the last winding test ({tf_count} in all, "
                                      f"cumulative I2t {tf_i2t:.1f} % of one withstand): repeated short-circuit forces can "
                                      f"loosen or move the windings - run SFRA, winding resistance and ratio tests."})
        if tf_tested_on is not None and d >= tf_tested_on:
            events.append({"day": d, "type": "THROUGH_FAULT_DUTY_TESTED", "level": "MAINTENANCE",
                           "message": "Winding tests done (SFRA, resistance, ratio within limits): severe through-fault count reset."})
            tf_severe, tf_flagged, tf_tested_on = 0, None, None
        row.update({"through_faults": tf_count, "through_faults_severe": tf_severe,
                    "through_fault_i2t_pct": round(tf_i2t, 2),
                    "through_fault_force_pct": max((ft["force_pct_of_design"] for ft in year_faults.get(d, [])), default=None),
                    "through_fault_status": "MAINTENANCE_REQUIRED" if tf_flagged is not None else "NORMAL"})
        rows.append(row)
    return {"start": start, "days": days, "rows": rows, "events": events}
