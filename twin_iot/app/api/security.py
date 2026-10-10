"""Write-access credentials and request bounds for the twin_iot API.

This server has its own credential set (TWIN_API_CREDENTIALS); keys for the main
backend's AGENT_CREDENTIALS grant nothing here. The format follows the main app:
semicolon-separated ``transformer_id[,transformer_id...]:secret`` entries.
"""
import os
import secrets
from datetime import datetime, timedelta, timezone

from fastapi import Header, HTTPException

API_KEY_HEADER = "X-Twin-API-Key"
DEFAULT_ALLOWED_HOSTS = ("localhost", "127.0.0.1")
DEFAULT_MAX_FUTURE_SKEW_SECONDS = 300  # clock-skew tolerance, not a transformer threshold
MAX_UPLOAD_BYTES = 2 * 1024 * 1024
MAX_TRANSFORMER_ID_LENGTH = 255
MAX_READING_FIELDS = 256


def _credentials() -> list[tuple[str, set[str]]]:
    credentials = []
    for entry in os.environ.get("TWIN_API_CREDENTIALS", "").split(";"):
        if ":" not in entry:
            continue
        scope, secret = entry.split(":", 1)
        ids = {item.strip() for item in scope.split(",") if item.strip()}
        if secret and ids:
            credentials.append((secret, ids))
    return credentials


def require_write_access(x_twin_api_key: str | None = Header(default=None)) -> set[str]:
    """Return the transformer IDs the supplied key may write; fail closed otherwise."""
    credentials = _credentials()
    if not credentials:
        raise HTTPException(503, "Write authentication is not configured")
    authorized: set[str] = set()
    if x_twin_api_key:
        supplied = x_twin_api_key.encode()
        for secret, ids in credentials:
            # Compare bytes: str comparison raises on non-ASCII header values.
            if secrets.compare_digest(supplied, secret.encode()):
                authorized |= ids
    if not authorized:
        raise HTTPException(401, "Invalid API credentials", headers={"WWW-Authenticate": "ApiKey"})
    return authorized


def authorize_transformer(transformer_id: str, authorized: set[str]) -> None:
    if transformer_id not in authorized:
        raise HTTPException(403, "Transformer access is not authorized")


def allowed_hosts() -> list[str]:
    configured = os.environ.get("TWIN_ALLOWED_HOSTS", "")
    hosts = [host.strip() for host in configured.split(",") if host.strip()]
    return hosts or list(DEFAULT_ALLOWED_HOSTS)


def check_transformer_id(value: str) -> str:
    value = value.strip()
    if not value or len(value) > MAX_TRANSFORMER_ID_LENGTH or not value.isprintable():
        raise ValueError(
            f"transformer_id must be 1-{MAX_TRANSFORMER_ID_LENGTH} printable, non-blank characters"
        )
    return value


def check_not_future(timestamp: datetime) -> datetime:
    """A far-future reading would make every later genuine reading OUT_OF_ORDER."""
    skew = float(os.environ.get("TWIN_MAX_FUTURE_SKEW_SECONDS", DEFAULT_MAX_FUTURE_SKEW_SECONDS))
    aware = timestamp if timestamp.tzinfo else timestamp.replace(tzinfo=timezone.utc)
    if aware > datetime.now(timezone.utc) + timedelta(seconds=skew):
        raise ValueError("timestamp is in the future")
    return timestamp
