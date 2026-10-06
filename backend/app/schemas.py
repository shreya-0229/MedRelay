"""Pydantic v2 schemas: API I/O, agent I/O, incident state, WebSocket messages."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, Field


class Location(BaseModel):
    """Incident location."""

    lat: float
    lon: float
    address: str = ""


class TriageResult(BaseModel):
    """TriageAgent output embedded in the incident state."""

    severity: int = Field(ge=1, le=5)
    pathway: str
    confidence: float = Field(ge=0.0, le=1.0)
    rationale: str = ""


class AmbulanceSelection(BaseModel):
    """DispatchAgent output embedded in the incident state."""

    id: str
    capability: str
    eta_min: float


class HospitalSelection(BaseModel):
    """HospitalLiaisonAgent output embedded in the incident state."""

    id: str
    name: str
    distance_km: float


class VerificationCheck(BaseModel):
    """One named verification check."""

    name: str
    passed: bool
    detail: str = ""


class VerificationResults(BaseModel):
    """VerificationAgent output embedded in the incident state."""

    passed: bool
    issues: list[str] = Field(default_factory=list)
    checks: list[VerificationCheck] = Field(default_factory=list)


class Communication(BaseModel):
    """One outbound message drafted by CommunicationAgent."""

    channel: str
    text: str
    ts: datetime


class TimelineEntry(BaseModel):
    """One timestamped pipeline event."""

    ts: datetime
    event: str
    detail: str = ""


class AgentOutput(BaseModel):
    """Standard envelope every agent returns from run().

    New standard fields (agent, incident_id, status, decision,
    reasoning_summary, warnings, requires_human, timestamp) are the
    canonical contract. The legacy fields (agent_name, rationale, data,
    success) mirror them and are kept so the existing dashboard keeps
    working unchanged.
    """

    agent_name: str
    confidence: float = Field(ge=0.0, le=1.0)
    rationale: str = ""
    data: dict[str, Any] = Field(default_factory=dict)
    success: bool = True
    # --- Standard envelope (canonical) ---
    agent: str = ""
    incident_id: str = ""
    status: Literal["success", "failed", "escalated"] = "success"
    decision: dict[str, Any] = Field(default_factory=dict)
    reasoning_summary: str = ""
    warnings: list[str] = Field(default_factory=list)
    requires_human: bool = False
    timestamp: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc))


class IncidentReport(BaseModel):
    """POST /api/incidents request body — the raw caller report.

    Structured fields are used directly when present; free-text fields
    (report_text / voice_transcript) are parsed by IntakeAgent when the
    caller reports in natural language. All new fields are optional, so
    the existing API contract is unchanged.
    """

    # incident_type / lat / lon are optional: a truly raw free-text report
    # (report_text only) must be accepted — IntakeAgent parses what it can
    # and flags the rest in missing_information.
    incident_type: str = "unknown"
    lat: float | None = Field(default=None, ge=-90.0, le=90.0)
    lon: float | None = Field(default=None, ge=-180.0, le=180.0)
    address: str = ""
    patient_count: int = Field(default=1, ge=1)
    symptoms: list[str] = Field(default_factory=list)
    breathing_status: str = "normal"
    bleeding_status: str = "none"
    family_contact: str = ""
    report_text: str | None = None
    voice_transcript: str | None = None
    image_metadata: dict[str, Any] | None = None
    consciousness: str | None = None
    language: str | None = None


class ReviewState(BaseModel):
    """Operator review state for low-confidence / conflict pauses."""

    status: str = "none"  # none | pending | approved | rejected | info_requested | replanned
    reason: str = ""
    affected_decision: str = ""  # e.g. "triage" | "conflict"
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    note: str = ""
    decided_at: datetime | None = None


class IncidentState(BaseModel):
    """The full incident blackboard, persisted as JSON per incident."""

    incident_id: str
    created_at: datetime
    incident_type: str
    location: Location
    patient_count: int = Field(default=1, ge=1)
    symptoms: list[str] = Field(default_factory=list)
    breathing_status: str = "normal"
    bleeding_status: str = "none"
    severity: int | None = Field(default=None, ge=1, le=5)
    triage_result: TriageResult | None = None
    selected_ambulance: AmbulanceSelection | None = None
    selected_hospital: HospitalSelection | None = None
    hospital_status: str = "pending"    # pending | reserved | unavailable
    ambulance_status: str = "pending"   # pending | en_route | unavailable (+available after resolve)
    family_contact: str = ""
    communications: list[Communication] = Field(default_factory=list)
    agent_outputs: dict[str, AgentOutput] = Field(default_factory=dict)
    verification_results: VerificationResults | None = None
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    current_status: str = "received"
    escalation_status: str = "none"     # none | replanning | escalated
    review: ReviewState = Field(default_factory=ReviewState)
    timeline: list[TimelineEntry] = Field(default_factory=list)


class IncidentSummary(BaseModel):
    """Row in GET /api/incidents."""

    incident_id: str
    incident_type: str
    severity: int | None = None
    current_status: str
    escalation_status: str
    created_at: datetime
    ambulance_id: str | None = None
    hospital_name: str | None = None


class FleetStatusUpdate(BaseModel):
    """POST /api/demo/fleet/ambulance/{amb_id} body."""

    status: Literal["available", "out_of_service", "en_route"]


class FailureInjection(BaseModel):
    """POST /api/incidents/{incident_id}/failures body — failure drill."""

    resource_type: Literal["ambulance", "hospital", "conflict"]
    resource_id: str | None = None
    reason: str = "simulated failure"
    # For resource_type="conflict": {"agent": "DispatchAgent",
    #   "decision": {overrides}, "selected_ambulance": {...} |
    #   "selected_hospital": {...}}
    conflict: dict[str, Any] | None = None


class ReviewDecisionIn(BaseModel):
    """POST /api/incidents/{incident_id}/review/decision body."""

    decision: Literal["approve", "reject", "request_info", "replan"]
    note: str = ""


# --- WebSocket messages ------------------------------------------------------


class AgentEventMsg(BaseModel):
    """Broadcast after each agent completes."""

    type: Literal["agent_event"] = "agent_event"
    incident_id: str
    agent: str
    action: str
    rationale: str = ""
    confidence: float = 0.0
    ts: str


class IncidentUpdateMsg(BaseModel):
    """Broadcast when the pipeline finishes an incident."""

    type: Literal["incident_update"] = "incident_update"
    incident: IncidentState


class FleetUpdateMsg(BaseModel):
    """Broadcast when fleet state changes."""

    type: Literal["fleet_update"] = "fleet_update"
    fleet: dict[str, Any]


class IncidentListMsg(BaseModel):
    """Sent to a WebSocket client right after connect."""

    type: Literal["incident_list"] = "incident_list"
    incidents: list[IncidentSummary]
