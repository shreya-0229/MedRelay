"""Failure-recovery tests for the MedRelay autonomous recovery system.

Covers the four specified scenarios against a throwaway SQLite DB:
  S1  ambulance breakdown mid-plan → replacement, family notified, audit
  S2  hospital becomes unavailable → replacement, family notified, audit
  S3  low-confidence triage → human_review_required pause + the four
      operator decisions (approve / reject / request_info / replan)
  S4  injected agent conflict → detected, reservations released, review

Runs against a throwaway SQLite DB in /tmp (never touches medrelay.db).
Plain-Python asserts with a PASS/FAIL summary — run with:
    cd backend && .venv/bin/python test_recovery.py
"""
from __future__ import annotations

import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app import models
from app.audit import get_audit_events
from app.llm import DeterministicProvider
from app.orchestrator import (
    PipelineContext,
    RecoveryManager,
    run_pipeline,
)
from app.schemas import FailureInjection, IncidentReport

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
    path = "/tmp/test_medrelay_recovery.db"
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


class Capture:
    """Collects broadcast WS messages."""

    def __init__(self) -> None:
        self.messages: list[dict] = []

    async def __call__(self, message: dict) -> None:
        self.messages.append(message)


def make_ctx(Session, cap):
    return PipelineContext(session_factory=Session,
                           llm=DeterministicProvider(), broadcast=cap)


def cardiac_report() -> IncidentReport:
    return IncidentReport(
        incident_type="cardiac_arrest",
        lat=18.5314, lon=73.8446, address="Deccan Gymkhana",
        breathing_status="absent", bleeding_status="none",
        family_contact="+91-9000000001")


def unknown_report() -> IncidentReport:
    return IncidentReport(
        incident_type="mystery_syndrome_xyz",
        lat=18.5314, lon=73.8446, address="Deccan Gymkhana",
        breathing_status="normal", bleeding_status="none",
        family_contact="+91-9000000002")


def audit_actions(db, incident_id) -> list[str]:
    return [e.action for e in get_audit_events(db, incident_id)]


def comm_langs(db, incident_id) -> list[str]:
    rows = (db.query(models.CommunicationMessage)
              .filter(models.CommunicationMessage.incident_id == incident_id,
                      models.CommunicationMessage.channel == "recovery_family")
              .all())
    return sorted({r.language for r in rows})


def recovery_notices(db, incident_id):
    return (db.query(models.CommunicationMessage)
              .filter(models.CommunicationMessage.incident_id == incident_id,
                      models.CommunicationMessage.channel == "recovery_family")
              .all())


# ---------------------------------------------------------------------------
# S1 — ambulance breakdown mid-plan
# ---------------------------------------------------------------------------

def test_s1_ambulance_breakdown():
    print("S1: ambulance breakdown → replacement")
    db, Session = fresh_db()
    cap = Capture()
    state = run(run_pipeline(cardiac_report(), make_ctx(Session, cap)))
    assert state.current_status == "completed", state.current_status
    old_id = state.selected_ambulance.id
    old_hosp = state.selected_hospital.id

    mgr = RecoveryManager(db=db, llm=DeterministicProvider(),
                          broadcast=cap)
    out = run(mgr.inject(
        state.incident_id,
        FailureInjection(resource_type="ambulance", resource_id=old_id,
                         reason="breakdown")))

    new_id = out.selected_ambulance.id
    check("replacement selected (different unit)", new_id != old_id,
          f"old={old_id} new={new_id}")
    check("old unit marked out_of_service",
          db.get(models.Ambulance, old_id).status == "out_of_service")
    check("failed unit excluded from fleet",
          db.get(models.Ambulance, old_id).status != "available")
    new_amb = db.get(models.Ambulance, new_id)
    check("replacement en_route + assigned",
          new_amb.status == "en_route"
          and new_amb.assigned_incident == state.incident_id)
    check("status recovered", out.current_status == "recovered",
          out.current_status)
    check("hospital untouched", out.selected_hospital.id == old_hosp)

    langs = comm_langs(db, state.incident_id)
    check("family notified in EN/HI/MR", langs == ["en", "hi", "mr"], langs)
    acts = audit_actions(db, state.incident_id)
    for needle in ("Failure detected", "Ambulance failure",
                   "Replanning after failure", "Ambulance dispatched",
                   "Replacement ambulance selected",
                   "Family notified of replacement"):
        check(f"audit has '{needle}'",
              any(needle in a for a in acts), str(acts[-8:]))
    tl = [t.event for t in out.timeline]
    for ev in ("failure_detected", "replanning", "resource_replaced",
               "recovery_complete"):
        check(f"timeline has '{ev}'", ev in tl, str(tl))
    # the replacement notice names old → new
    msgs = recovery_notices(db, state.incident_id)
    check("notice names old and new unit",
          all(old_id in m.text and new_id in m.text for m in msgs))
    db.close()


