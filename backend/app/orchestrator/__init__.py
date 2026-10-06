"""MedRelay orchestration package.

Public surface: ``run_pipeline``, ``Orchestrator``, ``PipelineContext``,
``Workflow``, ``EventBus``, ``StateManager``, ``Event``, ``EventType``,
``AgentBus``, ``AgentMessage``, ``RecoveryManager``, ``RecoveryError``,
``detect_conflicts``, ``release_reservations``.

The heavier ``workflow`` / ``orchestrator`` / ``recovery`` modules are
imported lazily (PEP 562) so that ``app.agents`` — which re-exports the
bus types from :mod:`app.orchestrator.event_bus` — can never trigger a
circular import at package import time.
"""
from app.orchestrator.event_bus import (
    AgentBus,
    AgentMessage,
    Event,
    EventBus,
    EventType,
)
from app.orchestrator.state_manager import StateManager

__all__ = [
    "AgentBus",
    "AgentMessage",
    "Event",
    "EventBus",
    "EventType",
    "Orchestrator",
    "PipelineContext",
    "RecoveryError",
    "RecoveryManager",
    "StateManager",
    "Workflow",
    "detect_conflicts",
    "release_reservations",
    "run_pipeline",
]

_LAZY = {
    "Workflow": "app.orchestrator.workflow",
    "Orchestrator": "app.orchestrator.orchestrator",
    "PipelineContext": "app.orchestrator.orchestrator",
    "run_pipeline": "app.orchestrator.orchestrator",
    "RecoveryManager": "app.orchestrator.recovery",
    "RecoveryError": "app.orchestrator.recovery",
    "detect_conflicts": "app.orchestrator.workflow",
    "release_reservations": "app.orchestrator.workflow",
}


def __getattr__(name: str):
    if name in _LAZY:
        import importlib

        module = importlib.import_module(_LAZY[name])
        return getattr(module, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
