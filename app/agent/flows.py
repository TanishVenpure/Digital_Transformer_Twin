"""Separate orchestration boundaries for future event alerts and chatbot use."""

from typing import Protocol

from app.agent.models import AgentEvent, AgentResponse, ChatRequest, ChatResponse, EvidenceBundle


class EventAlertFlow(Protocol):
    """Event-to-alert orchestration; it must not be reused as chat orchestration."""

    def handle_event(self, event: AgentEvent | dict, evidence: EvidenceBundle | None = None) -> AgentResponse:
        """Build a guarded structured alert from an event and retrieved evidence."""


class MaintenanceChatFlow(Protocol):
    """Maintenance Q&A orchestration; implementation remains a later increment."""

    def answer(self, request: ChatRequest | dict, evidence: EvidenceBundle | None = None) -> ChatResponse:
        """Answer a question using bounded evidence and no operational authorization."""
