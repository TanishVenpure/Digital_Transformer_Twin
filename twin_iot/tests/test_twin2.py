"""Two-timebar model: the formulas, and that every condition appears in the generated data."""
import math

import pytest

from app.twin2 import formulas as f
from app.twin2.build import build

CFG = f.load_config()
TH = CFG["thermal"]


# ---------------------------------------------------------------- electrical

def test_rated_currents_of_the_cpri_transformer():
    assert f.rated_current(630, 11) == pytest.approx(33.07, abs=0.01)
    assert f.rated_current(630, 0.433) == pytest.approx(840.0, abs=0.5)


@pytest.mark.parametrize("loading,status", [(100.0, "NORMAL"), (100.1, "WARNING"), (120.0, "WARNING"), (120.1, "CRITICAL")])
def test_overload_warning_above_100_trip_above_120(loading, status):
    assert f.overload_status(loading, CFG) == status
    assert f.loading_pct(840 * loading / 100, 840) == pytest.approx(loading)


@pytest.mark.parametrize("winding,dev,cond", [
    ("primary", 6.1, "OVERVOLTAGE"), ("primary", -6.1, "UNDERVOLTAGE"), ("primary", -5.9, "NORMAL"),
    ("secondary", 6.1, "OVERVOLTAGE"), ("secondary", -8.9, "NORMAL"), ("secondary", -9.1, "UNDERVOLTAGE"),
])
def test_voltage_limits_per_winding(winding, dev, cond):
    """Primary +-6 %; secondary +6 % on the high side, -9 % on the low side."""
    assert f.voltage_condition(dev, f.voltage_rule(CFG, winding)) == cond


def test_both_windings_are_judged():
    assert [r["winding"] for r in f.voltage_rules(CFG)] == ["primary", "secondary"]
    assert f.voltage_deviation_pct(11660, 11000) == pytest.approx(6.0)


# ---------------------------------------------------------------- precision

def test_precision_device_needs_maintenance_above_1_percent_on_any_phase():
    assert f.precision_deviation_pct(101.2, 100) == pytest.approx(1.2)
    assert f.precision_status([0.3, 0.99, 0.5], CFG) == "NORMAL"
    assert f.precision_status([0.3, 1.01, 0.5], CFG) == "MAINTENANCE_REQUIRED"
    assert f.precision_status([None, math.nan], CFG) == "NO_DATA"


# ---------------------------------------------------------------- thermal

def test_iec_rated_load_gives_rated_rises():
    assert f.ultimate_top_oil_rise(1.0, TH) == pytest.approx(TH["top_oil_rise_rated"])
    assert f.ultimate_hotspot_gradient(1.0, TH) == pytest.approx(TH["hotspot_factor"] * TH["winding_gradient_rated"])


def test_first_order_lag_reaches_63_percent_after_one_time_constant():
    x = None
    x = f.lag(x, 0.0, 1, 10)
    for _ in range(600):           # 10 min of 1 s steps, tau = 10 min
        x = f.lag(x, 10.0, 1, 10)
    assert x == pytest.approx(10 * (1 - math.exp(-1)), rel=1e-3)


def test_ageing_rate_is_1_at_98_and_doubles_every_6_k():
    assert f.relative_ageing_rate(98, TH) == pytest.approx(1)
    assert f.relative_ageing_rate(104, TH) == pytest.approx(2)


@pytest.mark.parametrize("oti,wti,trip", [
    (94.9, 109.9, False), (105.0, 110.0, True), (95.0, 120.0, True), (104.9, 119.9, False),
])
def test_trip_is_oti_trip_or_wti_trip_never_an_average(oti, wti, trip):
    o, w = f.oti_condition(oti, TH), f.wti_condition(wti, TH)
    assert f.thermal_trip(o, w) is trip
    # an average of the two would trip at (104.9 + 119.9) / 2 = 112.4 -> must not
    assert f.thermal_trip(f.oti_condition(104.9, TH), f.wti_condition(119.9, TH)) is False


