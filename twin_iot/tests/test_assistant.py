import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from app.agent.chat import explain, get_client
from app.agent.llm import GeminiClient, LLMUnavailable
from app.api import assistant
from app.twin2 import sheet


EVIDENCE = [{"source_id": "timebar:1", "content": {"measurements": {"loading": 112.0}, "synthetic": True}}]
VALID = {"answer": "Recorded loading is 112 percent.", "facts": [{"statement": "Loading is 112 percent.",
         "assertions": [{"source_id": "timebar:1", "field": "measurements.loading", "expected_value": 112.0}]}],
         "evidence_source_ids": ["timebar:1"]}


class Fake:
    def __init__(self, value): self.value = value
    def complete(self, **kwargs): return json.dumps(self.value)


def test_llm_output_with_verified_evidence():
    result = explain("loading?", EVIDENCE, {}, Fake(VALID))
    assert result["answer_source"] == "llm"
    assert result["evidence_source_ids"] == ["timebar:1"]
    assert "risk_assessment" not in result


@pytest.mark.parametrize("change", ["wrong_value", "wrong_source", "extra_score", "missing_facts", "safety_claim"])
def test_bad_llm_outputs_fall_back(change):
    bad = json.loads(json.dumps(VALID))
    if change == "wrong_value": bad["facts"][0]["assertions"][0]["expected_value"] = 999
    if change == "wrong_source": bad["evidence_source_ids"] = ["invented"]
    if change == "extra_score": bad["health_score"] = 100
    if change == "missing_facts": bad["facts"] = []
    if change == "safety_claim": bad["answer"] = "The transformer is safe to operate."
    assert explain("question", EVIDENCE, {}, Fake(bad))["answer_source"] == "deterministic_fallback"


def test_timeout_and_disabled_provider(monkeypatch):
    class Timeout:
        def complete(self, **kwargs): raise LLMUnavailable("timeout")
    assert explain("question", EVIDENCE, {}, Timeout())["answer_source"] == "deterministic_fallback"
    monkeypatch.setenv("LLM_PROVIDER", "none")
    assert get_client() is None
    assert explain("question", EVIDENCE, {})["answer_source"] == "deterministic"


def test_source_gemini_sdk_contract_without_network():
    class Models:
        def generate_content(self, **kwargs):
            assert kwargs["config"].response_mime_type == "application/json"
            return SimpleNamespace(text=json.dumps(VALID), candidates=[], usage_metadata=None)
    client = GeminiClient(api_key=SecretStr("test-not-a-real-key"), thinking_level="default",
                          sdk_client=SimpleNamespace(models=Models()))
    assert json.loads(client.complete(system="system", prompt="prompt")) == VALID


def payloads():
    tb1 = {"start": "2026-07-01T00:00:00+00:00", "minutes": 10,
           "events": [{"timebar": 1, "t": 60, "t_end": 240, "monitor": "loading",
                       "level": "WARNING", "type": "OVERLOAD", "message": "Recorded warning"}],
           "status": {"loading": ["NORMAL"] * 10}, "series": {"loading_max": [50] * 10}}
    tb2 = {"start": "2026-01-01", "days": 10, "events": [],
           "status": {"oil": ["NORMAL"] * 10}, "series": {"oil": [70] * 10}}
    return tb1, tb2


def test_cursors_do_not_leak_future_resolution():
    a, b = payloads()
    checks = sheet.build_sheet(a, b)
    early = sheet.cursors(a, b, "2026-07-01T00:02:00Z", "2026-01-02")
    record = sheet.sheet_at(checks, early)[0]
    assert record["status"] == "OPEN"
    assert record["resolved_at"] is None and record["resolution"] is None
    before = sheet.cursors(a, b, "2026-07-01T00:00:00Z", None)
    assert not sheet.sheet_at(checks, before)
    later = sheet.cursors(a, b, "2026-07-01T00:05:00Z", None)
    assert sheet.sheet_at(checks, later)[0]["status"] == "RESOLVED"


def test_chat_api_cursor_scope_and_validation(monkeypatch):
    from fastapi import FastAPI
    a, b = payloads()
    monkeypatch.setattr(sheet, "load_sheet", lambda: (sheet.build_sheet(a, b), a, b))
    app = FastAPI(); app.include_router(assistant.router)
    app.dependency_overrides[get_client] = lambda: None
    with TestClient(app) as client:
        request = {"question": "loading?", "until": "2026-07-01T00:02:00Z", "until2": "2026-01-02"}
        result = client.post("/api/assistant/chat", json=request)
        assert result.status_code == 200
        assert result.json()["answer_source"] == "deterministic"
        assert "1 open" in result.json()["answer"]
        assert "OPEN" in result.json()["facts"][0]["statement"]
        assert client.post("/api/assistant/chat", json={**request, "transformer_id": "other"}).status_code == 404
        assert client.post("/api/assistant/chat", json={**request, "until": "invalid"}).status_code == 422
        assert client.post("/api/assistant/chat", json={"question": ""}).status_code == 422


def test_fallback_open_question_does_not_list_resolved_checks():
    evidence = [{"source_id": "check:1", "content": {"iot_name": "Oil", "flag": "LOW_OIL",
                 "status": "RESOLVED", "opened_at": "2026-07-01"}}]
    result = explain("Which checks are open?", evidence, {})
    assert "RESOLVED" not in result["answer"]
    assert "No matching" in result["answer"]


