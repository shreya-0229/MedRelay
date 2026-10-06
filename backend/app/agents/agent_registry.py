"""Agent registry: canonical name -> agent class.

Each agent module registers itself with @register at import time; importing
app.agents (the package __init__) imports every agent module, so the
registry is fully populated after that import. The orchestrator and
/api/health resolve agents through get()/list_agents() instead of
hardcoding imports per agent.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:  # runtime import would be circular
    from app.agents.base_agent import BaseAgent

_REGISTRY: dict[str, type["BaseAgent"]] = {}


def register(cls: type["BaseAgent"]) -> type["BaseAgent"]:
    """Class decorator: register an agent under its canonical name."""
    _REGISTRY[cls.name] = cls
    return cls


def get(name: str) -> type["BaseAgent"]:
    """Return the agent class for a canonical name, or raise KeyError."""
    try:
        return _REGISTRY[name]
    except KeyError:
        raise KeyError(
            f"unknown agent {name!r}; known agents: {list_agents()}") from None


def list_agents() -> list[str]:
    """Canonical agent names, sorted."""
    return sorted(_REGISTRY)