def test_de_energised_transformer_cools_toward_ambient():
    s = f.ThermalState(top_oil_rise=50.0, hotspot_gradient=20.0, image_gradient=20.0)
    for _ in range(3600 * 6):
        s = f.thermal_step(s, 0.0, 1, TH, energized=False)
    assert s.top_oil_rise == pytest.approx(50 * math.exp(-2), rel=0.01)   # 6 h = 2 oil time constants
    assert s.hotspot_gradient < 0.01


# ---------------------------------------------------------------- pressure and oil

def test_pressure_matches_the_ideal_gas_worked_example():
    p = dict(CFG["pressure"], gas_volume_l=30.0)
    r = f.tank_pressure(80.0, p)
    assert r["oil_expansion_volume"] == pytest.approx(11.25)
    assert r["headspace_volume_final"] == pytest.approx(18.75)
    assert r["pressure_final_abs"] == pytest.approx(101.3 * (353.15 / 303.15) * (30 / 18.75))   # ~188.8 kPa abs
    assert r["pressure_gauge"] == pytest.approx(r["pressure_final_abs"] - 101.3)


def test_pressure_is_invalid_when_the_headspace_is_gone():
    r = f.tank_pressure(400.0, CFG["pressure"])
    assert r["validity_flag"] is False and r["pressure_final_abs"] is None


def test_oil_volume_balance():
    o = CFG["oil_leak"]
    assert f.expected_oil_volume(30, o) == pytest.approx(300)
    assert f.expected_oil_volume(50, o) == pytest.approx(304.5)
    assert f.oil_leak_status(301.0, 304.5, o) == "WARNING"     # 3.5 L below expectation
    assert f.oil_leak_status(287.0, 290.0, o) == "CRITICAL"    # below the minimum volume
    assert f.oil_leak_status(304.0, 304.5, o) == "NORMAL"


# ---------------------------------------------------------------- generated data covers every condition

@pytest.fixture(scope="module")
def payloads(tmp_path_factory):
    return build(out_dir=tmp_path_factory.mktemp("twin2"))


def test_timebar1_shows_every_electrical_thermal_and_pressure_condition(payloads):
    types = {e["type"] for e in payloads["timebar1"]["events"]}
    assert {"OVERLOAD", "OVERLOAD_TRIP", "OVERVOLTAGE", "UNDERVOLTAGE",
            "OTI_ALARM", "OTI_TRIP", "WTI_ALARM", "WTI_TRIP", "THERMAL_TRIP_OPERATED",
            "TOP_OIL_RISE_OVER_LIMIT", "COOLING_DEGRADATION", "WINDING_EXCESS_HEATING",
            "ACCELERATED_THERMAL_AGEING", "WINDING_PROBE_INVALID", "PRESSURE_WARNING", "PRECISION_CURRENT"} <= types


def test_pressure_near_failure_is_caught_below_the_limit(payloads):
    tb = payloads["timebar1"]
    peak = max(p for p in tb["series"]["pressure_abs"] if p is not None)
    assert tb["limits"]["pressure_warning_kpa"] < peak < tb["limits"]["pressure_limit_kpa"]
    assert "CRITICAL" not in tb["status"]["pressure"]


def test_timebar2_every_precision_device_fails_and_oil_reaches_minimum(payloads):
    tb = payloads["timebar2"]
    assert tb["days"] == 365
    types = [e["type"] for e in tb["events"]]
    for dev in ("CURRENT", "VOLTAGE", "TEMPERATURE"):
        assert types.count(f"PRECISION_{dev}") >= 1 and f"PRECISION_{dev}_RECALIBRATED" in types
    assert "OIL_LEAK" in types and "MINIMUM_OIL_LEVEL" in types and "OIL_LEAK_REPAIRED" in types


