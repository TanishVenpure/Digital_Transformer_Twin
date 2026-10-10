"""HTTP boundary for the evidence-bound maintenance agent."""

import logging
import secrets
from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Security
from fastapi.security import APIKeyHeader
from sqlalchemy.orm import Session

from app.agent.backend_provider import BackendEvidenceProvider
from app.agent.guardrails import GuardrailViolation
from app.agent.llm import LLMClient, get_llm_client
from app.agent.maintenance_risk import MaintenanceRiskPolicy, assess_maintenance_risk, load_policy
from app.agent.models import AgentEvent, AgentResponse, ChatRequest, ChatResponse, MaintenanceRiskAssessment
from app.agent.orchestration import (
    DeterministicEventAlertOrchestrator,
    EvidenceBoundMaintenanceChatbot,
)
from app.database.db import get_db
from app.database.repository import Repository

router = APIRouter(prefix="/agent", tags=["agent"])
logger = logging.getLogger(__name__)
agent_api_key = APIKeyHeader(name="X-Agent-API-Key", auto_error=False)


# Scope items starting with "@" are permissions, never transformer IDs, so a
# transformer named like a permission cannot grant (or receive) that permission.
PERMISSION_PREFIX = "@"
SIMULATION_PERMISSION = "@simulation"
# Writes telemetry and maintenance records that later become agent evidence.
INGEST_PERMISSION = "@ingest"
# Acknowledges/resolves stored alerts.
ALERTS_PERMISSION = "@alerts"
KNOWN_PERMISSIONS = frozenset({SIMULATION_PERMISSION, INGEST_PERMISSION, ALERTS_PERMISSION})


def _configured_credentials(settings) -> list[tuple[str, set[str], set[str]]]:
    credentials: list[tuple[str, set[str], set[str]]] = []
    for entry in settings.agent_credentials.split(";"):
        if not entry.strip() or ":" not in entry:
            continue
        scope, secret = entry.split(":", 1)
        if not secret:
            continue
        items = {item.strip() for item in scope.split(",") if item.strip()}
        ids = {item for item in items if not item.startswith(PERMISSION_PREFIX)}
        # Unknown permissions grant nothing.
        permissions = items & KNOWN_PERMISSIONS
        if ids or permissions:
            credentials.append((secret, ids, permissions))
    return credentials


def _authenticate(request: Request, api_key: str | None) -> tuple[set[str], set[str]]:
    """Return the (transformer IDs, permissions) granted to a configured API key."""
    credentials = _configured_credentials(request.app.state.settings)
    if not credentials:
        raise HTTPException(status_code=503, detail="Agent authentication is not configured")
    matched = False
    transformers: set[str] = set()
    permissions: set[str] = set()
    if api_key:
        supplied = api_key.encode()
        for configured_secret, transformer_ids, granted in credentials:
            # Compare bytes: str comparison raises on non-ASCII header values.
            if secrets.compare_digest(supplied, configured_secret.encode()):
                matched = True
                transformers.update(transformer_ids)
                permissions.update(granted)
    if not matched:
        raise HTTPException(
            status_code=401,
            detail="Invalid agent credentials",
            headers={"WWW-Authenticate": "ApiKey"},
        )
    return transformers, permissions


def require_agent_access(
    request: Request,
    x_agent_api_key: str | None = Security(agent_api_key),
) -> set[str]:
    """Authenticate the agent caller and authorize its transformer scope."""
    authorized_transformers, _permissions = _authenticate(request, x_agent_api_key)
    if not authorized_transformers:
        raise HTTPException(status_code=403, detail="Transformer access is not authorized")
    return authorized_transformers


def _require_scoped_permission(permission: str, denied: str):
    """Dependency: a write needs the explicit permission AND a transformer scope.

    Returns the authorized transformer IDs; routes still call authorize_transformer
    for each written transformer. Transformer scope alone (a read/chat key) never
    grants writes, and misspelled permissions grant nothing.
    """
    def dependency(
        request: Request,
        x_agent_api_key: str | None = Security(agent_api_key),
    ) -> set[str]:
        authorized_transformers, permissions = _authenticate(request, x_agent_api_key)
        if permission not in permissions:
            raise HTTPException(status_code=403, detail=denied)
        if not authorized_transformers:
            raise HTTPException(status_code=403, detail="Transformer access is not authorized")
        return authorized_transformers

    return dependency


