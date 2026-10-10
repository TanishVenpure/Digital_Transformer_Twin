from datetime import datetime, timezone

from app.services.processing_service import ProcessingService
from app.simulation.degradation_source import MONITORS, WtiEpisode, generate

START = datetime(2026, 7, 1, tzinfo=timezone.utc)


def test_every_monitor_degrades_to_critical_and_is_repaired():
    readings, repairs = generate(start=START, days=90, seed=7, invalid_rate=0)
    service = ProcessingService()
    seen = {slot: set() for slot in range(1, 8)}
    for timestamp, data in readings:
        for e in service.process("T1", timestamp, data).evaluations.values():
            if e.iot_slot == 8:
                assert e.evaluation_status == "SKIPPED" and not e.alerts
            else:
                seen[e.iot_slot].add(e.status)
    assert all(repairs[name] for name in MONITORS if name not in ("current", "voltage", "winding")), repairs
    # the duct fault is not repaired in the window; the only winding event ends the WTI overload episode
    assert repairs["winding"] == [WtiEpisode().end_hour]
    assert repairs["current"] == []   # load follows demand; it is managed, never "repaired"
    assert repairs["voltage"] == []   # supply voltage is external; nothing to repair
    for slot, statuses in seen.items():
        assert "NORMAL" in statuses
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


def test_wti_episode_crosses_alarm_then_trip_and_ends():
    readings, _ = generate(start=START, days=90, seed=7, invalid_rate=0)
    ep = WtiEpisode()
    wti = [max(d[f"winding_temperature_{p}"] for p in "ryb") for _, d in readings]
    over_alarm = [h for h, t in enumerate(wti) if t >= 110]
    over_trip = [h for h, t in enumerate(wti) if t >= 120]
    assert over_trip and over_alarm[0] < over_trip[0]               # alarm first, then trip
    assert all(ep.active(h) for h in over_alarm)                     # only during the episode
    assert max(wti) < 140                                            # CPRI-plausible emergency peak
    assert wti[ep.end_hour + 2] < 110                                # back below the alarm after it ends