def test_voltage_events_name_each_winding(payloads):
    ev = [e for e in payloads["timebar1"]["events"] if e["type"] in ("OVERVOLTAGE", "UNDERVOLTAGE")]
    assert {(e["type"], e["winding"]) for e in ev} >= {("OVERVOLTAGE", "primary"), ("UNDERVOLTAGE", "primary"),
                                                       ("UNDERVOLTAGE", "secondary")}
    limits = payloads["timebar1"]["limits"]
    assert (limits["voltage_secondary_high_pct"], limits["voltage_secondary_low_pct"]) == (6.0, 9.0)


def test_out_of_service_is_consistent(payloads):
    """With the breaker open: no CT / PT comparison, no expected top oil, no winding excess."""
    tb = payloads["timebar1"]
    s = tb["series"]
    off = [m for m, on in enumerate(s["energized"]) if not on]
    assert off
    for m in (m for m in off if m - 1 in off and m + 1 in off):     # whole minutes out of service
        assert s["prec_current"][m] == [None, None, None] and s["prec_voltage"][m] == [None, None, None]
        assert s["prec_temperature"][m][0] is not None
        assert s["top_oil_expected"][m] is None
    # the blocked duct shows only under load: the residual decays to ~0 a few winding time constants into an outage
    late = [m for m in off if m - 40 in off and m + 1 in off]
    assert late and all(abs(s["residual"][m]) < 0.2 for m in late if s["residual"][m] is not None)


def test_load_factor_keeps_the_overload_surge(payloads):
    s = payloads["timebar1"]["series"]
    assert max(x for x in s["load_factor"] if x) == pytest.approx(max(x for x in s["loading_max"] if x) / 100, rel=0.02)


def test_measured_winding_is_never_drawn_below_the_hot_spot(payloads):
    """The probe sits at the hot spot: equal when healthy, above it with a blocked duct (same per-minute max)."""
    s = payloads["timebar1"]["series"]
    pairs = [(w, h) for w, h in zip(s["winding_measured"], s["hotspot"]) if w is not None and h is not None]
    assert pairs and min(w - h for w, h in pairs) > -0.5


# ---------------------------------------------------------------- short circuit

SC = CFG["short_circuit"]


def _cycles(pre, fault, post, phases=("Y",), pu=10.0, v_fault=20.0):
    """Per-cycle rows: load 60 %, then the fault on `phases`, then the breaker open."""
    i, v = [], []
    for c in range(pre + fault + post):
        if c < pre:
            i.append([60, 59, 58]); v.append([100, 100, 100])
        elif c < pre + fault:
            i.append([pu * 100 if p in phases else 60 for p in "RYB"])
            v.append([v_fault if p in phases else 104 for p in "RYB"])
        else:
            i.append([0, 0, 0]); v.append([0, 0, 0])
    return i, v


def test_rated_short_circuit_current_from_impedance():
    assert f.rated_short_circuit_multiple(SC) == pytest.approx(100 / SC["impedance_pct"])


@pytest.mark.parametrize("phases,kind", [(("Y",), "single line-to-ground (Y)"), (("R", "B"), "line-to-line (R-B)"),
                                         (("R", "Y", "B"), "three-phase")])
def test_short_circuit_is_classified_and_timed(phases, kind):
    i, v = _cycles(10, 3, 10, phases)
    det = f.detect_short_circuit(i, v, 20.0, SC)
    assert det["kind"] == "SHORT_CIRCUIT" and det["fault"] == kind and det["phases"] == list(phases)
    assert det["pickup_ms"] == 200 and det["trip_ms"] - det["pickup_ms"] == 20 * SC["trip_cycles"]
    assert det["duration_ms"] == 60 and not det["breaker_failure"]


def test_high_current_without_voltage_collapse_is_not_a_short_circuit():
    i, v = _cycles(10, 3, 10, pu=5.0, v_fault=97.0)
    assert f.detect_short_circuit(i, v, 20.0, SC)["kind"] == "INSTANT_OVERCURRENT"


def test_overload_below_pickup_is_left_to_the_overload_protection():
    i, v = _cycles(10, 3, 10, pu=1.3, v_fault=98.0)
    assert f.detect_short_circuit(i, v, 20.0, SC) is None