require_ingest_access = _require_scoped_permission(INGEST_PERMISSION, "Ingestion access is not authorized")
require_alert_management = _require_scoped_permission(ALERTS_PERMISSION, "Alert management is not authorized")


def require_simulation_access(
    request: Request,
    x_agent_api_key: str | None = Security(agent_api_key),
) -> None:
    """Authorize the process-wide simulator; transformer scope never implies this."""
    _transformers, permissions = _authenticate(request, x_agent_api_key)
    if SIMULATION_PERMISSION not in permissions:
        raise HTTPException(status_code=403, detail="Simulation access is not authorized")


def authorize_transformer(
    request: Request, transformer_id: str, authorized_transformers: set[str]
) -> None:
    if transformer_id not in authorized_transformers:
        raise HTTPException(status_code=403, detail="Transformer access is not authorized")


def get_risk_policy() -> MaintenanceRiskPolicy | None:
    """FastAPI dependency; None when the policy file is missing or invalid."""
    try:
        return load_policy()
    except Exception as exc:  # noqa: BLE001 - reported as an unavailable dependency
        logger.error("Maintenance risk policy unavailable (%s)", type(exc).__name__)
        return None


def _provider(db: Session) -> BackendEvidenceProvider:
    # Authorization is enforced by the route dependencies before this provider
    # is constructed; the repository remains transformer-scoped evidence only.
    return BackendEvidenceProvider(Repository(db), page_size=50)


@router.post("/events", response_model=AgentResponse)
def agent_event(
    event: AgentEvent,
    request: Request,
    authorized_transformers: set[str] = Depends(require_agent_access),
    db: Session = Depends(get_db),
) -> AgentResponse:
    """Process supplied event evidence and bounded backend evidence.

    Access requires the configured API key and transformer allowlist.
    """
    try:
        authorize_transformer(request, event.transformer_id, authorized_transformers)
        return DeterministicEventAlertOrchestrator(_provider(db)).handle_event(event)
    except GuardrailViolation as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=503, detail="Backend evidence is unavailable") from exc


@router.post("/chat", response_model=ChatResponse)
def agent_chat(
    chat: ChatRequest,
    request: Request,
    authorized_transformers: set[str] = Depends(require_agent_access),
    db: Session = Depends(get_db),
    policy: MaintenanceRiskPolicy | None = Depends(get_risk_policy),
    llm: LLMClient | None = Depends(get_llm_client),
) -> ChatResponse:
    """Answer from bounded transformer-scoped evidence only.

    Access requires the configured API key and transformer allowlist.
    """
    try:
        authorize_transformer(request, chat.transformer_id, authorized_transformers)
        return EvidenceBoundMaintenanceChatbot(_provider(db), llm=llm, risk_policy=policy).answer(chat)
    except GuardrailViolation as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=503, detail="Backend evidence is unavailable") from exc


@router.get("/risk", response_model=MaintenanceRiskAssessment)
def maintenance_risk(
    transformer_id: Annotated[str, Query(min_length=1, max_length=200)],
    request: Request,
    authorized_transformers: set[str] = Depends(require_agent_access),
    db: Session = Depends(get_db),
    policy: MaintenanceRiskPolicy | None = Depends(get_risk_policy),
) -> MaintenanceRiskAssessment:
    """Deterministic Maintenance Risk Index from stored maintenance records (read-only).

    Requires a key scoped to transformer_id. No caller-supplied score is accepted.
    Insufficient history returns 200 with risk_category INSUFFICIENT_EVIDENCE and a
    null index; a transformer outside the key's scope returns 403.
    """
    authorize_transformer(request, transformer_id, authorized_transformers)
    if policy is None:
        raise HTTPException(status_code=503, detail="Maintenance risk policy is unavailable")
    try:
        records, complete = _provider(db).maintenance_history_evidence(
            transformer_id, max_records=policy.max_records,
        )
    except ValueError as exc:
        raise HTTPException(status_code=503, detail="Backend evidence is unavailable") from exc
    return assess_maintenance_risk(
        transformer_id, records, policy, now=datetime.now(timezone.utc), history_complete=complete,
    )
