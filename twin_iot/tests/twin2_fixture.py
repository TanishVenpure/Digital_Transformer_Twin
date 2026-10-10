"""Small two-timebar payloads with the shape of data/twin2/timebar1.json and timebar2.json.

Timebar 1: 180 minutes from 2026-07-15 00:00 UTC.
  #1 OVERLOAD_WARNING (loading) minute 60, condition ended at 66:40   -> resolved from minute 67
  #2 OTI_TRIP (oti, CRITICAL)   minute 120, still active at the end   -> never resolved
Timebar 2: 100 days from 2026-08-01.
  #3 OIL_LEAK (MAINTENANCE)              day 10   both closed by OIL_LEAK_REPAIRED on day 30
  #4 MINIMUM_OIL_LEVEL (CRITICAL)        day 20
  #5 PRECISION_CURRENT (MAINTENANCE)     day 40 -> PRECISION_CURRENT_RECALIBRATED day 50
  #6 PRECISION_VOLTAGE (WARNING)         day 60 -> no follow-up
"""
MIN1, DAYS2 = 180, 100


def _const(n, v):
    return [v] * n


def timebar1():
    status = {m: _const(MIN1, "NORMAL") for m in
              ("loading", "voltage", "oti", "wti", "thermal", "pressure", "prec_current", "prec_voltage", "prec_temperature")}
    status["oti"][120:] = ["CRITICAL"] * (MIN1 - 120)
    series = {
        "loading_max": _const(MIN1, 80.0), "v_pri_min": _const(MIN1, -1.0), "v_pri_max": _const(MIN1, 2.5),
        "v_sec_min": _const(MIN1, -3.0), "v_sec_max": _const(MIN1, 1.0), "top_oil": _const(MIN1, 70.0),
        "wti": _const(MIN1, 85.0), "residual": _const(MIN1, 0.5), "pressure_abs": _const(MIN1, 110.0),
        "prec_current": _const(MIN1, [0.1, 0.3, None]), "prec_voltage": _const(MIN1, [0.1, 0.1, 0.1]),
        "prec_temperature": _const(MIN1, [0.2, 0.2, 0.2]), "energized": _const(MIN1, True),
    }
    series["top_oil"][120:] = [101.0] * (MIN1 - 120)
    series["energized"][150:] = [False] * (MIN1 - 150)
    return {
        "timebar": 1, "start": "2026-07-15T00:00:00+00:00", "minutes": MIN1, "series": series, "status": status,
        "events": [
            {"timebar": 1, "monitor": "oti", "level": "CRITICAL", "type": "OTI_TRIP", "t": 7200.0, "t_end": None,
             "message": "Top oil above the trip setting: breaker opened."},
            {"timebar": 1, "monitor": "loading", "level": "WARNING", "type": "OVERLOAD_WARNING", "t": 3600.0,
             "t_end": 4000.0, "message": "Loading above the warning limit."},
        ],
    }


def timebar2():
    status = {m: _const(DAYS2, "NORMAL") for m in ("oil", "prec_current", "prec_voltage", "prec_temperature")}
    status["prec_voltage"][60:] = ["WARNING"] * (DAYS2 - 60)
    series = {"residual": _const(DAYS2, -2.0), "prec_current": _const(DAYS2, [0.2, 0.2, 0.2]),
              "prec_voltage": _const(DAYS2, [0.1, 0.6, 0.1]), "prec_temperature": _const(DAYS2, [0.1, 0.1, 0.1])}
    ev = lambda day, mon, level, typ, msg: {"timebar": 2, "monitor": mon, "level": level, "type": typ, "day": day, "message": msg}
    return {
        "timebar": 2, "start": "2026-08-01", "days": DAYS2, "series": series, "status": status,
        "events": [
            ev(10, "oil", "MAINTENANCE", "OIL_LEAK", "Oil volume falling: find and repair the leak."),
            ev(20, "oil", "CRITICAL", "MINIMUM_OIL_LEVEL", "Oil below the minimum level: top up and repair."),
            ev(30, "oil", "MAINTENANCE", "OIL_LEAK_REPAIRED", "Leak repaired and oil topped up."),
            ev(40, "prec_current", "MAINTENANCE", "PRECISION_CURRENT", "CT drift beyond tolerance: recalibrate."),
            ev(50, "prec_current", "MAINTENANCE", "PRECISION_CURRENT_RECALIBRATED", "CT recalibrated."),
            ev(60, "prec_voltage", "WARNING", "PRECISION_VOLTAGE", "PT drift rising."),
        ],
    }
