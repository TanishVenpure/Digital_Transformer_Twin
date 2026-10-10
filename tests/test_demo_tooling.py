"""Offline safety tests for scripts/run_demo_api.py and scripts/seed_demo.py.

A temporary fake .env (never the developer's) supplies a fake database URL, a fake
Gemini key and LLM_PROVIDER=gemini: the situation behind the earlier accidental live call.
"""
import os
from types import SimpleNamespace

import pytest
from sqlalchemy.engine import make_url

from app.agent.llm import GeminiClient, LLMUnavailable, get_llm_client
from app.config.settings import Settings
from scripts import run_demo_api, seed_demo
from scripts.run_demo_api import DemoConfigError, plan

FAKE_KEY = "FAKE-gemini-key-for-tests-0123456789"
FAKE_PASSWORD = "fake-db-password"
DEMO_CREDENTIALS = {"AGENT_CREDENTIALS": "DEMO-TX-01,@ingest:ingest-secret;DEMO-TX-01:chat-secret"}


@pytest.fixture
def fake_env_file(tmp_path, monkeypatch):
    path = tmp_path / ".env"
    path.write_text(
        f"DATABASE_URL=postgresql+psycopg://postgres:{FAKE_PASSWORD}@localhost:5432/digital_transformer\n"
        f"GEMINI_API_KEY={FAKE_KEY}\nLLM_PROVIDER=gemini\n"
        "AGENT_CREDENTIALS=TX-001:real-looking-dev-key\n", encoding="utf-8")
    for name in ("DATABASE_URL", "GEMINI_API_KEY", "LLM_PROVIDER", "AGENT_CREDENTIALS", "GOOGLE_API_KEY"):
        monkeypatch.delenv(name, raising=False)  # the autouse LLM_PROVIDER=none guard is removed on purpose
    return lambda: Settings(_env_file=path)


@pytest.fixture
def no_network(monkeypatch):
    def refuse(**_kwargs):
        raise AssertionError("a Gemini client was constructed: this would be a live request")
    monkeypatch.setattr("google.genai.Client", refuse)


def resolved(overrides, factory):
    with run_demo_api._patched_environ(overrides):
        return factory()


def chat_client(settings):
    return get_llm_client(SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(settings=settings))))


# --- settings resolution facts behind the incident -----------------------------------------

def test_env_file_key_applies_when_the_variable_is_absent(fake_env_file):
    # PowerShell `$env:GEMINI_API_KEY = ""` deletes the variable, so this is what happened.
    assert fake_env_file().gemini_api_key.get_secret_value() == FAKE_KEY


