"""Provider-neutral LLM boundary for evidence-bound chat explanations.

No provider is enabled by default (``LLM_PROVIDER=none``), so the chatbot returns
its deterministic summary. ``LLM_PROVIDER=gemini`` with ``GEMINI_API_KEY`` enables
Google Gemini through the official Google Gen AI SDK (``google-genai``). A client
only returns text for the chatbot to parse and validate; it never computes or
changes the risk assessment.
"""
import logging
import re
import time
from dataclasses import dataclass
from typing import Any, Protocol

from fastapi import Request
from pydantic import SecretStr

DEFAULT_GEMINI_MODEL = "gemini-2.5-flash"
logger = logging.getLogger(__name__)
_KEY_LIKE = re.compile(r"AIza[0-9A-Za-z_\-]{10,}")
_ENUM_NAME = re.compile(r"^[A-Z][A-Z0-9_]{0,40}$")


@dataclass(frozen=True)
class ProviderDiagnostic:
    """Sanitized failure details for operators; never the key, headers, prompt or records."""

    category: str
    http_status: int | None = None
    provider_status: str | None = None  # e.g. NOT_FOUND, PERMISSION_DENIED, or a finish reason
    exception_type: str | None = None
    message: str | None = None  # the provider's own error text, key-redacted and truncated


@dataclass(frozen=True)
class CallMetrics:
    """Numbers and fixed categories only: no key, prompt, evidence, answer or raw response."""

    outcome: str  # "ok" or a failure category from ProviderDiagnostic
    elapsed_ms: int | None = None  # monotonic time spent in the provider request
    http_status: int | None = None
    finish_reason: str | None = None  # SDK enum name, e.g. STOP, MAX_TOKENS, SAFETY
    prompt_tokens: int | None = None
    thinking_tokens: int | None = None
    output_tokens: int | None = None
    total_tokens: int | None = None


class LLMUnavailable(RuntimeError):
    """The LLM could not answer; callers fall back safely.

    ``reason`` is a short, non-sensitive category shown to users; ``diagnostic``
    carries sanitized details for operators.
    """

    def __init__(self, reason: str, diagnostic: ProviderDiagnostic | None = None):
        super().__init__(reason)
        self.reason = reason
        self.diagnostic = diagnostic


class LLMClient(Protocol):
    def complete(self, *, system: str, prompt: str) -> str:
        """Return the model's raw text reply (expected to be the JSON output contract)."""


class GeminiClient:
    """Google Gemini via ``google-genai``; JSON-mode, temperature 0, bounded time.

    Failures raise ``LLMUnavailable`` with one of: missing_credentials,
    sdk_not_installed, authentication_failed, permission_denied, model_not_found,
    invalid_request, rate_limited, timeout, provider_server_error, network_error,
    provider_error, empty_response. Only the category reaches chat users; the
    sanitized diagnostic of the last failure is kept in ``last_diagnostic`` and logged.
    ``last_metrics`` holds the last call's elapsed time and token counts (numbers only).
    """

    def __init__(
        self, *, api_key: SecretStr | None, model: str = DEFAULT_GEMINI_MODEL,
        timeout_seconds: float = 30.0, thinking_level: str = "low", sdk_client=None,
    ):
        self._api_key = api_key
        self.model = model
        # The SDK also sends this as X-Server-Timeout, so it is the server deadline too.
        self.timeout_seconds = timeout_seconds
        self.thinking_level = thinking_level
        self._client = sdk_client
        self.last_diagnostic: ProviderDiagnostic | None = None
        self.last_metrics: CallMetrics | None = None

    def __repr__(self) -> str:  # never include the key
        return (f"GeminiClient(model={self.model!r}, timeout_seconds={self.timeout_seconds}, "
                f"thinking_level={self.thinking_level!r})")

    def _sdk(self):
        try:
            from google import genai
            from google.genai import types
        except ImportError:
            self._fail(ProviderDiagnostic(category="sdk_not_installed"))
        if self._client is None:
            if self._api_key is None or not self._api_key.get_secret_value().strip():
                self._fail(ProviderDiagnostic(category="missing_credentials"))
            # The explicit key takes precedence over any ambient GOOGLE_API_KEY.
            self._client = genai.Client(
                api_key=self._api_key.get_secret_value(),
                http_options=types.HttpOptions(
                    timeout=int(self.timeout_seconds * 1000),  # milliseconds
                    # One attempt: a chat request never waits through several timeouts;
                    # rate limits fall back to the deterministic answer immediately.
                    retry_options=types.HttpRetryOptions(attempts=1),
                ),
            )
        return self._client, types

    def complete(self, *, system: str, prompt: str) -> str:
        self.last_diagnostic = None
        self.last_metrics = None
        self._elapsed_ms: int | None = None
        client, types = self._sdk()
        config = types.GenerateContentConfig(
            system_instruction=system,
            temperature=0,
            response_mime_type="application/json",
            thinking_config=(
                None if self.thinking_level == "default"
                else types.ThinkingConfig(thinking_level=self.thinking_level.upper())
            ),
        )
        started = time.monotonic()
        try:
            response = client.models.generate_content(model=self.model, contents=prompt, config=config)
        except Exception as exc:  # noqa: BLE001 - classified; only sanitized details are kept
            self._elapsed_ms = _elapsed_ms(started)
            self._fail(_diagnose(exc, self._api_key))
        self._elapsed_ms = _elapsed_ms(started)
        text = getattr(response, "text", None)
        if not isinstance(text, str) or not text.strip():
            self._fail(ProviderDiagnostic(category="empty_response", provider_status=_finish_reason(response)),
                       response=response)
        self.last_metrics = _metrics("ok", self._elapsed_ms, None, response)
        logger.info(
            "Gemini request ok: elapsed_ms=%s finish_reason=%s prompt_tokens=%s thinking_tokens=%s "
            "output_tokens=%s total_tokens=%s model=%s deadline_s=%s thinking_level=%s",
            self.last_metrics.elapsed_ms, self.last_metrics.finish_reason, self.last_metrics.prompt_tokens,
            self.last_metrics.thinking_tokens, self.last_metrics.output_tokens, self.last_metrics.total_tokens,
            self.model, self.timeout_seconds, self.thinking_level,
        )
        return text

    def _fail(self, diagnostic: ProviderDiagnostic, *, response: Any = None):
        self.last_diagnostic = diagnostic
        self.last_metrics = _metrics(
            diagnostic.category, getattr(self, "_elapsed_ms", None), diagnostic.http_status, response,
        )
        logger.warning(
            "Gemini request failed: category=%s http_status=%s provider_status=%s type=%s elapsed_ms=%s "
            "model=%s deadline_s=%s thinking_level=%s message=%s",
            diagnostic.category, diagnostic.http_status, diagnostic.provider_status, diagnostic.exception_type,
            self.last_metrics.elapsed_ms, self.model, self.timeout_seconds, self.thinking_level, diagnostic.message,
        )
        raise LLMUnavailable(diagnostic.category, diagnostic) from None