def test_s1_no_replacement_escalates():
    print("S1b: breakdown with no capable unit → escalation")
    db, Session = fresh_db()
    cap = Capture()
    state = run(run_pipeline(cardiac_report(), make_ctx(Session, cap)))
    old_id = state.selected_ambulance.id
    # take every other ALS unit out of service
    for a in db.query(models.Ambulance).all():
        if a.capability == "ALS" and a.id != old_id:
            a.status = "out_of_service"
    db.commit()
    mgr = RecoveryManager(db=db, llm=DeterministicProvider(),
                          broadcast=cap)
    out = run(mgr.inject(
        state.incident_id,
        FailureInjection(resource_type="ambulance", resource_id=old_id,
                         reason="breakdown")))
    check("escalated when no replacement",
          out.escalation_status == "escalated"
          and out.current_status == "escalated",
          f"{out.escalation_status}/{out.current_status}")
    check("escalation notice drafted",
          any("escalation_notice" in c.channel
              for c in out.communications))
    db.close()


# ---------------------------------------------------------------------------
# S2 — hospital becomes unavailable
# ---------------------------------------------------------------------------

def test_s2_hospital_unavailable():
    print("S2: hospital unavailable → replacement")
    db, Session = fresh_db()
    cap = Capture()
    state = run(run_pipeline(cardiac_report(), make_ctx(Session, cap)))
    old_hosp = state.selected_hospital.id
    old_hosp_name = state.selected_hospital.name
    old_beds = db.get(models.Hospital, old_hosp).free_beds

    mgr = RecoveryManager(db=db, llm=DeterministicProvider(),
                          broadcast=cap)
    out = run(mgr.inject(
        state.incident_id,
        FailureInjection(resource_type="hospital", resource_id=old_hosp,
                         reason="power outage")))
    new_hosp = out.selected_hospital.id
    check("replacement hospital selected", new_hosp != old_hosp,
          f"old={old_hosp} new={new_hosp}")
    check("failed hospital has zero beds",
          db.get(models.Hospital, old_hosp).free_beds == 0)
    check("replacement bed reserved",
          out.agent_outputs["HospitalLiaisonAgent"].decision.get(
              "reservation_status") == "reserved")
    check("hospital_status reserved", out.hospital_status == "reserved")
    check("ambulance untouched",
          out.selected_ambulance.id == state.selected_ambulance.id)
    langs = comm_langs(db, state.incident_id)
    check("family notified in EN/HI/MR", langs == ["en", "hi", "mr"], langs)
    acts = audit_actions(db, state.incident_id)
    for needle in ("Failure detected", "Hospital failure",
                   "Hospital bed reserved", "Replacement hospital selected"):
        check(f"audit has '{needle}'",
              any(needle in a for a in acts), str(acts[-8:]))
    check("old hospital name in notice",
          any(old_hosp_name in m.text
              for m in recovery_notices(db, state.incident_id)))
    check("failed hospital excluded (not reselected)",
          out.selected_hospital.id != old_hosp)
    _ = old_beds
    db.close()


