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


# ---------------------------------------------------------------- insulation (timebar 2)

INS = CFG["insulation"]


def test_paper_ageing_faster_when_hotter_and_wetter():
    assert f.paper_ageing_k(110, 1.9, INS) > 10 * f.paper_ageing_k(80, 1.9, INS)
    assert f.paper_ageing_k(90, 3.0, INS) > f.paper_ageing_k(90, 1.0, INS)
    assert f.arrhenius_a(1.5, INS) == pytest.approx(1.5e11)


def test_water_in_oil_rises_with_temperature_for_the_same_paper():
    assert f.water_in_oil_ppm(2.0, 60) > f.water_in_oil_ppm(2.0, 40)
    assert f.relative_saturation_pct(3.0) > f.relative_saturation_pct(2.0)


def test_hot_paper_makes_relatively_more_co():
    assert f.co2_co_generation_ratio(70) == pytest.approx(10.0)
    assert f.co2_co_generation_ratio(115) < INS["ratio_limit"]


def test_year_shows_insulation_moisture_and_dga_signals(payloads):
    tb = payloads["timebar2"]
    types = [e["type"] for e in tb["events"]]
    for t in ("ACCELERATED_INSULATION_AGEING", "DGA_PAPER_OVERHEATING", "PAPER_MOISTURE", "OIL_RECONDITIONED"):
        assert t in types, t
    s = tb["series"]
    assert s["dp"][0] > s["dp"][-1]                                   # paper only ages
    day = tb["limits"]["timebar1_day"]
    assert s["ageing_hours"][day] > 10 * s["ageing_hours"][day - 1]    # the timebar-1 overheating day
    assert all(s["dp"][i + 1] <= s["dp"][i] + 1e-6 for i in range(len(s["dp"]) - 1))


def test_reconditioning_dries_the_paper_and_degasses_the_oil(payloads):
    tb = payloads["timebar2"]
    d = next(e["day"] for e in tb["events"] if e["type"] == "OIL_RECONDITIONED")
    s = tb["series"]
    assert s["wcp"][d] < s["wcp"][d - 1] and s["co"][d] < 0.2 * s["co"][d - 1]
    leak2 = [e["day"] for e in tb["events"] if e["type"] == "OIL_LEAK"][-1]
    assert s["wcp"][d - 1] > s["wcp"][leak2 - 20]                     # humid air got in while the gasket leaked
