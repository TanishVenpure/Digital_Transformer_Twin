"""Maintenance sheet built from the two timebars: numbering, resolution mapping and cursor views."""
import pytest

from app.twin2 import sheet
from tests.twin2_fixture import timebar1, timebar2


@pytest.fixture
def tbs():
    return timebar1(), timebar2()


@pytest.fixture
def checks(tbs):
    return sheet.build_sheet(*tbs)


def test_checks_are_numbered_timebar_1_first_and_follow_ups_are_not_checks(checks):
    assert [(c["check_no"], c["timebar"], c["flag"]) for c in checks] == [
        (1, 1, "OVERLOAD_WARNING"), (2, 1, "OTI_TRIP"), (3, 2, "OIL_LEAK"), (4, 2, "MINIMUM_OIL_LEVEL"),
        (5, 2, "PRECISION_CURRENT"), (6, 2, "PRECISION_VOLTAGE")]
    assert checks[2]["signal"] == "MAINTENANCE_REQUIRED"   # MAINTENANCE level maps to the sheet signal
    assert checks[1]["iot_name"] == "Top oil (OTI)" and checks[4]["iot_name"] == "Current device (CT), daily"


def test_timebar_1_resolves_when_the_recorded_condition_ends(checks):
    c1, c2 = checks[0], checks[1]
    assert (c1["index"], c1["time_utc"], c1["resolved_index"]) == (60, "01:00:00", 67)
    assert c1["resolved_at"] == "2026-07-15T01:06:40+00:00" and c1["resolution"] == "Recorded condition ended."
    assert c2["resolved_at"] is None and c2["resolved_index"] is None


def test_timebar_2_resolves_only_by_the_matching_follow_up(checks):
    leak, low, ct, pt = checks[2:]
    assert leak["resolved_index"] == low["resolved_index"] == 30
    assert leak["resolution"] == "Leak repaired and oil topped up."
    assert (ct["resolved_index"], ct["resolution"]) == (50, "CT recalibrated.")
    assert pt["resolved_at"] is None
    assert (low["opened_at"], low["date"], low["time_utc"]) == ("2026-08-21T00:00:00+00:00", "2026-08-21", None)


def test_cursors_default_to_the_end_and_clamp(tbs):
    cur = sheet.cursors(*tbs, None, None)
    assert (cur["minute"], cur["day"]) == (179, 99)
    cur = sheet.cursors(*tbs, "2026-07-15T01:30:59Z", "2026-08-11")
    assert (cur["minute"], cur["day"]) == (90, 10)
    cur = sheet.cursors(*tbs, "2020-01-01T00:00:00", "2030-01-01")
    assert (cur["minute"], cur["day"]) == (0, 99)
    with pytest.raises(ValueError):
        sheet.cursors(*tbs, "yesterday", None)


def test_sheet_at_hides_future_checks_and_future_resolutions(checks, tbs):
    view = lambda until, until2: {c["check_no"]: c["status"] for c in sheet.sheet_at(checks, sheet.cursors(*tbs, until, until2))}
    assert view("2026-07-15T00:59:00Z", "2026-08-10") == {}
    assert view("2026-07-15T00:59:00Z", "2026-08-11") == {3: "OPEN"}
    assert view("2026-07-15T01:06:00Z", "2026-08-30") == {1: "OPEN", 3: "OPEN", 4: "OPEN"}
    assert view("2026-07-15T01:07:00Z", "2026-08-31") == {1: "RESOLVED", 3: "RESOLVED", 4: "RESOLVED"}
    assert view(None, None) == {1: "RESOLVED", 2: "OPEN", 3: "RESOLVED", 4: "RESOLVED", 5: "RESOLVED", 6: "OPEN"}


def test_sheet_at_is_newest_first_per_timebar(checks, tbs):
    out = sheet.sheet_at(checks, sheet.cursors(*tbs, None, None))
    assert [c["check_no"] for c in out] == [6, 5, 4, 3, 2, 1]
    assert out[0]["as_of"] == "2026-11-08T00:00:00+00:00" and out[-1]["as_of"] == "2026-07-15T02:59:00+00:00"
