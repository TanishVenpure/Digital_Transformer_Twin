"""Assistant chat and maintenance risk, grounded in the twin2 datasets at the two timebar cursors.

The payloads come from tests/twin2_fixture.py (see its docstring for the six checks), so the tests do not
depend on the generated data/twin2 files. Everything here is deterministic: no LLM is called.
"""
import pytest
from fastapi.testclient import TestClient

from app.api import main
from app.twin2 import sheet as twin_sheet
from tests.twin2_fixture import timebar1, timebar2

T = twin_sheet.TRANSFORMER_ID


@pytest.fixture
def client(monkeypatch):
    tb1, tb2 = timebar1(), timebar2()
    monkeypatch.setattr(twin_sheet, "load_sheet", lambda: (twin_sheet.build_sheet(tb1, tb2), tb1, tb2))
    return TestClient(main.app, base_url="http://localhost")


def chat(client, question, **cursor):
    r = client.post("/api/assistant/chat", json={"transformer_id": T, "question": question, **cursor})
    assert r.status_code == 200, r.text
    return r.json()


def risk(client, **cursor):
    r = client.get("/api/assistant/risk", params={"transformer_id": T, **cursor})
    assert r.status_code == 200, r.text
    return r.json()


class TestChat:
    def test_reply_is_deterministic_synthetic_and_states_the_cursors(self, client):
        body = chat(client, "summary", until="2026-07-15T02:30:00Z", until2="2026-09-20")
        assert body["answer_source"] == "deterministic" and body["synthetic_evidence"] is True
        assert body["cursor"] == {"timebar1": "2026-07-15T02:30:00+00:00", "timebar2": "2026-09-20", "minute": 150, "day": 50}
        assert body["answer"].startswith("At timebar 1 02:30 UTC on 2026-07-15 and timebar 2 2026-09-20:")

    def test_summary_counts_only_checks_up_to_both_cursors(self, client):
        body = chat(client, "summary", until="2026-07-15T01:30:00Z", until2="2026-08-25")
        assert "3 checks, 2 open, 1 resolved" in body["answer"]
        assert "#4" in body["answer"] and "#2" not in body["answer"] and "#5" not in body["answer"]
        body = chat(client, "summary")   # defaults to the end of both timebars
        assert "6 checks, 2 open, 4 resolved" in body["answer"]

    def test_summary_reports_the_open_breaker(self, client):
        assert "breaker is open" in chat(client, "status", until="2026-07-15T02:40:00Z")["answer"]
        assert "breaker is open" not in chat(client, "status", until="2026-07-15T02:00:00Z")["answer"]

    def test_topic_question_gives_monitor_value_status_and_checks(self, client):
        body = chat(client, "what about the oil leak?", until2="2026-08-25")
        assert "Oil volume (leak) (timebar 2) is NORMAL at -2 L vs no-leak expectation" in body["answer"]
        assert "2 check(s) recorded so far, 2 open" in body["answer"]
        assert any("Oil below the minimum level" in f["statement"] for f in body["facts"])

    def test_short_keywords_match_whole_words_only(self, client):
        body = chat(client, "is the ct accurate")
        assert "Current device (CT) (timebar 1)" in body["answer"]
        assert "Winding" not in chat(client, "what is the tank pressure?")["answer"]   # "pressure" must not match "pt"

    def test_repairs_list_timebar_2_actions_with_their_resolution(self, client):
        body = chat(client, "which repairs were done?", until2="2026-09-30")
        assert "3 maintenance action(s)" in body["answer"]
        assert "CT recalibrated." in body["answer"] and "Leak repaired and oil topped up." in body["answer"]
        assert "0 maintenance action(s)" in chat(client, "repairs", until2="2026-08-30")["answer"]

    def test_signals_are_counted_by_level(self, client):
        body = chat(client, "list the alarms")
        assert "6 signals recorded up to the cursors" in body["answer"]
        assert "2 CRITICAL" in body["answer"]

    def test_greeting_and_off_topic(self, client):
        assert chat(client, "hello")["answer"].startswith("Hello!")
        assert "I can only answer questions about transformer" in chat(client, "best pizza in town")["answer"]

    def test_unknown_transformer_and_bad_cursor(self, client):
        r = client.post("/api/assistant/chat", json={"transformer_id": "T1", "question": "summary"})
        assert r.status_code == 404
        r = client.post("/api/assistant/chat", json={"transformer_id": T, "question": "summary", "until": "soon"})
        assert r.status_code == 422
        r = client.post("/api/assistant/chat", json={"transformer_id": T, "question": ""})
        assert r.status_code == 422


class TestRisk:
    def test_no_checks_before_the_first_event_is_insufficient_evidence(self, client):
        body = risk(client, until="2026-07-15T00:10:00Z", until2="2026-08-02")
        assert body["risk_category"] == "INSUFFICIENT_EVIDENCE"
        assert body["synthetic"] is True

    def test_unresolved_critical_check_forces_high(self, client):
        body = risk(client, until="2026-07-15T00:10:00Z", until2="2026-08-25")
        assert body["risk_category"] == "HIGH_MAINTENANCE_RISK"
        assert body["recommendation"]["based_on_check"] == 4

    def test_resolution_known_only_from_the_follow_up_day(self, client):
        before = risk(client, until="2026-07-15T00:10:00Z", until2="2026-08-30")
        after = risk(client, until="2026-07-15T00:10:00Z", until2="2026-08-31")
        assert before["risk_category"] == "HIGH_MAINTENANCE_RISK"
        assert after["risk_category"] != "HIGH_MAINTENANCE_RISK"

    def test_result_names_cursors_policy_and_data_source(self, client):
        body = risk(client, until="2026-07-15T02:00:00Z", until2="2026-09-20")
        assert body["cursors"] == {"timebar1": "2026-07-15T02:00:00+00:00", "timebar2": "2026-09-20"}
        assert body["data_source"].startswith("twin2 simulated datasets")
        assert [f["weight"] for f in body["contributing_factors"]] == [30, 25, 20, 15, 10]
        recurrence = next(f for f in body["contributing_factors"] if f.get("iot_breakdown"))
        assert {(b["timebar"], b["iot"]) for b in recurrence["iot_breakdown"]} <= {
            (1, "loading"), (1, "oti"), (2, "oil"), (2, "prec_current")}

    def test_same_cursors_give_the_same_assessment(self, client):
        a = risk(client, until="2026-07-15T02:00:00Z", until2="2026-09-20")
        b = risk(client, until="2026-07-15T02:00:00Z", until2="2026-09-20")
        assert a["assessment_id"] == b["assessment_id"] and a["maintenance_risk_index"] == b["maintenance_risk_index"]
        c = risk(client, until="2026-07-15T02:01:00Z", until2="2026-09-20")
        assert c["assessment_id"] != a["assessment_id"]

    def test_unknown_transformer_and_bad_cursor(self, client):
        assert client.get("/api/assistant/risk", params={"transformer_id": "T1"}).status_code == 404
        assert client.get("/api/assistant/risk", params={"until2": "not-a-date"}).status_code == 422


class TestSheetAndStatus:
    def test_sheet_endpoint_lists_every_check_without_status(self, client):
        body = client.get("/api/twin2/sheet").json()
        assert body["transformer_id"] == T
        assert [c["check_no"] for c in body["checks"]] == [1, 2, 3, 4, 5, 6]
        assert all("status" not in c for c in body["checks"])

    def test_status(self, client):
        assert client.get("/api/assistant/status").json() == {"backend": "self-contained", "status": "ok", "transformer_id": T}
