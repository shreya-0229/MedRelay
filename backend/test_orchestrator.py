"""Orchestration-layer tests for the MedRelay pipeline.

Covers: canonical event emission, parallel fan-out of Dispatch/Hospital,
StateManager validation, and the verification-failure -> replan ->
escalation event sequence.

Runs against a throwaway SQLite DB in /tmp (never touches medrelay.db).
Plain-Python asserts with a PASS/FAIL summary — run with:
    cd backend && .venv/bin/python test_orchestrator.py
"""
from __future__ import annotations

import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from pydantic import ValidationError
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app import models
from app.llm import DeterministicProvider
from app.orchestrator import (
    EventBus,
    EventType,
    Orchestrator,
    PipelineContext,
    StateManager,
    Workflow,
    run_pipeline,
)
from app.schemas import IncidentReport

PASS, FAIL = 0, 0
FAILURES: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  PASS {name}")
    else:
        FAIL += 1
        FAILURES.append(name)
        print(f"  FAIL {name} {detail}")


def fresh_db():
    path = "/tmp/test_medrelay_orchestrator.db"
    if os.path.exists(path):
        os.remove(path)
    engine = create_engine(f"sqlite:///{path}")
    models.Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    db = Session()
    from app.seed import seed_fleet
    seed_fleet(db)
    return db, Session


def run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


def cardiac_report() -> IncidentReport:
    return IncidentReport(
        incident_type="cardiac_arrest",
        lat=18.5314, lon=73.8446, address="Deccan Gymkhana",
        breathing_status="absent", bleeding_status="none",
        family_contact="+91-9000000001")


class Capture:
    """Collects broadcast WS messages."""

    def __init__(self) -> None:
        self.messages: list[dict] = []

    async def __call__(self, message: dict) -> None:
        self.messages.append(message)


def run_workflow(db, report):
    """Run the Workflow directly and return (state, bus, ws_messages)."""
    cap = Capture()
    bus = EventBus(db=db, broadcast=cap)
    sm = StateManager(db)
    wf = Workflow(db=db, llm=DeterministicProvider(), report=report,
                  state=sm, bus=bus)
    state = run(wf.run())
    return state, bus, cap.messages


