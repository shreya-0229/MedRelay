"""Per-agent unit tests for the MedRelay v2 agent layer.

Runs against a throwaway SQLite DB in /tmp (never touches medrelay.db).
Plain-Python asserts with a PASS/FAIL summary — run with:
    cd backend && .venv/bin/python test_agents.py
"""
from __future__ import annotations

import asyncio
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app import models
from app.agents import (
    AgentBus,
    CommunicationAgent,
    DispatchAgent,
    HospitalAgent,
    IntakeAgent,
    TriageAgent,
    VerificationAgent,
    get_agent,
    list_agents,
)
from app.agents.base_agent import utcnow
from app.llm import DeterministicProvider
from app.schemas import IncidentReport, IncidentState, Location
from app.seed import seed_fleet

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
    path = "/tmp/test_medrelay_agents.db"
    if os.path.exists(path):
        os.remove(path)
    engine = create_engine(f"sqlite:///{path}")
    models.Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    db = Session()
    seed_fleet(db)
    return db


def blank_state(incident_id: str = "INC-TEST-0001") -> IncidentState:
    return IncidentState(
        incident_id=incident_id,
        created_at=datetime.now(timezone.utc),
        incident_type="unknown",
        location=Location(lat=18.5204, lon=73.8567, address="Shivajinagar"),
    )


def run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


