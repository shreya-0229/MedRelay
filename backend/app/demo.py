"""Demo-mode scenario runner for hackathon judging.

Each scenario drives the REAL pipeline (``run_pipeline``) and the REAL
``RecoveryManager`` — nothing is simulated. Scenario runs execute as
tracked asyncio background tasks; the dashboard follows them through the
normal WebSocket feed and audit trail, exactly as if a human operator had
triggered every step.

Run records live in ``_RUNS``; ``reset_demo`` cancels and ignores stale
runs, so a reset in the middle of a scenario can never corrupt later runs.
"""
from __future__ import annotations

import asyncio
import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Awaitable, Callable

from sqlalchemy.orm import Session, sessionmaker

from app import models
from app.audit import write_audit
from app.llm import LLMProvider
from app.orchestrator import (
    PipelineContext,
    RecoveryManager,
    run_pipeline,
)
from app.orchestrator.event_bus import EventBus
from app.orchestrator.state_manager import StateManager
from app.schemas import (
    AgentOutput,
    AmbulanceSelection,
    Communication,
    FailureInjection,
    HospitalSelection,
    IncidentReport,
    IncidentState,
    Location,
    ReviewState,
    TimelineEntry,
    TriageResult,
    VerificationCheck,
    VerificationResults,
)
from app.seed import fleet_snapshot, reseed_fleet


class UnknownScenario(Exception):
    """Raised when a scenario id is not registered (→ HTTP 404)."""


class _Aborted(Exception):
    """Internal: the run was cancelled by a demo reset."""


class _OutageProvider(LLMProvider):
    """Honest external-service outage.

    ``real = True`` so agents take the LLM path, but ``complete()`` always
    raises — :meth:`BaseAgent.llm_assist` catches it and falls back to the
    deterministic path, exactly as in a production provider outage.
    """

    real = True

    def complete(self, prompt: str) -> str:
        raise RuntimeError("external AI service unavailable (demo outage)")


# ---------------------------------------------------------------------------
# Scenario definitions.
#
# Each step has a matching rule against REAL backend events:
#   {"type": "action", "match": "<substring of the WS agent_event action>"}
#   {"type": "action_all", "matches": ["a", "b"]}  (parallel group)
#   {"type": "incident_known"}                     (incident_id observed)
#   {"type": "incident_status", "status": "<current_status>"}
# The frontend advances a pointer through the chronological event feed, so
# repeated action strings (dispatch runs twice in the flagship) resolve to
# the correct occurrence.
# ---------------------------------------------------------------------------

_PIPELINE_STEPS: list[dict[str, Any]] = [
    {"id": "intake", "label": "Intake extracts the report",
     "rule": {"type": "action", "match": "parsed"}},
    {"id": "created", "label": "Incident created",
     "rule": {"type": "incident_known"}},
    {"id": "triage", "label": "Triage determines severity",
     "rule": {"type": "action", "match": "triage decision"}},
    {"id": "fanout", "label": "Dispatch + Hospital Liaison fan out (parallel)",
     "rule": {"type": "action_all",
              "matches": ["dispatch started", "hospital search started"]}},
    {"id": "secured",
     "label": "Ambulance dispatched + hospital bed reserved (parallel)",
     "rule": {"type": "action_all",
              "matches": ["ambulance dispatched", "hospital bed reserved"]}},
    {"id": "verify", "label": "Verification checks all outputs",
     "rule": {"type": "action", "match": "verification started"}},
    {"id": "verdict", "label": "Verification passed",
     "rule": {"type": "action", "match": "verification passed"}},
    {"id": "comms", "label": "Family update sent (EN/HI/MR)",
     "rule": {"type": "action", "match": "notifications sent"}},
    {"id": "completed", "label": "Incident completed — response underway",
     "rule": {"type": "incident_status", "status": "completed"}},
]

_AMB_RECOVERY_STEPS: list[dict[str, Any]] = [
    {"id": "faildetect", "label": "Failure detected",
     "rule": {"type": "action", "match": "failure detected"}},
    {"id": "fail", "label": "Ambulance failure injected",
     "rule": {"type": "action", "match": "ambulance failure"}},
    {"id": "failvalid", "label": "Verification confirms the failure",
     "rule": {"type": "action", "match": "verification failed"}},
    {"id": "replan", "label": "Orchestrator starts replanning",
     "rule": {"type": "action", "match": "replanning after failure"}},
    {"id": "research", "label": "Dispatch re-searches (failed unit excluded)",
     "rule": {"type": "action", "match": "dispatch started"}},
    {"id": "reselect", "label": "Replacement ambulance dispatched",
     "rule": {"type": "action", "match": "ambulance dispatched"}},
    {"id": "reverify", "label": "Replacement verified",
     "rule": {"type": "action_all",
              "matches": ["verification started", "verification passed"]}},
    {"id": "replaced", "label": "New resource selected",
     "rule": {"type": "action", "match": "replacement ambulance selected"}},
    {"id": "renotify", "label": "Family receives updated ETA",
     "rule": {"type": "action", "match": "family notified"}},
    {"id": "recovered", "label": "Incident recovered",
     "rule": {"type": "incident_status", "status": "recovered"}},
]

_HOSP_RECOVERY_STEPS: list[dict[str, Any]] = [
    {"id": "faildetect", "label": "Failure detected",
     "rule": {"type": "action", "match": "failure detected"}},
    {"id": "fail", "label": "Hospital failure injected",
     "rule": {"type": "action", "match": "hospital failure"}},
    {"id": "failvalid", "label": "Verification confirms the failure",
     "rule": {"type": "action", "match": "verification failed"}},
    {"id": "replan", "label": "Orchestrator starts replanning",
     "rule": {"type": "action", "match": "replanning after failure"}},
    {"id": "research", "label": "Liaison re-searches (failed hospital excluded)",
     "rule": {"type": "action", "match": "hospital search started"}},
    {"id": "reselect", "label": "Replacement hospital selected",
     "rule": {"type": "action", "match": "hospital bed reserved"}},
    {"id": "reverify", "label": "Replacement verified",
     "rule": {"type": "action_all",
              "matches": ["verification started", "verification passed"]}},
    {"id": "replaced", "label": "New hospital selected",
     "rule": {"type": "action", "match": "replacement hospital selected"}},
    {"id": "renotify", "label": "Family notified of new hospital",
     "rule": {"type": "action", "match": "family notified"}},
    {"id": "recovered", "label": "Incident recovered",
     "rule": {"type": "incident_status", "status": "recovered"}},
]

_MAIN_STEPS: list[dict[str, Any]] = _PIPELINE_STEPS + _AMB_RECOVERY_STEPS + [
    {"id": "handoff", "label": "HOSPITAL READY — audit trail complete",
     "rule": {"type": "action", "match": "hospital ready"}},
]