def test_whitespace_variable_overrides_env_file_key(fake_env_file, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", " ")
    assert not fake_env_file().gemini_api_key.get_secret_value().strip()


# --- launcher modes ------------------------------------------------------------------------

def test_none_mode_disables_gemini_even_with_key_and_provider_in_env_file(fake_env_file, no_network):
    overrides = plan("digital_transformer_test", "none", False, DEMO_CREDENTIALS, fake_env_file)
    settings = resolved(overrides, fake_env_file)
    assert settings.llm_provider == "none"
    assert chat_client(settings) is None


def test_offline_mode_never_uses_the_env_file_key_or_the_network(fake_env_file, no_network):
    overrides = plan("digital_transformer_test", "gemini-offline", False, DEMO_CREDENTIALS, fake_env_file)
    settings = resolved(overrides, fake_env_file)
    client = chat_client(settings)
    assert isinstance(client, GeminiClient)
    with pytest.raises(LLMUnavailable) as failure:
        client.complete(system="s", prompt="p")
    assert failure.value.reason == "missing_credentials"


def test_offline_mode_is_safe_even_if_the_variable_was_deleted(fake_env_file, no_network):
    environ = {**DEMO_CREDENTIALS}  # no GEMINI_API_KEY at all, as after `$env:GEMINI_API_KEY = ""`
    overrides = plan("digital_transformer_test", "gemini-offline", False, environ, fake_env_file)
    assert overrides["GEMINI_API_KEY"] == " "
    assert not resolved(overrides, fake_env_file).gemini_api_key.get_secret_value().strip()


def test_offline_mode_refuses_if_the_key_would_still_resolve(fake_env_file):
    """Defense in depth: if a key still resolved despite the override, nothing starts."""
    def leaky():  # simulates any configuration source that ignores the blank override
        return fake_env_file().model_copy(update={
            "llm_provider": "gemini",
            "gemini_api_key": Settings(_env_file=None, gemini_api_key=FAKE_KEY).gemini_api_key,
        })
    with pytest.raises(DemoConfigError, match="real Gemini key would be used"):
        plan("digital_transformer_test", "gemini-offline", False, DEMO_CREDENTIALS, leaky)


def test_live_mode_requires_explicit_confirmation(fake_env_file):
    with pytest.raises(DemoConfigError, match="--allow-live-gemini"):
        plan("digital_transformer_test", "gemini-live", False, DEMO_CREDENTIALS, fake_env_file)
    overrides = plan("digital_transformer_test", "gemini-live", True, DEMO_CREDENTIALS, fake_env_file)
    assert "GEMINI_API_KEY" not in overrides
    assert resolved(overrides, fake_env_file).llm_provider == "gemini"


def test_live_mode_refuses_without_a_key(tmp_path, monkeypatch):
    for name in ("GEMINI_API_KEY", "LLM_PROVIDER", "DATABASE_URL"):
        monkeypatch.delenv(name, raising=False)
    path = tmp_path / ".env"
    path.write_text(f"DATABASE_URL=postgresql+psycopg://u:{FAKE_PASSWORD}@localhost/devdb\n", encoding="utf-8")
    with pytest.raises(DemoConfigError, match="needs GEMINI_API_KEY"):
        plan("x_demo", "gemini-live", True, DEMO_CREDENTIALS, lambda: Settings(_env_file=path))


# --- database and credential guards --------------------------------------------------------

@pytest.mark.parametrize("database,message", [
    ("digital_transformer", "ending in _demo or _test"),
    ("scratch", "ending in _demo or _test"),
])
def test_database_must_be_a_separate_demo_or_test_database(fake_env_file, database, message):
    with pytest.raises(DemoConfigError, match=message):
        plan(database, "none", False, DEMO_CREDENTIALS, fake_env_file)


def test_database_equal_to_development_database_is_refused(tmp_path, monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    path = tmp_path / ".env"
    path.write_text(f"DATABASE_URL=postgresql+psycopg://u:{FAKE_PASSWORD}@localhost/shared_demo\n", encoding="utf-8")
    with pytest.raises(DemoConfigError, match="must not be the DATABASE_URL"):
        plan("shared_demo", "none", False, DEMO_CREDENTIALS, lambda: Settings(_env_file=path))


def test_database_override_points_at_the_requested_database(fake_env_file):
    overrides = plan("digital_transformer_test", "none", False, DEMO_CREDENTIALS, fake_env_file)
    url = make_url(resolved(overrides, fake_env_file).database_url.get_secret_value())
    assert (url.database, url.port) == ("digital_transformer_test", 5432)


@pytest.mark.parametrize("environ", [{}, {"AGENT_CREDENTIALS": " "}, {"AGENT_CREDENTIALS": "TX-001:dev-key"}])
def test_demo_credentials_must_come_from_the_session(fake_env_file, environ):
    # .env holds TX-001 credentials; they never satisfy the demo requirement.
    with pytest.raises(DemoConfigError, match="AGENT_CREDENTIALS"):
        plan("digital_transformer_test", "none", False, environ, fake_env_file)


def test_refusals_never_reveal_secrets(fake_env_file, capsys):
    for args in (("digital_transformer", "none", False), ("x_test", "gemini-live", False),
                 ("digital_transformer_test", "bogus", False)):
        try:
            plan(*args, DEMO_CREDENTIALS, fake_env_file)
        except DemoConfigError as error:
            assert FAKE_KEY not in str(error) and FAKE_PASSWORD not in str(error)


def test_main_refuses_and_prints_no_secret(fake_env_file, monkeypatch, capsys):
    monkeypatch.setattr(run_demo_api, "Settings", fake_env_file)
    monkeypatch.setattr(run_demo_api, "plan", lambda *a, **k: plan(*a[:4], fake_env_file))
    assert run_demo_api.main(["--database", "digital_transformer"]) == 2
    out = capsys.readouterr().out
    assert "Refusing to start" in out and FAKE_KEY not in out and FAKE_PASSWORD not in out


def test_patched_environ_restores_previous_values(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "none")
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    with run_demo_api._patched_environ({"LLM_PROVIDER": "gemini", "GEMINI_API_KEY": " "}):
        assert os.environ["LLM_PROVIDER"] == "gemini"
    assert os.environ["LLM_PROVIDER"] == "none" and "GEMINI_API_KEY" not in os.environ


# --- seed guard ----------------------------------------------------------------------------

def fake_api(latest_status, latest_body):
    calls = []

    def call(base, key, method, path, body=None):
        calls.append((method, path))
        if path == "/api/v1/transformer/latest":
            return latest_status, latest_body
        if method == "GET":
            return 200, []
        return 201, {"id": len(calls), "evaluations": {}}
    return call, calls


@pytest.mark.parametrize("status,body,safe", [
    (200, {"transformer_id": None}, True),
    (200, {"transformer_id": "DEMO-TX-01"}, True),
    (200, {"transformer_id": "TX-001"}, False),
    (403, {"detail": "Transformer access is not authorized"}, False),
    (400, {"detail": "multiple IDs"}, False),
    (503, {"detail": "Database unavailable"}, False),
])
def test_seed_guard_accepts_only_a_dedicated_database(status, body, safe):
    call, _ = fake_api(status, body)
    problem = seed_demo.dedicated_database_problem(call, "http://x", "k", "DEMO-TX-01")
    assert (problem is None) is safe


def test_seed_refuses_development_database_without_writing(monkeypatch, capsys):
    call, calls = fake_api(403, {"detail": "Transformer access is not authorized"})
    monkeypatch.setattr(seed_demo, "call", call)
    monkeypatch.setenv("DEMO_INGEST_KEY", "ingest-secret")
    monkeypatch.setattr("sys.argv", ["seed_demo.py"])
    assert seed_demo.main() == 2
    assert all(method == "GET" for method, _ in calls)
    assert "already holds another transformer" in capsys.readouterr().out


def test_seed_writes_only_synthetic_labelled_records_to_dedicated_database(monkeypatch):
    call, calls = fake_api(200, {"transformer_id": None})
    posted = []

    def recording_call(base, key, method, path, body=None):
        if method == "POST":
            posted.append(body)
        return call(base, key, method, path, body)
    monkeypatch.setattr(seed_demo, "call", recording_call)
    monkeypatch.setenv("DEMO_INGEST_KEY", "ingest-secret")
    monkeypatch.setattr("sys.argv", ["seed_demo.py"])
    assert seed_demo.main() == 0
    assert len(posted) == 7
    for body in posted:
        assert body["transformer_id"] == "DEMO-TX-01"
        payload = body.get("data") or body.get("details")
        assert payload["source"] == "synthetic-demo"
    assert all(body["notes"] == seed_demo.SYNTHETIC_NOTE for body in posted if "notes" in body)


def test_seed_refuses_non_demo_transformer_before_any_request(monkeypatch):
    monkeypatch.setattr(seed_demo, "call", lambda *a, **k: pytest.fail("network used"))
    monkeypatch.setenv("DEMO_INGEST_KEY", "ingest-secret")
    monkeypatch.setattr("sys.argv", ["seed_demo.py", "--transformer", "TX-001"])
    assert seed_demo.main() == 2