def test_slow_clearing_is_a_breaker_failure_and_pressure_surge_an_internal_fault():
    i, v = _cycles(10, 12, 5)
    assert f.detect_short_circuit(i, v, 20.0, SC)["breaker_failure"]          # 240 ms > 150 ms
    assert f.detect_short_circuit(i, v, 20.0, SC, pressure_rise_kpa=30)["internal"]


def test_fault_effects_stay_inside_the_iec_withstand():
    i, v = _cycles(10, 3, 10, ("R", "Y", "B"), pu=f.rated_short_circuit_multiple(SC))
    eff = f.fault_effects(f.detect_short_circuit(i, v, 20.0, SC), 90.0, SC)
    assert eff["force_pct_of_design"] == pytest.approx(100, abs=0.5)          # a full rated fault = design force
    assert eff["winding_after_c"] < SC["winding_limit_c"]
    assert f.adiabatic_winding_temperature(90, 3 * 22.2, 2.0) < SC["winding_limit_c"]   # 2 s rated withstand


def test_timebar1_short_circuits_and_timebar2_through_fault_duty(payloads):
    tb1, tb2 = payloads["timebar1"], payloads["timebar2"]
    types1 = [e["type"] for e in tb1["events"]]
    assert types1.count("SHORT_CIRCUIT") == 2 and "BREAKER_FAILURE" in types1 and "FAULT_CLEARED" in types1
    faults = [w for w in tb1["windows"] if w["kind"] == "FAULT"]
    assert {w["detection"]["fault"] for w in faults} == {"single line-to-ground (Y)", "three-phase"}
    assert all(w["effects"]["force_pct_of_design"] < 100 for w in faults)
    assert "CRITICAL" in tb1["status"]["fault"] and "OFF" in tb1["status"]["fault"]
    types2 = [e["type"] for e in tb2["events"]]
    assert "THROUGH_FAULT_DUTY" in types2 and "THROUGH_FAULT_DUTY_TESTED" in types2
    day = (__import__("datetime").date.fromisoformat(tb1["start"][:10]) - __import__("datetime").date.fromisoformat(tb2["start"])).days
    assert tb2["series"]["through_faults"][day] - tb2["series"]["through_faults"][day - 1] == 2   # the two timebar-1 faults


# ---------------------------------------------------------------- overall health score

from app.twin2 import health as hl   # noqa: E402

HC = CFG["health"]


def test_score_interpolates_and_clamps():
    pts = HC["timebar1"]["points"]["top_oil"]
    assert hl.score(60, pts) == 100 and hl.score(95, pts) == pytest.approx(60) and hl.score(200, pts) == 0
    assert hl.score(None, pts) is None


def test_one_bad_part_cannot_be_averaged_away():
    subs = {k: 100.0 for k in hl.SUB1}
    subs["top_oil"] = 20.0
    hi = hl.combine(subs, HC["timebar1"]["weights"], HC["cap_margin"])
    assert hi == pytest.approx(20 + HC["cap_margin"]) and hl.band(hi, HC) == "CRITICAL"


def test_health_score_by_the_second_and_by_the_day(payloads):
    tb1, tb2 = payloads["timebar1"], payloads["timebar2"]
    h1, h2 = tb1["series"]["health_1s"], tb2["series"]["health"]
    assert len(h1) == tb1["minutes"] * 60 and len(h2) == tb2["days"]
    assert min(h1[:3 * 3600]) >= HC["good"] - 2                 # quiet night (one through-fault at 01:47 dents it)
    assert min(h1[13 * 3600:15 * 3600]) < HC["poor"]            # cooling failure / OTI trip
    assert h2[14] < h2[13] and min(h2) < HC["poor"] and h2[-1] >= HC["good"]
    assert not [e for e in tb1["events"] + tb2["events"] if e["type"].startswith("HEALTH")]   # a score, not a signal
