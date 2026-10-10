from datetime import datetime, timedelta, timezone

import pytest

from app.analytics.winding import FLAG, ThermalConfig, ageing_rate, analyse, expected_rises

START = datetime(2026, 7, 1, tzinfo=timezone.utc)
CONFIG = ThermalConfig()


def test_steady_rated_load_gives_design_rise():
    rises = expected_rises([100.0] * 48, CONFIG)
    assert rises[-1] == pytest.approx(CONFIG.top_oil_rise_rated + CONFIG.gradient_rated, abs=0.05)


def test_ageing_rate_doubles_every_6_k():
    assert ageing_rate(98, CONFIG) == pytest.approx(1.0)
    assert ageing_rate(104, CONFIG) == pytest.approx(2.0)
    assert ageing_rate(92, CONFIG) == pytest.approx(0.5)


def rows(days: int, excess_per_day: float = 0.0, loading: float = 80.0, ambient: float = 30.0):
    expected = expected_rises([loading] * (days * 24), CONFIG)
    return [(START + timedelta(hours=h), e + excess_per_day * h / 24, loading, ambient)
            for h, e in enumerate(expected)]


def test_healthy_winding_tracks_the_model():
    result = analyse(rows(35), CONFIG)
    assert result["flags"] == []
    assert all(abs(r["mean_excess_k"]) < 0.01 for r in result["reviews"])


def test_growing_excess_is_flagged():
    result = analyse(rows(35, excess_per_day=0.12), CONFIG)
    flag = result["flags"][0]
    assert flag["flag"] == FLAG and flag["first"]
    assert flag["excess_by_week"] == sorted(flag["excess_by_week"]) and flag["value"] >= CONFIG.min_excess
    assert "restricted oil flow" in flag["message"]


def test_loss_of_life_accumulates_from_hot_spot():
    result = analyse(rows(7, loading=100.0, ambient=98.0 - 45.0), CONFIG)   # hot-spot = 98 degC -> rate 1
    assert result["loss_of_life_days"] == pytest.approx(7, rel=0.02)


def test_expected_wti_is_ambient_plus_expected_rise():
    data = rows(8, ambient=31.5)
    result = analyse(data, CONFIG)
    assert len(result["expected_wti"]) == len(data)
    assert all(w == pytest.approx(e + 31.5, abs=0.01) for w, e in zip(result["expected_wti"], result["expected"]))


def test_rise_analytics_use_the_wti_details():
    """The WTI evaluation keeps the rise over ambient in its details for these analytics."""
    from app.services.processing_service import ProcessingService
    e = ProcessingService().process("T1", START, {"winding_temperature_r": 75, "winding_temperature_y": 80,
                                                   "winding_temperature_b": 78, "ambient_temperature": 30})
    w = e.evaluations["winding_temperature"]
    assert w.value == 80 and w.details["winding_rise"] == 50
    result = analyse([(START + timedelta(hours=h), w.details["winding_rise"], 80.0, 30.0) for h in range(24)], CONFIG)
    assert result["loss_of_life_days"] > 0 and len(result["expected"]) == 24
