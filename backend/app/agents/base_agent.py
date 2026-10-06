"""BaseAgent v2 — standard envelope, declared I/O schemas, LLM assist hook.

Agents are async, receive the shared IncidentState, mutate it in place, and
return an AgentOutput envelope built with self.envelope(). The helper fills
BOTH the canonical envelope fields and the legacy fields the dashboard
still reads, so the REST/WS contract stays stable.

Deterministic-first: self.llm_assist() returns None unless a real provider
is configured, and never raises — agents must work fully offline.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.llm import DeterministicProvider, LLMProvider
from app.schemas import AgentOutput, IncidentState


def utcnow() -> datetime:
    """Timezone-aware UTC now, used for timeline/audit timestamps."""
    return datetime.now(timezone.utc)


class BaseAgent:
    """Shared behaviour: name, version, declared schemas, DB session, LLM."""

    name: str = "BaseAgent"
    version: str = "2.0"
    input_schema: type[BaseModel] | None = None   # what the agent consumes
    output_schema: type[BaseModel] = AgentOutput  # always the envelope

    def __init__(self, db: Session, llm: LLMProvider | None = None) -> None:
        # Deterministic by default; pass a real LLMProvider to upgrade.
        self.db = db
        self.llm = llm or DeterministicProvider()

    async def run(self, state: IncidentState) -> AgentOutput:
        """Run the agent against the incident state. Mutates state in place."""
        raise NotImplementedError

    def llm_assist(self, prompt: str) -> str | None:
        """Optional LLM interpretation hook.

        Returns the model's text, or None when no real provider is
        configured. Never raises — a model outage must never break the
        deterministic pipeline.
        """
        if not getattr(self.llm, "real", False):
            return None
        try:
            text = self.llm.complete(prompt)
        except Exception:
            return None
        text = (text or "").strip()
        return text or None

    def envelope(
        self,
        state: IncidentState,
        *,
        status: Literal["success", "failed", "escalated"] = "success",
        confidence: float = 0.0,
        decision: dict[str, Any] | None = None,
        reasoning_summary: str = "",
        warnings: list[str] | None = None,
        requires_human: bool = False,
        action: str = "",
    ) -> AgentOutput:
        """Build the standard envelope for this agent's run() result.

        Populates the canonical fields (agent, incident_id, status,
        decision, reasoning_summary, warnings, requires_human, timestamp)
        AND the legacy dashboard fields (agent_name, rationale, data,
        success) so existing clients keep working.
        """
        decision = decision or {}
        warnings = warnings or []
        legacy_data = dict(decision)
        if action:
            legacy_data.setdefault("action", action)
        return AgentOutput(
            agent=self.name,
            incident_id=state.incident_id,
            status=status,
            confidence=max(0.0, min(1.0, float(confidence))),
            decision=decision,
            reasoning_summary=reasoning_summary,
            warnings=warnings,
            requires_human=requires_human,
            timestamp=utcnow(),
            agent_name=self.name,
            rationale=reasoning_summary,
            data=legacy_data,
            success=(status == "success"),
        )
