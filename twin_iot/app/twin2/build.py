"""Turn the two simulations into (a) the dataset files and (b) the compact payloads the dashboard plays.

Datasets (twin_iot/data/twin2/):
  timebar1_electrical_1s.csv     energised flag, loading %, primary / secondary voltage deviation min / max per second
  timebar1_electrical_20ms.csv   per-cycle (20 ms) loading and voltage deviation in a window around each event
  timebar1_thermal_1s.csv        the thermal schema (load factor, top oil, rises, hot spot, WTI, residual, ageing,
                                 OTI / WTI conditions and the analytics flags)
  timebar1_ambient_1min.csv, timebar1_pressure_1min.csv, timebar1_precision_1min.csv
  timebar2_daily.csv             oil volume balance + precision devices, one row a day
  events.csv                     every warning, alarm, trip, flag and maintenance action of both timebars
Payloads: timebar1.json (1-minute resolution for playback + the 20 ms windows), timebar2.json (daily).
"""
import csv
import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from app.paths import PROJECT_ROOT
from app.twin2 import formulas as f
from app.twin2 import timebar1, timebar2

OUT_DIR = PROJECT_ROOT / "data" / "twin2"
RANK = {"OFF": -1, "NO_DATA": -1, "NORMAL": 0, "WARNING": 1, "PREDICTED_MAINTENANCE": 2, "MAINTENANCE_REQUIRED": 3,
        "CRITICAL": 4}


def _runs(values: list, predicate, gap: int = 0) -> list[tuple[int, int]]:
    """[start, end) index runs where predicate(value) holds; runs separated by <= `gap` samples are merged
    (one event per episode instead of one per flicker across a limit)."""
    runs, start = [], None
    for i, v in enumerate(values):
        if predicate(v):
            if start is None:
                start = i
        elif start is not None:
            runs.append((start, i))
            start = None
    if start is not None:
        runs.append((start, len(values)))
    merged: list[tuple[int, int]] = []
    for a, b in runs:
        if merged and a - merged[-1][1] <= gap:
            merged[-1] = (merged[-1][0], b)
        else:
            merged.append((a, b))
    return merged


def _clock(day0, seconds: float) -> str:
    return (day0 + timedelta(seconds=seconds)).isoformat()


def thermal_flags(sec: dict[str, list], cfg: dict[str, Any]) -> dict[str, list]:
    """Per-second analytics flags (never trips): top-oil rise > limit, cooling degradation, winding excess
    heating (scaled persistence window), accelerated thermal ageing."""
    th = cfg["thermal"]
    n = len(sec["top_oil"])
    on = sec["energized"]
    rise_flag = [bool(on[i]) and sec["top_oil_rise_measured"][i] > th["top_oil_rise_limit_k"] for i in range(n)]
    cooling = [False] * n
    w = th["cooling_window_min"] * 60
    for a in range(0, n, w):
        idx = [i for i in range(a, min(a + w, n)) if on[i] and (sec["top_oil_rise_expected"][i] or 0) >= 8]
        if len(idx) >= w * 0.8:
            ratio = sum(sec["top_oil_rise_measured"][i] for i in idx) / sum(sec["top_oil_rise_expected"][i] for i in idx)
            if ratio > th["cooling_ratio_limit"]:
                for i in range(a + w - 1, min(a + 2 * w - 1, n)):   # raised at the end of the window
                    cooling[i] = True
    excess = [False] * n
    w = th["residual_window_min"] * 60
    means = []
    for a in range(0, n, w):
        vals = [sec["winding_thermal_residual"][i] for i in range(a, min(a + w, n))
                if on[i] and sec["winding_thermal_residual"][i] is not None]
        means.append(sum(vals) / len(vals) if len(vals) >= w * 0.8 else None)
    for k in range(th["residual_windows"], len(means)):
        recent = means[k - th["residual_windows"]:k + 1]
        if None not in recent and recent[-1] >= th["residual_min_k"] and all(b > a for a, b in zip(recent, recent[1:])):
            for i in range((k + 1) * w - 1, min((k + 2) * w - 1, n)):
                excess[i] = True
    ageing = [False] * n
    w = th["ageing_window_min"] * 60
    acc = 0.0
    for i in range(n):
        acc += sec["relative_ageing_rate"][i]
        if i >= w:
            acc -= sec["relative_ageing_rate"][i - w]
        if i >= w - 1 and acc / w > th["ageing_ratio_limit"]:
            ageing[i] = True
    return {"top_oil_rise_over_limit": rise_flag, "cooling_degradation": cooling, "winding_excess_heating": excess,
            "accelerated_thermal_ageing": ageing}