def main() -> None:
    # ---------- happy path: canonical events ----------
    print("happy-path events")
    db, _ = fresh_db()
    state, bus, ws = run_workflow(db, cardiac_report())
    types = bus.types()
    expected = [
        EventType.INTAKE_COMPLETED,
        EventType.TRIAGE_STARTED, EventType.TRIAGE_COMPLETED,
        EventType.DISPATCH_STARTED, EventType.DISPATCH_COMPLETED,
        EventType.HOSPITAL_SEARCH_STARTED, EventType.HOSPITAL_SELECTED,
        EventType.VERIFICATION_STARTED, EventType.VERIFICATION_PASSED,
        EventType.COMMUNICATION_SENT,
    ]
    for t in expected:
        check(f"emits {t}", t in types)
    for t in (EventType.VERIFICATION_FAILED, EventType.REPLAN_STARTED,
              EventType.HUMAN_ESCALATION):
        check(f"no {t} on happy path", t not in types)
    check("pipeline completed", state.current_status == "completed",
          state.current_status)
    check("all events have message_id",
          all(e.message_id for e in bus.events))
    check("blackboard has stage messages", len(bus.blackboard) >= 6,
          str(len(bus.blackboard)))

    # ---------- parallelism: dispatch + hospital overlap ----------
    print("parallel fan-out")
    idx = {t: types.index(t) for t in types}
    both_started_first = (
        idx[EventType.DISPATCH_STARTED] < idx[EventType.DISPATCH_COMPLETED]
        and idx[EventType.DISPATCH_STARTED] < idx[EventType.HOSPITAL_SELECTED]
        and idx[EventType.HOSPITAL_SEARCH_STARTED]
        < idx[EventType.DISPATCH_COMPLETED]
        and idx[EventType.HOSPITAL_SEARCH_STARTED]
        < idx[EventType.HOSPITAL_SELECTED])
    check("both STARTED precede both COMPLETED (fan-out, not sequential)",
          both_started_first, str(types))
    check("triage completes before fan-out starts",
          idx[EventType.TRIAGE_COMPLETED] < idx[EventType.DISPATCH_STARTED])
    started_ts = max(e.timestamp for e in bus.events
                     if e.event_type in (EventType.DISPATCH_STARTED,
                                         EventType.HOSPITAL_SEARCH_STARTED))
    completed_ts = min(e.timestamp for e in bus.events
                       if e.event_type in (EventType.DISPATCH_COMPLETED,
                                           EventType.HOSPITAL_SELECTED))
    check("started timestamps <= completed timestamps",
          started_ts <= completed_ts)

    # ---------- WS bridge: started events reach the frontend feed ----------
    print("ws bridge")
    agent_events = [m for m in ws if m.get("type") == "agent_event"]
    actions = [m["action"] for m in agent_events]
    check("WS agent_event for triage start",
          any("Triage started" in a for a in actions), str(actions[:4]))
    check("WS keeps AgentEventMsg shape",
          all(set(("type", "incident_id", "agent", "action", "rationale",
                   "confidence", "ts")) <= set(m) for m in agent_events))
    check("WS incident_update broadcast",
          any(m.get("type") == "incident_update" for m in ws))
    check("WS fleet_update broadcast",
          any(m.get("type") == "fleet_update" for m in ws))

    # ---------- audit persistence ----------
    print("audit persistence")
    rows = db.query(models.AuditEvent).filter(
        models.AuditEvent.incident_id == state.incident_id).all()
    audit_actions = [r.action for r in rows]
    check("audit has stage-completion rows",
          any("completed" in a or "parsed" in a or "reserved" in a
              for a in audit_actions), str(audit_actions[:6]))
    check("audit has started-event rows",
          any("started" in a.lower() for a in audit_actions),
          str(audit_actions[:8]))

    # ---------- state manager validation ----------
    print("state manager")
    db2, _ = fresh_db()
    sm = StateManager(db2)
    sm.create(cardiac_report())
    try:
        sm.update(confidence=2.0)
        check("rejects confidence > 1.0", False, "no error raised")
    except ValidationError:
        check("rejects confidence > 1.0", True)
    sm.update(current_status="triaged")
    check("valid update applies + persists",
          sm.state.current_status == "triaged"
          and db2.query(models.Incident).first().current_status == "triaged")
    try:
        sm2 = StateManager(db2)
        sm2.update(current_status="x")
        check("update before create raises", False, "no error raised")
    except RuntimeError:
        check("update before create raises", True)

    # ---------- failure path: VERIFICATION_FAILED -> REPLAN -> ESCALATION --
    print("failure path")
    db3, _ = fresh_db()
    db3.query(models.Ambulance).filter(
        models.Ambulance.capability == "ALS").update(
        {"status": "out_of_service"}, synchronize_session=False)
    db3.commit()
    state3, bus3, _ = run_workflow(db3, cardiac_report())
    t3 = bus3.types()
    check("emits VERIFICATION_FAILED", EventType.VERIFICATION_FAILED in t3)
    check("emits REPLAN_STARTED", EventType.REPLAN_STARTED in t3)
    check("emits HUMAN_ESCALATION", EventType.HUMAN_ESCALATION in t3)
    i_vf = t3.index(EventType.VERIFICATION_FAILED)
    i_rs = t3.index(EventType.REPLAN_STARTED)
    i_he = t3.index(EventType.HUMAN_ESCALATION)
    check("event order FAILED -> REPLAN -> ESCALATION", i_vf < i_rs < i_he,
          str(t3))
    check("escalation_status=escalated",
          state3.escalation_status == "escalated", state3.escalation_status)

    # ---------- public run_pipeline signature unchanged ----------
    print("public api")
    db4, Session4 = fresh_db()
    cap4 = Capture()
    ctx = PipelineContext(session_factory=Session4,
                          llm=DeterministicProvider(), broadcast=cap4)
    state4 = run(run_pipeline(cardiac_report(), ctx))
    check("run_pipeline returns completed IncidentState",
          state4.current_status == "completed"
          and state4.incident_id.startswith("INC-"),
          state4.current_status)

    print(f"\n{PASS} passed, {FAIL} failed")
    if FAILURES:
        print("FAILURES:", FAILURES)
        sys.exit(1)


if __name__ == "__main__":
    main()
