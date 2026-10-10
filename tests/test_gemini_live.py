"""Opt-in live check of the Gemini path. Skipped unless explicitly requested.

Run (sends only the synthetic fixture records below to Google):
    RUN_GEMINI_LIVE=1 GEMINI_API_KEY=... pytest -m gemini_live -s
"""
import os

import pytest

from app.agent.llm import GeminiClient
from app.config.settings import Settings
from tests.test_risk_chat import RECORDS, ask

pytestmark = pytest.mark.gemini_live


def test_live_gemini_answer_is_validated_or_safely_rejected():
    if os.environ.get("RUN_GEMINI_LIVE") != "1":
        pytest.skip("Set RUN_GEMINI_LIVE=1 to call the live Gemini API")
    settings = Settings()
    if settings.gemini_api_key is None:
        pytest.skip("GEMINI_API_KEY is not configured")
    client = GeminiClient(api_key=settings.gemini_api_key, model=settings.gemini_model,
                          timeout_seconds=settings.gemini_timeout_seconds,
                          thinking_level=settings.gemini_thinking_level)
    result = ask(client, "Which faults have recurred, and which repair outcomes are unresolved?", RECORDS)
    print("\nmodel:", client.model, "| deadline_s:", client.timeout_seconds, "| thinking_level:", client.thinking_level)
    print("answer_source:", result.answer_source)
    print("warnings:", result.warnings[-1] if result.warnings else None)
    # Sanitized: category, HTTP status, Google status, exception type, key-redacted message.
    print("provider diagnostic:", client.last_diagnostic)
    # Numbers and fixed categories only: elapsed_ms, finish_reason, token counts.
    print("call metrics:", client.last_metrics)
    # From the chatbot's existing safe path: names the failed check, never the reply text.
    rejection = next((w for w in result.warnings if "failed evidence validation" in w), None)
    print("validation rejection:", rejection)
    print("answer:", result.answer)
    for fact in result.facts:
        print("fact:", fact.statement, [(a.source_id, a.field, a.expected_value) for a in fact.assertions])
    # The assessment is always the engine's; an accepted answer passed every validation check.
    assert result.risk_assessment is not None
    assert result.answer_source == "llm", f"Gemini answer was not accepted: {result.warnings[-1]}"
