"""TriageAgent — deterministic severity/pathway routing.

Severity comes from a fixed map, with a deterministic vitals safety
override: absent/agonal breathing or severe bleeding forces severity 5;
labored breathing floors it at 4. A real LLM may assess *unknown* incident
types only, and its reply is strictly validated (ignored on any failure).
"""
from __future__ import annotations

import re

from app.agents.agent_registry import register
from app.agents.base_agent import BaseAgent
from app.schemas import AgentOutput, IncidentState, TriageResult

_LLM_SEV_RE = re.compile(r"SEVERITY\s*=\s*([1-5])")
_LLM_PATHWAY_RE = re.compile(r"PATHWAY\s*=\s*([\w-]+)")
_LLM_CONF_RE = re.compile(r"CONFIDENCE\s*=\s*([01](?:\.\d+)?)")


@register
class TriageAgent(BaseAgent):
    """Clinical router: severity 1-5 + care pathway from a deterministic map."""

    name = "TriageAgent"
    version = "2.0"

    SEVERITY_MAP = {
        "cardiac_arrest": 5,
        "road_accident": 4,
        "stroke": 4,
        "trauma_fall": 3,
        "obstetric": 3,
        "chest_pain": 2,
    }
    PATHWAY_MAP = {
        "cardiac_arrest": "cath-lab",
        "road_accident": "trauma-center",
        "stroke": "stroke-unit",
        "trauma_fall": "general-er",
        "obstetric": "obstetric",
        "chest_pain": "general-er",
    }
    VALID_PATHWAYS = {"cath-lab", "trauma-center", "stroke-unit",
                      "general-er", "obstetric"}
    SEVERITY_LABELS = {5: "CRITICAL", 4: "HIGH", 3: "MODERATE",
                       2: "LOW", 1: "LOW"}
    PRIORITY_MAP = {5: "P1", 4: "P2", 3: "P3"}
    DISCLAIMER = ("Prototype triage aid — NOT a medical diagnosis. Final "
                  "clinical decisions rest with qualified medical personnel.")

    # -- LLM assist (unknown incident types only) --------------------------
    def _llm_assess(self, incident_type: str) -> tuple[int, str, float] | None:
        """Ask a real LLM to assess an unknown incident type. Strictly
        validated; returns None on any failure (deterministic fallback)."""
        prompt = (
            "You are an emergency triage assistant. An emergency report "
            f"arrived with incident type '{incident_type}'. "
            "Reply with exactly: SEVERITY=<1-5> PATHWAY=<pathway> "
            "CONFIDENCE=<0-1>. SEVERITY: 1=minor ... 5=critical. PATHWAY is "
            "one of: cath-lab, trauma-center, stroke-unit, general-er, "
            "obstetric. Nothing else."
        )
        resp = self.llm_assist(prompt)
        if not resp:
            return None
        sev = _LLM_SEV_RE.search(resp)
        pw = _LLM_PATHWAY_RE.search(resp)
        cf = _LLM_CONF_RE.search(resp)
        if not (sev and pw and cf):
            return None
        pathway = pw.group(1).strip().lower()
        if pathway not in self.VALID_PATHWAYS:
            return None
        try:
            confidence = float(cf.group(1))
        except ValueError:
            return None
        if not 0.0 <= confidence <= 1.0:
            return None
        return int(sev.group(1)), pathway, confidence

    # -- main ---------------------------------------------------------------
    async def run(self, state: IncidentState) -> AgentOutput:
        itype = state.incident_type or "unknown"
        known = itype in self.SEVERITY_MAP
        severity = self.SEVERITY_MAP.get(itype, 3)   # fallback: medium
        pathway = self.PATHWAY_MAP.get(itype, "general-er")
        confidence = 0.95 if known else 0.55
        llm_used = False

        # A real LLM gets a say only on unknown types; the map wins otherwise.
        # SAFETY RAIL: an LLM must never be able to bypass the human-review
        # floor with an overconfident guess. Its confidence is capped below
        # 0.6, so LLM-assessed unknown types ALWAYS route to human review —
        # the deterministic 0.55 default and the LLM path behave identically
        # w.r.t. the review guarantee.
        if not known:
            assessed = self._llm_assess(itype)
            if assessed:
                severity, pathway, llm_confidence = assessed
                llm_used = True
                confidence = min(llm_confidence, 0.59)

        # Deterministic vitals safety override — always applies.
        warnings: list[str] = []
        breathing = (state.breathing_status or "normal").lower()
        bleeding = (state.bleeding_status or "none").lower()
        if breathing in ("absent", "agonal") or bleeding == "severe":
            if severity != 5:
                warnings.append(
                    "Vitals override: "
                    f"breathing={breathing}, bleeding={bleeding} — "
                    "severity escalated to 5.")
            severity = 5
        elif breathing == "labored" and severity < 4:
            warnings.append(
                "Vitals override: labored breathing — severity raised to 4.")
            severity = 4

        label = self.SEVERITY_LABELS[severity]
        priority = self.PRIORITY_MAP.get(severity, "P4")
        requires_human = confidence < 0.6
        status = "success" if confidence >= 0.6 else "failed"

        summary = (f"Triaged {itype} as severity {severity} ({label}, "
                   f"{priority}) on the {pathway} pathway.")
        if llm_used:
            summary += " (LLM-assisted assessment)"
        if requires_human:
            summary += " Below confidence threshold — needs human triage."

        state.severity = severity
        state.triage_result = TriageResult(
            severity=severity, pathway=pathway,
            confidence=confidence, rationale=summary)

        decision = {
            "severity": severity,
            "severity_label": label,
            "priority": priority,
            "care_pathway": pathway,
            "warnings": warnings,
            "disclaimer": self.DISCLAIMER,
            "llm_used": llm_used,
            "known_type": known,
        }
        return self.envelope(
            state, status=status, confidence=confidence, decision=decision,
            reasoning_summary=summary, warnings=warnings,
            requires_human=requires_human, action="Triage decision")