SCENARIOS: list[dict[str, Any]] = [
    {
        "id": "critical_road_accident",
        "name": "Critical Road Accident",
        "description": "Two injured, one unconscious — full 6-agent pipeline, CRITICAL triage.",
        "steps": _PIPELINE_STEPS,
    },
    {
        "id": "heart_emergency",
        "name": "Heart Emergency",
        "description": "Chest pain, conscious — cardiac pathway, full pipeline.",
        "steps": _PIPELINE_STEPS,
    },
    {
        "id": "multiple_casualties",
        "name": "Multiple Casualties",
        "description": "5 patients in a bus accident — full pipeline at scale.",
        "steps": _PIPELINE_STEPS,
    },
    {
        "id": "low_confidence",
        "name": "Low Confidence Report",
        "description": "Deliberately vague report — triage pauses for human review.",
        "steps": [
            {"id": "intake", "label": "Intake extracts what it can",
             "rule": {"type": "action", "match": "parsed"}},
            {"id": "created", "label": "Incident created",
             "rule": {"type": "incident_known"}},
            {"id": "triage", "label": "Triage below confidence threshold",
             "rule": {"type": "action", "match": "triage decision"}},
            {"id": "review", "label": "Paused — operator decision required",
             "rule": {"type": "incident_status",
                      "status": "human_review_required"}},
        ],
    },
    {
        "id": "ambulance_failure",
        "name": "Ambulance Failure",
        "description": "Breakdown mid-response — full 11-step autonomous recovery.",
        "steps": _PIPELINE_STEPS + _AMB_RECOVERY_STEPS,
    },
    {
        "id": "hospital_unavailable",
        "name": "Hospital Unavailable",
        "description": "Hospital goes dark mid-plan — replan to a new facility.",
        "steps": _PIPELINE_STEPS + _HOSP_RECOVERY_STEPS,
    },
    {
        "id": "agent_conflict",
        "name": "Agent Conflict",
        "description": "Contradictory outputs — verification blocks, human review.",
        "steps": _PIPELINE_STEPS + [
            {"id": "conflict", "label": "Agent conflict detected",
             "rule": {"type": "action", "match": "agent conflict detected"}},
            {"id": "review", "label": "Routed to human review",
             "rule": {"type": "incident_status",
                      "status": "human_review_required"}},
        ],
    },
    {
        "id": "external_service_failure",
        "name": "External Service Failure",
        "description": "AI provider outage — deterministic fallback completes the job.",
        "steps": _PIPELINE_STEPS,
    },
    {
        "id": "main_judging",
        "name": "Flagship: Crash → Breakdown → Recovery",
        "description": "The 20-step judge demo — critical accident, ambulance "
                       "failure, replanning, HOSPITAL READY. One button.",
        "flagship": True,
        "steps": _MAIN_STEPS,
    },
]

_SPEC: dict[str, dict[str, Any]] = {s["id"]: s for s in SCENARIOS}


def list_scenarios() -> list[dict[str, Any]]:
    """Scenario definitions for the Demo Scenarios page."""
    import copy
    return copy.deepcopy(SCENARIOS)


# ---------------------------------------------------------------------------
# Canned reports (deterministic by construction).
# ---------------------------------------------------------------------------

def _road_accident_report() -> IncidentReport:
    # NOTE: "bleeding heavily" (not just "bleeding") is what deterministically
    # drives the triage vitals override to CRITICAL — see intake_agent's
    # _BLEEDING_MARKERS. The visible report stays faithful to the script.
    return IncidentReport(
        report_text=("Two people have been injured in a road accident. "
                     "One person is unconscious and bleeding heavily. "
                     "Location: Pune."),
        lat=18.5204, lon=73.8567, address="Pune, Maharashtra",
        family_contact="+91-9822012345")


def _heart_report() -> IncidentReport:
    return IncidentReport(
        incident_type="cardiac_arrest",
        lat=18.5314, lon=73.8446, address="Deccan Gymkhana, Pune",
        patient_count=1,
        symptoms=["chest pain", "radiating arm pain", "sweating"],
        breathing_status="normal", bleeding_status="none",
        family_contact="+91-9000000011")


def _multi_casualty_report() -> IncidentReport:
    return IncidentReport(
        incident_type="road_accident",
        lat=18.5626, lon=73.8087, address="Pune-Mumbai Highway",
        patient_count=5,
        symptoms=["multiple injuries", "fractures"],
        breathing_status="normal", bleeding_status="minor",
        family_contact="+91-9000000012")


def _vague_report() -> IncidentReport:
    return IncidentReport(
        report_text="Someone is not feeling well downtown, not sure what happened.",
        lat=18.5204, lon=73.8567, address="Downtown Pune",
        family_contact="+91-9000000013")


# ---------------------------------------------------------------------------
# Run tracking.
# ---------------------------------------------------------------------------

Broadcast = Callable[[dict], Awaitable[None]]


@dataclass
class _DemoCtx:
    session_factory: sessionmaker
    llm: LLMProvider
    broadcast: Broadcast


@dataclass
class _Run:
    run_id: str
    scenario_id: str
    status: str = "running"  # running | done | failed | cancelled
    incident_id: str | None = None
    terminal_state: str | None = None
    error: str = ""
    cancelled: bool = False
    generation: int = 0
    started_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat())
    finished_at: str | None = None
    task: Any = field(default=None, repr=False)

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "scenario_id": self.scenario_id,
            "status": self.status,
            "incident_id": self.incident_id,
            "terminal_state": self.terminal_state,
            "error": self.error,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
        }


_RUNS: dict[str, _Run] = {}
_GENERATION: list[int] = [0]


def get_run(run_id: str) -> _Run | None:
    return _RUNS.get(run_id)


def _tracking_broadcast(run: _Run, broadcast: Broadcast) -> Broadcast:
    """Capture the incident_id from the first broadcast that carries it."""

    async def _send(msg: dict) -> None:
        if run.incident_id is None:
            iid = msg.get("incident_id")
            if iid:
                run.incident_id = iid
            elif isinstance(msg.get("incident"), dict):
                iid2 = msg["incident"].get("incident_id")
                if iid2:
                    run.incident_id = iid2
        await broadcast(msg)

    return _send


def _check(run: _Run) -> None:
    if run.cancelled or run.generation != _GENERATION[0]:
        raise _Aborted()


# ---------------------------------------------------------------------------
# Scenario executors — each returns the terminal current_status.
# ---------------------------------------------------------------------------

async def _pipeline(run: _Run, ctx: _DemoCtx,
                    report: IncidentReport) -> IncidentState:
    _check(run)
    state = await run_pipeline(
        report,
        PipelineContext(session_factory=ctx.session_factory, llm=ctx.llm,
                        broadcast=ctx.broadcast))
    run.incident_id = state.incident_id
    return state


def _recovery_manager(ctx: _DemoCtx, db: Session) -> RecoveryManager:
    return RecoveryManager(db=db, llm=ctx.llm, broadcast=ctx.broadcast)


async def _s_critical_road_accident(run: _Run, ctx: _DemoCtx) -> str:
    state = await _pipeline(run, ctx, _road_accident_report())
    run.terminal_state = state.current_status
    return state.current_status


async def _s_heart_emergency(run: _Run, ctx: _DemoCtx) -> str:
    state = await _pipeline(run, ctx, _heart_report())
    run.terminal_state = state.current_status
    return state.current_status


async def _s_multiple_casualties(run: _Run, ctx: _DemoCtx) -> str:
    state = await _pipeline(run, ctx, _multi_casualty_report())
    run.terminal_state = state.current_status
    return state.current_status


async def _s_low_confidence(run: _Run, ctx: _DemoCtx) -> str:
    state = await _pipeline(run, ctx, _vague_report())
    # The pipeline pauses itself at human_review_required; the operator
    # decides in the dashboard — the scenario ends here by design.
    run.terminal_state = state.current_status
    return state.current_status


