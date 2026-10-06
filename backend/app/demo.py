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
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
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
from app.schemas import FailureInjection, IncidentReport, IncidentState
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
