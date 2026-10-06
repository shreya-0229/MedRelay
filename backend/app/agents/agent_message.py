"""Thin re-export: the canonical ``AgentBus`` / ``AgentMessage`` now live
in :mod:`app.orchestrator.event_bus` (the pipeline event system).

Import from either location — they are the same objects. Kept so
existing ``from app.agents import AgentBus`` imports keep working.
"""
from app.orchestrator.event_bus import AgentBus, AgentMessage

__all__ = ["AgentBus", "AgentMessage"]