def _elapsed_ms(started: float) -> int:
    return int(round((time.monotonic() - started) * 1000))


def _count(value: Any) -> int | None:
    """Accept only non-negative integers; anything else is reported as unavailable."""
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


def _metrics(outcome: str, elapsed_ms: int | None, http_status: int | None, response: Any) -> CallMetrics:
    usage = getattr(response, "usage_metadata", None) if response is not None else None
    return CallMetrics(
        outcome=outcome,
        elapsed_ms=elapsed_ms,
        http_status=http_status,
        finish_reason=_finish_reason(response) if response is not None else None,
        prompt_tokens=_count(getattr(usage, "prompt_token_count", None)),
        thinking_tokens=_count(getattr(usage, "thoughts_token_count", None)),
        output_tokens=_count(getattr(usage, "candidates_token_count", None)),
        total_tokens=_count(getattr(usage, "total_token_count", None)),
    )


def _sanitize(text, api_key: SecretStr | None) -> str | None:
    if not isinstance(text, str) or not text.strip():
        return None
    secret = api_key.get_secret_value() if api_key else ""
    if secret.strip():
        text = text.replace(secret, "[redacted]")
    text = _KEY_LIKE.sub("[redacted]", text)
    return " ".join(text.split())[:300]


def _category(code: int | None, provider_status: str | None, message: str | None, exc: Exception) -> str:
    status = (provider_status or "").upper()
    lowered = (message or "").lower()
    if code == 429 or status == "RESOURCE_EXHAUSTED":
        return "rate_limited"
    if code in (408, 504) or status == "DEADLINE_EXCEEDED":
        return "timeout"
    if code == 401 or status == "UNAUTHENTICATED" or "api key not valid" in lowered or "api_key_invalid" in lowered:
        return "authentication_failed"
    if code == 403 or status == "PERMISSION_DENIED":
        return "permission_denied"
    if code == 404 or status == "NOT_FOUND":
        return "model_not_found"
    if code == 400 or status in ("INVALID_ARGUMENT", "FAILED_PRECONDITION"):
        return "invalid_request"
    if isinstance(code, int) and code >= 500:
        return "provider_server_error"
    name = type(exc).__name__.lower()
    if "timeout" in name or isinstance(exc, TimeoutError):
        return "timeout"
    if "connect" in name or "network" in name or isinstance(exc, ConnectionError):
        return "network_error"
    return "provider_error"


def _diagnose(exc: Exception, api_key: SecretStr | None) -> ProviderDiagnostic:
    code = getattr(exc, "code", None) or getattr(exc, "status_code", None)
    code = code if isinstance(code, int) and not isinstance(code, bool) else None
    # google.genai.errors.APIError exposes .code/.status/.message parsed from the
    # provider's error body. Other exceptions may embed request data: type only.
    is_api_error = any(cls.__name__ == "APIError" for cls in type(exc).__mro__)
    provider_status = getattr(exc, "status", None) if is_api_error else None
    provider_status = provider_status if isinstance(provider_status, str) else None
    message = _sanitize(getattr(exc, "message", None), api_key) if is_api_error else None
    return ProviderDiagnostic(
        category=_category(code, provider_status, message, exc),
        http_status=code, provider_status=provider_status,
        exception_type=type(exc).__name__, message=message,
    )


def _finish_reason(response) -> str | None:
    """Why a reply was empty (e.g. SAFETY, MAX_TOKENS, a prompt block reason)."""
    try:
        reason = response.candidates[0].finish_reason
    except Exception:  # noqa: BLE001 - no candidates (e.g. a blocked prompt)
        reason = getattr(getattr(response, "prompt_feedback", None), "block_reason", None)
    if reason is None:
        return None
    name = str(getattr(reason, "name", reason))
    # Only enum-style names (e.g. STOP, SAFETY) are kept; never free text.
    return name if _ENUM_NAME.match(name) else None


def get_llm_client(request: Request) -> LLMClient | None:
    """FastAPI dependency: the configured provider, or None for deterministic answers only."""
    settings = request.app.state.settings
    if settings.llm_provider != "gemini":
        return None
    # A missing key still returns a client so the chat reports "missing_credentials".
    return GeminiClient(
        api_key=settings.gemini_api_key, model=settings.gemini_model,
        timeout_seconds=settings.gemini_timeout_seconds, thinking_level=settings.gemini_thinking_level,
    )
