"""VerificationAgent — the pipeline gatekeeper.

Re-reads the resource tables from the DB (``self.db.get``) instead of
trusting in-memory state, then runs the eight named checks. Any failure
returns status="failed" so the orchestrator replans or escalates.
"""
from __future__ import annotations

from app import models
from app.agents.agent_registry import register
from app.agents.base_agent import BaseAgent
from app.agents.triage_agent import TriageAgent
from app.schemas import (AgentOutput, IncidentState, VerificationCheck,
                         VerificationResults)

_ALS_PATHWAYS = {"cath-lab", "stroke-unit"}
_CONFIDENCE_FLOOR = 0.6


@register
class VerificationAgent(BaseAgent):
    """Independent checker: validates triage, resources, and consistency."""

    name = "VerificationAgent"
    version = "2.0"

    async def run(self, state: IncidentState) -> AgentOutput:
        checks: list[VerificationCheck] = []

        def add(name: str, passed: bool, detail: str = "") -> None:
            checks.append(VerificationCheck(name=name, passed=passed,
                                            detail=detail))

        triage = state.triage_result
        severity = state.severity
        pathway = triage.pathway if triage else "general-er"

        # 1. triage_valid
        if triage is None:
            add("triage_valid", False, "no triage result on state")
        else:
            ok = (1 <= triage.severity <= 5
                  and triage.pathway in TriageAgent.VALID_PATHWAYS)
            add("triage_valid", ok,
                f"severity={triage.severity} pathway={triage.pathway!r}"
                if ok else
                f"invalid triage: severity={triage.severity} "
                f"pathway={triage.pathway!r}")

        # 2. ambulance_capability_ok (vacuous pass when none selected)
        sel_amb = state.selected_ambulance
        if sel_amb is None:
            add("ambulance_capability_ok", True,
                "no ambulance selected — nothing to check")
        else:
            row = self.db.get(models.Ambulance, sel_amb.id)
            if row is None:
                add("ambulance_capability_ok", False,
                    f"ambulance {sel_amb.id} not found in DB")
            else:
                needs_als = (severity or 0) >= 4 or pathway in _ALS_PATHWAYS
                ok = row.capability == "ALS" if needs_als else True
                add("ambulance_capability_ok", ok,
                    f"{row.id} capability={row.capability}, "
                    f"ALS required={needs_als}"
                    if ok else
                    f"{row.id} capability={row.capability} does not satisfy "
                    f"the ALS rule (severity={severity}, pathway={pathway})")

        # 3. ambulance_assignment_ok (vacuous pass when none selected)
        if sel_amb is None:
            add("ambulance_assignment_ok", True,
                "no ambulance selected — nothing to check")
        else:
            row = self.db.get(models.Ambulance, sel_amb.id)
            if row is None:
                add("ambulance_assignment_ok", False,
                    f"ambulance {sel_amb.id} not found in DB")
            else:
                ok = (row.status == "en_route"
                      and row.assigned_incident == state.incident_id)
                add("ambulance_assignment_ok", ok,
                    f"{row.id} status={row.status} "
                    f"assigned_incident={row.assigned_incident}"
                    if ok else
                    f"{row.id} not en_route to this incident "
                    f"(status={row.status}, "
                    f"assigned_incident={row.assigned_incident})")

        # 4. hospital_bed_ok (vacuous pass when none selected)
        sel_hosp = state.selected_hospital
        if sel_hosp is None:
            add("hospital_bed_ok", True,
                "no hospital selected — nothing to check")
        else:
            row = self.db.get(models.Hospital, sel_hosp.id)
            if row is None:
                add("hospital_bed_ok", False,
                    f"hospital {sel_hosp.id} not found in DB")
            else:
                ok = (pathway in (row.specialties or [])
                      and state.hospital_status == "reserved")
                add("hospital_bed_ok", ok,
                    f"{row.name}: pathway {pathway!r} covered, "
                    f"hospital_status={state.hospital_status}"
                    if ok else
                    f"{row.name}: pathway {pathway!r} in "
                    f"specialties={row.specialties}, "
                    f"hospital_status={state.hospital_status}")

        # 5. resource_consistency_ok
        dispatch_out = state.agent_outputs.get("DispatchAgent")
        hosp_out = state.agent_outputs.get("HospitalLiaisonAgent")
        consistent = True
        details: list[str] = []
        if dispatch_out is not None:
            if not dispatch_out.success and sel_amb is not None:
                consistent = False
                details.append("DispatchAgent failed but an ambulance is "
                               "selected")
            if dispatch_out.success and sel_amb is None:
                consistent = False
                details.append("DispatchAgent succeeded but no ambulance is "
                               "selected")
        if hosp_out is not None:
            if not hosp_out.success and sel_hosp is not None:
                consistent = False
                details.append("HospitalLiaisonAgent failed but a hospital is "
                               "selected")
            if hosp_out.success and sel_hosp is None:
                consistent = False
                details.append("HospitalLiaisonAgent succeeded but no "
                               "hospital is selected")
        add("resource_consistency_ok", consistent,
            "resources match agent outcomes" if consistent
            else "; ".join(details))

        # 6. required_fields_present
        loc = state.location
        fields_ok = (bool(state.incident_id) and bool(state.incident_type)
                     and -90.0 <= loc.lat <= 90.0
                     and -180.0 <= loc.lon <= 180.0
                     and state.patient_count >= 1)
        add("required_fields_present", fields_ok,
            f"incident_id={state.incident_id!r} "
            f"incident_type={state.incident_type!r} "
            f"location=({loc.lat}, {loc.lon}) "
            f"patient_count={state.patient_count}")

        # 7. triage_consistency_ok
        triage_out = state.agent_outputs.get("TriageAgent")
        if triage_out is None:
            add("triage_consistency_ok", False, "no TriageAgent output")
        else:
            dec = triage_out.decision or {}
            expected = (TriageAgent.SEVERITY_LABELS.get(severity)
                        if severity else None)
            label_ok = expected is not None and (
                dec.get("severity_label") == expected)
            disclaimer_ok = bool(dec.get("disclaimer"))
            ok = label_ok and disclaimer_ok
            add("triage_consistency_ok", ok,
                f"severity {severity} -> label {expected!r} vs decision "
                f"{dec.get('severity_label')!r}; disclaimer "
                f"{'present' if disclaimer_ok else 'missing'}")

        # 8. confidence_thresholds_ok (comms + self are excluded)
        skip = {"CommunicationAgent", "VerificationAgent"}
        low = [name for name, out in state.agent_outputs.items()
               if name not in skip
               and (not out.success or out.confidence < _CONFIDENCE_FLOOR)]
        add("confidence_thresholds_ok", not low,
            "all agents meet the 0.6 confidence floor" if not low
            else f"below threshold or failed: {', '.join(low)}")

        passed = all(c.passed for c in checks)
        issues = [f"{c.name}: {c.detail}" for c in checks if not c.passed]
        state.verification_results = VerificationResults(
            passed=passed, issues=issues, checks=checks)

        decision: dict = {
            "passed": passed,
            "checks": [c.model_dump() for c in checks],
            "issues": issues,
        }
        if passed:
            return self.envelope(
                state, status="success", confidence=0.99, decision=decision,
                reasoning_summary="All verification checks passed.",
                action="Verification passed")
        decision["verification_error"] = "; ".join(issues)
        failed_names = ", ".join(c.name for c in checks if not c.passed)
        return self.envelope(
            state, status="failed", confidence=0.45, decision=decision,
            reasoning_summary=(f"Verification failed on "
                               f"{len(issues)} check(s): {failed_names}."),
            warnings=issues,
            action="Verification failed")