async def _s_ambulance_failure(run: _Run, ctx: _DemoCtx) -> str:
    state = await _pipeline(run, ctx, _road_accident_report())
    _check(run)
    db: Session = ctx.session_factory()
    try:
        mgr = _recovery_manager(ctx, db)
        assert state.selected_ambulance is not None
        out = await mgr.recover_ambulance_failure(
            state.incident_id, state.selected_ambulance.id,
            "demo: simulated breakdown")
    finally:
        db.close()
    run.terminal_state = out.current_status
    return out.current_status


async def _s_hospital_unavailable(run: _Run, ctx: _DemoCtx) -> str:
    state = await _pipeline(run, ctx, _road_accident_report())
    _check(run)
    db: Session = ctx.session_factory()
    try:
        mgr = _recovery_manager(ctx, db)
        assert state.selected_hospital is not None
        out = await mgr.recover_hospital_failure(
            state.incident_id, state.selected_hospital.id,
            "demo: simulated outage")
    finally:
        db.close()
    run.terminal_state = out.current_status
    return out.current_status


async def _s_agent_conflict(run: _Run, ctx: _DemoCtx) -> str:
    state = await _pipeline(run, ctx, _heart_report())
    _check(run)
    db: Session = ctx.session_factory()
    try:
        mgr = _recovery_manager(ctx, db)
        # Inject the proven contradiction: BLS unit for a severity-5 case.
        out = await mgr.inject(
            state.incident_id,
            FailureInjection(
                resource_type="conflict",
                reason="demo: tampered dispatch output",
                conflict={"agent": "DispatchAgent",
                          "decision": {"ambulance_id": "A6",
                                       "capabilities": "BLS",
                                       "eta_min": 9.0}}))
    finally:
        db.close()
    run.terminal_state = out.current_status
    return out.current_status


async def _s_external_service_failure(run: _Run, ctx: _DemoCtx) -> str:
    outage = _DemoCtx(session_factory=ctx.session_factory,
                      llm=_OutageProvider(), broadcast=ctx.broadcast)
    state = await _pipeline(run, outage, _heart_report())
    _check(run)
    # Record, honestly, that the whole run executed on the fallback path.
    db: Session = ctx.session_factory()
    try:
        sm = StateManager(db)
        sm.load(state.incident_id)
        sm.note("external_service_outage",
                "External AI service unavailable during this run — "
                "deterministic fallback engaged for all agent decisions")
        write_audit(db, state.incident_id, agent="DemoRunner",
                    action="External service fallback",
                    rationale=("AI provider outage: llm_assist returned None "
                               "throughout; deterministic templates used"),
                    confidence=1.0)
        await ctx.broadcast({"type": "incident_update",
                             "incident": sm.state.model_dump(mode="json")})
    finally:
        db.close()
    run.terminal_state = state.current_status
    return state.current_status


async def _s_main_judging(run: _Run, ctx: _DemoCtx) -> str:
    """The 20-step flagship: critical accident → breakdown → recovery →
    HOSPITAL READY."""
    # Fail fast: the flagship needs one ALS for the initial dispatch and a
    # second ALS as the replacement after the simulated breakdown. Running
    # with a depleted fleet would escalate mid-demo and (before the honest
    # gate below existed) could misreport the outcome.
    db0: Session = ctx.session_factory()
    try:
        als_available = (db0.query(models.Ambulance)
                         .filter(models.Ambulance.capability == "ALS",
                                 models.Ambulance.status == "available")
                         .count())
    finally:
        db0.close()
    if als_available < 2:
        run.status = "failed"
        run.error = (f"ALS fleet exhausted ({als_available} available, 2 "
                     "required) — press Reset Demo and re-run")
        run.terminal_state = None
        return ""
    # Steps 1-9: the full real pipeline.
    state = await _pipeline(run, ctx, _road_accident_report())
    _check(run)
    # Steps 10-19: real ambulance failure + 11-step recovery.
    db: Session = ctx.session_factory()
    try:
        mgr = _recovery_manager(ctx, db)
        assert state.selected_ambulance is not None
        out = await mgr.recover_ambulance_failure(
            state.incident_id, state.selected_ambulance.id,
            "demo: simulated breakdown")
    finally:
        db.close()
    _check(run)
    return await _stamp_hospital_ready(run, ctx, out)


def _recovery_succeeded(out: IncidentState) -> bool:
    """Honest-gate predicate for the flagship handoff: HOSPITAL READY may
    only be stamped when the recovery genuinely succeeded — the incident is
    not escalated and a replacement ambulance is actually assigned."""
    return (out.escalation_status == "none"
            and out.selected_ambulance is not None)


async def _stamp_hospital_ready(run: _Run, ctx: _DemoCtx,
                               out: IncidentState) -> str:
    """Step 20 of the flagship. Stamps HOSPITAL READY only if the recovery
    genuinely succeeded; otherwise marks the run failed with an honest
    error and leaves the incident in its (escalated) state."""
    if not _recovery_succeeded(out):
        run.status = "failed"
        run.error = (f"Flagship did not reach HOSPITAL READY: incident "
                     f"{out.incident_id} ended '{out.current_status}' "
                     f"(escalation={out.escalation_status}) — no safe handoff")
        run.terminal_state = out.current_status
        return out.current_status
    # Step 20: handoff — the receiving hospital team is standing by.
    db = ctx.session_factory()
    try:
        sm = StateManager(db)
        sm.load(out.incident_id)
        sm.update(current_status="hospital_ready")
        sm.note("hospital_ready",
                "Handoff complete — receiving hospital team standing by")
        bus = EventBus(db=db, broadcast=ctx.broadcast)
        await bus.emit("HOSPITAL_READY", sender="DemoRunner",
                       incident_id=out.incident_id, confidence=1.0,
                       action="Hospital ready",
                       rationale=("Incident reached HOSPITAL READY — "
                                  "receiving team standing by; full audit "
                                  "trail recorded"))
        await ctx.broadcast({"type": "incident_update",
                             "incident": sm.state.model_dump(mode="json")})
    finally:
        db.close()
    run.terminal_state = "hospital_ready"
    return "hospital_ready"


_EXEC: dict[str, Callable[[_Run, _DemoCtx], Awaitable[str]]] = {
    "critical_road_accident": _s_critical_road_accident,
    "heart_emergency": _s_heart_emergency,
    "multiple_casualties": _s_multiple_casualties,
    "low_confidence": _s_low_confidence,
    "ambulance_failure": _s_ambulance_failure,
    "hospital_unavailable": _s_hospital_unavailable,
    "agent_conflict": _s_agent_conflict,
    "external_service_failure": _s_external_service_failure,
    "main_judging": _s_main_judging,
}


async def execute_scenario(scenario_id: str, *, session_factory: sessionmaker,
                           llm: LLMProvider, broadcast: Broadcast) -> _Run:
    """Run a scenario synchronously to completion (tests / scripting).

    Returns the run record with incident_id and terminal_state set.
    """
    if scenario_id not in _EXEC:
        raise UnknownScenario(scenario_id)
    run = _Run(run_id=uuid.uuid4().hex[:12], scenario_id=scenario_id,
               generation=_GENERATION[0])
    ctx = _DemoCtx(session_factory=session_factory, llm=llm,
                   broadcast=_tracking_broadcast(run, broadcast))
    await _EXEC[scenario_id](run, ctx)
    if run.status == "running":
        run.status = "done"
    run.finished_at = datetime.now(timezone.utc).isoformat()
    return run


