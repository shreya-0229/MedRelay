"""Demo-mode tests for the MedRelay hackathon judging scenarios.

Every scenario drives the REAL pipeline / RecoveryManager against a
throwaway SQLite DB in /tmp (never touches medrelay.db). Plain-Python
asserts with a PASS/FAIL summary — run with:
    cd backend && .venv/bin/python test_demo.py
"""
from __future__ import annotations

import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app import demo, models
from app.audit import get_audit_events
from app.llm import DeterministicProvider
from app.orchestrator.state_manager import StateManager

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


def run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


class Capture:
    """Collects broadcast WS messages."""

    def __init__(self) -> None:
        self.messages: list[dict] = []

    async def __call__(self, message: dict) -> None:
        self.messages.append(message)


def fresh_db():
    path = "/tmp/test_medrelay_demo.db"
    if os.path.exists(path):
        os.remove(path)
    engine = create_engine(f"sqlite:///{path}")
    models.Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    db = Session()
    from app.seed import seed_fleet
    seed_fleet(db)
    return db, Session


def exec_scenario(sid, Session, cap):
    return run(demo.execute_scenario(
        sid, session_factory=Session, llm=DeterministicProvider(),
        broadcast=cap))


def audit_actions(db, iid):
    return [e.action for e in get_audit_events(db, iid)]


def test_scenarios_listed():
    print("demo: 9 scenarios listed, flagship has 20 steps")
    specs = demo.list_scenarios()
    check("9 scenarios", len(specs) == 9, str(len(specs)))
    ids = [s["id"] for s in specs]
    for want in ("critical_road_accident", "heart_emergency",
                 "multiple_casualties", "low_confidence", "ambulance_failure",
                 "hospital_unavailable", "agent_conflict",
                 "external_service_failure", "main_judging"):
        check(f"scenario {want}", want in ids)
    main = next(s for s in specs if s["id"] == "main_judging")
    check("flagship flagged", main.get("flagship") is True)
    check("flagship has 20 steps", len(main["steps"]) == 20,
          str(len(main["steps"])))
    check("all steps have rules",
          all("rule" in st and "label" in st
              for s in specs for st in s["steps"]))
    try:
        run(demo.execute_scenario(
            "nope", session_factory=None, llm=None, broadcast=None))
        check("unknown scenario raises", False)
    except demo.UnknownScenario:
        check("unknown scenario raises", True)


def test_pipeline_scenarios():
    print("demo: pipeline scenarios reach completed")
    for sid in ("critical_road_accident", "heart_emergency",
                "multiple_casualties"):
        db, Session = fresh_db()
        cap = Capture()
        r = exec_scenario(sid, Session, cap)
        check(f"{sid} done", r.status == "done", r.status)
        check(f"{sid} completed", r.terminal_state == "completed",
              str(r.terminal_state))
        check(f"{sid} incident tracked", bool(r.incident_id))
        acts = audit_actions(db, r.incident_id)
        check(f"{sid} audit trail", len(acts) >= 6, str(len(acts)))
    # the flagship's accident must triage CRITICAL (severity 5)
    db2, Session2 = fresh_db()
    r = exec_scenario("critical_road_accident", Session2, Capture())
    dbx = Session2()
    from app.schemas import IncidentState
    row = dbx.query(models.Incident).filter(
        models.Incident.incident_id == r.incident_id).one()
    st = IncidentState.model_validate_json(row.state_json)
    check("road accident triaged CRITICAL (sev 5)", st.severity == 5,
          str(st.severity))
    check("2 patients parsed", st.patient_count == 2, str(st.patient_count))
    dbx.close()


def test_low_confidence():
    print("demo: low_confidence pauses for human review")
    db, Session = fresh_db()
    cap = Capture()
    r = exec_scenario("low_confidence", Session, cap)
    check("done", r.status == "done", r.status)
    check("paused at human_review_required",
          r.terminal_state == "human_review_required",
          str(r.terminal_state))
    dbx = Session()
    from app.schemas import IncidentState
    row = dbx.query(models.Incident).filter(
        models.Incident.incident_id == r.incident_id).one()
    st = IncidentState.model_validate_json(row.state_json)
    check("no ambulance reserved", st.selected_ambulance is None)
    check("no hospital reserved", st.selected_hospital is None)
    dbx.close()


def test_ambulance_failure():
    print("demo: ambulance_failure recovers")
    db, Session = fresh_db()
    cap = Capture()
    r = exec_scenario("ambulance_failure", Session, cap)
    check("done", r.status == "done", r.status)
    check("recovered", r.terminal_state == "recovered",
          str(r.terminal_state))
    acts = audit_actions(db, r.incident_id)
    check("recovery audit trail",
          any("Ambulance failure" in a for a in acts)
          and any("Replacement ambulance selected" in a for a in acts),
          str(acts[-4:]))


def test_hospital_unavailable():
    print("demo: hospital_unavailable recovers")
    db, Session = fresh_db()
    cap = Capture()
    r = exec_scenario("hospital_unavailable", Session, cap)
    check("done", r.status == "done", r.status)
    check("recovered", r.terminal_state == "recovered",
          str(r.terminal_state))
    acts = audit_actions(db, r.incident_id)
    check("recovery audit trail",
          any("Hospital failure" in a for a in acts)
          and any("Replacement hospital selected" in a for a in acts))


