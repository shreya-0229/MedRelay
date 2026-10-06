"""MedRelay agent package (v2 file layout).

Importing this package imports every agent module, which self-registers in
app.agents.agent_registry — so get()/list_agents() work right after this
import.
"""
from app.agents.agent_message import AgentBus, AgentMessage
from app.agents.agent_registry import get as get_agent
from app.agents.agent_registry import list_agents, register
from app.agents.base_agent import BaseAgent, utcnow
from app.agents.communication_agent import CommunicationAgent
from app.agents.dispatch_agent import DispatchAgent
from app.agents.hospital_agent import HospitalAgent
from app.agents.intake_agent import IntakeAgent
from app.agents.triage_agent import TriageAgent
from app.agents.verification_agent import VerificationAgent

__all__ = [
    "AgentBus",
    "AgentMessage",
    "BaseAgent",
    "CommunicationAgent",
    "DispatchAgent",
    "HospitalAgent",
    "IntakeAgent",
    "TriageAgent",
    "VerificationAgent",
    "get_agent",
    "list_agents",
    "register",
    "utcnow",
]