def timebar1_events(d: dict[str, Any], flags: dict[str, list], cfg: dict[str, Any]) -> list[dict[str, Any]]:
    sec, day0 = d["sec"], d["day0"]
    th, pr = cfg["thermal"], cfg["pressure"]
    ev: list[dict[str, Any]] = []

    def add(kind, level, a, b, monitor, message, **extra):
        ev.append({"timebar": 1, "type": kind, "level": level, "monitor": monitor, "start": _clock(day0, a),
                   "end": _clock(day0, b), "t": a, "t_end": b, "message": message, **extra})

    for a, b in _runs(sec["overload"], lambda v: v in ("WARNING", "CRITICAL"), gap=300):
        peak = max(x for x in sec["loading_max"][a:b] if x is not None)
        add("OVERLOAD", "WARNING", a, b, "loading",
            f"Loading above {cfg['electrical']['overload_warning_pct']:.0f} % of rated for {b - a} s (peak {peak:.1f} %).")
    for a, b in _runs(sec["overload"], lambda v: v == "CRITICAL"):
        peak = max(sec["loading_max"][a:b])
        add("OVERLOAD_TRIP", "CRITICAL", a, b, "loading",
            f"Loading {peak:.1f} % is above the {cfg['electrical']['overload_trip_pct']:.0f} % trip setting.")
    for rule in f.voltage_rules(cfg):            # both windings, each against its own band
        w = "pri" if rule["winding"] == "primary" else "sec"
        for kind in ("OVERVOLTAGE", "UNDERVOLTAGE"):
            for a, b in _runs(sec[f"voltage_{w}"], lambda v, k=kind: v == k):
                key = f"v_{w}_max" if kind == "OVERVOLTAGE" else f"v_{w}_min"
                vals = [x for x in sec[key][a:b] if x is not None]
                peak = max(vals) if kind == "OVERVOLTAGE" else min(vals)
                limit = rule["high_pct"] if kind == "OVERVOLTAGE" else -rule["low_pct"]
                add(kind, "WARNING", a, b, "voltage",
                    f"{rule['winding'].capitalize()} winding voltage {peak:+.2f} % from nominal (limit {limit:+.0f} %) "
                    f"for {b - a} s.", winding=rule["winding"])
    for monitor, key, alarm, trip in (("oti", "oti_condition", th["oti_alarm_c"], th["oti_trip_c"]),
                                      ("wti", "wti_condition", th["wti_alarm_c"], th["wti_trip_c"])):
        temp = "top_oil" if monitor == "oti" else "wti"
        for a, b in _runs(sec[key], lambda v: v in ("ALARM", "TRIP"), gap=900):
            add(f"{monitor.upper()}_ALARM", "WARNING", a, b, monitor,
                f"{monitor.upper()} at or above the {alarm:.0f} degC alarm (peak {max(sec[temp][a:b]):.1f} degC).")
        for a, b in _runs(sec[key], lambda v: v == "TRIP", gap=120):   # merge sensor flicker around the setting
            add(f"{monitor.upper()}_TRIP", "CRITICAL", a, b, monitor,
                f"{monitor.upper()} reached the {trip:.0f} degC trip setting ({max(sec[temp][a:b]):.1f} degC).")
    for fl in d["faults"]:   # short circuits (20 ms protection decision + effects on the transformer)
        det, eff, spec = fl["detection"], fl["effects"], fl["spec"]
        if not det:
            continue
        a = int(fl["t"])
        where = spec["where"]
        clear = (f"tripped {det['trip_ms'] - det['pickup_ms']:.0f} ms after pickup, current cleared after "
                 f"{det['duration_ms']:.0f} ms" if det["duration_ms"] is not None else "current NOT cleared in the recording")
        add("SHORT_CIRCUIT", "CRITICAL", a, a + 1, "fault",
            f"Short circuit, {det['fault'] or 'high current'}: {det['peak_current_pct'] / 100:.1f} x rated "
            f"({det['peak_current_pct'] * d['i_rated_sec'] / 100 / 1000:.1f} kA LV) with the voltage down to "
            f"{det['min_voltage_pct']:.0f} %; {clear}. {where}. Peak force {eff['force_pct_of_design']:.0f} % of the "
            f"design withstand, I2t {eff['i2t_pct_of_withstand']:.1f} %, winding {eff['winding_before_c']:.0f} -> "
            f"{eff['winding_after_c']:.0f} degC (limit {eff['winding_limit_c']:.0f}).",
            fault=fl["t"])
        if det["breaker_failure"]:
            add("BREAKER_FAILURE", "CRITICAL", a, a + 1, "fault",
                f"Breaker failure: the fault flowed for {det['duration_ms']:.0f} ms (limit "
                f"{cfg['short_circuit']['breaker_failure_ms']:.0f} ms) - the main breaker did not open and the backup "
                f"cleared it. Inspect / test the main breaker before re-energising.")
        if det["internal"]:
            add("INTERNAL_FAULT", "CRITICAL", a, a + 1, "fault",
                f"Internal fault: tank pressure jumped {det['pressure_rise_kpa']:.0f} kPa during the fault (arc in the oil). "
                f"Do not re-energise: DGA, winding resistance / ratio and SFRA first.")
        else:
            add("FAULT_CLEARED", "MAINTENANCE_REQUIRED", a, a + spec["restore_s"], "fault",
                f"External fault (no tank pressure surge): transformer re-energised after {spec['restore_s'] // 60} min "
                f"once the faulted section was isolated; counted as a through-fault on timebar 2.")
    for e in d["events"]:   # breaker operations
        add(e["type"], e["level"], e["t"], e["t"] + 1, "thermal" if "THERMAL" in e["type"] else "loading", e["message"])
    names = {"top_oil_rise_over_limit": ("TOP_OIL_RISE_OVER_LIMIT", "WARNING", "thermal",
                                         f"Top-oil rise over ambient above {th['top_oil_rise_limit_k']:.0f} K (analytics only, not a trip)."),
             "cooling_degradation": ("COOLING_DEGRADATION", "MAINTENANCE_REQUIRED", "thermal",
                                     f"Measured top-oil rise more than {th['cooling_ratio_limit']:.2f} x the IEC model over "
                                     f"{th['cooling_window_min']} min: radiators not cooling as designed (ONAN: check radiator valves, blocked fins)."),
             "winding_excess_heating": ("WINDING_EXCESS_HEATING", "MAINTENANCE_REQUIRED", "thermal",
                                        f"Winding {th['residual_min_k']:.1f} K or more above the IEC model and growing for "
                                        f"{th['residual_windows']} windows of {th['residual_window_min']} min (scaled from the weekly "
                                        "review for a one-day timebar): blocked ducts / winding fault."),
             "accelerated_thermal_ageing": ("ACCELERATED_THERMAL_AGEING", "WARNING", "thermal",
                                            f"Mean relative ageing rate above {th['ageing_ratio_limit']:.0f} over "
                                            f"{th['ageing_window_min']} min: insulation ageing faster than design.")}
    for key, (kind, level, monitor, message) in names.items():
        for a, b in _runs(flags[key], bool, gap=900):
            add(kind, level, a, b, monitor, message)
    for a, b in _runs(sec["probe_valid"], lambda v: not v):
        add("WINDING_PROBE_INVALID", "WARNING", a, b, "thermal",
            f"Winding temperature probe returned no valid reading for {b - a} s: residual marked no-data, no trip raised.")
    prs = d["pressure"]
    for a, b in _runs([r["status"] for r in prs], lambda v: v == "WARNING", gap=15):
        peak = max(r["pressure_final_abs"] for r in prs[a:b])
        add("PRESSURE_WARNING", "WARNING", a * 60, b * 60, "pressure",
            f"Tank pressure up to {peak:.1f} kPa abs (warning {pr['warning_kpa_abs']:.0f}, assumed limit "
            f"{pr['limit_kpa_abs']:.0f} kPa): near failure caught - the source of heat was removed and the pressure "
            f"peaked {pr['limit_kpa_abs'] - peak:.1f} kPa below the limit.")
    pc = d["precision"]
    for dev in timebar2.DEVICES:
        for a, b in _runs([r[f"{dev}_status"] for r in pc], lambda v: v == "MAINTENANCE_REQUIRED", gap=30):
            worst = max(v for r in pc[a:b] for v in r[dev] if v is not None)
            add(f"PRECISION_{dev.upper()}", "MAINTENANCE_REQUIRED", a * 60, b * 60, f"prec_{dev}",
                f"{dev.capitalize()} device deviates up to {worst:.2f} % from the reference (limit "
                f"{cfg['precision']['max_deviation_pct']:.0f} %): maintenance on the 1-year timebar.")
    ev.sort(key=lambda e: e["t"])
    return ev


