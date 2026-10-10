from datetime import datetime, timedelta, timezone

import pytest

from app.analytics.top_oil import COOLING_FLAG, FLAG, TopOilConfig, analyse, expected_components
from app.analytics.winding import ThermalConfig

START = datetime(2026, 7, 1, tzinfo=timezone.utc)
THERMAL = ThermalConfig()
CONFIG = TopOilConfig()


def rows(days: int, factor=lambda h: 1.0, loading: float = 80.0, ambient: float = 30.0, level: float = 70.0,
         gradient_extra: float = 0.0):
    comps = expected_components([loading] * (days * 24), THERMAL)
    out = []
    for h, (oil, grad) in enumerate(comps):
        rise = oil * factor(h)
        out.append((START + timedelta(hours=h), ambient + rise, ambient, loading, loading, level,
                    rise + grad + gradient_extra))
    return out


def test_steady_rated_load_gives_design_top_oil_rise():
    assert expected_components([100.0] * 48, THERMAL)[-1][0] == pytest.approx(THERMAL.top_oil_rise_rated, abs=0.05)


def test_healthy_cooling_is_100_percent_and_unflagged():
    result = analyse(rows(21), cleanings=[])
    assert result["current"]["cooling_health"] == pytest.approx(100, abs=0.1)
    assert result["cooling_checks"] == [] and result["flags"] == []
    assert all(w["diagnosis"] == "healthy" for w in result["weeks"])
    assert result["weeks"][0]["normalised_top_oil_c"] == pytest.approx(30 + THERMAL.top_oil_rise_rated, abs=0.1)


def fouling_with_ageing(cleanings_h: list[int], fouling_per_day=0.012, ageing_per_day=0.0015):
    def factor(h):
        last = max([c for c in cleanings_h if c <= h], default=0)
        return 1 + fouling_per_day * (h - last) / 24 + ageing_per_day * h / 24
    return factor


def test_cooling_degraded_and_oil_ageing_from_cleaning_cycles():
    cleans = [20 * 24, 37 * 24, 51 * 24, 63 * 24]
    data = rows(70, fouling_with_ageing(cleans))
    result = analyse(data, cleanings=[START + timedelta(hours=h) for h in cleans])
    assert [c["interval_days"] for c in result["cycles"] if c["interval_days"]] == [17.0, 14.0, 12.0]
    recovered = [c["recovered_health"] for c in result["cycles"][1:]]
    assert recovered == sorted(recovered, reverse=True)
    assert len(result["cooling_checks"]) >= 2 and result["cooling_checks"][0]["flag"] == COOLING_FLAG
    flag = result["flags"][0]
    assert flag["flag"] == FLAG and "test the oil" in flag["message"]
    assert result["forecast"]["cleaning_due_days"] is not None


def test_cleanings_detected_from_cooling_jump_without_a_log():
    cleans = [20 * 24, 37 * 24]
    result = analyse(rows(45, fouling_with_ageing(cleans)), cleanings=None)
    found = [c["start"] for c in result["cycles"] if c["started_by_cleaning"]]
    assert [datetime.fromisoformat(s).date() for s in found] == [(START + timedelta(hours=h)).date() for h in cleans]


def test_low_oil_heat_is_attributed_to_low_oil_not_cooling():
    data = rows(10, factor=lambda h: 1.2 if h >= 5 * 24 else 1.0)
    data = [(t, o, a, l, r, 21.0 if i >= 5 * 24 else 70.0, w) for i, (t, o, a, l, r, _, w) in enumerate(data)]
    result = analyse(data, cleanings=[])
    hourly = result["hourly"]
    assert hourly["low_oil_k"][-1] > 3 and abs(hourly["cooling_k"][-1]) < 0.5
    assert result["current"]["cooling_health"] == pytest.approx(100, abs=0.5)


def test_winding_cross_check_separates_ducts_from_cooling():
    ducts = analyse(rows(14, gradient_extra=3.0), cleanings=[])
    cooling = analyse(rows(14, factor=lambda h: 1.15), cleanings=[])
    assert {w["diagnosis"] for w in ducts["weeks"]} == {"ducts"}
    assert {w["diagnosis"] for w in cooling["weeks"]} == {"cooling"}
