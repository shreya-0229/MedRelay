"""Pipeline stage graph.

::

    INTAKE → TRIAGE ─┬─→ DISPATCH ─────────┐
                     └─→ HOSPITAL_LIAISON ─┴─→ VERIFICATION ─┬─→ COMMUNICATE
                                                            └─→ REPLAN(×2) → ESCALATE

Triage runs first (dispatch/hospital need its severity + care pathway);
Dispatch and HospitalLiaison then fan out with ``asyncio.gather`` — both
are launched (STARTED events emitted) before either completes, which the
event log proves. Every stage transition emits a canonical bus event;
agents never run outside this graph.
"""
from __future__ import annotations

import asyncio
from typing import Any

from sqlalchemy.orm import Session

from app import models
from app.agents import BaseAgent, get_agent
from app.audit import write_agent_run, write_audit
from app.llm import LLMProvider
from app.orchestrator.event_bus import EventBus, EventType
from app.orchestrator.state_manager import StateManager
from app.schemas import (
    AgentOutput,
    IncidentReport,
    IncidentState,
    ReviewState,
)
from app.seed import fleet_snapshot

STAGE_PAUSE_S = 0.35        # keeps the dashboard animating live
MAX_REPLAN_ATTEMPTS = 2
LOW_CONFIDENCE = 0.6

RETRY_ORDER = ["TriageAgent", "DispatchAgent", "HospitalLiaisonAgent"]


def detect_conflicts(state: IncidentState) -> dict[str, Any] | None:
    """Cross-agent contradiction check (orchestrator-level safety net).

    Honest agents can never trip these rules — dispatch only picks
    capability-satisfying units and hospital only picks pathway-eligible
    hospitals — so any hit means tampered or inconsistent state, and the
    pipeline must fail closed.
    """
    t_out = state.agent_outputs.get("TriageAgent")
    d_out = state.agent_outputs.get("DispatchAgent")
    h_out = state.agent_outputs.get("HospitalLiaisonAgent")
    if not (t_out and d_out and h_out):
        return None
    if not (t_out.success and d_out.success and h_out.success):
        return None
    sev = state.severity
    amb = state.selected_ambulance
    cap = amb.capability if amb else d_out.decision.get("capabilities")
    if sev is not None and sev >= 4 and cap != "ALS":
        return {
            "agents_involved": ["TriageAgent", "DispatchAgent"],
            "field": "ambulance_capability",
            "values": {"severity": sev, "required": "ALS",
                       "dispatched": cap},
        }
    pathway = state.triage_result.pathway if state.triage_result else None
    hspec = h_out.decision.get("specialty")
    if pathway and hspec and hspec != pathway:
        return {
            "agents_involved": ["TriageAgent", "HospitalLiaisonAgent"],
            "field": "hospital_specialty",
            "values": {"required_pathway": pathway,
                       "selected_specialty": hspec},
        }
    return None


def release_reservations(db: Session, sm: StateManager) -> dict[str, str]:
    """Compensate: release ambulance + hospital holds back to the pool.

    Returns what was released. Used when a plan must not execute
    (conflict) — the incident keeps no half-held resources.
    """
    released: dict[str, str] = {}
    st = sm.state
    if st.selected_ambulance is not None:
        amb = db.get(models.Ambulance, st.selected_ambulance.id)
        if amb is not None:
            amb.status = "available"
            amb.assigned_incident = None
            db.add(amb)
        released["ambulance"] = st.selected_ambulance.id
    if st.selected_hospital is not None:
        hosp = db.get(models.Hospital, st.selected_hospital.id)
        if hosp is not None:
            hosp.free_beds = (hosp.free_beds or 0) + 1
            db.add(hosp)
        released["hospital"] = st.selected_hospital.id
    db.commit()
    sm.update(selected_ambulance=None, selected_hospital=None,
              ambulance_status="pending", hospital_status="pending")
    return released