def _minute_status(values: list[str], m: int, mapping: dict[str, str]) -> str:
    worst = "NORMAL"
    for v in values[m * 60:(m + 1) * 60]:
        s = mapping.get(v, v)
        if RANK.get(s, 0) > RANK.get(worst, 0):
            worst = s
    if all(v == "OFF" for v in values[m * 60:(m + 1) * 60]):
        return "OFF"
    return worst


def build(cfg: dict[str, Any] | None = None, out_dir: Path | None = None) -> dict[str, Any]:
    cfg = cfg or f.load_config()
    out_dir = out_dir or OUT_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    d1 = timebar1.simulate(cfg)
    # the timebar-1 day is one day of the timebar-2 year: its short circuits are that day's through-faults
    day_index = (d1["day0"].date() - datetime.fromisoformat(cfg["timebar2"]["start"]).date()).days
    day_faults = ({"day": day_index, "faults": [fl["effects"] for fl in d1["faults"] if fl["effects"]]}
                  if 0 <= day_index < cfg["timebar2"]["days"] else None)
    d2 = timebar2.simulate(cfg, day_faults)
    sec = d1["sec"]
    flags = thermal_flags(sec, cfg)
    ev1 = timebar1_events(d1, flags, cfg)
    n_min = len(sec["top_oil"]) // 60

    # ---------- per-minute playback series (min / max keep short excursions visible)
    def agg(key, fn):
        out = []
        for m in range(n_min):
            vals = [v for v in sec[key][m * 60:(m + 1) * 60] if v is not None]
            out.append(round(fn(vals), 3) if vals else None)
        return out

    mean = lambda v: sum(v) / len(v)  # noqa: E731
    series = {
        "loading_max": agg("loading_max", max), "loading_mean": agg("loading_mean", mean),
        "v_pri_min": agg("v_pri_min", min), "v_pri_max": agg("v_pri_max", max),
        "v_sec_min": agg("v_sec_min", min), "v_sec_max": agg("v_sec_max", max),
        "ambient": [round(x, 2) for x in d1["ambient_min"]],
        "load_factor": agg("load_factor", max), "top_oil": agg("top_oil", max),
        "top_oil_expected": agg("top_oil_expected", mean), "top_oil_rise_measured": agg("top_oil_rise_measured", mean),
        "top_oil_rise_expected": agg("top_oil_rise_expected", mean),
        "hotspot": agg("hotspot_temperature_estimated", max), "wti": agg("wti", max),
        # same per-minute reduction (max) as hot spot and WTI, so lines that are equal second by second overlap
        "winding_measured": agg("winding_measured", max), "winding_expected": agg("winding_expected", max),
        "residual": agg("winding_thermal_residual", mean), "ageing_rate": agg("relative_ageing_rate", mean),
        "ageing_hours": agg("equivalent_ageing_hours", max), "ageing_ratio": agg("ageing_ratio", max),
        "energized": [all(sec["energized"][m * 60:(m + 1) * 60]) for m in range(n_min)],
        "pressure_abs": [round(r["pressure_final_abs"], 2) if r["validity_flag"] else None for r in d1["pressure"]],
        "pressure_gauge": [round(r["pressure_gauge"], 2) if r["validity_flag"] else None for r in d1["pressure"]],
        "headspace": [round(r["headspace_volume_final"], 3) for r in d1["pressure"]],
        "oil_expansion": [round(r["oil_expansion_volume"], 3) for r in d1["pressure"]],
        "minutes_to_limit": [round(r["minutes_to_limit"], 1) if r["minutes_to_limit"] is not None else None for r in d1["pressure"]],
    }
    for dev in timebar2.DEVICES:
        series[f"prec_{dev}"] = [r[dev] for r in d1["precision"]]
    ovmap = {"OFF": "OFF"}
    # short-circuit protection: CRITICAL in the minute of a fault, out of service while the breaker is open
    fault_min = {int(fl["t"]) // 60: "CRITICAL" for fl in d1["faults"] if fl["detection"]}
    fault_status = [fault_min.get(m) or ("NORMAL" if any(sec["energized"][m * 60:(m + 1) * 60]) else "OFF")
                    for m in range(n_min)]
    fault_peak = [None] * n_min
    for fl in d1["faults"]:
        if fl["detection"]:
            fault_peak[int(fl["t"]) // 60] = fl["detection"]["peak_current_pct"]
    series["fault_peak_pct"] = fault_peak
    status = {
        "loading": [_minute_status(sec["overload"], m, ovmap) for m in range(n_min)],
        "voltage": [_minute_status(sec["voltage"], m, {"OVERVOLTAGE": "WARNING", "UNDERVOLTAGE": "WARNING"}) for m in range(n_min)],
        "voltage_primary": [_minute_status(sec["voltage_pri"], m, {"OVERVOLTAGE": "WARNING", "UNDERVOLTAGE": "WARNING"}) for m in range(n_min)],
        "voltage_secondary": [_minute_status(sec["voltage_sec"], m, {"OVERVOLTAGE": "WARNING", "UNDERVOLTAGE": "WARNING"}) for m in range(n_min)],
        "oti": [_minute_status(sec["oti_condition"], m, {"ALARM": "WARNING", "TRIP": "CRITICAL"}) for m in range(n_min)],
        "wti": [_minute_status(sec["wti_condition"], m, {"ALARM": "WARNING", "TRIP": "CRITICAL"}) for m in range(n_min)],
        "pressure": [r["status"] for r in d1["pressure"]],
        "fault": fault_status,
    }
    therm = []
    for m in range(n_min):
        s = "NORMAL"
        sl = slice(m * 60, (m + 1) * 60)
        if any(flags["cooling_degradation"][sl]) or any(flags["winding_excess_heating"][sl]):
            s = "MAINTENANCE_REQUIRED"
        elif any(flags["accelerated_thermal_ageing"][sl]) or any(flags["top_oil_rise_over_limit"][sl]) \
                or not all(sec["probe_valid"][sl]):
            s = "WARNING"
        therm.append(s)
    status["thermal"] = therm
    for dev in timebar2.DEVICES:
        status[f"prec_{dev}"] = [r[f"{dev}_status"] for r in d1["precision"]]

    th, pr, tr = cfg["thermal"], cfg["pressure"], cfg["transformer"]
    scfg = cfg["short_circuit"]
    vp, vs = f.voltage_rule(cfg, "primary"), f.voltage_rule(cfg, "secondary")
    limits = {
        "overload_warning_pct": cfg["electrical"]["overload_warning_pct"], "overload_trip_pct": cfg["electrical"]["overload_trip_pct"],
        "voltage_class": tr["voltage_class"],
        "voltage_primary_high_pct": vp["high_pct"], "voltage_primary_low_pct": vp["low_pct"],
        "voltage_secondary_high_pct": vs["high_pct"], "voltage_secondary_low_pct": vs["low_pct"],
        "oti_alarm_c": th["oti_alarm_c"], "oti_trip_c": th["oti_trip_c"],
        "wti_alarm_c": th["wti_alarm_c"], "wti_trip_c": th["wti_trip_c"], "top_oil_rise_limit_k": th["top_oil_rise_limit_k"],
        "residual_min_k": th["residual_min_k"], "pressure_warning_kpa": pr["warning_kpa_abs"], "pressure_limit_kpa": pr["limit_kpa_abs"],
        "precision_max_pct": cfg["precision"]["max_deviation_pct"],
        "impedance_pct": scfg["impedance_pct"], "short_circuit_multiple": round(f.rated_short_circuit_multiple(scfg), 1),
        "short_circuit_ka_lv": round(f.rated_short_circuit_multiple(scfg) * d1["i_rated_sec"] / 1000, 1),
        "sc_pickup_multiple": scfg["pickup_multiple"], "sc_sag_pct": scfg["sag_pct"],
        "breaker_failure_ms": scfg["breaker_failure_ms"], "winding_limit_c": scfg["winding_limit_c"],
        "i_rated_primary_a": round(d1["i_rated_pri"], 2), "i_rated_secondary_a": round(d1["i_rated_sec"], 1),
    }
    windows = [dict(w, start=_clock(d1["day0"], w["t0"])) for w in d1["windows"]]
    for k, fl in enumerate(d1["faults"]):      # 20 ms per-phase detail of each short circuit
        w = fl["window"]
        windows.append({"id": f"short_circuit_{k + 1}", "kind": "FAULT", "t0": w["t0"], "dt": w["dt"],
                        "start": _clock(d1["day0"], w["t0"]), "i_pct": w["i_pct"], "v_pct": w["v_pct"],
                        "pressure_rise_kpa": w["pressure_rise_kpa"], "detection": fl["detection"], "effects": fl["effects"]})
    tb1 = {"timebar": 1, "start": d1["day0"].isoformat(), "step_s": 60, "minutes": n_min, "series": series,
           "status": status, "events": ev1, "limits": limits, "windows": windows,
           "sampling": {"voltage_current": "20 ms per-cycle RMS (full rate in event windows, 1 s min / max stream)",
                        "thermal": "1 s", "ambient": "1 min", "pressure": "1 min", "precision": "1 min"}}

    # ---------- timebar 2
    rows = d2["rows"]
    ev2 = []
    monitor_of = {"OIL_LEAK": "oil", "MINIMUM_OIL_LEVEL": "oil", "OIL_LEAK_REPAIRED": "oil",
                  "THROUGH_FAULT": "through_faults", "THROUGH_FAULT_DUTY": "through_faults",
                  "THROUGH_FAULT_DUTY_TESTED": "through_faults"}
    for e in d2["events"]:
        mon = monitor_of.get(e["type"]) or "prec_" + e["type"].split("_")[1].lower()
        ev2.append({"timebar": 2, "type": e["type"], "level": e["level"], "monitor": mon, "day": e["day"],
                    "start": rows[e["day"]]["date"], "message": e["message"]})
    o = cfg["oil_leak"]
    tb2 = {"timebar": 2, "start": d2["start"].date().isoformat(), "days": d2["days"], "events": ev2,
           "series": {"dates": [r["date"] for r in rows], "oil_temperature": [r["oil_temperature"] for r in rows],
                      "volume_expected": [r["volume_expected_noleak"] for r in rows],
                      "volume_measured": [r["volume_measured"] for r in rows], "residual": [r["residual"] for r in rows],
                      "cumulative_leak": [r["cumulative_leak"] for r in rows],
                      **{key: [r[key] for r in rows] for key in ("through_faults", "through_faults_severe",
                                                                  "through_fault_i2t_pct", "through_fault_force_pct")},
                      **{f"prec_{dev}": [r[dev] for r in rows] for dev in timebar2.DEVICES}},
           "status": {"oil": [r["oil_status"] for r in rows],
                      "through_faults": [r["through_fault_status"] for r in rows],
                      **{f"prec_{dev}": [r[f"{dev}_status"] for r in rows] for dev in timebar2.DEVICES}},
           "limits": {"tolerance_l": o["tolerance_l"], "minimum_volume_l": o["minimum_volume_l"],
                      "severe_force_pct": cfg["short_circuit"]["severe_force_pct"],
                      "severe_faults_warning": cfg["short_circuit"]["severe_faults_warning"],
                      "reference_volume_l": o["reference_volume_l"], "precision_max_pct": cfg["precision"]["max_deviation_pct"]}}

    (out_dir / "timebar1.json").write_text(json.dumps(tb1, separators=(",", ":")), encoding="utf-8")
    (out_dir / "timebar2.json").write_text(json.dumps(tb2, separators=(",", ":")), encoding="utf-8")
    _write_csvs(out_dir, d1, flags, d2, ev1 + ev2)
    return {"timebar1": tb1, "timebar2": tb2}


def _write_csvs(out_dir: Path, d1, flags, d2, events) -> None:
    sec, day0 = d1["sec"], d1["day0"]
    n = len(sec["top_oil"])

    def write(name, header, rows):
        with (out_dir / name).open("w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            w.writerow(header)
            w.writerows(rows)

    write("timebar1_electrical_1s.csv",
          ["timestamp", "energized", "loading_pct_max", "loading_pct_mean", "loading_primary_pct", "v_primary_dev_min_pct",
           "v_primary_dev_max_pct", "v_secondary_dev_min_pct", "v_secondary_dev_max_pct", "overload",
           "voltage_condition_primary", "voltage_condition_secondary"],
          ([_clock(day0, i), sec["energized"][i], sec["loading_max"][i], sec["loading_mean"][i], sec["loading_pri"][i],
            sec["v_pri_min"][i], sec["v_pri_max"][i], sec["v_sec_min"][i], sec["v_sec_max"][i], sec["overload"][i],
            sec["voltage_pri"][i], sec["voltage_sec"][i]] for i in range(n)))
    write("timebar1_electrical_20ms.csv",
          ["window", "timestamp", "loading_pct", "v_primary_dev_pct", "v_secondary_dev_pct"],
          ([w["id"], _clock(day0, w["t0"] + j * w["dt"]), w["loading_pct"][j], w["voltage_deviation_pct"][j],
            w["voltage_secondary_pct"][j]] for w in d1["windows"] for j in range(len(w["loading_pct"]))))
    cols = ["load_factor", "ambient", "top_oil", "top_oil_rise_measured", "top_oil_rise_expected",
            "hotspot_temperature_estimated", "wti", "winding_measured", "winding_expected", "winding_thermal_residual",
            "relative_ageing_rate", "equivalent_ageing_hours", "ageing_ratio"]
    write("timebar1_thermal_1s.csv",
          ["timestamp", *[c.replace("ambient", "ambient_temperature").replace("top_oil", "top_oil_temperature", 1)
                          if c in ("ambient", "top_oil") else c for c in cols],
           "oti_alarm_condition", "oti_trip_condition", "wti_alarm_condition", "wti_trip_condition",
           "winding_excess_heating", "accelerated_thermal_ageing", "cooling_degradation", "top_oil_rise_over_limit"],
          ([_clock(day0, i), *[sec[c][i] for c in cols],
            sec["oti_condition"][i] in ("ALARM", "TRIP"), sec["oti_condition"][i] == "TRIP",
            sec["wti_condition"][i] in ("ALARM", "TRIP"), sec["wti_condition"][i] == "TRIP",
            flags["winding_excess_heating"][i], flags["accelerated_thermal_ageing"][i],
            flags["cooling_degradation"][i], flags["top_oil_rise_over_limit"][i]] for i in range(n)))
    write("timebar1_ambient_1min.csv", ["timestamp", "ambient_temperature"],
          ([_clock(day0, m * 60), round(a, 2)] for m, a in enumerate(d1["ambient_min"])))
    write("timebar1_pressure_1min.csv",
          ["timestamp", "top_oil_temperature", "oil_expansion_volume", "headspace_volume_final", "pressure_final_abs",
           "pressure_rise", "pressure_gauge", "validity_flag", "minutes_to_limit", "status"],
          ([_clock(day0, r["minute"] * 60), sec["top_oil"][r["minute"] * 60], r["oil_expansion_volume"],
            r["headspace_volume_final"], r["pressure_final_abs"], r["pressure_rise"], r["pressure_gauge"],
            r["validity_flag"], r["minutes_to_limit"], r["status"]] for r in d1["pressure"]))
    write("timebar1_precision_1min.csv",
          ["timestamp", *[f"{dev}_dev_{ph}_pct" for dev in timebar2.DEVICES for ph in "RYB"],
           *[f"{dev}_status" for dev in timebar2.DEVICES]],
          ([_clock(day0, r["minute"] * 60), *[v for dev in timebar2.DEVICES for v in r[dev]],
            *[r[f"{dev}_status"] for dev in timebar2.DEVICES]] for r in d1["precision"]))
    write("timebar2_daily.csv",
          ["date", "oil_temperature", "volume_expected_noleak", "volume_measured", "residual", "cumulative_leak",
           "oil_status", *[f"{dev}_dev_{ph}_pct" for dev in timebar2.DEVICES for ph in "RYB"],
           *[f"{dev}_status" for dev in timebar2.DEVICES]],
          ([r["date"], r["oil_temperature"], r["volume_expected_noleak"], r["volume_measured"], r["residual"],
            r["cumulative_leak"], r["oil_status"], *[v for dev in timebar2.DEVICES for v in r[dev]],
            *[r[f"{dev}_status"] for dev in timebar2.DEVICES]] for r in d2["rows"]))
    write("timebar1_faults_20ms.csv",
          ["fault", "timestamp", "i_r_pct", "i_y_pct", "i_b_pct", "v_r_pct", "v_y_pct", "v_b_pct", "tank_pressure_rise_kpa"],
          ([k + 1, _clock(day0, fl["window"]["t0"] + j * fl["window"]["dt"]), *fl["window"]["i_pct"][j],
            *fl["window"]["v_pct"][j], fl["window"]["pressure_rise_kpa"][j]]
           for k, fl in enumerate(d1["faults"]) for j in range(len(fl["window"]["i_pct"]))))
    write("events.csv", ["timebar", "start", "end", "type", "level", "monitor", "message"],
          ([e["timebar"], e["start"], e.get("end", e["start"]), e["type"], e["level"], e["monitor"], e["message"]]
           for e in events))


def load_payload(n: int) -> dict[str, Any]:
    path = OUT_DIR / f"timebar{n}.json"
    if not path.exists():
        build()
    return json.loads(path.read_text(encoding="utf-8"))