def main() -> None:
    llm = DeterministicProvider()

    # ---------- registry + bus ----------
    print("registry/bus")
    check("list_agents has 6", len(list_agents()) == 6, str(list_agents()))
    check("get known", get_agent("TriageAgent") is TriageAgent)
    try:
        get_agent("Nope")
        check("get unknown raises", False)
    except KeyError:
        check("get unknown raises", True)
    bus = AgentBus()
    bus.publish("IntakeAgent", "blackboard", "INC-1", {"a": 1})
    check("bus for_incident", len(bus.for_incident("INC-1")) == 1)
    check("bus latest", bus.latest("INC-1", "IntakeAgent").payload == {"a": 1})
    check("bus empty miss", bus.latest("INC-1", "TriageAgent") is None)

    # ---------- intake: structured ----------
    print("intake (structured)")
    db = fresh_db()
    rep = IncidentReport(incident_type="cardiac_arrest", lat=18.52, lon=73.85,
                         address="Shivajinagar", patient_count=1,
                         symptoms=["chest pain"], breathing_status="labored",
                         bleeding_status="none")
    st = blank_state()
    out = run(IntakeAgent(report=rep, db=db, llm=llm).run(st))
    check("structured success", out.status == "success" and out.success)
    check("envelope fields",
          all([out.agent == "IntakeAgent", out.incident_id == st.incident_id,
               isinstance(out.decision, dict), isinstance(out.warnings, list),
               out.timestamp is not None, out.agent_name == "IntakeAgent",
               out.rationale == out.reasoning_summary]))
    check("state normalized", st.incident_type == "cardiac_arrest"
          and st.breathing_status == "labored")

    # ---------- intake: free text, missing info ----------
    print("intake (free text)")
    db = fresh_db()
    rep2 = IncidentReport(
        incident_type="unknown", lat=18.53, lon=73.86,
        report_text="There was a road accident near the station, a man is "
                    "bleeding and not responding. Please send help fast!")
    st2 = blank_state("INC-TEST-0002")
    out2 = run(IntakeAgent(report=rep2, db=db, llm=llm).run(st2))
    d2 = out2.decision
    check("freetext success", out2.status == "success")
    check("type extracted", d2.get("incident_type") == "road_accident", str(d2.get("incident_type")))
    check("bleeding minor", d2.get("bleeding_status") == "minor", str(d2.get("bleeding")))
    check("consciousness", d2.get("consciousness") == "unconscious", str(d2.get("consciousness")))
    check("count from 'a man'", d2.get("patient_count") == 1,
          str(d2.get("patient_count")))
    check("missing info listed", len(d2.get("missing_information", [])) > 0)

    # ambiguous count must NOT be invented
    rep2b = IncidentReport(
        incident_type="unknown", lat=18.53, lon=73.86,
        report_text="Several people injured in a bus crash, heavy bleeding everywhere!")
    st2b = blank_state("INC-TEST-0002B")
    out2b = run(IntakeAgent(report=rep2b, db=db, llm=llm).run(st2b))
    d2b = out2b.decision
    check("ambiguous count not invented", d2b.get("patient_count") == 1
          and "patient_count" in d2b.get("missing_information", []),
          str((d2b.get("patient_count"), d2b.get("missing_information"))))
    check("heavy bleeding severe", d2b.get("bleeding_status") == "severe",
          str(d2b.get("bleeding_status")))

    # ---------- triage ----------
    print("triage")
    db = fresh_db()
    st3 = blank_state("INC-TEST-0003")
    st3.incident_type = "cardiac_arrest"
    st3.breathing_status = "absent"
    out3 = run(TriageAgent(db=db, llm=llm).run(st3))
    d3 = out3.decision
    check("cardiac CRITICAL/5", d3.get("severity") == 5 and d3.get("severity_label") == "CRITICAL",
          str(d3.get("severity_label")))
    check("pathway cath-lab", d3.get("care_pathway") == "cath-lab")
    check("priority P1", d3.get("priority") == "P1")
    check("disclaimer present", "NOT a medical diagnosis" in d3.get("disclaimer", ""))
    check("no human needed", out3.requires_human is False and out3.status == "success")
    check("state severity set", st3.severity == 5 and st3.triage_result is not None)

    st4 = blank_state("INC-TEST-0004")
    st4.incident_type = "mystery_event_xyz"
    out4 = run(TriageAgent(db=db, llm=llm).run(st4))
    check("unknown low conf", out4.confidence < 0.6, str(out4.confidence))
    check("unknown requires_human", out4.requires_human is True)
    check("unknown failed status", out4.status == "failed")

    st5 = blank_state("INC-TEST-0005")
    st5.incident_type = "chest_pain"
    st5.breathing_status = "absent"
    out5 = run(TriageAgent(db=db, llm=llm).run(st5))
    check("vitals override to 5", out5.decision.get("severity") == 5,
          str(out5.decision.get("severity")))

    # Safety rail: even an overconfident LLM must not bypass human review.
    st_llm = blank_state("INC-TEST-0011")
    st_llm.incident_type = "mystery_event_xyz"
    agent_llm = TriageAgent(db=db, llm=llm)
    agent_llm._llm_assess = lambda itype: (5, "trauma-center", 0.95)
    out_llm = run(agent_llm.run(st_llm))
    check("llm confidence capped below 0.6", out_llm.confidence < 0.6,
          str(out_llm.confidence))
    check("llm-capped requires_human", out_llm.requires_human is True)
    check("llm-capped failed status", out_llm.status == "failed")
    check("llm severity kept", out_llm.decision.get("severity") == 5)

    # ---------- dispatch ----------
    print("dispatch")
    db = fresh_db()
    st6 = blank_state("INC-TEST-0006")
    st6.incident_type = "cardiac_arrest"
    run(TriageAgent(db=db, llm=llm).run(st6))
    out6 = run(DispatchAgent(db=db, llm=llm).run(st6))
    d6 = out6.decision
    check("dispatch success", out6.status == "success")
    check("decision fields", all(k in d6 for k in
          ("ambulance_id", "eta_min", "capabilities", "status", "confidence"))
          or all(k in d6 for k in ("ambulance_id", "eta_min", "capabilities", "status")),
          str(sorted(d6.keys())))
    amb = db.get(models.Ambulance, d6["ambulance_id"])
    check("ambulance from DB", amb is not None and amb.status == "en_route"
          and amb.assigned_incident == st6.incident_id)
    check("ALS for critical", amb.capability == "ALS")

    db = fresh_db()
    for a in db.query(models.Ambulance).all():
        a.status = "out_of_service"
    db.commit()
    st7 = blank_state("INC-TEST-0007")
    st7.incident_type = "road_accident"
    run(TriageAgent(db=db, llm=llm).run(st7))
    out7 = run(DispatchAgent(db=db, llm=llm).run(st7))
    check("no-ambulance failed", out7.status == "failed" and not out7.success)
    check("no selection", st7.selected_ambulance is None
          and st7.ambulance_status == "unavailable")

    # ---------- hospital ----------
    print("hospital")
    db = fresh_db()
    st8 = blank_state("INC-TEST-0008")
    st8.incident_type = "cardiac_arrest"
    run(TriageAgent(db=db, llm=llm).run(st8))
    before = {h.id: h.free_beds for h in db.query(models.Hospital).all()}
    out8 = run(HospitalAgent(db=db, llm=llm).run(st8))
    d8 = out8.decision
    check("hospital success", out8.status == "success")
    check("decision fields", all(k in d8 for k in
          ("hospital_id", "hospital_name", "available_capacity", "specialty",
           "reservation_status")), str(sorted(d8.keys())))
    check("reserved", d8.get("reservation_status") == "reserved"
          and st8.hospital_status == "reserved")
    after = db.get(models.Hospital, d8["hospital_id"]).free_beds
    check("bed decremented", after == before[d8["hospital_id"]] - 1)
    check("capacity honest", d8["available_capacity"] == after)

    db = fresh_db()
    for h in db.query(models.Hospital).all():
        h.free_beds = 0
    db.commit()
    st9 = blank_state("INC-TEST-0009")
    st9.incident_type = "stroke"
    run(TriageAgent(db=db, llm=llm).run(st9))
    out9 = run(HospitalAgent(db=db, llm=llm).run(st9))
    check("no-bed failed", out9.status == "failed"
          and st9.hospital_status == "unavailable")

    # ---------- communication ----------
    print("communication")
    db = fresh_db()
    st10 = blank_state("INC-TEST-0010")
    st10.incident_type = "road_accident"
    run(TriageAgent(db=db, llm=llm).run(st10))
    run(DispatchAgent(db=db, llm=llm).run(st10))
    run(HospitalAgent(db=db, llm=llm).run(st10))
    out10 = run(CommunicationAgent(db=db, llm=llm).run(st10))
    rows = db.query(models.CommunicationMessage).filter_by(
        incident_id=st10.incident_id).all()
    langs = {r.language for r in rows}
    check("comms success", out10.status == "success")
    check("persisted to DB", len(rows) >= 7, f"got {len(rows)}")
    check("three languages", {"en", "hi", "mr"} <= langs, str(langs))
    check("state comms mirror", len(st10.communications) == len(rows))
    check("short factual", all(len(r.text) < 400 for r in rows))

    # ---------- verification ----------
    print("verification")
    db = fresh_db()
    st11 = blank_state("INC-TEST-0011")
    st11.incident_type = "cardiac_arrest"
    outs = {}
    for ag in (TriageAgent(db=db, llm=llm), DispatchAgent(db=db, llm=llm),
               HospitalAgent(db=db, llm=llm)):
        o = run(ag.run(st11))
        outs[ag.name] = o
        st11.agent_outputs[ag.name] = o
    out11 = run(VerificationAgent(db=db, llm=llm).run(st11))
    check("verification passed", out11.status == "success"
          and st11.verification_results.passed)
    check("checks recorded", len(st11.verification_results.checks) >= 8,
          str(len(st11.verification_results.checks)))

    # rejection case: tamper — dispatch failed but ambulance selected
    st12 = blank_state("INC-TEST-0012")
    st12.incident_type = "cardiac_arrest"
    run(TriageAgent(db=db, llm=llm).run(st12))
    fake = run(DispatchAgent(db=db, llm=llm).run(st12))
    st12.agent_outputs["DispatchAgent"] = fake
    st12.agent_outputs["HospitalLiaisonAgent"] = run(
        HospitalAgent(db=db, llm=llm).run(st12))
    out12 = run(VerificationAgent(db=db, llm=llm).run(st12))
    check("rejects contradiction", out12.status == "failed",
          "tamper not implemented by worker?" if out12.status == "success" else "")
    # force a contradiction explicitly if the worker's run was consistent
    if out12.status == "success":
        from app.schemas import AgentOutput
        bad = AgentOutput(agent_name="DispatchAgent", agent="DispatchAgent",
                          incident_id=st12.incident_id, status="failed",
                          confidence=0.0, decision={}, reasoning_summary="forced",
                          success=False, timestamp=utcnow())
        st12.agent_outputs["DispatchAgent"] = bad
        out12b = run(VerificationAgent(db=db, llm=llm).run(st12))
        check("rejects forced contradiction", out12b.status == "failed"
              and "verification_error" in out12b.decision,
              str(out12b.decision.keys()))
    else:
        check("verification_error present",
              "verification_error" in out12.decision)

    print(f"\n{ PASS} passed, {FAIL} failed")
    if FAILURES:
        print("failures:", FAILURES)
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