async def run_scenario(scenario_id: str, *, session_factory: sessionmaker,
                       llm: LLMProvider, broadcast: Broadcast) -> _Run:
    """Start a scenario as a tracked background task (HTTP 202 path)."""
    if scenario_id not in _EXEC:
        raise UnknownScenario(scenario_id)
    run = _Run(run_id=uuid.uuid4().hex[:12], scenario_id=scenario_id,
               generation=_GENERATION[0])
    _RUNS[run.run_id] = run
    ctx = _DemoCtx(session_factory=session_factory, llm=llm,
                   broadcast=_tracking_broadcast(run, broadcast))

    async def _guarded() -> None:
        try:
            await _EXEC[scenario_id](run, ctx)
            if run.status == "running":
                run.status = "done"
        except _Aborted:
            run.status = "cancelled"
        except asyncio.CancelledError:
            run.status = "cancelled"
            raise
        except Exception as exc:  # never leave a run stuck in "running"
            run.status = "failed"
            run.error = str(exc)[:500]
        finally:
            run.finished_at = datetime.now(timezone.utc).isoformat()

    run.task = asyncio.get_running_loop().create_task(_guarded())
    return run


async def reset_demo(db: Session, *, broadcast: Broadcast) -> dict[str, Any]:
    """Full demo reset: cancel tracked runs, wipe incident data, reseed the
    fleet to pristine state. Leaves the system exactly as a fresh boot."""
    for r in list(_RUNS.values()):
        r.cancelled = True
        if r.task is not None and not r.task.done():
            r.task.cancel()
    _RUNS.clear()
    _GENERATION[0] += 1

    incidents_deleted = db.query(models.Incident).count()
    db.query(models.CommunicationMessage).delete()
    db.query(models.AgentRun).delete()
    db.query(models.AuditEvent).delete()
    db.query(models.Incident).delete()
    db.commit()
    reseed_fleet(db)

    await broadcast({"type": "fleet_update", "fleet": fleet_snapshot(db)})
    await broadcast({"type": "incident_list", "incidents": []})
    return {"status": "reset", "incidents_deleted": incidents_deleted}


# ---------------------------------------------------------------------------
# Sample-day dataset (judging aid).
#
# POST /api/demo/seed-sample writes 6 realistic, pre-run incidents as REAL
# DB rows (full IncidentState JSON, audit events, EN/HI/MR communications),
# each flagged ``is_sample=True`` so the dashboard badges them SAMPLE.
# One incident pauses at ``human_review_required`` so judges can Approve /
# Reject / Request-info / Replan live against the real review API.
# Reset Demo wipes the sample rows with everything else.
#
# These rows are static history — they never touch the live fleet, the
# pipeline, or the orchestrator. The fleet stays pristine so a flagship
# demo can still run right after seeding.
# ---------------------------------------------------------------------------

_SEV_LABELS = {5: "CRITICAL", 4: "HIGH", 3: "MODERATE", 2: "LOW", 1: "MINIMAL"}

_TRIAGE_DISCLAIMER = (
    "AI-assisted triage is decision support, not a clinical diagnosis; "
    "a human dispatcher owns the final call.")


def _sout(agent: str, incident_id: str, ts: datetime, *,
          status: str = "success", confidence: float = 0.9,
          decision: dict | None = None, reasoning: str = "",
          warnings: list | None = None,
          requires_human: bool = False) -> AgentOutput:
    """One realistic agent output envelope for a seeded incident."""
    dec = dict(decision or {})
    return AgentOutput(
        agent_name=agent, confidence=confidence, rationale=reasoning,
        data=dict(dec), success=(status == "success"),
        agent=agent, incident_id=incident_id, status=status,
        decision=dec, reasoning_summary=reasoning,
        warnings=list(warnings or []), requires_human=requires_human,
        timestamp=ts)


def _schecks(incident_id: str, *, failed: dict[str, str] | None = None,
             amb_id: str = "", amb_cap: str = "", hosp_name: str = "",
             severity: int = 5, pathway: str = "trauma-center",
             amb_status: str = "en_route",
             hosp_status: str = "reserved") -> VerificationResults:
    """The 8 real verification checks, all passing unless ``failed`` names
    specific ones with their failure detail (mirrors VerificationAgent)."""
    failed = failed or {}

    def ck(name: str, detail_ok: str) -> VerificationCheck:
        if name in failed:
            return VerificationCheck(name=name, passed=False,
                                     detail=failed[name])
        return VerificationCheck(name=name, passed=True, detail=detail_ok)

    checks = [
        ck("required_fields_present",
           f"incident_id={incident_id!r} incident_type present "
           f"location=ok patient_count>=1"),
        ck("triage_valid",
           f"severity {severity} in 1-5, pathway {pathway!r} known"),
        ck("triage_consistency_ok",
           f"severity {severity} -> label {_SEV_LABELS[severity]!r} matches; "
           f"disclaimer present"),
        ck("confidence_thresholds_ok", "all agents meet the 0.6 confidence floor"),
        ck("ambulance_capability_ok",
           f"{amb_id} ({amb_cap}) satisfies the ALS rule"
           if amb_id else "no ambulance selected"),
        ck("ambulance_assignment_ok",
           f"{amb_id} en_route to {incident_id}"
           if amb_id else "no ambulance selected"),
        ck("hospital_bed_ok",
           f"{hosp_name}: pathway {pathway!r} covered, "
           f"hospital_status={hosp_status}"
           if hosp_name else "no hospital selected"),
        ck("resource_consistency_ok", "resources match agent outcomes"),
    ]
    issues = [f"{c.name}: {c.detail}" for c in checks if not c.passed]
    return VerificationResults(passed=not issues, issues=issues, checks=checks)