class Workflow:
    """Executes the agent stage graph for one incident."""

    def __init__(self, *, db: Session, llm: LLMProvider,
                 report: IncidentReport | None, state: StateManager,
                 bus: EventBus) -> None:
        self._db = db
        self._llm = llm
        self._report = report
        self._state = state
        self._bus = bus

    # -- agent construction ---------------------------------------------

    def _agent_for(self, name: str) -> BaseAgent:
        """Build an agent by name via the registry (used for retries too)."""
        cls = get_agent(name)
        if name == "IntakeAgent":
            if self._report is None:
                raise RuntimeError("IntakeAgent needs a report (resume mode)")
            return cls(report=self._report, db=self._db, llm=self._llm)
        return cls(db=self._db, llm=self._llm)

    # -- gate helpers ----------------------------------------------------

    def _failed_agent_names(self) -> list[str]:
        """Retryable agents that failed or fell below the confidence bar.

        Outputs carrying ``decision["human_approved"]`` were explicitly
        accepted by an operator on review and are never retried.
        """
        state = self._state.state
        failed = {
            name for name, out in state.agent_outputs.items()
            if name not in ("VerificationAgent", "CommunicationAgent")
            and not out.decision.get("human_approved")
            and (not out.success or out.requires_human
                 or out.confidence < LOW_CONFIDENCE)
        }
        return [name for name in RETRY_ORDER if name in failed]

    def _gate_failed(self) -> bool:
        """True when the pipeline must replan: verification failed, an
        agent failed, or any agent confidence is below the safety bar
        (operator-approved outputs are exempt)."""
        state = self._state.state
        vr = state.verification_results
        if vr is None:
            return True
        if not vr.passed and not self._verification_acceptable():
            return True
        # The verification agent's own verdict is accounted for above via
        # vr / _verification_acceptable — only the pipeline agents gate here.
        return any(
            not out.decision.get("human_approved")
            and (not out.success or out.requires_human
                 or out.confidence < LOW_CONFIDENCE)
            for name, out in state.agent_outputs.items()
            if name not in ("CommunicationAgent", "VerificationAgent")
        )

    def _verification_acceptable(self) -> bool:
        """The verification agent failed, but only on confidence floors
        for outputs an operator explicitly approved on review — the human
        overrode the machine's uncertainty, so the plan may proceed."""
        state = self._state.state
        vr = state.verification_results
        if vr is None or vr.passed:
            return True
        for check in vr.checks:
            if check.passed:
                continue
            if check.name != "confidence_thresholds_ok":
                return False
        low = [name for name, out in state.agent_outputs.items()
               if name not in ("CommunicationAgent", "VerificationAgent")
               and (not out.success or out.requires_human
                    or out.confidence < LOW_CONFIDENCE)]
        return bool(low) and all(
            state.agent_outputs[name].decision.get("human_approved")
            for name in low)

    def _escalation_reason(self) -> str:
        state = self._state.state
        parts = [
            f"{name}: {out.rationale}"
            for name, out in state.agent_outputs.items()
            if not out.success or out.confidence < LOW_CONFIDENCE
        ]
        vr = state.verification_results
        if vr and not vr.passed:
            parts.extend(vr.issues)
        return "; ".join(parts) or "unspecified pipeline failure"

    # -- stage primitives -------------------------------------------------

    async def _begin(self, agent_name: str, event_type: str) -> None:
        """Emit a stage-started event (audit + WS feed)."""
        await self._bus.emit(
            event_type, sender=agent_name,
            incident_id=self._state.state.incident_id)

    async def _run_agent(self, agent_name: str) -> AgentOutput:
        """Execute one agent against the shared state (no events)."""
        agent = self._agent_for(agent_name)
        return await agent.run(self._state.state)

    async def _finish(self, agent_name: str, event_type: str,
                      out: AgentOutput, timeline_event: str) -> None:
        """Record a completed stage: state, agent-run, event, timeline."""
        sm, bus = self._state, self._bus
        iid = sm.state.incident_id
        sm.apply_agent_output(agent_name, out)
        write_agent_run(self._db, iid, out)
        action = out.data.get("action") or f"{agent_name} completed"
        await bus.emit(event_type, sender=agent_name, incident_id=iid,
                       payload={"status": out.status}, confidence=out.confidence,
                       action=action, rationale=out.rationale)
        sm.note(timeline_event, out.rationale)
        bus.blackboard.publish(
            from_agent=agent_name, to_agent="blackboard", incident_id=iid,
            payload={"action": action, "status": out.status,
                     "confidence": out.confidence})
        await asyncio.sleep(STAGE_PAUSE_S)

    async def _stage(self, agent_name: str, started: str | None,
                     completed: str, timeline_event: str) -> AgentOutput:
        """Run one full stage: started event → agent → completed event."""
        if started:
            await self._begin(agent_name, started)
        out = await self._run_agent(agent_name)
        await self._finish(agent_name, completed, out, timeline_event)
        return out

    async def _escalate(self, why: str) -> None:
        """Flag the incident for a human dispatcher (event + audit)."""
        sm, bus = self._state, self._bus
        reason = self._escalation_reason()
        sm.update(escalation_status="escalated", current_status="escalated")
        sm.note("escalated", f"Human escalation: {reason} ({why})")
        await bus.emit(EventType.HUMAN_ESCALATION, sender="Orchestrator",
                       incident_id=sm.state.incident_id,
                       action="Human escalation",
                       rationale=f"Human escalation: {reason}",
                       confidence=0.5)

    async def _replan(self) -> None:
        """Verification gate failed: retry failed steps (≤2), else escalate."""
        sm, bus = self._state, self._bus
        iid = sm.state.incident_id
        sm.update(escalation_status="replanning", current_status="replanning")
        sm.note("replanning",
                "Verification gate failed — retrying failed steps")
        await bus.emit(EventType.REPLAN_STARTED, sender="Orchestrator",
                       incident_id=iid, action="Replan started",
                       rationale=self._escalation_reason(), confidence=0.5)
        for attempt in range(1, MAX_REPLAN_ATTEMPTS + 1):
            retry = self._failed_agent_names()
            if not retry and not self._gate_failed():
                break
            if not retry:
                # Agents claim success but verification disagrees —
                # re-run the resource steps against fresh fleet state.
                retry = ["DispatchAgent", "HospitalLiaisonAgent"]
            sm.note(f"replan_attempt_{attempt}",
                    f"Retrying: {', '.join(retry)}")
            # (audit row kept exactly as before; no canonical event type
            #  exists for individual attempts)
            write_audit(self._db, iid, agent="Orchestrator",
                        action=f"Replan attempt {attempt}",
                        rationale=f"Retrying failed steps: {', '.join(retry)}",
                        confidence=0.5)
            sm.persist()
            for name in retry:
                started = {
                    "TriageAgent": EventType.TRIAGE_STARTED,
                    "DispatchAgent": EventType.DISPATCH_STARTED,
                    "HospitalLiaisonAgent": EventType.HOSPITAL_SEARCH_STARTED,
                }[name]
                completed = {
                    "TriageAgent": EventType.TRIAGE_COMPLETED,
                    "DispatchAgent": EventType.DISPATCH_COMPLETED,
                    "HospitalLiaisonAgent": EventType.HOSPITAL_SELECTED,
                }[name]
                await self._stage(name, started, completed,
                                  f"replan_retry_{name}")
            await self._begin("VerificationAgent",
                              EventType.VERIFICATION_STARTED)
            vout = await self._run_agent("VerificationAgent")
            verdict = (EventType.VERIFICATION_PASSED if vout.success
                       else EventType.VERIFICATION_FAILED)
            await self._finish("VerificationAgent", verdict, vout,
                               "reverification_complete")
            if not self._gate_failed():
                break
        if self._gate_failed():
            await self._escalate(
                f"replan exhausted after {MAX_REPLAN_ATTEMPTS} attempts")
        else:
            sm.update(escalation_status="none", current_status="verified")
            sm.note("replan_succeeded",
                    "Retry resolved the failure — resuming pipeline")
            write_audit(self._db, iid, agent="Orchestrator",
                        action="Replan succeeded",
                        rationale="Failed steps recovered on retry",
                        confidence=0.8)
            sm.persist()

    async def _pause_for_review(self, *, reason: str, affected: str,
                                confidence: float,
                                extra: dict[str, Any] | None = None) -> None:
        """Pause the pipeline BEFORE any unsafe action; an operator must
        decide (approve / reject / request_info / replan) via the review
        API before anything is reserved or executed."""
        sm, bus = self._state, self._bus
        iid = sm.state.incident_id
        sm.update(current_status="human_review_required",
                  review=ReviewState(
                      status="pending", reason=reason,
                      affected_decision=affected, confidence=confidence))
        sm.note("review_required",
                f"Paused for human review ({affected}): {reason}")
        payload: dict[str, Any] = {
            "reason": reason, "affected_decision": affected,
            "confidence": confidence,
        }
        if extra:
            payload.update(extra)
        await bus.emit(EventType.REVIEW_REQUIRED, sender="Orchestrator",
                       incident_id=iid, payload=payload, confidence=confidence,
                       action="Human review required", rationale=reason)

    async def _finalize_paused(self) -> None:
        """Light finalize for a paused pipeline: confidence rollup and
        live broadcasts, but NO communication stage and NO completion."""
        sm, bus = self._state, self._bus
        confidences = [o.confidence for o in sm.state.agent_outputs.values()]
        sm.update(confidence=round(min(confidences) if confidences else 0.0, 2))
        sm.note("paused", "Pipeline paused — no reservations made; "
                          "awaiting operator decision")
        await bus.broadcast_raw({"type": "incident_update",
                                 "incident": sm.state.model_dump(mode="json")})
        await bus.broadcast_raw({"type": "fleet_update",
                                 "fleet": fleet_snapshot(self._db)})

    async def _handle_conflict(self, vout: AgentOutput,
                               conflict: dict[str, Any],
                               release: bool = True) -> None:
        """Agent outputs contradict each other: fail the verification
        stage, release every held reservation (do not execute anything
        unsafe), and route to human review — the operator's REPLAN is the
        re-evaluation path, APPROVE resumes with honestly recomputed
        resources. ``release=False`` skips compensation (the caller already
        compensated, e.g. the conflict drill)."""
        sm, bus = self._state, self._bus
        iid = sm.state.incident_id
        a, b = conflict["agents_involved"]
        field = conflict["field"]
        # 1. Fail the verification stage with the conflict attached.
        vout.status = "failed"
        vout.success = False
        vout.decision["conflict_detected"] = True
        vout.decision["conflict"] = conflict
        vout.rationale = (
            f"{vout.rationale} CONFLICT DETECTED: {a} vs {b} disagree on "
            f"{field} ({conflict['values']}).")
        vout.warnings.append(
            "Agent outputs contradict each other — unsafe to proceed.")
        await self._finish("VerificationAgent", EventType.VERIFICATION_FAILED,
                           vout, "verification_complete")
        # 2. Record the conflict and release all holds (unless the caller
        # already compensated).
        released: dict[str, str] = {}
        if release:
            released = release_reservations(self._db, sm)
        sm.note("conflict_detected",
                f"Conflict on {field}: {conflict['values']}. "
                f"Released reservations: {released or 'none held'}.")
        await bus.emit(EventType.CONFLICT_DETECTED, sender="Orchestrator",
                       incident_id=iid,
                       payload={"conflict": conflict, "released": released},
                       confidence=0.4,
                       action="Agent conflict detected",
                       rationale=(f"Conflicting agent outputs on {field}; "
                                  "reservations released, routing to human "
                                  "review."))
        # 3. Human review — the operator decides re-evaluate vs approve.
        await self._pause_for_review(
            reason=(f"Agent conflict on {field}: {a} reported "
                    f"{conflict['values'].get('dispatched', conflict['values'])}, "
                    f"contradicting {b}."),
            affected="conflict", confidence=0.4,
            extra={"conflict": conflict})

    # -- the graph --------------------------------------------------------

    async def run(self) -> IncidentState:
        """Execute the full stage graph; return the final IncidentState."""
        sm, bus, report = self._state, self._bus, self._report
        state = sm.create(report)

        sm.note("received",
                f"Report received: {report.incident_type} "
                f"({report.patient_count} patient(s)) at "
                f"({state.location.lat}, {state.location.lon})")

        # 1. Intake — validates + normalizes the raw report.
        intake_out = await self._stage("IntakeAgent", None,
                                       EventType.INTAKE_COMPLETED,
                                       "intake_complete")

        if not intake_out.success:
            await self._escalate("intake validation failed")
            await self.run_from_communication()
            return sm.state

        # 2. Triage — must precede dispatch/hospital (they need
        #    pathway + severity).
        triage_out = await self._stage("TriageAgent", EventType.TRIAGE_STARTED,
                                       EventType.TRIAGE_COMPLETED,
                                       "triage_complete")

        # Low-confidence checkpoint: PAUSE before any reservation is made.
        if triage_out.requires_human or triage_out.confidence < LOW_CONFIDENCE:
            await self._pause_for_review(
                reason=(triage_out.rationale
                        or "Triage confidence below the safety threshold"),
                affected="triage", confidence=triage_out.confidence)
            await self._finalize_paused()
            return sm.state

        await self.run_from_dispatch()
        return sm.state

    async def run_from_dispatch(self) -> None:
        """Stages 3-8 after triage: dispatch ∥ hospital → verification
        (with conflict check) → replan loop → communication → finalize."""
        sm, bus = self._state, self._bus
        sm.update(current_status="triaged")

        # 3+4. Dispatch + HospitalLiaison fan out in PARALLEL: both
        # STARTED events are emitted before either branch completes.
        await self._begin("DispatchAgent", EventType.DISPATCH_STARTED)
        await self._begin("HospitalLiaisonAgent",
                          EventType.HOSPITAL_SEARCH_STARTED)
        await asyncio.gather(
            self._finish_after_run("DispatchAgent",
                                   EventType.DISPATCH_COMPLETED,
                                   "dispatch_complete"),
            self._finish_after_run("HospitalLiaisonAgent",
                                   EventType.HOSPITAL_SELECTED,
                                   "hospital_complete"),
        )
        sm.update(current_status="dispatched")

        # 5. Verification gate, with the cross-agent conflict check.
        await self._begin("VerificationAgent",
                          EventType.VERIFICATION_STARTED)
        vout = await self._run_agent("VerificationAgent")
        conflict = detect_conflicts(sm.state)
        if conflict is not None:
            await self._handle_conflict(vout, conflict)
            await self._finalize_paused()
            return
        # An operator-approved low-confidence output is acceptable even
        # though the verification agent flags its confidence floor.
        acceptable = vout.success or self._verification_acceptable()
        if not acceptable:
            sm.note("verification_override_check",
                    "Verification failed on non-confidence checks — "
                    "no operator override applies")
        verdict = (EventType.VERIFICATION_PASSED if acceptable
                   else EventType.VERIFICATION_FAILED)
        await self._finish("VerificationAgent", verdict, vout,
                           "verification_complete")
        sm.update(current_status="verified")

        # 6. Replan loop (max 2 attempts) when the gate fails.
        if self._gate_failed():
            await self._replan()

        await self.run_from_communication()

    async def run_from_communication(self) -> None:
        """Shared tail: communication stage, confidence rollup, broadcasts."""
        sm, bus = self._state, self._bus
        # 7. Communication — always runs; drafts an escalation notice when
        # the incident was escalated to a human.
        await self._stage("CommunicationAgent", None,
                          EventType.COMMUNICATION_SENT,
                          "communication_complete")
        if sm.state.escalation_status != "escalated":
            sm.update(current_status="communicating")

        # 8. Finalize: overall confidence = min of agent confidences.
        confidences = [o.confidence for o in sm.state.agent_outputs.values()]
        sm.update(confidence=round(min(confidences) if confidences else 0.0, 2))
        if sm.state.escalation_status != "escalated":
            sm.update(current_status="completed")
            sm.note("completed",
                    f"Pipeline finished — overall confidence "
                    f"{sm.state.confidence}")
        else:
            sm.note("escalated", "Pipeline finished — awaiting human dispatcher")
        await bus.broadcast_raw({"type": "incident_update",
                                 "incident": sm.state.model_dump(mode="json")})
        await bus.broadcast_raw({"type": "fleet_update",
                                 "fleet": fleet_snapshot(self._db)})

    async def _finish_after_run(self, agent_name: str, completed: str,
                                timeline_event: str) -> AgentOutput:
        """Run an agent (started event already emitted) and finish it."""
        out = await self._run_agent(agent_name)
        await self._finish(agent_name, completed, out, timeline_event)
        return out
