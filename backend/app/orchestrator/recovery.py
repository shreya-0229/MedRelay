"""Autonomous failure recovery for ACTIVE MedRelay incidents.

The pipeline's verification gate handles failures *during planning*.
This module handles failures *after* a plan is live: a reserved
ambulance breaks down, a hospital goes dark, agent outputs are found to
contradict each other, or a human must review a low-confidence triage.

Every recovery step runs for real — DB mutations, the actual agents
(re-dispatched through the registry), the real VerificationAgent, and
trilingual family notifications — and every step is audit-logged and
bridged to the WebSocket feed. Nothing here is simulated.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable

from sqlalchemy.orm import Session

from app import models
from app.agents import utcnow
from app.agents.communication_agent import persist_message
from app.llm import LLMProvider
from app.orchestrator.event_bus import EventBus, EventType
from app.orchestrator.state_manager import StateManager
from app.orchestrator.workflow import (
    LOW_CONFIDENCE,
    Workflow,
    detect_conflicts,
    release_reservations,
)
from app.schemas import (
    AgentOutput,
    AmbulanceSelection,
    Communication,
    FailureInjection,
    HospitalSelection,
    IncidentState,
    ReviewState,
)


class RecoveryError(Exception):
    """A recovery or review request that cannot be honored (→ HTTP 409)."""


# -- trilingual family notifications ---------------------------------------

_REPLACEMENT_TEMPLATES: dict[str, dict[str, str]] = {
    "ambulance": {
        "en": ("MedRelay update: Ambulance {old} is unavailable ({reason}). "
               "Ambulance {new} has been assigned. "
               "New estimated arrival: {eta} minutes."),
        "hi": ("MedRelay अपडेट: एम्बुलेंस {old} उपलब्ध नहीं है ({reason})। "
               "एम्बुलेंस {new} भेजी गई है। "
               "नया अनुमानित आगमन: {eta} मिनट।"),
        "mr": ("MedRelay अपडेट: रुग्णवाहिका {old} उपलब्ध नाही ({reason})। "
               "रुग्णवाहिका {new} पाठवली आहे. "
               "नवीन अंदाजे आगमन: {eta} मिनिटे."),
    },
    "hospital": {
        "en": ("MedRelay update: {old} is unavailable ({reason}). "
               "A bed has been reserved at {new} instead "
               "({extra} km away)."),
        "hi": ("MedRelay अपडेट: {old} उपलब्ध नहीं है ({reason})। "
               "इसके बजाय {new} में बेड आरक्षित किया गया है "
               "({extra} किमी दूर)।"),
        "mr": ("MedRelay अपडेट: {old} उपलब्ध नाही ({reason})। "
               "त्याऐवजी {new} येथे बेड राखीव केला आहे "
               "({extra} किमी अंतरावर)।"),
    },
}


class RecoveryManager:
    """Executes failure recovery + operator review flows for one incident."""

    def __init__(self, *, db: Session, llm: LLMProvider,
                 broadcast: Callable[[dict], Awaitable[None]]) -> None:
        self._db = db
        self._llm = llm
        self._bus = EventBus(db=db, broadcast=broadcast)

    # -- loading ----------------------------------------------------------

    def _load(self, incident_id: str) -> StateManager:
        sm = StateManager(self._db)
        try:
            sm.load(incident_id)
        except LookupError as e:
            raise RecoveryError(str(e)) from e
        return sm

    def _workflow(self, sm: StateManager) -> Workflow:
        """A Workflow bound to the loaded state (no report needed —
        recovery never re-runs Intake)."""
        return Workflow(db=self._db, llm=self._llm, report=None,
                        state=sm, bus=self._bus)

    async def _broadcast_state(self, sm: StateManager) -> None:
        from app.seed import fleet_snapshot
        await self._bus.broadcast_raw({
            "type": "incident_update",
            "incident": sm.state.model_dump(mode="json")})
        await self._bus.broadcast_raw({
            "type": "fleet_update",
            "fleet": fleet_snapshot(self._db)})

    # -- entry point ------------------------------------------------------

    async def inject(self, incident_id: str,
                     spec: FailureInjection) -> IncidentState:
        """Dispatch a failure drill to the right recovery flow."""
        if spec.resource_type == "ambulance":
            if not spec.resource_id:
                raise RecoveryError("resource_id is required for ambulance")
            return await self.recover_ambulance_failure(
                incident_id, spec.resource_id, spec.reason)
        if spec.resource_type == "hospital":
            if not spec.resource_id:
                raise RecoveryError("resource_id is required for hospital")
            return await self.recover_hospital_failure(
                incident_id, spec.resource_id, spec.reason)
        if spec.resource_type == "conflict":
            if not spec.conflict:
                raise RecoveryError("conflict spec is required")
            return await self.inject_conflict(
                incident_id, spec.conflict, spec.reason)
        raise RecoveryError(f"unknown resource_type {spec.resource_type!r}")

    # -- Scenario 1: ambulance breakdown ----------------------------------

    async def recover_ambulance_failure(
            self, incident_id: str, ambulance_id: str,
            reason: str) -> IncidentState:
        sm = self._load(incident_id)
        bus = self._bus
        iid = incident_id
        state = sm.state
        sel = state.selected_ambulance
        if (state.ambulance_status != "en_route" or sel is None
                or sel.id != ambulance_id):
            raise RecoveryError(
                f"Ambulance {ambulance_id} is not the actively assigned "
                f"unit for {iid}")

        # 1. Mark the unit failed in the fleet DB.
        amb = self._db.get(models.Ambulance, ambulance_id)
        amb.status = "out_of_service"
        amb.assigned_incident = None
        self._db.add(amb)
        self._db.commit()

        # 2. Failure events (audit + live feed).
        detail = (f"Ambulance {ambulance_id} reported '{reason}' while "
                  f"en route to {iid}")
        sm.note("failure_detected", detail)
        payload = {"resource_type": "ambulance",
                   "resource_id": ambulance_id, "reason": reason}
        await bus.emit(EventType.FAILURE_DETECTED, sender="RecoveryManager",
                       incident_id=iid, payload=payload, confidence=0.95,
                       action="Failure detected", rationale=detail)
        await bus.emit(EventType.AMBULANCE_FAILURE, sender="RecoveryManager",
                       incident_id=iid, payload=payload, confidence=0.95,
                       action="Ambulance failure",
                       rationale=f"Ambulance {ambulance_id} is out of service: "
                                 f"{reason}")

        wf = self._workflow(sm)

        # 3. The real VerificationAgent validates the failure: the
        #    selected unit must now trip the assignment checks.
        await wf._begin("VerificationAgent", EventType.VERIFICATION_STARTED)
        vout = await wf._run_agent("VerificationAgent")
        failed_checks = [
            (c.get("name") if isinstance(c, dict) else c.name)
            for c in vout.decision.get("checks", [])
            if (c.get("passed") if isinstance(c, dict) else c.passed)
            is False
        ]
        await wf._finish(
            "VerificationAgent",
            EventType.VERIFICATION_FAILED if not vout.success
            else EventType.VERIFICATION_PASSED,
            vout, "failure_validated")
        sm.note("failure_validated",
                f"Verification on failed unit: "
                f"{'FAILED as expected' if not vout.success else 'unexpectedly passed'} "
                f"({', '.join(failed_checks) or 'no failing checks'})")

        # 4-5. Pause the affected plan and start replanning.
        sm.update(current_status="replanning", escalation_status="replanning")
        sm.note("replanning",
                f"Plan paused after {ambulance_id} failure — "
                f"searching for a replacement unit")
        await bus.emit(EventType.REPLANNING, sender="Orchestrator",
                       incident_id=iid, payload=payload, confidence=0.7,
                       action="Replanning after failure",
                       rationale=f"Searching for a replacement for failed "
                                 f"ambulance {ambulance_id}")

        # 6-7. DispatchAgent re-searches. The failed unit is out_of_service
        # in the DB, so the agent's own availability query excludes it.
        dout = await wf._stage("DispatchAgent", EventType.DISPATCH_STARTED,
                               EventType.DISPATCH_COMPLETED,
                               "recovery_redispatch")
        new = sm.state.selected_ambulance
        if not dout.success or new is None or new.id == ambulance_id:
            return await self._escalate_no_resource(
                sm, kind="ambulance",
                reason=f"no capable replacement for failed unit "
                       f"{ambulance_id} ({reason})")

        # 8. VerificationAgent verifies the replacement.
        await wf._begin("VerificationAgent", EventType.VERIFICATION_STARTED)
        vout2 = await wf._run_agent("VerificationAgent")
        await wf._finish(
            "VerificationAgent",
            EventType.VERIFICATION_PASSED if vout2.success
            else EventType.VERIFICATION_FAILED,
            vout2, "reverification_complete")
        if not vout2.success:
            return await self._escalate_no_resource(
                sm, kind="ambulance",
                reason=f"replacement {new.id} failed verification: "
                       f"{vout2.decision.get('verification_error')}")

        # 9. Reserved: DispatchAgent marked the unit en_route on selection.
        sm.update(ambulance_status="en_route")
        sm.note("resource_replaced",
                f"Ambulance {ambulance_id} → {new.id} "
                f"({new.capability}, ETA {new.eta_min} min)")
        await bus.emit(
            EventType.RESOURCE_REPLACED, sender="Orchestrator",
            incident_id=iid,
            payload={"resource_type": "ambulance", "old_id": ambulance_id,
                     "new_id": new.id, "new_eta_min": new.eta_min,
                     "reason": reason},
            confidence=0.9, action="Replacement ambulance selected",
            rationale=f"Ambulance {ambulance_id} replaced by {new.id}, "
                      f"ETA {new.eta_min} min")

        # 10. Family notification in EN/HI/MR.
        await self._notify_family(
            sm, kind="ambulance", old_id=ambulance_id, new_id=new.id,
            eta=str(new.eta_min), reason=reason)

        # 11. Done — every step above was audit-logged via bus.emit.
        sm.update(current_status="recovered", escalation_status="none")
        sm.note("recovery_complete",
                f"Recovery complete: {new.id} en route, family notified")
        await self._broadcast_state(sm)
        return sm.state

    # -- Scenario 2: hospital becomes unavailable --------------------------

    async def recover_hospital_failure(
            self, incident_id: str, hospital_id: str,
            reason: str) -> IncidentState:
        sm = self._load(incident_id)
        bus = self._bus
        iid = incident_id
        state = sm.state
        sel = state.selected_hospital
        if (state.hospital_status != "reserved" or sel is None
                or sel.id != hospital_id):
            raise RecoveryError(
                f"Hospital {hospital_id} is not the reserved hospital "
                f"for {iid}")

        # 1. Mark the hospital unavailable (zero free beds → ineligible).
        hosp = self._db.get(models.Hospital, hospital_id)
        hosp.free_beds = 0
        self._db.add(hosp)
        self._db.commit()
        sm.update(hospital_status="unavailable")

        # 2. Failure events.
        detail = (f"Hospital {sel.name} ({hospital_id}) reported "
                  f"'{reason}' — no longer able to receive the patient")
        sm.note("failure_detected", detail)
        payload = {"resource_type": "hospital", "resource_id": hospital_id,
                   "resource_name": sel.name, "reason": reason}
        await bus.emit(EventType.FAILURE_DETECTED, sender="RecoveryManager",
                       incident_id=iid, payload=payload, confidence=0.95,
                       action="Failure detected", rationale=detail)
        await bus.emit(EventType.HOSPITAL_FAILURE, sender="RecoveryManager",
                       incident_id=iid, payload=payload, confidence=0.95,
                       action="Hospital failure", rationale=detail)

        wf = self._workflow(sm)

        # 3. The real VerificationAgent validates the failure.
        await wf._begin("VerificationAgent", EventType.VERIFICATION_STARTED)
        vout = await wf._run_agent("VerificationAgent")
        failed_checks = [
            (c.get("name") if isinstance(c, dict) else c.name)
            for c in vout.decision.get("checks", [])
            if (c.get("passed") if isinstance(c, dict) else c.passed)
            is False
        ]
        await wf._finish(
            "VerificationAgent",
            EventType.VERIFICATION_FAILED if not vout.success
            else EventType.VERIFICATION_PASSED,
            vout, "failure_validated")
        sm.note("failure_validated",
                f"Verification on failed hospital: "
                f"{'FAILED as expected' if not vout.success else 'unexpectedly passed'} "
                f"({', '.join(failed_checks) or 'no failing checks'})")

        # 4-5. Pause and replan.
        sm.update(current_status="replanning", escalation_status="replanning")
        sm.note("replanning",
                f"Plan paused after {sel.name} became unavailable — "
                f"searching for another hospital")
        await bus.emit(EventType.REPLANNING, sender="Orchestrator",
                       incident_id=iid, payload=payload, confidence=0.7,
                       action="Replanning after failure",
                       rationale=f"Searching for a replacement for failed "
                                 f"hospital {sel.name}")

        # 6-7. HospitalLiaisonAgent re-searches (failed hospital has no
        # free beds, so its own eligibility query excludes it).
        hout = await wf._stage("HospitalLiaisonAgent",
                               EventType.HOSPITAL_SEARCH_STARTED,
                               EventType.HOSPITAL_SELECTED,
                               "recovery_rehospital")
        new = sm.state.selected_hospital
        if not hout.success or new is None or new.id == hospital_id:
            return await self._escalate_no_resource(
                sm, kind="hospital",
                reason=f"no eligible replacement for failed hospital "
                       f"{sel.name} ({reason})")

        # 8. Verify the replacement.
        await wf._begin("VerificationAgent", EventType.VERIFICATION_STARTED)
        vout2 = await wf._run_agent("VerificationAgent")
        await wf._finish(
            "VerificationAgent",
            EventType.VERIFICATION_PASSED if vout2.success
            else EventType.VERIFICATION_FAILED,
            vout2, "reverification_complete")
        if not vout2.success:
            return await self._escalate_no_resource(
                sm, kind="hospital",
                reason=f"replacement {new.name} failed verification: "
                       f"{vout2.decision.get('verification_error')}")

        # 9. Reserved: the agent decremented the bed counter on selection.
        sm.update(hospital_status="reserved")
        sm.note("resource_replaced",
                f"Hospital {sel.name} → {new.name} ({new.distance_km} km)")
        await bus.emit(
            EventType.RESOURCE_REPLACED, sender="Orchestrator",
            incident_id=iid,
            payload={"resource_type": "hospital", "old_id": hospital_id,
                     "old_name": sel.name, "new_id": new.id,
                     "new_name": new.name,
                     "new_distance_km": new.distance_km, "reason": reason},
            confidence=0.9, action="Replacement hospital selected",
            rationale=f"Hospital {sel.name} replaced by {new.name}, "
                      f"{new.distance_km} km away")

        # 10. Family notification.
        await self._notify_family(
            sm, kind="hospital", old_id=sel.name, new_id=new.name,
            eta=str(new.distance_km), reason=reason)

        # 11. Done.
        sm.update(current_status="recovered", escalation_status="none")
        sm.note("recovery_complete",
                f"Recovery complete: bed reserved at {new.name}, "
                f"family notified")
        await self._broadcast_state(sm)
        return sm.state

    async def _escalate_no_resource(self, sm: StateManager, *, kind: str,
                                    reason: str) -> IncidentState:
        """No replacement exists: human escalation + escalation notice."""
        bus = self._bus
        iid = sm.state.incident_id
        sm.update(escalation_status="escalated", current_status="escalated")
        sm.note("escalated", f"Human escalation: {reason}")
        await bus.emit(EventType.HUMAN_ESCALATION, sender="Orchestrator",
                       incident_id=iid, confidence=0.5,
                       action="Human escalation",
                       rationale=f"Human escalation: {reason}")
        wf = self._workflow(sm)
        # CommunicationAgent drafts the trilingual escalation notice when
        # escalation_status == "escalated".
        await wf._stage("CommunicationAgent", None,
                        EventType.COMMUNICATION_SENT,
                        "communication_complete")
        await self._broadcast_state(sm)
        return sm.state

    # -- Scenario 4: injected agent conflict --------------------------------

    async def inject_conflict(self, incident_id: str,
                              conflict_spec: dict[str, Any],
                              reason: str) -> IncidentState:
        """Test/drill hook: overwrite one agent's output with a conflicting
        one, then run the real conflict handling (detect → compensate →
        human review)."""
        sm = self._load(incident_id)
        bus = self._bus
        iid = incident_id
        agent_name = conflict_spec.get("agent", "DispatchAgent")
        decision_override = conflict_spec.get("decision", {}) or {}
        out: AgentOutput | None = sm.state.agent_outputs.get(agent_name)
        if out is None:
            raise RecoveryError(
                f"no {agent_name} output to override on {iid}")

        # 1. Compensate the honest plan first so the drill never leaks or
        #    double-releases real holds.
        released = release_reservations(self._db, sm)
        sm.note("conflict_drill",
                f"Drill '{reason}': released honest reservations {released} "
                f"before injecting conflicting {agent_name} output")

        # 2. Apply the tampered output + keep selections in sync.
        new_decision = {**(out.decision or {}), **decision_override}
        tampered = out.model_copy(update={
            "decision": new_decision, "success": True, "status": "success"})
        sm.apply_agent_output(agent_name, tampered)
        if agent_name == "DispatchAgent":
            amb_id = new_decision.get("ambulance_id")
            if amb_id:
                sm.update(selected_ambulance=AmbulanceSelection(
                    id=amb_id,
                    capability=new_decision.get("capabilities") or "BLS",
                    eta_min=float(new_decision.get("eta_min") or 0)))
                sm.update(ambulance_status="en_route")
        if agent_name == "HospitalLiaisonAgent":
            hid = new_decision.get("hospital_id")
            if hid:
                sm.update(selected_hospital=HospitalSelection(
                    id=hid,
                    name=new_decision.get("hospital_name") or hid,
                    distance_km=float(
                        new_decision.get("distance_km") or 0)))
                sm.update(hospital_status="reserved")
        from app.audit import write_agent_run
        write_agent_run(self._db, iid, tampered)

        # 3. Run the real verification + conflict check on the tampered state.
        wf = self._workflow(sm)
        await wf._begin("VerificationAgent", EventType.VERIFICATION_STARTED)
        vout = await wf._run_agent("VerificationAgent")
        conflict = detect_conflicts(sm.state)
        if conflict is None:
            raise RecoveryError(
                "injected output did not produce a detectable conflict")
        # 4-6. Same handling as the live pipeline: record, then clear the
        # fictional selections (the honest holds were already compensated
        # above, so release=False avoids touching the DB again), then
        # route to human review.
        await wf._handle_conflict(vout, conflict, release=False)
        sm.update(selected_ambulance=None, selected_hospital=None,
                  ambulance_status="pending", hospital_status="pending")
        sm.note("conflict_contained",
                "Conflicting selections cleared — no resources held")
        await wf._finalize_paused()
        await self._broadcast_state(sm)
        return sm.state

    # -- Scenario 3: operator review decisions -------------------------------

    async def decide_review(self, incident_id: str, decision: str,
                            note: str = "") -> IncidentState:
        """Apply an operator decision to a paused incident."""
        sm = self._load(incident_id)
        bus = self._bus
        iid = incident_id
        state = sm.state
        if state.current_status != "human_review_required":
            raise RecoveryError(
                f"incident {iid} is not awaiting human review "
                f"(status={state.current_status})")
        now = datetime.now(timezone.utc)
        review = state.review

        if decision == "approve":
            # The operator takes responsibility for the low-confidence /
            # conflicted output: mark it human-approved so the safety
            # rails (gate + verification interpretation) let the pipeline
            # continue, then resume from the dispatch stage.
            target = self._approval_target(state)
            if target is not None:
                out = state.agent_outputs[target]
                out.requires_human = False
                out.decision["human_approved"] = True
                out.warnings.append(
                    f"Operator approved on review: {note or 'no note'}")
                sm.apply_agent_output(target, out)
            sm.update(review=ReviewState(
                status="approved", reason=review.reason,
                affected_decision=review.affected_decision,
                confidence=review.confidence, note=note, decided_at=now))
            sm.note("review_approved", f"Operator approved: {note}")
            await bus.emit(EventType.REVIEW_DECIDED, sender="Operator",
                           incident_id=iid,
                           payload={"decision": "approve", "note": note},
                           confidence=1.0, action="Review approved",
                           rationale=f"Operator approved resumption: {note}")
            wf = self._workflow(sm)
            await wf.run_from_dispatch()
            return sm.state

        if decision == "reject":
            sm.update(current_status="cancelled",
                      review=ReviewState(
                          status="rejected", reason=review.reason,
                          affected_decision=review.affected_decision,
                          confidence=review.confidence, note=note,
                          decided_at=now))
            sm.note("review_rejected",
                    f"Operator rejected the plan: {note}")
            await bus.emit(EventType.REVIEW_DECIDED, sender="Operator",
                           incident_id=iid,
                           payload={"decision": "reject", "note": note},
                           confidence=1.0, action="Review rejected",
                           rationale=f"Operator cancelled the incident: {note}")
            await self._broadcast_state(sm)
            return sm.state

        if decision == "request_info":
            sm.update(review=ReviewState(
                status="info_requested", reason=review.reason,
                affected_decision=review.affected_decision,
                confidence=review.confidence, note=note, decided_at=now))
            sm.note("review_info_requested",
                    f"Operator requested caller callback: {note}")
            await bus.emit(EventType.REVIEW_DECIDED, sender="Operator",
                           incident_id=iid,
                           payload={"decision": "request_info", "note": note},
                           confidence=1.0,
                           action="More information requested",
                           rationale=f"Operator requested caller callback: "
                                     f"{note}")
            await self._broadcast_state(sm)
            return sm.state

        if decision == "replan":
            sm.update(review=ReviewState(
                status="replanned", reason=review.reason,
                affected_decision=review.affected_decision,
                confidence=review.confidence, note=note, decided_at=now))
            sm.note("review_replan",
                    f"Operator ordered a full replan: {note}")
            await bus.emit(EventType.REVIEW_DECIDED, sender="Operator",
                           incident_id=iid,
                           payload={"decision": "replan", "note": note},
                           confidence=1.0, action="Review replan",
                           rationale=f"Operator ordered replan: {note}")
            wf = self._workflow(sm)
            await wf._stage("TriageAgent", EventType.TRIAGE_STARTED,
                            EventType.TRIAGE_COMPLETED,
                            "review_replan_triage")
            tout = sm.state.agent_outputs["TriageAgent"]
            if tout.requires_human or tout.confidence < LOW_CONFIDENCE:
                await wf._pause_for_review(
                    reason=(tout.rationale
                            or "Triage confidence below the safety threshold"),
                    affected="triage", confidence=tout.confidence)
                await wf._finalize_paused()
                return sm.state
            await wf.run_from_dispatch()
            return sm.state

        raise RecoveryError(f"unknown review decision {decision!r}")

    @staticmethod
    def _approval_target(state: IncidentState) -> str | None:
        """Which agent output the approval applies to."""
        if state.review.affected_decision == "conflict":
            return None  # conflict approvals resume honestly; nothing to bless
        tout = state.agent_outputs.get("TriageAgent")
        if tout is not None and (tout.requires_human
                                 or tout.confidence < LOW_CONFIDENCE):
            return "TriageAgent"
        return None

    # -- family notification --------------------------------------------------

    async def _notify_family(self, sm: StateManager, *, kind: str,
                             old_id: str, new_id: str, eta: str,
                             reason: str) -> None:
        """Trilingual replacement notice: persisted to DB + state + audit."""
        iid = sm.state.incident_id
        templates = _REPLACEMENT_TEMPLATES[kind]
        for lang in ("en", "hi", "mr"):
            text = templates[lang].format(
                old=old_id, new=new_id, eta=eta, reason=reason, extra=eta)
            persist_message(self._db, iid, "recovery_family", lang, text)
            sm.state.communications.append(Communication(
                channel=f"recovery_family_{lang}", text=text, ts=utcnow()))
        sm.persist()
        sm.note("family_notified",
                f"Family notified of {kind} replacement "
                f"({old_id} → {new_id}) in EN/HI/MR")
        await self._bus.emit(EventType.COMMUNICATION_SENT,
                             sender="RecoveryManager", incident_id=iid,
                             confidence=0.95,
                             action="Family notified of replacement",
                             rationale=f"Family updated: {old_id} → {new_id}")