def _insert_sample(db: Session, *, incident_id: str, created_at: datetime,
                   incident_type: str, address: str, lat: float, lon: float,
                   patient_count: int, symptoms: list[str],
                   breathing: str, bleeding: str,
                   severity: int | None, pathway: str | None,
                   triage_conf: float, triage_rationale: str,
                   dispatch_conf: float, dispatch_decision: dict | None,
                   hospital_conf: float, hospital_decision: dict | None,
                   sel_amb: tuple[str, str, float] | None,
                   sel_hosp: tuple[str, str, float] | None,
                   verification: VerificationResults | None,
                   overall_conf: float, current_status: str,
                   escalation_status: str,
                   review: ReviewState | None,
                   timeline: list[tuple[int, str, str]],
                   audit: list[tuple[int, str, str, str, float]],
                   comms: list[tuple[int, str, str, str]],
                   family_contact: str = "+91 98220 00000") -> None:
    """Write one complete sample incident: state row + audit events +
    agent runs + communications. Offsets are seconds after ``created_at``."""
    ts = lambda off: created_at + timedelta(seconds=off)

    triage = (TriageResult(severity=severity, pathway=pathway,
                           confidence=triage_conf,
                           rationale=triage_rationale)
              if severity is not None else None)
    amb = (AmbulanceSelection(id=sel_amb[0], capability=sel_amb[1],
                              eta_min=sel_amb[2]) if sel_amb else None)
    hosp = (HospitalSelection(id=sel_hosp[0], name=sel_hosp[1],
                              distance_km=sel_hosp[2]) if sel_hosp else None)

    outputs: dict[str, AgentOutput] = {}
    outputs["IntakeAgent"] = _sout(
        "IntakeAgent", incident_id, ts(timeline[0][0]),
        confidence=0.98, reasoning="Report validated and normalized; nothing invented.",
        decision={"incident_type": incident_type, "patient_count": patient_count,
                  "location": address})
    if triage is not None:
        req_human = triage_conf < 0.6
        outputs["TriageAgent"] = _sout(
            "TriageAgent", incident_id, ts(timeline[1][0] if len(timeline) > 1 else 5),
            status="escalated" if req_human else "success",
            confidence=triage_conf,
            reasoning=triage_rationale,
            decision={"severity": severity, "pathway": pathway,
                      "severity_label": _SEV_LABELS.get(severity or 0),
                      "disclaimer": _TRIAGE_DISCLAIMER},
            warnings=(["Low confidence — routing to human review"]
                      if req_human else []),
            requires_human=req_human)
    if sel_amb is not None:
        outputs["DispatchAgent"] = _sout(
            "DispatchAgent", incident_id, ts(12), confidence=dispatch_conf,
            reasoning=(f"{sel_amb[0]} ({sel_amb[1]}) selected: nearest capable "
                       f"unit, ETA {sel_amb[2]} min."),
            decision=dict(dispatch_decision or {},
                          selected_ambulance={"id": sel_amb[0],
                                              "capability": sel_amb[1],
                                              "eta_min": sel_amb[2]}))
    if sel_hosp is not None:
        outputs["HospitalLiaisonAgent"] = _sout(
            "HospitalLiaisonAgent", incident_id, ts(14),
            confidence=hospital_conf,
            reasoning=(f"{sel_hosp[1]} selected: {pathway} specialty, "
                       f"bed available, {sel_hosp[2]} km away."),
            decision=dict(hospital_decision or {},
                          selected_hospital={"id": sel_hosp[0],
                                             "name": sel_hosp[1],
                                             "distance_km": sel_hosp[2]}))
    if verification is not None:
        outputs["VerificationAgent"] = _sout(
            "VerificationAgent", incident_id, ts(20),
            status="success" if verification.passed else "failed",
            confidence=0.99 if verification.passed else 0.45,
            reasoning=("All verification checks passed."
                       if verification.passed
                       else f"Verification failed on {len(verification.issues)} check(s)."),
            decision={"passed": verification.passed,
                      "checks": [c.model_dump() for c in verification.checks],
                      "issues": verification.issues},
            warnings=verification.issues)

    state = IncidentState(
        incident_id=incident_id, created_at=created_at,
        incident_type=incident_type,
        location=Location(lat=lat, lon=lon, address=address),
        patient_count=patient_count, symptoms=symptoms,
        breathing_status=breathing, bleeding_status=bleeding,
        severity=severity, triage_result=triage,
        selected_ambulance=amb, selected_hospital=hosp,
        hospital_status="reserved" if sel_hosp else "pending",
        ambulance_status="en_route" if sel_amb else "pending",
        family_contact=family_contact,
        communications=[Communication(channel=ch, text=text, ts=ts(off))
                        for off, ch, _lang, text in comms],
        agent_outputs=outputs, verification_results=verification,
        confidence=overall_conf, current_status=current_status,
        escalation_status=escalation_status,
        review=review or ReviewState(),
        timeline=[TimelineEntry(ts=ts(off), event=ev, detail=det)
                  for off, ev, det in timeline],
        is_sample=True)

    row = models.Incident(
        incident_id=incident_id, state_json=state.model_dump_json(),
        current_status=current_status, escalation_status=escalation_status,
        created_at=created_at, is_sample=True)
    db.add(row)
    db.flush()

    for off, agent, action, rationale, conf in audit:
        db.add(models.AuditEvent(incident_id=incident_id, ts=ts(off),
                                 agent=agent, action=action,
                                 rationale=rationale, confidence=conf))
    for name, out in outputs.items():
        db.add(models.AgentRun(incident_id=incident_id, agent_name=name,
                               confidence=out.confidence, success=out.success,
                               rationale=out.rationale,
                               data_json=json.dumps(out.data, default=str),
                               ts=out.timestamp))
    for off, channel, language, text in comms:
        db.add(models.CommunicationMessage(incident_id=incident_id,
                                           channel=channel, language=language,
                                           text=text, ts=ts(off)))
    db.commit()


def _clear_samples(db: Session) -> int:
    """Remove any previously seeded sample set (idempotent re-seed)."""
    ids = [r.incident_id for r in
           db.query(models.Incident)
             .filter(models.Incident.is_sample == True).all()]  # noqa: E712
    if not ids:
        return 0
    db.query(models.CommunicationMessage)\
      .filter(models.CommunicationMessage.incident_id.in_(ids)).delete(
          synchronize_session=False)
    db.query(models.AgentRun)\
      .filter(models.AgentRun.incident_id.in_(ids)).delete(
          synchronize_session=False)
    db.query(models.AuditEvent)\
      .filter(models.AuditEvent.incident_id.in_(ids)).delete(
          synchronize_session=False)
    n = db.query(models.Incident)\
          .filter(models.Incident.is_sample == True).delete(  # noqa: E712
              synchronize_session=False)
    db.commit()
    return n