# ---------------------------------------------------------------------------
# S3 — low-confidence human review
# ---------------------------------------------------------------------------

def test_s3_pause_and_approve():
    print("S3: low-confidence triage pauses; approve resumes")
    db, Session = fresh_db()
    cap = Capture()
    state = run(run_pipeline(unknown_report(), make_ctx(Session, cap)))
    check("paused for human review",
          state.current_status == "human_review_required",
          state.current_status)
    check("review pending", state.review.status == "pending")
    check("review reason mentions triage",
          "triage" in state.review.reason.lower()
          or "confidence" in state.review.reason.lower(),
          state.review.reason)
    check("no ambulance reserved",
          state.selected_ambulance is None
          and not any(a.assigned_incident == state.incident_id
                      for a in db.query(models.Ambulance).all()))
    check("no hospital reserved", state.selected_hospital is None)
    check("REVIEW_REQUIRED emitted",
          any(m.get("action") == "Human review required"
              for m in cap.messages if m.get("type") == "agent_event"))

    mgr = RecoveryManager(db=db, llm=DeterministicProvider(),
                          broadcast=cap)
    out = run(mgr.decide_review(state.incident_id, "approve",
                                note="looks like a fall, proceed"))
    check("approve resumes to completed", out.current_status == "completed",
          out.current_status)
    check("ambulance dispatched after approve",
          out.selected_ambulance is not None)
    check("triage marked human-approved",
          out.agent_outputs["TriageAgent"].decision.get("human_approved")
          is True)
    db.close()


def test_s3_reject_and_request_info():
    print("S3: reject cancels; request_info flags callback")
    db, Session = fresh_db()
    cap = Capture()
    mgr = RecoveryManager(db=db, llm=DeterministicProvider(),
                          broadcast=cap)

    s1 = run(run_pipeline(unknown_report(), make_ctx(Session, cap)))
    out = run(mgr.decide_review(s1.incident_id, "reject",
                                note="prank call"))
    check("reject cancels incident", out.current_status == "cancelled",
          out.current_status)
    check("review marked rejected", out.review.status == "rejected")
    check("nothing reserved after reject", out.selected_ambulance is None)

    s2 = run(run_pipeline(unknown_report(), make_ctx(Session, cap)))
    out2 = run(mgr.decide_review(s2.incident_id, "request_info",
                                 note="call the bystander back"))
    check("request_info keeps review open",
          out2.current_status == "human_review_required"
          and out2.review.status == "info_requested",
          f"{out2.current_status}/{out2.review.status}")
    check("note recorded", out2.review.note == "call the bystander back")
    db.close()


def test_s3_replan_reruns():
    print("S3: replan re-runs triage and pauses again on low confidence")
    db, Session = fresh_db()
    cap = Capture()
    mgr = RecoveryManager(db=db, llm=DeterministicProvider(),
                          broadcast=cap)
    s = run(run_pipeline(unknown_report(), make_ctx(Session, cap)))
    out = run(mgr.decide_review(s.incident_id, "replan",
                                note="try again with fresh eyes"))
    check("replan pauses again (still unknown type)",
          out.current_status == "human_review_required",
          out.current_status)
    check("review marked replanned", out.review.status == "pending",
          out.review.status)  # re-paused → pending again
    acts = audit_actions(db, s.incident_id)
    check("replan decision audited",
          any("replan" in a.lower() for a in acts))
    db.close()


def test_s3_wrong_state_rejected():
    print("S3: decision on non-paused incident → 409-style error")
    db, Session = fresh_db()
    cap = Capture()
    mgr = RecoveryManager(db=db, llm=DeterministicProvider(),
                          broadcast=cap)
    s = run(run_pipeline(cardiac_report(), make_ctx(Session, cap)))
    try:
        run(mgr.decide_review(s.incident_id, "approve"))
        check("decision rejected when not paused", False, "no error raised")
    except Exception as e:
        check("decision rejected when not paused", "not awaiting" in str(e),
              str(e))
    db.close()


