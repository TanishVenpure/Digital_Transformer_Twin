from datetime import datetime, timezone

from app.services.processing_service import ProcessingService
from app.simulation.degradation_source import MONITORS, generate

START = datetime(2026, 7, 1, tzinfo=timezone.utc)


def test_demo_degradation_and_supplied_loading_thresholds():
    readings, repairs = generate(start=START, days=90, seed=7, invalid_rate=0)
    service = ProcessingService()
    seen = {slot: set() for slot in range(1, 8)}
    for timestamp, data in readings:
        for e in service.process("T1", timestamp, data).evaluations.values():
            if e.iot_slot in (4, 8, 9):
                assert e.evaluation_status == "SKIPPED" and not e.alerts
            else:
                seen[e.iot_slot].add(e.status)
    assert all(repairs[name] for name in MONITORS if name not in ("current", "voltage", "winding")), repairs
    assert repairs["winding"] == []   # cooling-duct fault needs an internal inspection; not repaired in the window
    assert repairs["current"] == []   # load follows demand; it is managed, never "repaired"
    assert repairs["voltage"] == []   # supply voltage is external; nothing to repair
    for slot, statuses in seen.items():
        if slot == 4:
            assert not statuses  # legacy inputs do not identify the voltage side
            continue
        assert "NORMAL" in statuses
        if slot == 3:
            # Preserved demo demand crosses 110%, but not the supplied 120% trip limit.
            assert "WARNING" in statuses and "CRITICAL" not in statuses
            continue
        if slot == 6:   # top-oil: radiators are cleaned on cooling condition, so the trip is never reached
            assert "CRITICAL" not in statuses, statuses
            continue
        assert {"CRITICAL", "MAINTENANCE_REQUIRED"} & statuses, (slot, statuses)


def test_same_seed_gives_same_data():
    a, _ = generate(start=START, days=2, seed=3)
    b, _ = generate(start=START, days=2, seed=3)
    assert list(a) == list(b)


def test_top_oil_cleaning_intervals_shorten_and_oil_gauge_follows_temperature():
    readings, repairs = generate(start=START, days=90, seed=7, invalid_rate=0)
    rows = list(readings)
    days = [h / 24 for h in repairs["top_oil"]]
    intervals = [b - a for a, b in zip(days, days[1:])]
    assert len(intervals) >= 3 and all(b < a for a, b in zip(intervals, intervals[1:]))
    # conservator gauge rises with oil temperature within a day (no top-up on day 2)
    day = [data for _, data in rows[24:48]]
    hot = max(day, key=lambda d: d["top_oil_temperature"])
    cold = min(day, key=lambda d: d["top_oil_temperature"])
    assert hot["oil_level"] > cold["oil_level"]