def seed_sample_data(db: Session) -> dict[str, Any]:
    """Insert the 6-incident "sample day" judging dataset.

    Returns a summary dict. Re-seeding replaces the previous sample set;
    the live fleet is never touched.
    """
    _clear_samples(db)
    now = datetime.now(timezone.utc)
    seeded: list[str] = []

    # -- 1. Critical road accident WITH ambulance recovery (2h ago) --------
    t0 = now - timedelta(hours=2, minutes=5)
    v1 = _schecks("SAMPLE-01", amb_id="A3", amb_cap="ALS",
                  hosp_name="Shivajinagar Trauma Institute",
                  severity=5, pathway="trauma-center")
    _insert_sample(
        db, incident_id="SAMPLE-01", created_at=t0,
        incident_type="road_accident",
        address="Hadapsar Bypass, near Gadital, Pune",
        lat=18.5074, lon=73.9252, patient_count=2,
        symptoms=["severe bleeding", "unconscious", "head injury"],
        breathing="labored", bleeding="severe",
        severity=5, pathway="trauma-center", triage_conf=0.95,
        triage_rationale=("Vitals override: road_accident maps to HIGH, but "
                          "severe bleeding + labored breathing escalate to "
                          "CRITICAL per the physiology ladder."),
        dispatch_conf=0.92, dispatch_decision={"eta_min": 9.2},
        hospital_conf=0.90, hospital_decision={"beds_free_after": 104},
        sel_amb=("A3", "ALS", 9.2), sel_hosp=("h6", "Shivajinagar Trauma Institute", 12.4),
        verification=v1, overall_conf=0.90,
        current_status="hospital_ready", escalation_status="none",
        review=None,
        timeline=[
            (0, "incident_created", "Free-text 112-style report received."),
            (4, "intake_completed", "Report parsed: road_accident, 2 patients, severe bleeding."),
            (9, "triage_completed", "CRITICAL (5) · trauma-center — vitals override applied."),
            (14, "dispatch_completed", "Ambulance A1 (ALS) dispatched, ETA 7.5 min."),
            (16, "hospital_selected", "Bed reserved at Shivajinagar Trauma Institute (trauma-center)."),
            (21, "verification_passed", "All 8 verification checks passed."),
            (26, "notifications_sent", "Family notified in EN/HI/MR; ER pre-alert sent."),
            (480, "failure_detected", "Ambulance A1 reported a breakdown 8 min into the response."),
            (486, "ambulance_failure", "A1 marked out_of_service after verification of the failure."),
            (492, "replanning", "Orchestrator replanning: failed unit excluded from search."),
            (501, "dispatch_completed", "Replacement ambulance A3 (ALS) dispatched, ETA 9.2 min."),
            (508, "verification_passed", "Replacement verified against all 8 checks."),
            (512, "resource_replaced", "A1 → A3; family notified of the new ETA in EN/HI/MR."),
            (520, "hospital_ready", "Receiving trauma team at Shivajinagar is standing by."),
        ],
        audit=[
            (4, "IntakeAgent", "Free-text report parsed", "road_accident, 2 patients extracted", 0.98),
            (9, "TriageAgent", "Triage decision", "CRITICAL via vitals override", 0.95),
            (14, "DispatchAgent", "Ambulance dispatched", "A1 (ALS), ETA 7.5 min", 0.92),
            (16, "HospitalLiaisonAgent", "Hospital bed reserved", "Shivajinagar Trauma Institute", 0.90),
            (21, "VerificationAgent", "Verification passed", "8/8 checks", 0.99),
            (26, "CommunicationAgent", "Notifications sent", "EN/HI/MR family + ER pre-alert", 0.97),
            (480, "RecoveryManager", "Failure detected", "A1 breakdown reported mid-response", 1.0),
            (486, "RecoveryManager", "Ambulance failure", "A1 verified out_of_service", 1.0),
            (492, "Orchestrator", "Replanning after failure", "excluding failed unit A1", 0.9),
            (501, "DispatchAgent", "Ambulance dispatched", "replacement A3 (ALS), ETA 9.2 min", 0.92),
            (508, "VerificationAgent", "Verification passed", "replacement verified, 8/8", 0.99),
            (512, "RecoveryManager", "Family notified of replacement", "new ETA in EN/HI/MR", 0.97),
            (520, "Orchestrator", "Incident status: hospital_ready", "trauma team standing by", 1.0),
        ],
        comms=[
            (26, "family", "en",
             "MedRelay: Ambulance A1 (ALS) is on the way to Hadapsar Bypass. ETA ~8 min. 2 injured, severe bleeding reported. Bed reserved at Shivajinagar Trauma Institute."),
            (26, "family", "hi",
             "MedRelay: एम्बुलेंस A1 (ALS) हडपसर बायपास के लिए रवाना हो गई है। ETA ~8 मिनट। 2 घायल, गंभीर रक्तस्राव। शिवाजीनगर ट्रॉमा इंस्टीट्यूट में बेड आरक्षित।"),
            (26, "family", "mr",
             "MedRelay: रुग्णवाहिका A1 (ALS) हडपसर बायपासकडे निघाली आहे. अंदाजे वेळ ~8 मिनिटे. 2 जखमी, गंभीर रक्तस्राव. शिवाजीनगर ट्रॉमा इन्स्टिट्यूटमध्ये बेड राखीव."),
            (512, "family", "en",
             "MedRelay update: Ambulance A1 broke down; replacement A3 (ALS) is now en route. New ETA ~9 min. Your family member's care continues without interruption."),
            (512, "family", "hi",
             "MedRelay अपडेट: एम्बुलेंस A1 खराब हो गई; नई एम्बुलेंस A3 (ALS) रवाना हो गई है। नया ETA ~9 मिनट।"),
            (512, "family", "mr",
             "MedRelay अपडेट: रुग्णवाहिका A1 बंद पडली; नवीन रुग्णवाहिका A3 (ALS) निघाली आहे. नवीन अंदाजे वेळ ~9 मिनिटे."),
            (26, "hospital", "en",
             "ER pre-alert: 2 trauma patients inbound to Shivajinagar Trauma Institute, ETA ~20 min. Severe bleeding, 1 unconscious."),
        ])
    seeded.append("SAMPLE-01")

    # -- 2. Heart emergency, clean happy path (5h ago) ----------------------
    t0 = now - timedelta(hours=5, minutes=12)
    v2 = _schecks("SAMPLE-02", amb_id="A4", amb_cap="ALS",
                  hosp_name="Baner Lifeline Hospital",
                  severity=5, pathway="cath-lab")
    _insert_sample(
        db, incident_id="SAMPLE-02", created_at=t0,
        incident_type="cardiac_arrest",
        address="FC Road, Shivajinagar, Pune",
        lat=18.5314, lon=73.8446, patient_count=1,
        symptoms=["chest pain", "collapsed", "unresponsive"],
        breathing="absent", bleeding="none",
        severity=5, pathway="cath-lab", triage_conf=0.95,
        triage_rationale="cardiac_arrest → CRITICAL / cath-lab per deterministic lookup.",
        dispatch_conf=0.92, dispatch_decision={"eta_min": 4.1},
        hospital_conf=0.90, hospital_decision={"beds_free_after": 117},
        sel_amb=("A4", "ALS", 4.1), sel_hosp=("h2", "Baner Lifeline Hospital", 8.3),
        verification=v2, overall_conf=0.90,
        current_status="hospital_ready", escalation_status="none",
        review=None,
        timeline=[
            (0, "incident_created", "Cardiac arrest report received."),
            (4, "intake_completed", "Report parsed: cardiac_arrest, 1 patient, not breathing."),
            (9, "triage_completed", "CRITICAL (5) · cath-lab."),
            (14, "dispatch_completed", "Ambulance A4 (ALS) dispatched, ETA 4.1 min."),
            (16, "hospital_selected", "Cath-lab bed reserved at Baner Lifeline Hospital."),
            (21, "verification_passed", "All 8 verification checks passed."),
            (26, "notifications_sent", "Family notified in EN/HI/MR; ER pre-alert sent."),
            (40, "hospital_ready", "Cath-lab team at Baner Lifeline is standing by."),
        ],
        audit=[
            (4, "IntakeAgent", "Free-text report parsed", "cardiac_arrest, 1 patient", 0.98),
            (9, "TriageAgent", "Triage decision", "CRITICAL / cath-lab", 0.95),
            (14, "DispatchAgent", "Ambulance dispatched", "A4 (ALS), ETA 4.1 min", 0.92),
            (16, "HospitalLiaisonAgent", "Hospital bed reserved", "Baner Lifeline Hospital", 0.90),
            (21, "VerificationAgent", "Verification passed", "8/8 checks", 0.99),
            (26, "CommunicationAgent", "Notifications sent", "EN/HI/MR family + ER pre-alert", 0.97),
            (40, "Orchestrator", "Incident status: hospital_ready", "cath-lab team standing by", 1.0),
        ],
        comms=[
            (26, "family", "en",
             "MedRelay: Ambulance A4 (ALS) is rushing to FC Road. ETA ~4 min. The patient is being taken to Baner Lifeline Hospital (cardiac unit)."),
            (26, "family", "hi",
             "MedRelay: एम्बुलेंस A4 (ALS) FC रोड के लिए रवाना हो गई है। ETA ~4 मिनट। मरीज़ को बानेर लाइफ़लाइन अस्पताल (कार्डियक यूनिट) ले जाया जा रहा है।"),
            (26, "family", "mr",
             "MedRelay: रुग्णवाहिका A4 (ALS) FC रोडकडे निघाली आहे. अंदाजे वेळ ~4 मिनिटे. रुग्णाला बानेर लाइफलाइन रुग्णालय (हृदय विभाग) येथे नेले जात आहे."),
        ])
    seeded.append("SAMPLE-02")

    # -- 3. Vague report → ACTIVE human review (judges decide live) ---------
    t0 = now - timedelta(minutes=42)
    review3 = ReviewState(
        status="pending",
        reason=("Triage confidence 0.55 is below the 0.6 safety floor — the "
                "pipeline paused BEFORE any ambulance or bed was reserved."),
        affected_decision="triage", confidence=0.55, note="",
        decided_at=None)
    _insert_sample(
        db, incident_id="SAMPLE-03", created_at=t0,
        incident_type="unknown",
        address="Deccan Gymkhana bus stop, Pune",
        lat=18.5120, lon=73.8320, patient_count=1,
        symptoms=["unclear"],
        breathing="normal", bleeding="none",
        severity=3, pathway="general-er", triage_conf=0.55,
        triage_rationale=("Unknown incident type — deterministic fallback "
                          "severity 3 / general-er at 0.55 confidence; flagged "
                          "for human review instead of proceeding blindly."),
        dispatch_conf=0.0, dispatch_decision=None,
        hospital_conf=0.0, hospital_decision=None,
        sel_amb=None, sel_hosp=None,
        verification=None, overall_conf=0.55,
        current_status="human_review_required", escalation_status="none",
        review=review3,
        timeline=[
            (0, "incident_created", "Vague caller report received."),
            (4, "intake_completed", "Report parsed; key details missing (no symptoms, no vitals)."),
            (9, "triage_completed", "Fallback triage at 0.55 confidence — below the 0.6 floor."),
            (12, "human_review_required", "Pipeline paused BEFORE reservations; operator decision needed."),
        ],
        audit=[
            (4, "IntakeAgent", "Free-text report parsed", "1 patient, details sparse", 0.98),
            (9, "TriageAgent", "Triage decision", "fallback 3/general-er at 0.55", 0.55),
            (12, "Orchestrator", "Human review required", "confidence 0.55 < 0.6 floor; paused before reservations", 1.0),
        ],
        comms=[])
    seeded.append("SAMPLE-03")

    # -- 4. Agent conflict → escalated --------------------------------------
    t0 = now - timedelta(hours=3, minutes=20)
    v4 = _schecks(
        "SAMPLE-04",
        failed={
            "triage_consistency_ok":
                "severity 5 (CRITICAL) requires ALS, but BLS unit A6 was selected",
            "confidence_thresholds_ok":
                "below threshold or failed: DispatchAgent",
        },
        amb_id="A6", amb_cap="BLS",
        hosp_name="Shivajinagar Trauma Institute",
        severity=5, pathway="trauma-center")
    review4 = ReviewState(
        status="pending",
        reason=("Verification vetoed the plan: a BLS ambulance was proposed "
                "for a CRITICAL case. Reservations were blocked and released."),
        affected_decision="conflict", confidence=0.45, note="",
        decided_at=None)
    _insert_sample(
        db, incident_id="SAMPLE-04", created_at=t0,
        incident_type="road_accident",
        address="Katraj Ghat, Pune",
        lat=18.4529, lon=73.8619, patient_count=1,
        symptoms=["fractured leg", "conscious"],
        breathing="normal", bleeding="minor",
        severity=5, pathway="trauma-center", triage_conf=0.95,
        triage_rationale="Road accident with vitals override → CRITICAL / trauma-center.",
        dispatch_conf=0.30, dispatch_decision={"eta_min": 11.0, "note": "conflicting BLS assignment"},
        hospital_conf=0.90, hospital_decision={"beds_free_after": 103},
        sel_amb=("A6", "BLS", 11.0), sel_hosp=("h6", "Shivajinagar Trauma Institute", 9.8),
        verification=v4, overall_conf=0.30,
        current_status="escalated", escalation_status="escalated",
        review=review4,
        timeline=[
            (0, "incident_created", "Road accident report received."),
            (4, "intake_completed", "Report parsed: road_accident, 1 patient."),
            (9, "triage_completed", "CRITICAL (5) · trauma-center."),
            (14, "dispatch_completed", "CONFLICT: BLS unit A6 proposed for a CRITICAL case."),
            (16, "hospital_selected", "Bed reserved at Shivajinagar Trauma Institute."),
            (21, "verification_failed", "Veto: triage_consistency_ok + confidence_thresholds_ok failed."),
            (24, "conflict_detected", "BLS-for-critical contradiction contained; reservations released."),
            (28, "human_escalation", "Incident escalated to a human dispatcher with the conflict dossier."),
            (30, "notifications_sent", "Escalation notice sent to family in EN/HI/MR."),
        ],
        audit=[
            (4, "IntakeAgent", "Free-text report parsed", "road_accident, 1 patient", 0.98),
            (9, "TriageAgent", "Triage decision", "CRITICAL / trauma-center", 0.95),
            (14, "DispatchAgent", "Ambulance dispatched", "A6 (BLS) — conflicts with CRITICAL", 0.30),
            (16, "HospitalLiaisonAgent", "Hospital bed reserved", "Shivajinagar Trauma Institute", 0.90),
            (21, "VerificationAgent", "Verification failed", "triage_consistency_ok, confidence_thresholds_ok", 0.45),
            (24, "Orchestrator", "Agent conflict detected", "BLS for critical; reservations released", 1.0),
            (28, "Orchestrator", "Human escalation", "conflict dossier attached", 1.0),
            (30, "CommunicationAgent", "Notifications sent", "escalation notice EN/HI/MR", 0.97),
        ],
        comms=[
            (30, "escalation", "en",
             "MedRelay: your emergency at Katraj Ghat needs a human dispatcher's decision. A specialist is reviewing the case right now; an update will follow shortly."),
            (30, "escalation", "hi",
             "MedRelay: कात्रज घाट की आपकी आपात स्थिति पर मानव डिस्पैचर का निर्णय आवश्यक है। एक विशेषज्ञ मामले की समीक्षा कर रहा है।"),
            (30, "escalation", "mr",
             "MedRelay: कात्रज घाट येथील आपत्कालीन परिस्थितीसाठी मानवी डिस्पॅचरचा निर्णय आवश्यक आहे. तज्ज्ञ या प्रकरणाचा आढावा घेत आहे."),
        ])
    seeded.append("SAMPLE-04")

    # -- 5. Moderate fracture, clean path (1 day ago) ------------------------
    t0 = now - timedelta(hours=26)
    v5 = _schecks("SAMPLE-05", amb_id="A7", amb_cap="BLS",
                  hosp_name="PCCOE General Hospital",
                  severity=3, pathway="general-er")
    _insert_sample(
        db, incident_id="SAMPLE-05", created_at=t0,
        incident_type="trauma_fall",
        address="Akurdi Railway Station, Pune",
        lat=18.6500, lon=73.7700, patient_count=1,
        symptoms=["fractured arm", "conscious", "stable"],
        breathing="normal", bleeding="minor",
        severity=3, pathway="general-er", triage_conf=0.95,
        triage_rationale="trauma_fall → MODERATE / general-er per deterministic lookup.",
        dispatch_conf=0.92, dispatch_decision={"eta_min": 6.8},
        hospital_conf=0.90, hospital_decision={"beds_free_after": 95},
        sel_amb=("A7", "BLS", 6.8), sel_hosp=("h1", "PCCOE General Hospital", 3.2),
        verification=v5, overall_conf=0.90,
        current_status="hospital_ready", escalation_status="none",
        review=None,
        timeline=[
            (0, "incident_created", "Fall injury report received."),
            (4, "intake_completed", "Report parsed: trauma_fall, 1 patient, stable."),
            (9, "triage_completed", "MODERATE (3) · general-er."),
            (14, "dispatch_completed", "Ambulance A7 (BLS) dispatched, ETA 6.8 min."),
            (16, "hospital_selected", "Bed reserved at PCCOE General Hospital."),
            (21, "verification_passed", "All 8 verification checks passed."),
            (26, "notifications_sent", "Family notified in EN/HI/MR."),
            (40, "hospital_ready", "ER team at PCCOE General is standing by."),
        ],
        audit=[
            (4, "IntakeAgent", "Free-text report parsed", "trauma_fall, 1 patient", 0.98),
            (9, "TriageAgent", "Triage decision", "MODERATE / general-er", 0.95),
            (14, "DispatchAgent", "Ambulance dispatched", "A7 (BLS), ETA 6.8 min", 0.92),
            (16, "HospitalLiaisonAgent", "Hospital bed reserved", "PCCOE General Hospital", 0.90),
            (21, "VerificationAgent", "Verification passed", "8/8 checks", 0.99),
            (26, "CommunicationAgent", "Notifications sent", "EN/HI/MR family", 0.97),
            (40, "Orchestrator", "Incident status: hospital_ready", "ER team standing by", 1.0),
        ],
        comms=[
            (26, "family", "en",
             "MedRelay: Ambulance A7 is on the way to Akurdi Railway Station. ETA ~7 min. Bed reserved at PCCOE General Hospital."),
            (26, "family", "hi",
             "MedRelay: एम्बुलेंस A7 आकुर्डी रेलवे स्टेशन के लिए रवाना हो गई है। ETA ~7 मिनट। PCCOE जनरल अस्पताल में बेड आरक्षित।"),
            (26, "family", "mr",
             "MedRelay: रुग्णवाहिका A7 आकुर्डी रेल्वे स्थानकाकडे निघाली आहे. अंदाजे वेळ ~7 मिनिटे. PCCOE जनरल रुग्णालयात बेड राखीव."),
        ])
    seeded.append("SAMPLE-05")

    # -- 6. Hospital outage → recovery (3h ago) ------------------------------
    t0 = now - timedelta(hours=3, minutes=2)
    v6 = _schecks("SAMPLE-06", amb_id="A2", amb_cap="ALS",
                  hosp_name="Hadapsar Metro Hospital",
                  severity=4, pathway="stroke-unit")
    _insert_sample(
        db, incident_id="SAMPLE-06", created_at=t0,
        incident_type="stroke",
        address="Koregaon Park, Pune",
        lat=18.5362, lon=73.8936, patient_count=1,
        symptoms=["slurred speech", "facial droop", "arm weakness"],
        breathing="normal", bleeding="none",
        severity=4, pathway="stroke-unit", triage_conf=0.95,
        triage_rationale="stroke → HIGH / stroke-unit per deterministic lookup.",
        dispatch_conf=0.92, dispatch_decision={"eta_min": 10.4},
        hospital_conf=0.90, hospital_decision={"beds_free_after": 70},
        sel_amb=("A2", "ALS", 10.4), sel_hosp=("h4", "Hadapsar Metro Hospital", 6.1),
        verification=v6, overall_conf=0.90,
        current_status="hospital_ready", escalation_status="none",
        review=None,
        timeline=[
            (0, "incident_created", "Stroke report received."),
            (4, "intake_completed", "Report parsed: stroke, 1 patient, FAST symptoms."),
            (9, "triage_completed", "HIGH (4) · stroke-unit."),
            (14, "dispatch_completed", "Ambulance A2 (ALS) dispatched, ETA 10.4 min."),
            (16, "hospital_selected", "Stroke-unit bed reserved at Baner Lifeline Hospital."),
            (21, "verification_passed", "All 8 verification checks passed."),
            (26, "notifications_sent", "Family notified in EN/HI/MR; ER pre-alert sent."),
            (300, "failure_detected", "Baner Lifeline Hospital reported a power outage; stroke unit dark."),
            (306, "hospital_failure", "h2 marked unavailable after verification of the outage."),
            (312, "replanning", "Orchestrator replanning: failed hospital excluded from search."),
            (320, "hospital_selected", "Replacement: stroke-unit bed reserved at Hadapsar Metro Hospital."),
            (326, "verification_passed", "Replacement hospital verified against all 8 checks."),
            (330, "resource_replaced", "h2 → h4; family notified of the new destination in EN/HI/MR."),
            (338, "hospital_ready", "Stroke team at Hadapsar Metro is standing by."),
        ],
        audit=[
            (4, "IntakeAgent", "Free-text report parsed", "stroke, FAST symptoms", 0.98),
            (9, "TriageAgent", "Triage decision", "HIGH / stroke-unit", 0.95),
            (14, "DispatchAgent", "Ambulance dispatched", "A2 (ALS), ETA 10.4 min", 0.92),
            (16, "HospitalLiaisonAgent", "Hospital bed reserved", "Baner Lifeline Hospital", 0.90),
            (21, "VerificationAgent", "Verification passed", "8/8 checks", 0.99),
            (26, "CommunicationAgent", "Notifications sent", "EN/HI/MR family + ER pre-alert", 0.97),
            (300, "RecoveryManager", "Failure detected", "h2 power outage, stroke unit dark", 1.0),
            (306, "RecoveryManager", "Hospital failure", "h2 verified unavailable", 1.0),
            (312, "Orchestrator", "Replanning after failure", "excluding failed hospital h2", 0.9),
            (320, "HospitalLiaisonAgent", "Hospital bed reserved", "replacement h4 Hadapsar Metro", 0.90),
            (326, "VerificationAgent", "Verification passed", "replacement verified, 8/8", 0.99),
            (330, "RecoveryManager", "Family notified of replacement", "new destination EN/HI/MR", 0.97),
            (338, "Orchestrator", "Incident status: hospital_ready", "stroke team standing by", 1.0),
        ],
        comms=[
            (26, "family", "en",
             "MedRelay: Ambulance A2 (ALS) is on the way to Koregaon Park. ETA ~10 min. Stroke-unit bed reserved at Baner Lifeline Hospital."),
            (26, "family", "hi",
             "MedRelay: एम्बुलेंस A2 (ALS) कोरेगांव पार्क के लिए रवाना हो गई है। ETA ~10 मिनट। बानेर लाइफ़लाइन में स्ट्रोक-यूनिट बेड आरक्षित।"),
            (26, "family", "mr",
             "MedRelay: रुग्णवाहिका A2 (ALS) कोरेगाव पार्ककडे निघाली आहे. अंदाजे वेळ ~10 मिनिटे. बानेर लाइफलाइनमध्ये स्ट्रोक-युनिट बेड राखीव."),
            (330, "family", "en",
             "MedRelay update: Baner Lifeline had a power outage, so the patient is now headed to Hadapsar Metro Hospital (stroke unit). No delay to care."),
            (330, "family", "hi",
             "MedRelay अपडेट: बानेर लाइफ़लाइन में बिजली गुल होने से मरीज़ अब हडपसर मेट्रो अस्पताल (स्ट्रोक यूनिट) ले जाया जा रहा है।"),
            (330, "family", "mr",
             "MedRelay अपडेट: बानेर लाइफलाइनमध्ये वीज गेल्याने रुग्ण आता हडपसर मेट्रो रुग्णालय (स्ट्रोक युनिट) येथे नेला जात आहे."),
        ])
    seeded.append("SAMPLE-06")

    return {"status": "seeded", "incidents": seeded, "is_sample": True}
