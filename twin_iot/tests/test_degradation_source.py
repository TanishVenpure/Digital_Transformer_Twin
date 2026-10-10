from datetime import datetime, timezone

from app.services.processing_service import ProcessingService
from app.simulation.degradation_source import MONITORS, generate

START = datetime(2026, 7, 1, tzinfo=timezone.utc)


def test_every_monitor_degrades_to_critical_and_is_repaired():
    readings, repairs = generate(start=START, days=90, seed=7, invalid_rate=0)
    service = ProcessingService()
    seen = {slot: set() for slot in range(1, 8)}
    for timestamp, data in readings:
        for e in service.process("T1", timestamp, data).evaluations.values():
            seen[e.iot_slot].add(e.status)
    assert all(repairs[name] for name in MONITORS if name not in ("current", "voltage", "winding")), repairs
    assert repairs["winding"] == []   # cooling-duct fault needs an internal inspection; not repaired in the window
    assert repairs["current"] == []   # load follows demand; it is managed, never "repaired"
    assert repairs["voltage"] == []   # supply voltage is external; nothing to repair
    for slot, statuses in seen.items():
        assert "NORMAL" in statuses
        assert {"CRITICAL", "MAINTENANCE_REQUIRED"} & statuses, (slot, statuses)


def test_same_seed_gives_same_data():
    a, _ = generate(start=START, days=2, seed=3)
    b, _ = generate(start=START, days=2, seed=3)
    assert list(a) == list(b)
