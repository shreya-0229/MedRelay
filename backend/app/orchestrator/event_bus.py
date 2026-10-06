"""Canonical structured event system for the MedRelay pipeline.

Every significant pipeline transition is an Event::

    {message_id, incident_id, sender, receiver, event_type,
     timestamp, payload, confidence}

The EventBus is the single place that:

- appends events to the in-memory log (one log per pipeline run),
- persists important events to the append-only audit DB,
- bridges agent lifecycle events to the WebSocket broadcast using the
  existing ``AgentEventMsg`` shape (frontend contract unchanged),
- owns the :class:`AgentBus` blackboard — the inter-agent message
  transport. (``AgentMessage``/``AgentBus`` are defined here canonically;
  ``app.agents.agent_message`` is a thin re-export for compatibility.)

Event types are the canonical vocabulary shared by the workflow, the
orchestrator, tests, and docs — use exactly these names.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable

from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app import models
from app.audit import write_audit
from app.schemas import AgentEventMsg


class EventType:
    """Canonical pipeline event vocabulary (names are contractual)."""

    INTAKE_COMPLETED = "INTAKE_COMPLETED"
    TRIAGE_STARTED = "TRIAGE_STARTED"
    TRIAGE_COMPLETED = "TRIAGE_COMPLETED"
    DISPATCH_STARTED = "DISPATCH_STARTED"
    DISPATCH_COMPLETED = "DISPATCH_COMPLETED"
    HOSPITAL_SEARCH_STARTED = "HOSPITAL_SEARCH_STARTED"
    HOSPITAL_SELECTED = "HOSPITAL_SELECTED"
    VERIFICATION_STARTED = "VERIFICATION_STARTED"
    VERIFICATION_PASSED = "VERIFICATION_PASSED"
    VERIFICATION_FAILED = "VERIFICATION_FAILED"
    REPLAN_STARTED = "REPLAN_STARTED"
    HUMAN_ESCALATION = "HUMAN_ESCALATION"
    COMMUNICATION_SENT = "COMMUNICATION_SENT"
    # --- Failure-recovery vocabulary ---
    FAILURE_DETECTED = "FAILURE_DETECTED"
    AMBULANCE_FAILURE = "AMBULANCE_FAILURE"
    HOSPITAL_FAILURE = "HOSPITAL_FAILURE"
    REPLANNING = "REPLANNING"
    RESOURCE_REPLACED = "RESOURCE_REPLACED"
    REVIEW_REQUIRED = "REVIEW_REQUIRED"
    REVIEW_DECIDED = "REVIEW_DECIDED"
    CONFLICT_DETECTED = "CONFLICT_DETECTED"

    ALL: tuple[str, ...] = (
        INTAKE_COMPLETED,
        TRIAGE_STARTED,
        TRIAGE_COMPLETED,
        DISPATCH_STARTED,
        DISPATCH_COMPLETED,
        HOSPITAL_SEARCH_STARTED,
        HOSPITAL_SELECTED,
        VERIFICATION_STARTED,
        VERIFICATION_PASSED,
        VERIFICATION_FAILED,
        REPLAN_STARTED,
        HUMAN_ESCALATION,
        COMMUNICATION_SENT,
        FAILURE_DETECTED,
        AMBULANCE_FAILURE,
        HOSPITAL_FAILURE,
        REPLANNING,
        RESOURCE_REPLACED,
        REVIEW_REQUIRED,
        REVIEW_DECIDED,
        CONFLICT_DETECTED,
    )


class Event(BaseModel):
    """One structured pipeline event."""

    message_id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    incident_id: str
    sender: str
    receiver: str = "orchestrator"
    event_type: str
    timestamp: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc))
    payload: dict[str, Any] = Field(default_factory=dict)
    confidence: float = 0.0


class AgentMessage(BaseModel):
    """One message on the inter-agent bus (blackboard)."""

    from_agent: str
    to_agent: str
    incident_id: str
    payload: dict[str, Any] = Field(default_factory=dict)
    timestamp: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc))


class AgentBus:
    """Append-only in-memory blackboard for one pipeline run."""

    def __init__(self) -> None:
        self._messages: list[AgentMessage] = []

    def publish(self, from_agent: str, to_agent: str, incident_id: str,
                payload: dict[str, Any] | None = None) -> AgentMessage:
        """Append a message and return it."""
        msg = AgentMessage(
            from_agent=from_agent, to_agent=to_agent,
            incident_id=incident_id, payload=payload or {})
        self._messages.append(msg)
        return msg

    def for_incident(self, incident_id: str) -> list[AgentMessage]:
        """All messages for one incident, in publish order."""
        return [m for m in self._messages if m.incident_id == incident_id]

    def latest(self, incident_id: str,
               from_agent: str) -> AgentMessage | None:
        """Most recent message from one agent for an incident, if any."""
        for msg in reversed(self._messages):
            if msg.incident_id == incident_id and msg.from_agent == from_agent:
                return msg
        return None

    def clear(self) -> None:
        """Drop all messages (used between pipeline runs / tests)."""
        self._messages.clear()

    def __len__(self) -> int:
        return len(self._messages)


def _humanize(event_type: str) -> str:
    """TRIAGE_STARTED -> 'Triage started' (for audit rows / WS feed)."""
    return event_type.replace("_", " ").capitalize()


class EventBus:
    """Single writer for pipeline events.

    Owns the per-run event log, the AgentBus blackboard, audit-DB
    persistence, and the WebSocket bridge. Construct one per pipeline run.
    """

    def __init__(self, db: Session,
                 broadcast: Callable[[dict], Awaitable[None]]) -> None:
        self._db = db
        self._broadcast = broadcast
        self.events: list[Event] = []
        self.blackboard = AgentBus()

    async def emit(self, event_type: str, *, sender: str, incident_id: str,
                   receiver: str = "orchestrator",
                   payload: dict[str, Any] | None = None,
                   confidence: float = 0.0,
                   action: str | None = None,
                   rationale: str = "",
                   audit: bool = True,
                   ws: bool = True) -> Event:
        """Record an event: log it, persist to audit, bridge to WebSocket."""
        event = Event(
            incident_id=incident_id, sender=sender, receiver=receiver,
            event_type=event_type, payload=payload or {},
            confidence=confidence)
        self.events.append(event)
        label = action or _humanize(event_type)
        text = rationale or event.payload.get("rationale", "")
        if audit:
            write_audit(self._db, incident_id, agent=sender, action=label,
                        rationale=text, confidence=confidence)
        if ws:
            await self._broadcast(AgentEventMsg(
                incident_id=incident_id, agent=sender, action=label,
                rationale=text, confidence=confidence,
                ts=event.timestamp.isoformat(),
            ).model_dump())
        return event

    async def broadcast_raw(self, message: dict) -> None:
        """Pass a non-agent message (incident/fleet update) to the WS."""
        await self._broadcast(message)

    def of_type(self, event_type: str) -> list[Event]:
        """Logged events of one type, in emission order."""
        return [e for e in self.events if e.event_type == event_type]

    def types(self) -> list[str]:
        """Event-type names in emission order."""
        return [e.event_type for e in self.events]