def test_agent_conflict():
    print("demo: agent_conflict routes to human review")
    db, Session = fresh_db()
    cap = Capture()
    r = exec_scenario("agent_conflict", Session, cap)
    check("done", r.status == "done", r.status)
    check("routed to human review",
          r.terminal_state == "human_review_required",
          str(r.terminal_state))
    acts = audit_actions(db, r.incident_id)
    check("conflict recorded",
          any("conflict" in a.lower() for a in acts))


def test_external_service_failure():
    print("demo: external_service_failure completes on fallback")
    db, Session = fresh_db()
    cap = Capture()
    r = exec_scenario("external_service_failure", Session, cap)
    check("done", r.status == "done", r.status)
    check("completed despite outage", r.terminal_state == "completed",
          str(r.terminal_state))
    acts = audit_actions(db, r.incident_id)
    check("fallback audit note",
          any("fallback" in a.lower() for a in acts), str(acts[-3:]))


def test_main_judging_twice():
    print("demo: main_judging twice back-to-back → both hospital_ready")
    db, Session = fresh_db()
    cap = Capture()
    r1 = exec_scenario("main_judging", Session, cap)
    check("run 1 done", r1.status == "done", r1.status)
    check("run 1 hospital_ready", r1.terminal_state == "hospital_ready",
          str(r1.terminal_state))
    r2 = exec_scenario("main_judging", Session, cap)
    check("run 2 done", r2.status == "done", f"{r2.status} {r2.error}")
    check("run 2 hospital_ready", r2.terminal_state == "hospital_ready",
          str(r2.terminal_state))
    check("distinct incidents", r1.incident_id != r2.incident_id)
    # fleet not corrupted: every held ambulance belongs to a real incident
    dbx = Session()
    known = {r.incident_id for r in
             dbx.query(models.Incident.incident_id).all()}
    leaked = [a.id for a in dbx.query(models.Ambulance).all()
              if a.assigned_incident and a.assigned_incident not in known]
    check("no leaked ambulance holds", not leaked, str(leaked))
    dbx.close()
    # WS saw the hospital-ready event for both runs
    hrs = [m for m in cap.messages
           if m.get("type") == "agent_event"
           and "hospital ready" in str(m.get("action", "")).lower()]
    check("hospital-ready WS events emitted", len(hrs) >= 2, str(len(hrs)))


def test_reset():
    print("demo: reset_demo wipes + reseeds pristine")
    db, Session = fresh_db()
    cap = Capture()
    r = exec_scenario("main_judging", Session, cap)
    check("precondition: incident exists",
          db.query(models.Incident).count() >= 1)
    out = run(demo.reset_demo(db, broadcast=cap))
    check("reset ok", out["status"] == "reset")
    check("0 incidents", db.query(models.Incident).count() == 0)
    check("0 agent_runs", db.query(models.AgentRun).count() == 0)
    check("0 audit_events", db.query(models.AuditEvent).count() == 0)
    check("0 communications",
          db.query(models.CommunicationMessage).count() == 0)
    # pristine = seeded values (A5 is out_of_service BY DESIGN in seed data)
    from app.seed import AMBULANCES
    seeded_status = {a["id"]: a["status"] for a in AMBULANCES}
    live_status = {a.id: a.status for a in db.query(models.Ambulance).all()}
    check("fleet at seeded statuses", live_status == seeded_status,
          str({k: live_status[k] for k in live_status
               if live_status[k] != seeded_status.get(k)}))
    check("no assigned incidents linger",
          all(a.assigned_incident is None
              for a in db.query(models.Ambulance).all()))
    check("14 ambulances", db.query(models.Ambulance).count() == 14)
    check("8 hospitals", db.query(models.Hospital).count() == 8)
    from app.seed import HOSPITALS
    seeded = {h["id"]: h["free_beds"] for h in HOSPITALS}
    live = {h.id: h.free_beds for h in db.query(models.Hospital).all()}
    check("beds at seeded values", live == seeded, str(live))
    # stale runs are ignored after reset
    check("run registry cleared", demo.get_run(r.run_id) is None)


def test_routes_registered():
    print("demo: API routes registered")
    from app.api import router as api_router
    paths = {getattr(r, "path", "") for r in api_router.routes}
    for want in ("/api/demo/scenarios",
                 "/api/demo/scenarios/{scenario_id}/run",
                 "/api/demo/scenarios/runs/{run_id}",
                 "/api/demo/reset"):
        check(f"route {want}", want in paths)


if __name__ == "__main__":
    test_scenarios_listed()
    test_pipeline_scenarios()
    test_low_confidence()
    test_ambulance_failure()
    test_hospital_unavailable()
    test_agent_conflict()
    test_external_service_failure()
    test_main_judging_twice()
    test_reset()
    test_routes_registered()
    print(f"\n{PASS} passed, {FAIL} failed")
    if FAILURES:
        print("FAILURES:", FAILURES)
        sys.exit(1)