# ---------------------------------------------------------------------------
# S4 — injected agent conflict
# ---------------------------------------------------------------------------

def test_s4_conflict_detected_and_contained():
    print("S4: injected BLS-for-critical conflict → detected + contained")
    db, Session = fresh_db()
    cap = Capture()
    state = run(run_pipeline(cardiac_report(), make_ctx(Session, cap)))
    assert state.severity == 5, state.severity
    orig_amb = state.selected_ambulance.id
    orig_hosp = state.selected_hospital.id
    orig_beds = db.get(models.Hospital, orig_hosp).free_beds  # seed - 1 held

    mgr = RecoveryManager(db=db, llm=DeterministicProvider(),
                          broadcast=cap)
    out = run(mgr.inject(
        state.incident_id,
        FailureInjection(
            resource_type="conflict",
            reason="drill: tampered dispatch output",
            conflict={"agent": "DispatchAgent",
                      "decision": {"ambulance_id": "A6",
                                   "capabilities": "BLS",
                                   "eta_min": 9.0}})))

    vout = out.agent_outputs["VerificationAgent"]
    check("verification failed", vout.status == "failed")
    check("conflict_detected flag",
          vout.decision.get("conflict_detected") is True)
    conflict = vout.decision.get("conflict") or {}
    check("agents involved",
          conflict.get("agents_involved") == ["TriageAgent", "DispatchAgent"],
          str(conflict))
    check("conflict field", conflict.get("field") == "ambulance_capability")
    check("no ambulance held", out.selected_ambulance is None)
    check("original ambulance released",
          db.get(models.Ambulance, orig_amb).status == "available")
    check("hospital bed restored (held bed released)",
          db.get(models.Hospital, orig_hosp).free_beds == orig_beds + 1,
          f"{db.get(models.Hospital, orig_hosp).free_beds} vs {orig_beds + 1}")
    check("routed to human review",
          out.current_status == "human_review_required",
          out.current_status)
    acts = audit_actions(db, state.incident_id)
    check("CONFLICT_DETECTED audited",
          any("conflict" in a.lower() for a in acts), str(acts[-6:]))

    # operator replan → honest agents clear the conflict → completes
    out2 = run(mgr.decide_review(state.incident_id, "replan",
                                 note="recompute honestly"))
    check("replan after conflict completes",
          out2.current_status == "completed", out2.current_status)
    check("honest re-dispatch is ALS",
          out2.selected_ambulance.capability == "ALS",
          str(out2.selected_ambulance))
    db.close()


def test_s4_no_conflict_injection_rejected():
    print("S4: injection without a real contradiction is rejected")
    db, Session = fresh_db()
    cap = Capture()
    state = run(run_pipeline(cardiac_report(), make_ctx(Session, cap)))
    mgr = RecoveryManager(db=db, llm=DeterministicProvider(),
                          broadcast=cap)
    try:
        run(mgr.inject(
            state.incident_id,
            FailureInjection(
                resource_type="conflict",
                reason="drill: consistent override",
                conflict={"agent": "DispatchAgent",
                          "decision": {"ambulance_id": "A2",
                                       "capabilities": "ALS",
                                       "eta_min": 5.0}})))
        check("non-conflicting injection rejected", False, "no error")
    except Exception as e:
        check("non-conflicting injection rejected",
              "detectable conflict" in str(e), str(e))
    db.close()


if __name__ == "__main__":
    test_s1_ambulance_breakdown()
    test_s1_no_replacement_escalates()
    test_s2_hospital_unavailable()
    test_s3_pause_and_approve()
    test_s3_reject_and_request_info()
    test_s3_replan_reruns()
    test_s3_wrong_state_rejected()
    test_s4_conflict_detected_and_contained()
    test_s4_no_conflict_injection_rejected()
    print(f"\n{PASS} passed, {FAIL} failed")
    if FAILURES:
        print("FAILURES:", FAILURES)
        sys.exit(1)
