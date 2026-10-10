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


@pytest.mark.parametrize("vclass,dev,cond", [
    ("MV_LV", 6.1, "OVERVOLTAGE"), ("MV_LV", -6.1, "UNDERVOLTAGE"), ("MV_LV", -5.9, "NORMAL"),
    ("HV", 6.1, "OVERVOLTAGE"), ("HV", -8.9, "NORMAL"), ("HV", -9.1, "UNDERVOLTAGE"),
])
def test_voltage_limits_per_class(vclass, dev, cond):
    assert f.voltage_condition(dev, f.voltage_rule(CFG, vclass)) == cond


def test_voltage_class_watches_the_right_winding():
    assert f.voltage_rule(CFG, "MV_LV")["winding"] == "primary"
    assert f.voltage_rule(CFG, "HV")["winding"] == "secondary"
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
