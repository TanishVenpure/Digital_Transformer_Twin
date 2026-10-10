"""Start the main API for a demo with fail-closed safety checks.

    python scripts/run_demo_api.py --database digital_transformer_test [--llm none] [--port 8000]

--database  a separate, already migrated database on the DATABASE_URL server. It must
            end in "_demo" or "_test" and must not be the DATABASE_URL (development)
            database. Credentials are reused from DATABASE_URL in-process, never printed.
--llm       none            Gemini disabled; no client is ever constructed.
            gemini-offline  Gemini selected with a blank key: chat shows the deterministic
                            fallback (missing_credentials) and no network request is made.
                            The blank key is set inside Python (a PowerShell
                            `$env:GEMINI_API_KEY = ""` deletes the variable and lets .env win).
            gemini-live     uses GEMINI_API_KEY from the environment or .env; requires
                            --allow-live-gemini and sends the demo evidence to Google.

AGENT_CREDENTIALS must be set in this process's environment (demo keys scoped to a
DEMO- transformer); credentials from .env are not used for the demo.
"""
import argparse
import os
import sys
from collections.abc import Callable, Mapping
from contextlib import contextmanager
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from sqlalchemy.engine import make_url  # noqa: E402

from app.config.settings import Settings  # noqa: E402

LLM_MODES = ("none", "gemini-offline", "gemini-live")


class DemoConfigError(RuntimeError):
    """The requested demo configuration is unsafe; nothing was started."""


@contextmanager
def _patched_environ(overrides: Mapping[str, str]):
    saved = {key: os.environ.get(key) for key in overrides}
    os.environ.update(overrides)
    try:
        yield
    finally:
        for key, value in saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def _key_is_blank(settings: Settings) -> bool:
    return settings.gemini_api_key is None or not settings.gemini_api_key.get_secret_value().strip()


def plan(database: str, llm: str, allow_live: bool, environ: Mapping[str, str],
         settings_factory: Callable[[], Settings] = Settings) -> dict[str, str]:
    """Return the environment overrides for a safe demo run, or raise DemoConfigError.

    Messages never contain URLs, keys or credentials.
    """
    if llm not in LLM_MODES:
        raise DemoConfigError(f"--llm must be one of {', '.join(LLM_MODES)}")
    if llm == "gemini-live" and not allow_live:
        raise DemoConfigError("gemini-live sends demo evidence to Google; add --allow-live-gemini to confirm")
    credentials = environ.get("AGENT_CREDENTIALS", "")
    if not credentials.strip() or "DEMO-" not in credentials:
        raise DemoConfigError("Set AGENT_CREDENTIALS in this session to demo keys scoped to a DEMO- transformer")
    if not (database.endswith("_demo") or database.endswith("_test")):
        raise DemoConfigError("--database must name a separate database ending in _demo or _test")

    base = settings_factory()
    if base.database_url is None:
        raise DemoConfigError("DATABASE_URL is not configured")
    url = make_url(base.database_url.get_secret_value())
    if url.database == database:
        raise DemoConfigError("--database must not be the DATABASE_URL development database")

    overrides = {"DATABASE_URL": url.set(database=database).render_as_string(hide_password=False)}
    if llm == "none":
        overrides["LLM_PROVIDER"] = "none"
    elif llm == "gemini-offline":
        overrides.update(LLM_PROVIDER="gemini", GEMINI_API_KEY=" ")
    else:
        overrides["LLM_PROVIDER"] = "gemini"

    # Defense in depth: re-resolve settings exactly as the server will and verify.
    with _patched_environ(overrides):
        resolved = settings_factory()
    if make_url(resolved.database_url.get_secret_value()).database != database:
        raise DemoConfigError("database override did not take effect; refusing to start")
    if llm == "none" and resolved.llm_provider != "none":
        raise DemoConfigError("Gemini would be enabled; refusing to start")
    if llm == "gemini-offline" and (resolved.llm_provider != "gemini" or not _key_is_blank(resolved)):
        raise DemoConfigError("a real Gemini key would be used in offline mode; refusing to start")
    if llm == "gemini-live" and _key_is_blank(resolved):
        raise DemoConfigError("gemini-live needs GEMINI_API_KEY in the environment or .env")
    return overrides


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Start the main API for a demo (fail-closed).")
    parser.add_argument("--database", required=True)
    parser.add_argument("--llm", default="none", choices=LLM_MODES)
    parser.add_argument("--allow-live-gemini", action="store_true")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args(argv)
    try:
        overrides = plan(args.database, args.llm, args.allow_live_gemini, os.environ)
    except DemoConfigError as error:
        print(f"Refusing to start: {error}")
        return 2
    os.environ.update(overrides)
    print(f"Demo API: database={args.database} llm={args.llm} url=http://127.0.0.1:{args.port}")
    import uvicorn

    uvicorn.run("app.main:app", host="127.0.0.1", port=args.port, log_level="warning")
    return 0


if __name__ == "__main__":
    sys.exit(main())
