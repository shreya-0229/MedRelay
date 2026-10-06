"""REST API (prefix /api) — the exact contract the frontend depends on."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app import models
from app import demo as demo_mode
from app.audit import get_audit_events, write_audit
from app.orchestrator import PipelineContext, RecoveryError, RecoveryManager, run_pipeline
from app.schemas import (
    FailureInjection,
    FleetStatusUpdate,
    IncidentReport,
    IncidentState,
    IncidentSummary,
    ReviewDecisionIn,
)
from app.seed import fleet_snapshot, reseed_fleet

router = APIRouter(prefix="/api")

AGENT_NAMES = [
    "IntakeAgent",
    "TriageAgent",
    "DispatchAgent",
    "HospitalLiaisonAgent",
    "CommunicationAgent",
    "VerificationAgent",
]


def _incident_or_404(db: Session, incident_id: str) -> models.Incident:
    row = (db.query(models.Incident)
             .filter(models.Incident.incident_id == incident_id)
             .first())
    if row is None:
        raise HTTPException(status_code=404, detail="not found")
    return row


def incident_list_payload(db: Session) -> list[dict]:
    """Newest-first incident summaries (also sent over WS on connect)."""
    rows = db.query(models.Incident).order_by(models.Incident.id.desc()).all()
    out: list[dict] = []
    for r in rows:
        s = IncidentState.model_validate_json(r.state_json)
        out.append(IncidentSummary(
            incident_id=s.incident_id,
            incident_type=s.incident_type,
            severity=s.severity,
            current_status=s.current_status,
            escalation_status=s.escalation_status,
            created_at=s.created_at,
            ambulance_id=s.selected_ambulance.id if s.selected_ambulance else None,
            hospital_name=s.selected_hospital.name if s.selected_hospital else None,
            is_sample=bool(getattr(r, "is_sample", False) or s.is_sample),
        ).model_dump(mode="json"))
    return out


@router.get("/health")
def health(request: Request, db: Session = Depends(models.get_db)) -> dict:
    """Liveness + provider + incident/agent inventory."""
    provider = request.app.state.llm
    return {
        "status": "ok",
        "version": "0.1.0",
        "llm_provider": "gemini" if getattr(provider, "real", False) else "deterministic",
        "incidents": db.query(models.Incident).count(),
        "agents": AGENT_NAMES,
    }


@router.post("/incidents", status_code=201, response_model=IncidentState)
async def create_incident(report: IncidentReport, request: Request) -> IncidentState:
    """Accept a raw report and run the FULL agent pipeline (201 + IncidentState)."""
    ctx = PipelineContext(
        session_factory=request.app.state.session_factory,
        llm=request.app.state.llm,
        broadcast=request.app.state.broadcast,
    )
    return await run_pipeline(report, ctx)


@router.get("/incidents")
def list_incidents(db: Session = Depends(models.get_db)) -> dict:
    return {"incidents": incident_list_payload(db)}


@router.get("/incidents/{incident_id}", response_model=IncidentState)
def get_incident(incident_id: str, db: Session = Depends(models.get_db)) -> IncidentState:
    row = _incident_or_404(db, incident_id)
    state = IncidentState.model_validate_json(row.state_json)
    state.is_sample = bool(getattr(row, "is_sample", False) or state.is_sample)
    return state


@router.get("/incidents/{incident_id}/communications")
def get_incident_communications(incident_id: str,
                                db: Session = Depends(models.get_db)) -> dict:
    """Durable multilingual message log for one incident.

    Reads the real ``communications`` table (language-tagged EN/HI/MR) —
    the trilingual evidence behind the dashboard comms viewer.
    """
    _incident_or_404(db, incident_id)
    rows = (db.query(models.CommunicationMessage)
              .filter(models.CommunicationMessage.incident_id == incident_id)
              .order_by(models.CommunicationMessage.id.asc())
              .all())
    return {
        "incident_id": incident_id,
        "messages": [
            {"id": m.id, "channel": m.channel, "language": m.language,
             "text": m.text, "ts": m.ts.isoformat()}
            for m in rows
        ],
    }


@router.get("/incidents/{incident_id}/audit")
def get_incident_audit(incident_id: str,
                       db: Session = Depends(models.get_db)) -> dict:
    _incident_or_404(db, incident_id)
    events = get_audit_events(db, incident_id)
    return {
        "incident_id": incident_id,
        "events": [
            {"id": e.id, "ts": e.ts.isoformat(), "agent": e.agent,
             "action": e.action, "rationale": e.rationale,
             "confidence": e.confidence}
            for e in events
        ],
    }


def _recovery_manager(request: Request, db: Session) -> RecoveryManager:
    return RecoveryManager(
        db=db,
        llm=request.app.state.llm,
        broadcast=request.app.state.broadcast,
    )


@router.post("/incidents/{incident_id}/failures",
             response_model=IncidentState)
async def inject_failure(incident_id: str, spec: FailureInjection,
                         request: Request,
                         db: Session = Depends(models.get_db)) -> IncidentState:
    """Failure drill / live recovery: an ambulance breaks down, a hospital
    goes unavailable, or a conflicting agent output is injected.

    Runs the real recovery flow — failure validation, replanning,
    re-dispatch, re-verification, family notification — all audit-logged.
    """
    _incident_or_404(db, incident_id)
    mgr = _recovery_manager(request, db)
    try:
        return await mgr.inject(incident_id, spec)
    except RecoveryError as e:
        raise HTTPException(status_code=409, detail=str(e))


@router.get("/incidents/{incident_id}/review")
def get_review(incident_id: str,
               db: Session = Depends(models.get_db)) -> dict:
    """Operator review dossier for a paused incident."""
    row = _incident_or_404(db, incident_id)
    state = IncidentState.model_validate_json(row.state_json)
    return {
        "incident_id": incident_id,
        "current_status": state.current_status,
        "review": state.review.model_dump(mode="json"),
        "reason": state.review.reason,
        "affected_decision": state.review.affected_decision,
        "confidence": state.review.confidence,
        "agent_outputs": {
            name: out.model_dump(mode="json")
            for name, out in state.agent_outputs.items()
        },
    }


@router.post("/incidents/{incident_id}/review/decision",
             response_model=IncidentState)
async def decide_review(incident_id: str, body: ReviewDecisionIn,
                        request: Request,
                        db: Session = Depends(models.get_db)) -> IncidentState:
    """Operator decision on a paused incident: approve | reject |
    request_info | replan. The decision and note are audit-logged."""
    _incident_or_404(db, incident_id)
    mgr = _recovery_manager(request, db)
    try:
        return await mgr.decide_review(incident_id, body.decision, body.note)
    except RecoveryError as e:
        raise HTTPException(status_code=409, detail=str(e))


@router.get("/fleet")
def get_fleet(db: Session = Depends(models.get_db)) -> dict:
    return fleet_snapshot(db)


@router.post("/incidents/{incident_id}/resolve", response_model=IncidentState)
def resolve_incident(incident_id: str,
                     db: Session = Depends(models.get_db)) -> IncidentState:
    """Release the ambulance back to the fleet and mark the incident completed."""
    row = _incident_or_404(db, incident_id)
    state = IncidentState.model_validate_json(row.state_json)
    if state.selected_ambulance:
        amb = db.get(models.Ambulance, state.selected_ambulance.id)
        if amb is not None:
            amb.status = "available"
            amb.assigned_incident = None
    state.ambulance_status = "available"
    state.current_status = "completed"
    state.escalation_status = "none"
    from app.agents import utcnow
    from app.schemas import TimelineEntry
    state.timeline.append(TimelineEntry(
        ts=utcnow(), event="resolved",
        detail="Incident resolved; ambulance released to fleet"))
    row.state_json = state.model_dump_json()
    row.current_status = "completed"
    row.escalation_status = "none"
    db.commit()
    write_audit(db, incident_id, agent="API", action="Incident resolved",
                rationale="Ambulance released to fleet", confidence=1.0)
    return state


@router.get("/stats")
def get_stats(db: Session = Depends(models.get_db)) -> dict:
    rows = db.query(models.Incident).all()
    confidences: list[float] = []
    for r in rows:
        try:
            confidences.append(
                IncidentState.model_validate_json(r.state_json).confidence)
        except Exception:
            continue
    return {
        "total": len(rows),
        "active": sum(1 for r in rows
                      if r.current_status not in ("completed", "escalated",
                                                  "failed", "cancelled",
                                                  "hospital_ready")),
        "completed": sum(1 for r in rows if r.current_status == "completed"),
        "escalated": sum(1 for r in rows if r.escalation_status == "escalated"),
        "review": sum(1 for r in rows
                      if r.current_status == "human_review_required"),
        "avg_confidence": (round(sum(confidences) / len(confidences), 2)
                           if confidences else None),
    }


@router.get("/demo/scenarios")
def list_demo_scenarios() -> dict:
    """Demo-mode scenario definitions for the judging page."""
    return {"scenarios": demo_mode.list_scenarios()}


@router.post("/demo/scenarios/{scenario_id}/run", status_code=202)
async def run_demo_scenario(scenario_id: str,
                            request: Request) -> dict:
    """Start a demo scenario as a tracked background task.

    Returns 202 immediately; the real pipeline/recovery runs async and the
    dashboard follows it through the normal WebSocket feed. Poll
    GET /api/demo/scenarios/runs/{run_id} for status and incident_id.
    """
    try:
        run = await demo_mode.run_scenario(
            scenario_id,
            session_factory=request.app.state.session_factory,
            llm=request.app.state.llm,
            broadcast=request.app.state.broadcast)
    except demo_mode.UnknownScenario as e:
        raise HTTPException(status_code=404, detail=str(e))
    return {"scenario_run_id": run.run_id, "incident_id": run.incident_id}


@router.get("/demo/scenarios/runs/{run_id}")
def get_demo_run(run_id: str) -> dict:
    """Status of a demo scenario run (202 → running → done/failed)."""
    run = demo_mode.get_run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="not found")
    return run.to_dict()


@router.post("/demo/reset")
async def demo_reset(request: Request,
                     db: Session = Depends(models.get_db)) -> dict:
    """Full demo reset: cancel tracked scenario runs, wipe all incident
    data (incidents, agent runs, audit, communications), and reseed the
    fleet to pristine state — exactly like a fresh boot."""
    return await demo_mode.reset_demo(db,
                                      broadcast=request.app.state.broadcast)


@router.post("/demo/seed-sample", status_code=201)
async def demo_seed_sample(request: Request,
                           db: Session = Depends(models.get_db)) -> dict:
    """Load the "sample day" dataset: 6 realistic pre-run incidents written
    as real DB rows (agent outputs, audit events, EN/HI/MR communications),
    all badged ``is_sample=True`` so the dashboard shows SAMPLE labels.
    One of them pauses at ``human_review_required`` so judges can decide
    live. Reset Demo wipes them along with everything else.

    Idempotent-ish: seeding twice replaces the previous sample set.
    """
    result = demo_mode.seed_sample_data(db)
    await request.app.state.broadcast({
        "type": "incident_list",
        "incidents": incident_list_payload(db)})
    return result


@router.get("/demo/analytics")
def demo_analytics(db: Session = Depends(models.get_db)) -> dict:
    """Dashboard analytics — every number computed live from the DB."""
    rows = db.query(models.Incident).all()
    severities: dict[str, int] = {}
    ver_passed = ver_total = 0
    confidences: list[float] = []
    for r in rows:
        try:
            s = IncidentState.model_validate_json(r.state_json)
        except Exception:
            continue
        label = {5: "CRITICAL", 4: "HIGH", 3: "MODERATE", 2: "LOW",
                 1: "MINIMAL"}.get(s.severity or 0, "UNKNOWN")
        severities[label] = severities.get(label, 0) + 1
        if s.verification_results is not None:
            ver_total += 1
            if s.verification_results.passed:
                ver_passed += 1
        confidences.append(s.confidence)
    return {
        "severity_distribution": severities,
        "verification": {
            "passed": ver_passed,
            "total": ver_total,
            "pass_rate": round(ver_passed / ver_total, 2) if ver_total else None,
        },
        "comms_sent": db.query(models.CommunicationMessage).count(),
        "audit_events": db.query(models.AuditEvent).count(),
        "review_queue": sum(
            1 for r in rows if r.current_status == "human_review_required"),
        "avg_confidence": (round(sum(confidences) / len(confidences), 2)
                           if confidences else None),
        "incidents": len(rows),
    }


@router.post("/demo/fleet/ambulance/{amb_id}")
async def set_ambulance_status(amb_id: str, update: FleetStatusUpdate,
                               request: Request,
                               db: Session = Depends(models.get_db)) -> dict:
    """Test helper for failure drills: force an ambulance's status."""
    amb = db.get(models.Ambulance, amb_id)
    if amb is None:
        raise HTTPException(status_code=404, detail="not found")
    amb.status = update.status
    if update.status == "available":
        amb.assigned_incident = None
    db.commit()
    await request.app.state.broadcast({"type": "fleet_update",
                                       "fleet": fleet_snapshot(db)})
    return {"id": amb.id, "lat": amb.lat, "lon": amb.lon,
            "capability": amb.capability, "status": amb.status,
            "assigned_incident": amb.assigned_incident}
