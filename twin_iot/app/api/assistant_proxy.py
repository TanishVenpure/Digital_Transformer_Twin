"""Thin server-side proxy to the main backend's chatbot and risk engine.

The dashboard JavaScript calls these endpoints on the twin_iot API; the proxy
forwards the request to the main backend (port 8000 by default) with the
configured API key.  This keeps the main backend's credentials out of the
browser and avoids cross-origin requests.

Environment variables (all optional):
  MAIN_BACKEND_URL       Base URL of the main backend (default http://127.0.0.1:8000)
  MAIN_BACKEND_API_KEY   The agent secret for the main backend's X-Agent-API-Key header
"""
import logging
import os
from typing import Any

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.api.security import require_write_access

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/assistant", tags=["assistant-proxy"])

_BACKEND_URL: str | None = None
_API_KEY: str | None = None
_TIMEOUT = 35.0  # slightly above Gemini's 30s timeout


def _backend_url() -> str:
    global _BACKEND_URL
    if _BACKEND_URL is None:
        _BACKEND_URL = os.environ.get("MAIN_BACKEND_URL", "http://127.0.0.1:8000").rstrip("/")
    return _BACKEND_URL


def _api_key() -> str | None:
    global _API_KEY
    if _API_KEY is None:
        _API_KEY = os.environ.get("MAIN_BACKEND_API_KEY", "") or None
    return _API_KEY


def _headers() -> dict[str, str]:
    key = _api_key()
    if not key:
        raise HTTPException(503, "Chatbot backend credentials are not configured (MAIN_BACKEND_API_KEY)")
    return {"X-Agent-API-Key": key, "Content-Type": "application/json"}


class ChatIn(BaseModel):
    transformer_id: str = Field(min_length=1, max_length=200)
    question: str = Field(min_length=1, max_length=2000)


@router.post("/chat")
async def proxy_chat(
    body: ChatIn,
    _authorized: set[str] = Depends(require_write_access),
) -> dict[str, Any]:
    """Proxy a chatbot question to the main backend.

    The twin_iot API key gates access to the proxy itself.  Transformer-level
    authorization is enforced by the main backend using its own credentials.
    """
    url = f"{_backend_url()}/api/v1/agent/chat"
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.post(url, json=body.model_dump(), headers=_headers())
    except httpx.ConnectError:
        raise HTTPException(502, "Chatbot backend is not reachable")
    except httpx.TimeoutException:
        raise HTTPException(504, "Chatbot backend timed out")
    except Exception as exc:
        logger.error("Proxy chat error: %s", type(exc).__name__)
        raise HTTPException(502, "Chatbot backend request failed")
    if resp.status_code == 401:
        raise HTTPException(502, "Chatbot backend rejected the configured API key")
    if resp.status_code == 403:
        raise HTTPException(403, "Transformer not authorized on the chatbot backend")
    if resp.status_code >= 400:
        detail = resp.json().get("detail", resp.text) if resp.headers.get("content-type", "").startswith("application/json") else resp.text
        raise HTTPException(resp.status_code, f"Chatbot backend error: {detail}")
    return resp.json()


@router.get("/risk")
async def proxy_risk(
    transformer_id: str,
    _authorized: set[str] = Depends(require_write_access),
) -> dict[str, Any]:
    """Proxy a maintenance-risk assessment request to the main backend.

    Transformer-level authorization is enforced by the main backend.
    """
    url = f"{_backend_url()}/api/v1/agent/risk"
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.get(url, params={"transformer_id": transformer_id}, headers=_headers())
    except httpx.ConnectError:
        raise HTTPException(502, "Risk backend is not reachable")
    except httpx.TimeoutException:
        raise HTTPException(504, "Risk backend timed out")
    except Exception as exc:
        logger.error("Proxy risk error: %s", type(exc).__name__)
        raise HTTPException(502, "Risk backend request failed")
    if resp.status_code == 401:
        raise HTTPException(502, "Risk backend rejected the configured API key")
    if resp.status_code == 403:
        raise HTTPException(403, "Transformer not authorized on the risk backend")
    if resp.status_code >= 400:
        detail = resp.json().get("detail", resp.text) if resp.headers.get("content-type", "").startswith("application/json") else resp.text
        raise HTTPException(resp.status_code, f"Risk backend error: {detail}")
    return resp.json()


@router.get("/status")
async def backend_status() -> dict[str, Any]:
    """Check whether the main backend is reachable (no auth required)."""
    url = f"{_backend_url()}/"
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            resp = await client.get(url)
        return {"backend": "reachable", "status_code": resp.status_code}
    except Exception:
        return {"backend": "unreachable"}