def conversation_payloads():
    a, b = payloads()
    a["status"].update({"voltage": ["WARNING"] * 10, "wti": ["NORMAL"] * 10,
                        "pressure": ["NORMAL"] * 10, "prec_current": ["WARNING"] * 10})
    a["series"].update({"v_pri_min": [-2.] * 10, "v_pri_max": [3.] * 10,
        "v_sec_min": [-1.] * 10, "v_sec_max": [2.] * 10, "wti": [83.] * 10,
        "pressure_abs": [110.] * 10, "prec_current": [[.4, .3, 1.2]] * 10,
        "energized": [True] * 10})
    b["series"]["residual"] = [-5.] * 10
    b["events"] = [
        {"timebar": 2, "day": 1, "monitor": "oil", "level": "WARNING",
         "type": "OIL_LEAK", "message": "Recorded leak inspection."},
        {"timebar": 2, "day": 3, "monitor": "oil", "level": "NORMAL",
         "type": "OIL_LEAK_REPAIRED", "message": "Recorded leak repair."}]
    return a, b


@pytest.mark.parametrize(("question", "expected"), [
    ("hello", "Hello!"), ("voltage?", "+3 %"), ("winding?", "83 °C"),
    ("pressure?", "110 kPa abs"), ("current precision?", "1.2 % max deviation"),
    ("oil?", "-5 L vs no-leak expectation"), ("signals?", "2 signals recorded"),
    ("repairs?", "Recorded leak repair."), ("maintenance summary", "2 checks, 0 open, 2 resolved"),
    ("write me a cake recipe", "I can only answer questions about transformer")])
def test_source_branch_conversation_behaviors(monkeypatch, question, expected):
    from fastapi import FastAPI
    a, b = conversation_payloads()
    monkeypatch.setattr(sheet, "load_sheet", lambda: (sheet.build_sheet(a, b), a, b))
    app = FastAPI(); app.include_router(assistant.router)
    app.dependency_overrides[get_client] = lambda: None
    with TestClient(app) as client:
        result = client.post("/api/assistant/chat", json={"question": question,
            "until": "2026-07-01T00:05:00Z", "until2": "2026-01-05"}).json()
    assert expected in result["answer"]
    assert result["synthetic_evidence"] is True
    assert "risk_assessment" not in result
    assert result["cursor"]["timebar1"].endswith("+00:00")


def test_provider_failure_retains_topic_specific_answer(monkeypatch):
    from fastapi import FastAPI
    class Timeout:
        def complete(self, **kwargs): raise LLMUnavailable("timeout")
    a, b = conversation_payloads()
    monkeypatch.setattr(sheet, "load_sheet", lambda: (sheet.build_sheet(a, b), a, b))
    app = FastAPI(); app.include_router(assistant.router)
    app.dependency_overrides[get_client] = lambda: Timeout()
    with TestClient(app) as client:
        result = client.post("/api/assistant/chat", json={"question": "voltage?"}).json()
    assert result["answer_source"] == "deterministic_fallback"
    assert "+3 %" in result["answer"]


def test_repair_answer_follows_daily_cursor(monkeypatch):
    from fastapi import FastAPI
    a, b = conversation_payloads()
    monkeypatch.setattr(sheet, "load_sheet", lambda: (sheet.build_sheet(a, b), a, b))
    app = FastAPI(); app.include_router(assistant.router)
    app.dependency_overrides[get_client] = lambda: None
    with TestClient(app) as client:
        early = client.post("/api/assistant/chat", json={"question": "repairs?", "until2": "2026-01-02"}).json()
        later = client.post("/api/assistant/chat", json={"question": "repairs?", "until2": "2026-01-05"}).json()
    assert "0 maintenance action(s)" in early["answer"]
    assert "Recorded leak repair." not in early["answer"]
    assert "Recorded leak repair." in later["answer"]


def test_precision_monitor_is_in_llm_evidence(monkeypatch):
    a, b = conversation_payloads()
    monkeypatch.setattr(sheet, "load_sheet", lambda: (sheet.build_sheet(a, b), a, b))
    evidence, cursor, ctx = assistant.context(assistant.ChatIn(question="precision?"))
    precision = next(e for e in evidence if e["source_id"] == "monitor:1:prec_current")
    assert precision["content"]["value"] == pytest.approx(1.2)
    assert len(ctx["checks"]) == 2


def test_popup_check_sheet_api_reuses_existing_recorded_events(monkeypatch):
    from fastapi import FastAPI
    from app.api.main import twin2_sheet
    a, b = conversation_payloads()
    monkeypatch.setattr(sheet, "load_sheet", lambda: (sheet.build_sheet(a, b), a, b))
    app = FastAPI(); app.add_api_route("/api/twin2/sheet", twin2_sheet)
    with TestClient(app) as client:
        response = client.get("/api/twin2/sheet")
    assert response.status_code == 200
    result = response.json()
    assert result["transformer_id"] == "CPRI-630"
    assert len(result["checks"]) == 2
    assert {c["timebar"] for c in result["checks"]} == {1, 2}
    assert result["checks"][0]["flag"] == "OVERLOAD"
    assert result["checks"][1]["flag"] == "OIL_LEAK"
    assert all("status" not in c for c in result["checks"])
